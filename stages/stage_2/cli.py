"""
CLI entry point for Stage 2.

Two flows:

  1. Default — preprocess pages that were already downloaded.
     Requires comic_context.json (from Stage 1) and raw_comic/manifest.json.

       python -m stages.stage_2 --project death_of_gwen_stacy
       python -m stages.stage_2 --project foo --force

  2. URL-direct — skip Stage 1 entirely. Paste a series URL + issues, OR
     a list of reader URLs. Auto-bootstraps a minimal comic_context.json,
     enriches it from wiki/fandom (silent), downloads, then preprocesses.

       # Series + range
       python -m stages.stage_2 --project venom \\
           --url "https://batcave.biz/6587-what-if-dark-venom-2023.html" \\
           --issues "#1-3"

       # Multiple reader URLs (one issue each)
       python -m stages.stage_2 --project venom \\
           --reader-urls \\
             "https://batcave.biz/reader/6587/34073" \\
             "https://batcave.biz/reader/6587/34074"

       # Same but skip enrichment (faster, lower-quality narration)
       python -m stages.stage_2 --project venom --url "..." --no-enrich

       # Download only (skip preprocessing — useful for inspecting pages first)
       python -m stages.stage_2 --project venom --url "..." --download-only
"""
import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .pipeline import preprocess_project
from .url_mode import download_from_readers, download_from_series


def main():
    parser = argparse.ArgumentParser(
        description="Stage 2: Download + preprocess comic pages. Supports URL-direct mode (no Stage 1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          # Default (needs Stage 1's comic_context.json already)
          python -m stages.stage_2 --project death_of_gwen_stacy
          python -m stages.stage_2 --project foo --force

          # URL-direct: series page + issues range
          python -m stages.stage_2 --project venom \\
              --url "https://batcave.biz/6587-what-if-dark-venom-2023.html" \\
              --issues "#1-3"

          # URL-direct: multiple reader URLs
          python -m stages.stage_2 --project venom \\
              --reader-urls "https://batcave.biz/reader/6587/34073" "https://batcave.biz/reader/6587/34074"
        """),
    )
    parser.add_argument("--project", required=True, help="Project name (folder under projects/)")
    parser.add_argument("--force", action="store_true",
                        help="Ignore SHA-256 cache and re-process pages")

    # URL-direct mode (mutually exclusive)
    url_group = parser.add_mutually_exclusive_group()
    url_group.add_argument("--url",
                           help="batcave.biz series URL — pairs with --issues")
    url_group.add_argument("--reader-urls", nargs="+", metavar="URL",
                           help="One or more batcave.biz reader URLs (each = 1 issue)")

    parser.add_argument("--issues", default="",
                        help="Issues range/list when --url is given (e.g. '#1-3', '#1,#3,#5')")
    parser.add_argument("--no-enrich", action="store_true",
                        help="Skip silent wiki/fandom enrichment after URL-direct download")
    parser.add_argument("--download-only", action="store_true",
                        help="Stop after download (skip Magi panel detect + VLM preprocessing)")

    args = parser.parse_args()
    using_url_mode = bool(args.url or args.reader_urls)

    try:
        if using_url_mode:
            enrich = not args.no_enrich
            if args.url:
                summary = download_from_series(
                    args.project, args.url, args.issues, enrich=enrich,
                )
            else:
                summary = download_from_readers(
                    args.project, args.reader_urls, enrich=enrich,
                )
            print(f"\n✓ URL-direct download — {summary['total_pages']} pages "
                  f"across {summary['chapters']} chapter(s)")
            print(f"  context: {summary['context_path']}")
            print(f"  manifest: {summary['manifest_path']}")

            if args.download_only:
                print("--download-only set: stopping before preprocessing.")
                return

        results = preprocess_project(args.project, force_refresh=args.force)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    total = len(results)
    story = sum(1 for r in results if r.get("is_story_page"))
    print(f"\n✓ Stage 2 complete — {story}/{total} story pages")
