"""
CLI entry point for Stage 2: page preprocessing.

Usage:
    python -m stages.stage_2 --project death_of_gwen_stacy
    python -m stages.stage_2 --project foo --force   # ignore cache
"""
import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .pipeline import preprocess_project


def main():
    parser = argparse.ArgumentParser(
        description="Stage 2: Scrape comic pages and VLM-preprocess each one.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.stage_2 --project death_of_gwen_stacy
          python -m stages.stage_2 --project foo --force
        """),
    )
    parser.add_argument("--project", required=True, help="Project name (must have comic_context.json)")
    parser.add_argument("--force", action="store_true", help="Ignore SHA-256 cache and re-process")
    args = parser.parse_args()

    try:
        results = preprocess_project(args.project, force_refresh=args.force)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    total = len(results)
    story = sum(1 for r in results if r.get("is_story_page"))
    print(f"\n✓ Stage 2 complete — {story}/{total} story pages")
