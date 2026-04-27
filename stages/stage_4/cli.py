"""
CLI entry point for Stage 4: Cartesia TTS.

Usage:
    python -m stages.stage_4 --project death_of_gwen_stacy
    python -m stages.stage_4 --project foo --speed 1.1
    python -m stages.stage_4 --project foo --voice <voice_id> --force
"""
import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .pipeline import synthesize_project


def main():
    parser = argparse.ArgumentParser(
        description="Stage 4: Synthesize audio via Cartesia TTS and align timings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.stage_4 --project death_of_gwen_stacy
          python -m stages.stage_4 --project foo --speed 1.1
          python -m stages.stage_4 --project foo --force
        """),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--speed", type=float, default=1.0, help="0.7-1.3, default 1.0")
    parser.add_argument("--voice", default=None, help="Cartesia voice UUID (overrides default)")
    parser.add_argument("--model", default=None, help="Cartesia model id (overrides default sonic-2)")
    parser.add_argument("--force", action="store_true", help="Regenerate even if audio.wav exists")
    args = parser.parse_args()

    if not (0.5 <= args.speed <= 2.0):
        print(f"WARN: speed={args.speed} is outside the typical 0.7-1.3 range", file=sys.stderr)

    try:
        result = synthesize_project(
            args.project,
            speed=args.speed,
            voice_id=args.voice,
            model=args.model,
            force=args.force,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Stage 4 complete")
    print(f"   audio:           {result.audio_path}")
    print(f"   duration:        {result.audio_duration_seconds:.2f}s")
    print(f"   scenes aligned:  {len(result.scene_timings)}")
    print(f"   caption chunks:  {len(result.caption_chunks)}")
    if result.audio_duration_seconds > 58:
        print(f"   ⚠️  audio > 58s — algo-unfriendly for Shorts; consider higher --speed")


if __name__ == "__main__":
    main()
