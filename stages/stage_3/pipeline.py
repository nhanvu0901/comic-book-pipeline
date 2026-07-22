"""
Stage 4 orchestrator: load inputs, propose modes (Phase 0),
then run outline -> glossary -> write_scenes (Phases A/B/C) for the chosen mode.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT, TRANSPARENCY_RETRY, get_project_dirs
from .propose_modes import propose_modes as _propose_modes
from .write_script import (write_script as _write_script, _load_direction,
                           _transparency_critic, _transparency_has_heavy,
                           _format_clarity_fixes, _grounding_critic)
from .schema import Narration

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "stage_4_runs"


def load_inputs(project_name: str) -> tuple[dict, list[dict]]:
    """Load comic_context.json and every preprocessed/*.json."""
    root = PROJECTS_ROOT / project_name
    ctx_path = root / "comic_context.json"
    if not ctx_path.exists():
        raise FileNotFoundError(f"comic_context.json missing: {ctx_path}")

    ctx = json.loads(ctx_path.read_text())

    prep_dir = root / "preprocessed"
    if not prep_dir.exists():
        raise FileNotFoundError(f"preprocessed/ missing: {prep_dir}. Run Stage 2 first.")

    pages: list[dict] = []
    for p in sorted(prep_dir.glob("page_*.json")):
        try:
            pages.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    if not pages:
        raise RuntimeError(f"preprocessed/ has no parseable pages in {prep_dir}")

    return ctx, pages


def filter_story_pages(pages: list[dict]) -> list[dict]:
    """Keep only pages marked as story (skip covers, recaps, ads, preview pages)."""
    return [p for p in pages if p.get("is_story_page")]


def propose_modes(
    project_name: str,
    n: int = 3,
    *,
    progress: Callable[[str], None] | None = None,
):
    log = progress or (lambda _msg: None)
    log(f"[stage4] loading inputs for project={project_name}")
    ctx, pages = load_inputs(project_name)
    story = filter_story_pages(pages)
    log(f"[stage4] {len(pages)} preprocessed pages — {len(story)} story page(s) kept")
    return _propose_modes(ctx, story, n=n, progress=progress)


def write_script(
    project_name: str,
    mode: str,
    hook_hint: str = "",
    *,
    progress: Callable[[str], None] | None = None,
) -> Narration:
    log = progress or (lambda _msg: None)
    log(f"[stage4] loading inputs for project={project_name}")
    ctx, pages = load_inputs(project_name)
    story = filter_story_pages(pages)
    log(f"[stage4] {len(pages)} preprocessed pages — {len(story)} story page(s) kept")

    direction = _load_direction(project_name)
    if direction:
        log(f"[stage4] direction spec loaded: {sorted(direction.keys())}")

    debug_dump: dict = {"project": project_name, "mode": mode, "hook_hint": hook_hint}
    try:
        nar = _write_script(ctx, story, mode, hook_hint=hook_hint,
                            all_pages=pages, direction=direction,
                            progress=progress, debug_dump=debug_dump)
        debug_dump["status"] = "ok"
    except Exception as exc:
        debug_dump["status"] = "error"
        debug_dump["error"] = repr(exc)
        _write_run_dump(project_name, debug_dump, narration=None)
        raise
    nar.source_project = project_name

    # TRANSPARENCY / CLARITY CRITIC — single convergence point for all 3 modes (recap,
    # micro, Q&A all return their nar through here). FLAG + LOG only; optional one-shot
    # re-write behind TRANSPARENCY_RETRY (default OFF) when heavy flags remain.
    flags = _transparency_critic(nar, ctx, mode, progress=progress)
    if flags:
        for f in flags:
            log(f"[stage4] ⚠ {f}")
        log(f"[stage4] ⚠ transparency critic: {len(flags)} clarity flag(s) — "
            f"review narration before render")
        # Feedback-retry (not a blind re-roll): route the critic's flags back INTO the
        # writer prompt so it repairs the exact scenes flagged. Skip when the flags
        # produce no actionable fix block (would just be a no-op re-roll).
        clarity_fixes = _format_clarity_fixes(flags) if TRANSPARENCY_RETRY else ""
        if TRANSPARENCY_RETRY and _transparency_has_heavy(flags) and clarity_fixes:
            log("[stage4] TRANSPARENCY_RETRY on + heavy flag(s) — feedback re-writing once…")
            try:
                retry_dump = {"project": project_name, "mode": mode,
                              "hook_hint": hook_hint, "transparency_retry": True,
                              "clarity_fixes": clarity_fixes}
                nar2 = _write_script(ctx, story, mode, hook_hint=hook_hint,
                                     all_pages=pages, direction=direction,
                                     progress=progress, debug_dump=retry_dump,
                                     clarity_fixes=clarity_fixes)
                nar2.source_project = project_name
                flags2 = _transparency_critic(nar2, ctx, mode, progress=progress)
                if len(flags2) < len(flags):
                    log(f"[stage4] retry improved transparency {len(flags)}→{len(flags2)} "
                        f"— keeping re-write")
                    nar, flags = nar2, flags2
                else:
                    log(f"[stage4] retry did not improve ({len(flags2)} flag(s)) — "
                        f"keeping original")
            except Exception as exc:
                log(f"[stage4] transparency retry failed — keeping original: {exc!r}")
    debug_dump["transparency_flags"] = flags
    debug_dump["narration"] = nar.to_dict()
    _write_run_dump(project_name, debug_dump, narration=nar)
    sm = (debug_dump or {}).get("story_map")
    if sm:
        (get_project_dirs(project_name)["root"] / "story_map.json").write_text(
            json.dumps(sm, indent=2, ensure_ascii=False))
    return nar


def save_narration(
    narration: Narration,
    project_name: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    log = progress or (lambda _msg: None)
    root = get_project_dirs(project_name)["root"]
    path = root / "narration.json"
    data = narration.to_dict()
    _enrich_scenes_with_panel_metadata(data, root, log)
    # GROUNDING CHECK — text↔shown-panel, runs HERE (not at write_script) because this is
    # the only point each scene carries panel_description. FLAG + LOG only; catches a line
    # that asserts a concrete place/action the panel it plays over never shows. Soft-skips
    # when panel metadata is absent (Q&A panels are assigned later at stage 5).
    for gf in _grounding_critic(data.get("scenes", []), progress=log):
        log(f"[stage4] ⚠ {gf}")
    # Slim output (final update of beat): no visual-beat split. Stage 5 no longer
    # anchors panels to beats/visual_beats — it matches panels to the narration
    # semantically (forward-only, no-reuse, hold-while-same-subject), so the pace
    # follows what's being SAID, not a fixed beat highlight. page_ref stays in the
    # JSON as a legacy hint but is no longer the panel authority.
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    log(f"[stage4] saved narration → {path}")
    return path


def reanchor_project(project_name: str, *, progress: Callable[[str], None] | None = None) -> bool:
    """Re-run CONTENT anchoring on an existing narration.json (fix drifted page_refs
    WITHOUT re-narrating), then re-enrich panel metadata + save. Returns True if changed."""
    from .write_script import reanchor_narration
    log = progress or (lambda _msg: None)
    root = get_project_dirs(project_name)["root"]
    path = root / "narration.json"
    data = json.loads(path.read_text())
    if not reanchor_narration(data, progress=log):
        return False
    _enrich_scenes_with_panel_metadata(data, root, log)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    log(f"[stage4] re-anchored narration → {path}")
    return True


def _enrich_scenes_with_panel_metadata(
    data: dict,
    project_root: Path,
    log: Callable[[str], None],
) -> None:
    """Resolve panel_bbox + source_image + panel_description per scene from preprocessed pages."""
    prep_dir = project_root / "preprocessed"
    if not prep_dir.exists():
        log("[stage4] enrich: preprocessed/ missing, skipping panel metadata")
        return
    pages: dict[int, dict] = {}
    for p in sorted(prep_dir.glob("page_*.json")):
        try:
            page = json.loads(p.read_text())
            pn = int(page.get("page_number", 0) or 0)
            if pn:
                pages[pn] = page
        except (json.JSONDecodeError, ValueError):
            continue
    enriched = 0
    for scene in data.get("scenes", []):
        pref = int(scene.get("page_ref", 0) or 0)
        panel_idx = int(scene.get("panel_ref", -1) if scene.get("panel_ref") is not None else -1)
        page = pages.get(pref)
        if not page:
            continue
        scene["source_image"] = str(page.get("source_image", ""))
        scene["image_dimensions"] = page.get("image_dimensions") or {}
        panels = page.get("panels") or []
        match = next((p for p in panels if int(p.get("index", -1)) == panel_idx), None)
        if match:
            scene["panel_bbox"] = match.get("bbox") or {}
            scene["panel_description"] = match.get("description", "")
            scene["panel_characters"] = match.get("characters") or []
            scene["panel_dominant_emotion"] = match.get("dominant_emotion", "")
        else:
            scene["panel_bbox"] = {}
            scene["panel_description"] = ""
            scene["panel_characters"] = []
            scene["panel_dominant_emotion"] = ""
        enriched += 1
    log(f"[stage4] enrich: {enriched}/{len(data.get('scenes', []))} scenes with panel metadata")


def _write_run_dump(project_name: str, dump: dict, narration: Narration | None) -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    log_path = _LOG_DIR / f"{project_name}_{now.strftime('%Y%m%d-%H%M%S')}.log"
    payload = {
        "timestamp": now.isoformat(timespec="seconds"),
        **dump,
    }
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return log_path
