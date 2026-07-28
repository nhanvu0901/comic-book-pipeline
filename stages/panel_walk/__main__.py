"""CLI: python -m stages.panel_walk --project <slug> [--title T] [--hook H]

Reads projects/<slug>/preprocessed/ and writes projects/<slug>/narration.json, which Stage 4
(TTS) and Stage 5 (render) then consume unchanged — this mode replaces Stage 1 + Stage 3, not
the stages after them.
"""
from __future__ import annotations

import argparse
import sys

from .narrate import build_narration
from ..stage_3.pipeline import save_narration


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="python -m stages.panel_walk",
        description="Long-form narration by walking every panel in reading order.",
        epilog="Requires Stage 2 to have run with VLM_EXTRACT=1 (real visual descriptions).",
    )
    ap.add_argument("--project", required=True, help="Project folder under projects/")
    ap.add_argument("--title", default="", help="Video title (defaults to the comic title)")
    ap.add_argument("--hook", default="", help="Spoken opening line over the cover")
    args = ap.parse_args()

    try:
        narration = build_narration(args.project, title=args.title, hook=args.hook,
                                    progress=print)
    except Exception as exc:                          # noqa: BLE001 - CLI boundary
        print(f"\n✗ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    path = save_narration(narration, args.project, progress=print)
    body = [s for s in narration.scenes if not s.is_intro]
    print(f"\n✓ panel-walk complete\n"
          f"   narration:  {path}\n"
          f"   scenes:     {len(narration.scenes)} ({len(body)} panels narrated)\n"
          f"   words:      {narration.total_word_count}\n"
          f"   duration:   ~{round(narration.estimated_duration_seconds / 60, 1)} min "
          f"at {narration.words_per_second} wps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
