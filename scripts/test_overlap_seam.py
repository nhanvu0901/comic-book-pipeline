"""
Test the overlap pattern at a batch seam.

Sim: batch 1 covers pages 21-23, batch 2 covers pages 24-26.
- Run batch 1 normally → extract data for page 23 (the "seam" page).
- Run batch 2 TWICE:
   a) WITHOUT overlap (no prior_page passed) — current Lever 2 baseline
   b) WITH overlap (prior_page = page 23 data, prior_image = page 23 image)
- Compare panel 0 of page 24 between the two runs.

We want to see if the overlap version produces a more grounded continuation
("Gwen tucks the revolver away and steps into the night...") versus the
no-overlap version ("A figure walks down the street.").
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image

from config import PROJECTS_ROOT
from stages.stage_2.panel_detect import detect_panels
from stages.stage_2.vlm_extract import extract_pages_batch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="What if gwen stacy")
    args = parser.parse_args()

    raw = PROJECTS_ROOT / args.project / "raw_comic"
    batch1 = [raw / f"ch01_page_{n:02d}.jpg" for n in (21, 22, 23)]
    batch2 = [raw / f"ch01_page_{n:02d}.jpg" for n in (24, 25, 26)]

    for p in batch1 + batch2:
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr); sys.exit(1)

    print(f"Detecting panels on {len(batch1) + len(batch2)} page(s)...")
    panels1 = [detect_panels(p) for p in batch1]
    panels2 = [detect_panels(p) for p in batch2]
    for p, ps in zip(batch1 + batch2, panels1 + panels2):
        print(f"  {p.name}: {len(ps)} panel(s)")

    # ── Batch 1 ──
    print("\n" + "=" * 70)
    print("BATCH 1 (pages 21-23) — establishes seam page 23")
    print("=" * 70)
    t0 = time.time()
    pages1, state1, model1 = extract_pages_batch(
        batch1, panels1, progress=lambda m: print(f"  [vlm] {m}"),
    )
    print(f"  done in {time.time() - t0:.1f}s via {model1}")
    if pages1 is None:
        print("Batch 1 failed — abort"); sys.exit(2)

    page23_dict = {
        "page_number": 23,
        "issue_label": "#1",
        "page_type": pages1[2].get("page_type"),
        "panels": pages1[2].get("panels", []),
        "text_blocks": pages1[2].get("text_blocks", []),
        "page_summary": pages1[2].get("page_summary", ""),
    }
    print(f"\n  Page 23 (seam) summary: {page23_dict['page_summary'][:200]}")
    print(f"  Page 23 last panel: {((page23_dict['panels'] or [{}])[-1].get('description', '?'))[:200]}")
    print(f"  running_state: {state1[:240]}")

    # ── Batch 2a: WITHOUT overlap ──
    print("\n" + "=" * 70)
    print("BATCH 2a (pages 24-26) — WITHOUT overlap (running_state only)")
    print("=" * 70)
    t0 = time.time()
    pages2a, _, model2a = extract_pages_batch(
        batch2, panels2,
        progress=lambda m: print(f"  [vlm] {m}"),
        running_state=state1,
    )
    print(f"  done in {time.time() - t0:.1f}s via {model2a}")

    # ── Batch 2b: WITH overlap ──
    print("\n" + "=" * 70)
    print("BATCH 2b (pages 24-26) — WITH OVERLAP (page 23 as prior context)")
    print("=" * 70)
    t0 = time.time()
    pages2b, _, model2b = extract_pages_batch(
        batch2, panels2,
        progress=lambda m: print(f"  [vlm] {m}"),
        running_state=state1,
        prior_page=page23_dict,
        prior_image_path=batch1[2],  # page 23 image
    )
    print(f"  done in {time.time() - t0:.1f}s via {model2b}")

    # ── A/B compare PAGE 24 — the seam crossing ──
    print("\n" + "=" * 70)
    print("A/B: PAGE 24 (immediately after seam) — the key test")
    print("=" * 70)
    p24a = (pages2a or [{}])[0]
    p24b = (pages2b or [{}])[0]
    panels_a = {int(p.get("index", -1)): p for p in (p24a.get("panels") or [])}
    panels_b = {int(p.get("index", -1)): p for p in (p24b.get("panels") or [])}
    for idx in range(max(len(panels_a), len(panels_b))):
        print(f"\n  Panel {idx} of page 24:")
        print(f"    [no-overlap]: {(panels_a.get(idx) or {}).get('description', '?')[:200]}")
        print(f"    [overlap]   : {(panels_b.get(idx) or {}).get('description', '?')[:200]}")

    print(f"\n  Page 24 summary:")
    print(f"    [no-overlap]: {p24a.get('page_summary', '?')[:240]}")
    print(f"    [overlap]   : {p24b.get('page_summary', '?')[:240]}")


if __name__ == "__main__":
    main()
