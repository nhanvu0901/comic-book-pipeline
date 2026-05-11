"""CLI entry point for Stage 5: video assembly.

Usage:
    python -m stages.stage_5 --project death_of_gwen_stacy
    python -m stages.stage_5 --project foo --force
    python -m stages.stage_5 --project foo --bg-music path/to/track.mp3
    python -m stages.stage_5 --project foo --no-music
"""
import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .pipeline import assemble_project


def main():
    parser = argparse.ArgumentParser(
        description="Stage 5: assemble 9:16 video with Ken Burns shots, captions, audio mix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.stage_5 --project death_of_gwen_stacy
          python -m stages.stage_5 --project foo --force
          python -m stages.stage_5 --project foo --bg-music assets/bgm/dramatic.mp3
          python -m stages.stage_5 --project foo --no-music
        """),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--force", action="store_true",
                        help="Re-render even if final.mp4 / intermediates exist")
    parser.add_argument("--bg-music", default=None,
                        help="Override BGM path (env BG_MUSIC_PATH otherwise)")
    parser.add_argument("--no-music", action="store_true",
                        help="Disable BGM entirely; narration-only mix")
    args = parser.parse_args()

    try:
        result = assemble_project(
            args.project,
            bg_music_path=args.bg_music,
            enable_music=not args.no_music,
            force=args.force,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Stage 5 complete")
    print(f"   final:        {result.final_path}")
    print(f"   duration:     {result.duration_seconds:.2f}s")
    print(f"   shots:        {result.shot_count} (across {result.scene_count} scenes)")
    print(f"   captions:     {result.caption_path}")
    print(f"   bgm:          {result.bgm_used or '(none)'}")
    final = Path(result.final_path)
    if final.exists():
        size_mb = final.stat().st_size / (1024 * 1024)
        print(f"   size:         {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
