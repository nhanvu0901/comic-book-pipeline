"""
CLI entry point for Stage 5: ffmpeg video assembly.

Usage:
    python -m stages.stage_5 --project death_of_gwen_stacy
    python -m stages.stage_5 --project foo --keep-tmp    # keep _stage5/ for debug
"""
import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .pipeline import assemble_project


def main():
    parser = argparse.ArgumentParser(
        description="Stage 5: ffmpeg assembly — 9:16 video with Ken Burns + captions + audio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.stage_5 --project death_of_gwen_stacy
          python -m stages.stage_5 --project foo --keep-tmp
        """),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--keep-tmp", action="store_true",
                        help="Preserve _stage5/ (per-scene clips, captions, silent mp4) for debugging")
    args = parser.parse_args()

    try:
        final = assemble_project(args.project, keep_intermediates=args.keep_tmp)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Stage 5 complete → {final}")
    size_mb = final.stat().st_size / (1024 * 1024)
    print(f"   size:  {size_mb:.1f} MB")
