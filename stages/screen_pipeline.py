"""Agent-first orchestrator CLI for screen_qa mode.

Runs the full pipeline for a Screen-driven Q&A Short end to end:
research canon from screen wikis -> write screen narration citing film/show and year ->
synthesize TTS audio -> render final video.

Unlike comic pipelines, this mode has NO download or preprocess steps —
visuals are pure video clips / stills / cards, not comic page panels.

Status output:
    [screen-pipeline] step=<name> status=<ok|fail> detail=...

Usage:
    python -m stages.screen_pipeline --question "How did the Avengers travel back in time in Endgame?" --project endgame_time_travel
    python -m stages.screen_pipeline --project endgame_time_travel --skip-research --stop-after narrate
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Callable

from config import POST_ATEMPO, get_project_dirs

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

STEPS = ["research", "narrate", "tts", "render"]


# ─── Step implementations ───────────────────────────────────────────────────


def _step_research(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    """Scout screen canon from fandom wikis / Wikipedia and save screen_context.json."""
    if not args.question:
        raise ValueError("--question is required unless --skip-research")

    from stages.stage_1.screen_research import research_screen

    path = research_screen(
        args.question,
        args.project,
        max_items=args.max_items,
        hint=getattr(args, "hint", ""),
        log=log,
    )
    context = json.loads(path.read_text())
    return f"{len(context.get('items') or [])} item(s) -> {path.name}"


def _step_narrate(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    """Write screen narration citing movie/show title and year, with clip query visual beats."""
    from stages.stage_3.screen_qa import save_screen_narration, write_screen_qa

    nar = write_screen_qa(
        args.project,
        hook_hint=getattr(args, "hint", ""),
        progress=log,
    )
    path = save_screen_narration(nar, args.project, progress=log)
    return f"{len(nar.scenes)} scene(s), ~{nar.estimated_duration_seconds}s"


def _step_tts(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    """Synthesize speech audio from narration.json."""
    from stages.stage_4.pipeline import synthesize_project

    result = synthesize_project(
        args.project,
        post_atempo=args.atempo,
        force=True,
        skip_review=args.skip_review,
    )
    return f"audio {result.audio_duration_seconds:.1f}s"


def _step_render(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    """Render step: calls screen shot builder from video-qa/p3-visual via contract.

    Contract:
    - narration.json uses Scene schema with mode 'screen_qa';
    - visual_beats are dicts {text, query};
    - screen_context.json = {question, items:[{entity, event, adaptation_title, year, summary, visual_query, source_urls}]}.
    Until p3-visual lands, stub the render call behind that contract.
    """
    try:
        from stages.stage_5.screen_shots import build_shots_for_screen_qa

        log("[screen-pipeline] invoking p3-visual build_shots_for_screen_qa...")
        root = get_project_dirs(args.project)["root"]
        nar_path = root / "narration.json"
        if not nar_path.exists():
            raise FileNotFoundError(f"Missing {nar_path} for render")
        nar = json.loads(nar_path.read_text())
        # Call builder when available
        shots = build_shots_for_screen_qa(nar, {}, {})
        return f"{len(shots)} shot(s) built (p3-visual)"
    except (ImportError, AttributeError):
        log("[screen-pipeline] p3-visual shot builder not yet present; stubbing render per contract")
        return "stubbed screen_qa render (waiting for p3-visual)"


def _run_step(step: str, args: argparse.Namespace, log: Callable[[str], None]) -> str:
    """Dispatch step by name dynamically for monkeypatching in tests."""
    return globals()[f"_step_{step}"](args, log)


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m stages.screen_pipeline",
        description="screen_qa mode: screen question -> researched screen Short, end to end.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.screen_pipeline --question "How did the Avengers travel back in time in Endgame?" --project endgame_time_travel
          python -m stages.screen_pipeline --project endgame_time_travel --skip-research --stop-after narrate
        """),
    )
    parser.add_argument("--question", default="", help="The question to answer (required unless --skip-research).")
    parser.add_argument("--project", required=True, help="Project slug under projects/.")
    parser.add_argument("--max-items", type=int, default=5, help="Max screen items to research. Default 5.")
    parser.add_argument("--hint", default="", help="Optional grounding hint for research.")
    parser.add_argument(
        "--atempo",
        type=float,
        default=POST_ATEMPO,
        help="ffmpeg atempo for Stage 4 TTS. Default from config.POST_ATEMPO.",
    )
    parser.add_argument(
        "--skip-research",
        action="store_true",
        help="Skip research; reuse existing screen_context.json.",
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Pass-through to Stage 4 TTS.",
    )
    parser.add_argument(
        "--stop-after",
        choices=STEPS,
        default=None,
        help="Run through this step then stop (default: run all the way to render).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    stop_index = STEPS.index(args.stop_after) if args.stop_after else len(STEPS) - 1
    skip = {"research": args.skip_research}

    for i, step in enumerate(STEPS):
        if skip.get(step):
            print(f"[screen-pipeline] step={step} status=ok detail=skipped")
        else:
            try:
                detail = _run_step(step, args, print)
            except SystemExit as exc:
                print(f"[screen-pipeline] step={step} status=fail detail=ReviewGateBlocked: {exc}")
                return 1
            except Exception as exc:  # noqa: BLE001
                print(f"[screen-pipeline] step={step} status=fail detail={type(exc).__name__}: {exc}")
                return 1
            print(f"[screen-pipeline] step={step} status=ok detail={detail}")
        if i >= stop_index:
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
