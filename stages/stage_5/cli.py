"""CLI entry point for Stage 5: video assembly.

Usage:
    python -m stages.stage_5 --project death_of_gwen_stacy
    python -m stages.stage_5 --project foo --force
"""
import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .pipeline import assemble_project


def main():
    parser = argparse.ArgumentParser(
        description="Stage 5: assemble 9:16 video with Ken Burns shots, audio mix (captions done externally in CapCut).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.stage_5 --project death_of_gwen_stacy
          python -m stages.stage_5 --project foo --force
        """),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--force", action="store_true",
                        help="Re-render even if final.mp4 / intermediates exist")
    parser.add_argument("--skip-review", action="store_true",
                        help="Bypass the review gate (ignored for answer_research/Q&A projects)")
    parser.add_argument("--panels-only", action="store_true",
                        help="Build shots + panel_sheet.jpg then stop, before any ffmpeg render")
    args = parser.parse_args()

    try:
        result = assemble_project(
            args.project,
            force=args.force,
            skip_review=args.skip_review,
            panels_only=args.panels_only,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.panels_only:
        print(f"\n✓ Stage 5 panels-only complete")
        print(f"   panel sheet:  {Path(result.shots_dir).parent / 'panel_sheet.jpg'}")
        print(f"   shots:        {result.shot_count} (across {result.scene_count} scenes)")
        return

    print(f"\n✓ Stage 5 complete")
    print(f"   final:        {result.final_path}")
    print(f"   duration:     {result.duration_seconds:.2f}s")
    print(f"   shots:        {result.shot_count} (across {result.scene_count} scenes)")
    final = Path(result.final_path)
    if final.exists():
        size_mb = final.stat().st_size / (1024 * 1024)
        print(f"   size:         {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
