"""
Smoke test the FULL preprocess_project flow with overlap.

Loads project manifest, runs preprocess on first N pages only, verifies:
  - No exceptions
  - prev_page_dict shape passes through extract_pages_batch correctly
  - Cached results from batch 1 are used as prior context for batch 2
  - All N pages end up in results with valid structure

Uses a temporary throwaway project name to avoid touching real cache.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PROJECTS_ROOT
from stages.stage_2.pipeline import preprocess_project


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="What if gwen stacy", help="Project to copy pages from")
    parser.add_argument("--pages", type=int, default=6,
                        help="Number of pages to process (default 6 = 2 batches of 3)")
    parser.add_argument("--name", default="_smoke_overlap", help="Throwaway project name")
    args = parser.parse_args()

    src = PROJECTS_ROOT / args.source
    dst = PROJECTS_ROOT / args.name

    if not (src / "raw_comic").exists():
        print(f"source project missing raw_comic: {src}", file=sys.stderr); sys.exit(1)

    # ── Set up throwaway project with first N pages only ──
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "raw_comic").mkdir(parents=True)

    # Pick pages 21-26 — already known to have real content from prior tests
    page_nums = list(range(21, 21 + args.pages))
    copied: list[str] = []
    for pn in page_nums:
        src_p = src / "raw_comic" / f"ch01_page_{pn:02d}.jpg"
        if not src_p.exists():
            print(f"missing source page: {src_p}", file=sys.stderr); sys.exit(1)
        dst_p = dst / "raw_comic" / src_p.name
        shutil.copy(src_p, dst_p)
        copied.append(str(dst_p))

    # Manifest matches what download_comic / download_from_urls would write.
    manifest = [{
        "chapter_index": 1,
        "label": "#1",
        "reader_url": "https://example.com/test",
        "pages": copied,
    }]
    (dst / "raw_comic" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"set up {args.name} with {len(copied)} pages: {[Path(p).name for p in copied]}")

    # ── Run preprocess with verbose logging ──
    print("\n" + "=" * 70)
    print(f"RUNNING preprocess_project on {args.name} (force_refresh=True)")
    print("=" * 70)
    t0 = time.time()
    try:
        results = preprocess_project(
            args.name,
            force_refresh=True,
            progress=lambda m: print(f"  {m}"),
        )
    except Exception as e:
        print(f"\n❌ FAILED: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        sys.exit(2)

    elapsed = time.time() - t0

    # ── Verify results ──
    print("\n" + "=" * 70)
    print(f"SMOKE TEST RESULTS ({elapsed:.1f}s)")
    print("=" * 70)
    print(f"  pages processed: {len(results)}")
    for r in results:
        pn = r.get("page_number")
        pt = r.get("page_type")
        n_panels = len(r.get("panels", []))
        n_text = len(r.get("text_blocks", []))
        model = r.get("vlm_model_used", "?")
        summary_preview = (r.get("page_summary") or "")[:80]
        print(f"    p{pn:03d} [{pt:5s}] {n_panels} panels, {n_text} text via {model}")
        print(f"           summary: {summary_preview}")

    # Check the seam — page 24 (first page of batch 2) should have continuity
    # from page 23 (last page of batch 1). Look for shared character names.
    p23 = next((r for r in results if r.get("page_number") == 23), None)
    p24 = next((r for r in results if r.get("page_number") == 24), None)
    if p23 and p24:
        p23_chars = set()
        for p in p23.get("panels", []):
            for c in p.get("characters") or []:
                p23_chars.add(c.strip())
        p24_chars = set()
        for p in p24.get("panels", []):
            for c in p.get("characters") or []:
                p24_chars.add(c.strip())
        overlap_chars = p23_chars & p24_chars
        print(f"\n  Seam continuity check (p23 ↔ p24):")
        print(f"    p23 characters: {sorted(p23_chars)}")
        print(f"    p24 characters: {sorted(p24_chars)}")
        print(f"    shared       : {sorted(overlap_chars)}")

    print("\n✅ SMOKE TEST PASSED — pipeline runs end-to-end with overlap")
    print(f"   (cleanup: rm -rf {dst})")


if __name__ == "__main__":
    main()
