"""
A/B test: per-page VLM (current) vs multi-page batch VLM (new).

Picks 3 consecutive story pages from a project, runs both modes on the SAME
panels, and shows side-by-side panel descriptions so you can judge whether
batching actually produced more continuous narration.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image

from config import PROJECTS_ROOT
from stages.stage_2.panel_detect import detect_panels
from stages.stage_2.vlm_extract import extract_page, extract_pages_batch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="What if gwen stacy")
    parser.add_argument("--pages", type=int, nargs="+", default=[21, 22, 23],
                        help="Page numbers (1-indexed within ch01) to compare")
    args = parser.parse_args()

    raw = PROJECTS_ROOT / args.project / "raw_comic"
    image_paths = [raw / f"ch01_page_{p:02d}.jpg" for p in args.pages]
    for p in image_paths:
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr); sys.exit(1)

    print(f"Detecting panels on {len(image_paths)} page(s)...")
    panels_per_page: list[list[dict]] = []
    for p in image_paths:
        t0 = time.time()
        panels = detect_panels(p)
        print(f"  {p.name}: {len(panels)} panel(s) in {time.time()-t0:.1f}s")
        panels_per_page.append(panels)

    # ── PER-PAGE MODE (current behavior) ──
    print("\n" + "=" * 70)
    print("PER-PAGE MODE (current, no cross-panel continuity)")
    print("=" * 70)
    per_page_results: list[dict] = []
    for img, panels in zip(image_paths, panels_per_page):
        print(f"\n→ {img.name} ({len(panels)} panels)…")
        t0 = time.time()
        result = extract_page(img, panels, progress=lambda m: None)
        print(f"  done in {time.time()-t0:.1f}s, model={result.get('_vlm_model_used', '?')}")
        per_page_results.append(result)

    # ── BATCH MODE (new) ──
    print("\n" + "=" * 70)
    print("BATCH MODE (multi-image, continuity-aware)")
    print("=" * 70)
    t0 = time.time()
    batch_results, running_state, model = extract_pages_batch(
        image_paths, panels_per_page,
        progress=lambda m: print(f"    [vlm] {m}"),
    )
    print(f"  done in {time.time()-t0:.1f}s, model={model}")
    print(f"  running_state[0:240]: {running_state[:240]}")

    if batch_results is None:
        print("\n❌ Batch mode TOTALLY FAILED — fix providers / chain before A+B is usable")
        sys.exit(2)

    # ── A/B comparison per panel ──
    print("\n" + "=" * 70)
    print("SIDE-BY-SIDE PANEL DESCRIPTIONS")
    print("=" * 70)
    for page_i, (img, panels, per_p, batch_p) in enumerate(
        zip(image_paths, panels_per_page, per_page_results, batch_results),
        start=1,
    ):
        print(f"\n── Page {page_i}: {img.name} ({len(panels)} panels) ──")
        per_panels = {int(p.get("index", -1)): p for p in (per_p.get("panels") or [])}
        batch_panels = {int(p.get("index", -1)): p for p in (batch_p.get("panels") or [])}
        for i in range(max(len(per_panels), len(batch_panels))):
            print(f"\n  Panel {i}:")
            print(f"    [per-page]: {(per_panels.get(i) or {}).get('description', '?')[:200]}")
            print(f"    [batch]   : {(batch_panels.get(i) or {}).get('description', '?')[:200]}")

        print(f"\n  Page summary [per-page]: {per_p.get('page_summary', '?')[:240]}")
        print(f"  Page summary [batch]   : {batch_p.get('page_summary', '?')[:240]}")


if __name__ == "__main__":
    main()
