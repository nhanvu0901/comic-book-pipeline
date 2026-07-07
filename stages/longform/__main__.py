"""
CLI for the long-form (8+ min) orchestrator. No interactivity — every decision
is a flag or a sane default so an agent can drive it unattended. run_longform
already prints one machine-parseable status line per phase:

    [longform] step=<name> status=<ok|fail> detail=...

Usage:
    python -m stages.longform --mode recap --source my_saga_slug --project my_saga_long
    python -m stages.longform --mode qa --source "Who has survived the Penance Stare?" --project penance_long
    python -m stages.longform --mode recap --source my_saga_slug --project my_saga_long --stop-after decompose

--source is the already-downloaded+preprocessed SAGA PROJECT SLUG for recap, or
the QUESTION TEXT for qa. --stop-after lets you inspect a phase / resume after
fixing a bad segment (a failed segment is skipped, never aborts the run).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from stages.longform.orchestrator import run_longform


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m stages.longform",
        description="Long-form (8+ min) segment-and-stitch: recap saga or Q&A question -> one long mp4.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--mode", required=True, choices=["recap", "qa"])
    p.add_argument("--source", required=True,
                   help="Saga project slug (recap) or question text (qa).")
    p.add_argument("--project", required=True, help="Output project slug under projects/.")
    p.add_argument("--target-minutes", type=float, default=8.0,
                   help="Target length. Q&A: drives item count. Recap: informational "
                        "(segment count = saga issue count). Default 8.")
    p.add_argument("--atempo", type=float, default=1.35,
                   help="ffmpeg atempo passed to every segment's Stage 4. Default 1.35.")
    p.add_argument("--stop-after", choices=["decompose", "segments", "stitch"], default=None,
                   help="Run through this phase then stop (default: stitch = full run).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        final = run_longform(
            args.mode, source=args.source, target_minutes=args.target_minutes,
            project=args.project, atempo=args.atempo, stop_after=args.stop_after,
            log=print)
    except Exception as exc:  # noqa: BLE001 - report uniformly, agent parses status=fail
        print(f"[longform] step=run status=fail detail={type(exc).__name__}: {exc}")
        return 1
    if final:
        print(f"[longform] step=done status=ok detail={final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
