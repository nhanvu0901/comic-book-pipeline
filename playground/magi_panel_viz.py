"""
Visualize Magi v3 panel detection to test threshold effects.

Draws detected panel bboxes (green box + index) over each page and prints a
page | n_panels | bbox-sizes table. Reuses stages/stage_2/panel_detect.py --
no separate model wiring.

NOTE: Magi's predict_detections_and_associations() has no panel-detection
threshold kwarg (verified against the real ragavsachdeva/magiv3 remote code
in ~/.cache/huggingface/modules/...) -- panel boxes come from deterministic
beam search with no exposed confidence. The only real lever over panel COUNT
is this repo's own post-detection filter, panel_detect.MIN_AREA_RATIO
(default 0.01 = drop panels below 1% of page area). --panel-threshold
overrides that constant for the run.

Usage:
    python scripts/magi_panel_viz.py                    # 5 random pages across ALL projects
    python scripts/magi_panel_viz.py --panel-threshold 0.002
    python scripts/magi_panel_viz.py --seed 7           # different random draw
    python scripts/magi_panel_viz.py --images page1.jpg page2.jpg
"""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

from config import PROJECTS_ROOT
from stages.stage_2 import panel_detect

BOX_COLOR = (0, 255, 0)
BOX_WIDTH = 5

# ponytail: sampling is hard-wired here -- pool = every projects/*/raw_comic/*.jpg
# from the 10th page onward (position in sorted list, skips covers/credits),
# projects starting with "_" excluded (throwaway), then pick N pages at random
# from the whole pool (so the 5 pages can come from 5 different comics)
PAGE_MIN_INDEX = 10
DEFAULT_PAGES = 5


def _free_lmstudio_ram() -> None:
    """Best-effort unload LM Studio before loading Magi (16GB Mac headroom)."""
    try:
        subprocess.run([str(Path.home() / ".lmstudio/bin/lms"), "unload", "--all"],
                        capture_output=True, timeout=30, check=False)
    except Exception:
        pass


def _detect_panels(image_path: Path, panel_threshold: float | None) -> list[dict]:
    """detect_full()'s panels, optionally with MIN_AREA_RATIO overridden for
    this call (see module docstring -- there's no real model-level knob)."""
    if panel_threshold is None:
        return panel_detect.detect_full(image_path)["panels"]
    orig = panel_detect.MIN_AREA_RATIO
    panel_detect.MIN_AREA_RATIO = panel_threshold
    try:
        return panel_detect.detect_full(image_path)["panels"]
    finally:
        panel_detect.MIN_AREA_RATIO = orig


def _draw_panels(image_path: Path, panels: list[dict], out_path: Path) -> None:
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
    except OSError:
        font = ImageFont.load_default()
    for i, p in enumerate(panels, 1):
        b = p["bbox"]
        x1, y1, x2, y2 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
        draw.rectangle([x1, y1, x2, y2], outline=BOX_COLOR, width=BOX_WIDTH)
        draw.rectangle([x1, y1, x1 + 44, y1 + 44], fill=(0, 0, 0))
        draw.text((x1 + 10, y1 + 6), str(i), fill=BOX_COLOR, font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=92)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--images", nargs="+", help="Explicit page image paths.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default 42).")
    parser.add_argument("--panel-threshold", type=float, default=None,
                         help="Override panel_detect.MIN_AREA_RATIO (default 0.01).")
    # ponytail: anchor default output to repo root, not cwd -- running from
    # inside scripts/ used to scatter magi_viz_out/ wherever you happened to be
    repo_root = Path(__file__).resolve().parent.parent
    default_out = str(Path(os.environ.get("SCRATCHPAD", repo_root)) / "magi_viz_out")
    parser.add_argument("--out", default=default_out, help=f"Output dir (default {default_out}).")
    args = parser.parse_args()

    if args.images:
        pages = [Path(p) for p in args.images]
    else:
        pool: list[Path] = []
        for raw_dir in sorted(PROJECTS_ROOT.glob("*/raw_comic")):
            if raw_dir.parent.name.startswith("_"):
                continue
            pool.extend(sorted(raw_dir.glob("*.jpg"))[PAGE_MIN_INDEX:])
        if not pool:
            parser.error(f"no candidate pages under {PROJECTS_ROOT}/*/raw_comic")
        pages = sorted(random.Random(args.seed).sample(pool, min(DEFAULT_PAGES, len(pool))))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    _free_lmstudio_ram()

    suffix = f"_t{args.panel_threshold:g}" if args.panel_threshold is not None else ""
    print(f"{'project':<28}{'page':<22}{'n_panels':>9}  bbox sizes")
    for page in pages:
        project = page.parent.parent.name  # projects/<slug>/raw_comic/<page>
        panels = _detect_panels(page, args.panel_threshold)
        out_path = out_dir / f"{project}_{page.stem}_panels{suffix}.jpg"
        _draw_panels(page, panels, out_path)
        sizes = ", ".join(f"{p['bbox']['w']}x{p['bbox']['h']}" for p in panels)
        print(f"{project:<28}{page.name:<22}{len(panels):>9}  {sizes}")
        print(f"  -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
