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
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Cartesia speed 0.6-1.5 (capped near 1.2 in practice). Default 1.0.")
    parser.add_argument("--atempo", type=float, default=1.35,
                        help="ffmpeg atempo factor (preserves pitch). Default 1.35 → "
                             "~3.3 wps, the user-chosen snappy pace (the slow Carl voice "
                             "needs it). 1.0 disables.")
    parser.add_argument("--voice", default=None, help="Cartesia voice UUID (overrides default)")
    parser.add_argument("--model", default=None, help="Cartesia model id (overrides default sonic-2)")
    parser.add_argument("--emotion", default=None,
                        help="Base Cartesia emotion (e.g. melancholic, contemplative, confident). "
                             "Default: derived from narration mode. Per-scene <emotion> tags add the arc.")
    parser.add_argument("--flat", action="store_true",
                        help="Disable per-scene emotion SSML — single base emotion for the whole video.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if audio.wav exists")
    parser.add_argument("--skip-review", action="store_true",
                        help="Bypass the review gate (ignored for answer_research/Q&A projects)")
    args = parser.parse_args()

    if not (0.5 <= args.speed <= 2.0):
        print(f"WARN: speed={args.speed} is outside the typical 0.7-1.3 range", file=sys.stderr)

    try:
        result = synthesize_project(
            args.project,
            speed=args.speed,
            post_atempo=args.atempo,
            emotion=args.emotion,
            flat=args.flat,
            voice_id=args.voice,
            model=args.model,
            force=args.force,
            skip_review=args.skip_review,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Stage 4 complete")
    print(f"   audio:           {result.audio_path}")
    print(f"   duration:        {result.audio_duration_seconds:.2f}s")
    print(f"   scenes aligned:  {len(result.scene_timings)}")
    print(f"   caption chunks:  {len(result.caption_chunks)}")
    # micro_moment may legitimately run long (whole-moment story); recap / Q&A still
    # flag >58s. Soft note only.
    soft_cap = 100 if result.mode == "micro_moment" else 58
    if result.audio_duration_seconds > soft_cap:
        print(f"   ⚠️  audio > {soft_cap}s — algo-unfriendly for Shorts; consider higher --speed")


if __name__ == "__main__":
    main()
