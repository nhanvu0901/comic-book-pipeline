"""
Long-form (8+ min) orchestrator — SEGMENT-AND-STITCH (see LONGFORM_DESIGN.md).

Core pipeline makes ONE tight ~60-90s segment. Long-form runs it N times (each
segment a normal sub-project through Stage 3->4->5) then seamlessly concats. NO
core edit — this is a thin layer over the frozen stage interfaces.

Every phase prints ONE machine-parseable status line (mirrors answer_pipeline):

    [longform] step=<name> status=<ok|fail> detail=...

decompose.py / stitch.py are built by another crew to the FROZEN interfaces in
the spec — imported lazily inside run_longform so this module imports (and its
unit test runs) even before those files exist.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from config import get_project_dirs

# Orchestrator's high-level mode -> the Stage 3 narration mode key it maps onto.
# (Verified against produced projects: recaps use "recap_summary", Q&A uses
# "explore_answer".) Keeping the public mode as {"recap","qa"} shields callers
# from Stage 3's internal catalog.
_STAGE3_MODE = {"recap": "recap_summary", "qa": "explore_answer"}

# Q&A only: target_minutes -> how many countdown items to research. Each segment
# is a normal ~60s countdown of ~4 items, so N segments ~= N minutes.
# ponytail: linear heuristic; if research quality caps out, decompose_qa can
# clamp the group count itself.
_QA_ITEMS_PER_SEGMENT = 4


def _status(log: Callable[[str], None], step: str, status: str, detail: str) -> None:
    log(f"[longform] step={step} status={status} detail={detail}")


def _pick_voice(mode: str, source: str, project: str, segments: list[str],
                log: Callable[[str], None]) -> str | None:
    """Pick the narrator voice ONCE so timbre is identical across every segment.

    Reads the best-available comic_context (the whole saga for recap, the
    top-level project for Q&A, falling back to the first segment) and asks the
    Resemble voice-picker. select_voice never raises (internal default
    fallback), so a missing/empty context just yields the default voice."""
    import json

    from stages.stage_4.resemble_tts import select_voice

    ctx_slug = source if mode == "recap" else project
    candidates = [ctx_slug] + (segments[:1] if segments else [])
    ctx: dict = {}
    for slug in candidates:
        p = get_project_dirs(slug)["root"] / "comic_context.json"
        if p.exists():
            try:
                ctx = json.loads(p.read_text())
                break
            except Exception:  # noqa: BLE001 - bad json = treat as no context
                continue
    voice_id, name = select_voice({}, ctx, log=log)
    _status(log, "voice", "ok", f"picked {name} ({voice_id})")
    return voice_id


def run_longform(mode: str, *, source: str, target_minutes: float = 8.0,
                 project: str, atempo: float = 1.35,
                 stop_after: str | None = None,
                 log: Callable[[str], None] | None = None) -> str:
    """Decompose `source` into N sub-projects, run Stage 3->5 on each, stitch.

    mode in {"recap","qa"}. recap: `source` is an already-downloaded+preprocessed
    saga project slug (one segment per issue). qa: `source` is the question text.

    Fail-loud PER segment — a segment that errors (including the Q&A review gate
    raising SystemExit) is logged and skipped; the run continues and reports which
    segments shipped. stop_after in {"decompose","segments","stitch"} for resume.
    Returns the stitched final.mp4 path (empty string for early stops)."""
    log = log or print
    mode = mode.lower()
    if mode not in _STAGE3_MODE:
        raise ValueError(f"mode must be 'recap' or 'qa', got {mode!r}")
    stage3_mode = _STAGE3_MODE[mode]

    # ── 1. DECOMPOSE ──────────────────────────────────────────────────────────
    from .decompose import decompose_qa, decompose_recap

    if mode == "recap":
        segments = decompose_recap(source, log=log)
    else:
        max_items = max(12, round(target_minutes) * _QA_ITEMS_PER_SEGMENT)
        segments = decompose_qa(source, project, max_items=max_items, log=log)
    _status(log, "decompose", "ok",
            f"{len(segments)} segment(s): {','.join(segments)}")
    if stop_after == "decompose":
        return ""

    # ── 2. PER-SEGMENT Stage 3->4->5 (fail-loud per segment) ──────────────────
    from stages.stage_3.pipeline import save_narration, write_script
    from stages.stage_4.pipeline import synthesize_project
    from stages.stage_5.pipeline import assemble_project

    voice_id = _pick_voice(mode, source, project, segments, log)
    shipped: list[Path] = []
    for slug in segments:
        try:
            # QA segments arrive with raw pages only (decompose_qa runs download, NOT
            # Stage 2) — Stage 3 needs preprocessed/*.json + panel vectors, so preprocess
            # here. Recap segments already carry copied preprocessed JSONs → skip.
            if mode == "qa":
                from stages.stage_2.pipeline import preprocess_project
                preprocess_project(slug)
                _status(log, "preprocess", "ok", slug)
            nar = write_script(slug, stage3_mode)
            save_narration(nar, slug)
            synthesize_project(slug, post_atempo=atempo, force=True,
                               voice_id=voice_id,
                               # recap = single-issue, panels auto-picked → no gate.
                               # qa = Master locks panels per segment → honor gate.
                               skip_review=(mode == "recap"))
            assemble_project(slug, force=True)
            final_mp4 = get_project_dirs(slug)["root"] / "final.mp4"
            shipped.append(final_mp4)
            _status(log, "segment", "ok", f"{slug} -> {final_mp4}")
        except SystemExit as exc:  # Q&A review gate blocks until Master approves
            _status(log, "segment", "fail", f"{slug} ReviewGateBlocked: {exc}")
        except Exception as exc:  # noqa: BLE001 - one bad issue must not abort the video
            _status(log, "segment", "fail", f"{slug} {type(exc).__name__}: {exc}")

    _status(log, "segments", "ok" if shipped else "fail",
            f"{len(shipped)}/{len(segments)} shipped: "
            f"{','.join(p.parent.name for p in shipped)}")
    if not shipped:
        raise RuntimeError("no segments shipped — nothing to stitch")
    if stop_after == "segments":
        return ""

    # ── 3. STITCH ─────────────────────────────────────────────────────────────
    from .stitch import stitch_segments

    out_path = get_project_dirs(project)["root"] / "final.mp4"
    stitch_segments(shipped, out_path, log=log)
    _status(log, "stitch", "ok", str(out_path))
    return str(out_path)
