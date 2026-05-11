"""
Stage 4 orchestrator: load inputs, propose modes (Phase 0),
then run outline -> glossary -> write_scenes (Phases A/B/C) for the chosen mode.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT, get_project_dirs
from .propose_modes import propose_modes as _propose_modes
from .write_script import write_script as _write_script
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

    debug_dump: dict = {"project": project_name, "mode": mode, "hook_hint": hook_hint}
    try:
        nar = _write_script(ctx, story, mode, hook_hint=hook_hint,
                            all_pages=pages,
                            progress=progress, debug_dump=debug_dump)
        debug_dump["status"] = "ok"
    except Exception as exc:
        debug_dump["status"] = "error"
        debug_dump["error"] = repr(exc)
        _write_run_dump(project_name, debug_dump, narration=None)
        raise
    nar.source_project = project_name
    debug_dump["narration"] = nar.to_dict()
    _write_run_dump(project_name, debug_dump, narration=nar)
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
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    log(f"[stage4] saved narration → {path}")
    return path


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
