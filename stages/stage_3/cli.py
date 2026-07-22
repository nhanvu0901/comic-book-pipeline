"""
CLI entry point for Stage 3: narration synthesis.

Usage:
    python -m stages.stage_3 --project death_of_gwen_stacy
    python -m stages.stage_3 --project death_of_gwen_stacy --mode hot_take
    python -m stages.stage_3 --project foo --mode micro-moment   # single-moment 30-50s Short
                                                                 # (reads comic_context.json's
                                                                 #  "target_moment" field)

If --mode is omitted, the LLM proposes 3 modes and you pick one interactively.
Hyphens in a mode name are accepted (micro-moment == micro_moment).
"""
import argparse
import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .modes import MODES_BY_KEY
from .pipeline import propose_modes, write_script, save_narration, reanchor_project


def main():
    parser = argparse.ArgumentParser(
        description="Stage 3: Propose narration modes and write a ≤58s script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.stage_3 --project death_of_gwen_stacy
          python -m stages.stage_3 --project death_of_gwen_stacy --mode tragedy
          python -m stages.stage_3 --project foo --mode hot_take --hook "Most villains take years to reveal themselves."
        """),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--mode",
        type=lambda s: s.strip().replace("-", "_"),  # accept micro-moment == micro_moment
        choices=sorted(MODES_BY_KEY.keys()),
        help="Skip mode proposal and use this mode directly.",
    )
    parser.add_argument("--hook", default="", help="Optional hook hint to steer the opening line.")
    parser.add_argument(
        "--no-embed", action="store_true",
        help="Skip all embedding/vector work (beat grounding + vector pin) — narration-only "
             "test mode. Use when the embedding server (LM Studio/Qwen) isn't running; "
             "narration.json still writes full scenes/visual_beats text, just without "
             "vector-grounded page_ref/panel_ref pins (Stage 5's own matcher handles that).",
    )
    parser.add_argument(
        "--reanchor", action="store_true",
        help="Re-run CONTENT anchoring on the EXISTING narration.json (fix drifted "
             "page_ref/panel_ref without re-narrating — TTS audio stays valid), then exit.",
    )
    args = parser.parse_args()

    if args.reanchor:
        changed = reanchor_project(args.project, progress=print)
        print(f"\n{'✓ re-anchored' if changed else '· no change'}: {args.project}")
        return

    if args.no_embed:
        os.environ["STAGE3_NO_EMBED"] = "1"

    chosen_mode = args.mode
    hook_hint = args.hook

    if not chosen_mode:
        print(f"🎭 Proposing narration modes for '{args.project}'...\n")
        proposals = propose_modes(args.project, n=3, progress=print)
        for i, p in enumerate(proposals, start=1):
            print(f"  [{i}] {p.mode}")
            print(f"      hook: {p.hook}")
            print(f"      why:  {p.rationale}\n")

        while True:
            choice = input("Pick a mode [1/2/3] or type mode key: ").strip().lower()
            if choice in ("1", "2", "3") and int(choice) <= len(proposals):
                chosen = proposals[int(choice) - 1]
                chosen_mode = chosen.mode
                hook_hint = chosen.hook
                break
            if choice in MODES_BY_KEY:
                chosen_mode = choice
                break
            print("  (enter 1/2/3 or a valid mode key)")

    print(f"\n✍️  Writing script in mode: {chosen_mode}")
    if hook_hint:
        print(f"    hook hint: {hook_hint}")

    nar = write_script(args.project, chosen_mode, hook_hint=hook_hint, progress=print)
    path = save_narration(nar, args.project, progress=print)

    print(f"\n✓ Saved: {path}")
    print(f"   mode:       {nar.mode}")
    print(f"   title:      {nar.title}")
    print(f"   scenes:     {len(nar.scenes)}")
    print(f"   words:      {nar.total_word_count}")
    # micro_moment is allowed to run long so the whole moment lands (Master, 2026-07-20);
    # recap / Q&A still target ≤58s. Soft note only — never trims.
    soft_cap = 100 if nar.mode == "micro_moment" else 58
    print(f"   duration:   ~{nar.estimated_duration_seconds}s (target ≤{soft_cap}s)")
    if nar.estimated_duration_seconds > soft_cap:
        print(f"   ⚠️  OVER {soft_cap}s — run again or edit narration.json before Stage 4")
    print()


if __name__ == "__main__":
    main()
