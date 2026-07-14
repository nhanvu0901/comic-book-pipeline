"""Automatic gutter-split for oversized Magi panel boxes.

Magi v3 (Florence-2, deterministic beam search, no confidence threshold) sometimes
MERGES several hand-drawn panels into one "whole-page" box — e.g. Joker "The Last
Smile" pages 5/6 come back as 1-2 giant boxes although a human sees 5-6 separate
cells. That collapses distinct scenes into ONE panel and breaks 1:1 scene→panel
matching downstream. This module re-splits such an oversized box along the white
GUTTERS between panels, using only a brightness projection profile (pure numpy —
no extra dependency).

Only VERY large boxes are ever touched (area > GUTTER_MIN_PANEL_AREA_FRAC of the
page): a normally-detected page has small panel fractions, so this is a strict
no-op there. A true full-page splash (no white gutters) also stays a no-op.

CACHE NOTE: splitting a panel changes the per-page panel INDEX/count. Stage 2 caches
per-page results keyed by image content hash, so this only affects projects that are
preprocessed FRESH after this lands — existing cached projects keep their old panel
numbering untouched (no silent drift) unless re-preprocessed with --force.
"""
import os

import numpy as np

# Only boxes larger than this fraction of the page are candidates (whole/half-page).
GUTTER_MIN_PANEL_AREA_FRAC = float(os.getenv("GUTTER_MIN_PANEL_AREA_FRAC", "0.55"))
# A pixel this bright (0-255) or brighter counts as gutter/white.
GUTTER_WHITE_THRESH = int(os.getenv("GUTTER_WHITE_THRESH", "235"))
# A row/col is a gutter line only if this fraction of it is near-white.
GUTTER_MIN_RUN_FRAC = float(os.getenv("GUTTER_MIN_RUN_FRAC", "0.7"))
# Minimum contiguous gutter thickness (px) to accept as a real split.
GUTTER_MIN_THICK = int(os.getenv("GUTTER_MIN_THICK", "12"))


def _to_gray(image) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 3:
        return arr[..., :3].mean(axis=2)
    return arr


def _gutter_runs(white_line: np.ndarray, min_thick: int) -> list[tuple[int, int]]:
    """Contiguous [start, end) runs of True (gutter) in a 1-D mask, thick enough to keep."""
    runs: list[tuple[int, int]] = []
    n = len(white_line)
    i = 0
    while i < n:
        if white_line[i]:
            j = i
            while j < n and white_line[j]:
                j += 1
            if j - i >= min_thick:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _content_segments(length: int, gutters: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Non-gutter [start, end) segments; leading/trailing border whitespace dropped."""
    segs: list[tuple[int, int]] = []
    pos = 0
    for gs, ge in gutters:
        if gs > pos:
            segs.append((pos, gs))
        pos = ge
    if pos < length:
        segs.append((pos, length))
    return segs


def _gutter_split(image, bbox: dict, page_w: int, page_h: int) -> list[dict]:
    """Split one panel `bbox` ({x,y,w,h}, absolute page pixels) along white gutters.

    Returns a list of sub-panel bboxes in reading order (top→bottom, left→right), with
    ABSOLUTE page coordinates. Returns [bbox] unchanged (no-op) when the box is not
    oversized, when no valid gutters are found (true splash), or when only one cell
    results. `image` is the whole-page array/PIL image (RGB or gray)."""
    page_area = max(page_w * page_h, 1)
    x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
    if (w * h) / page_area < GUTTER_MIN_PANEL_AREA_FRAC:
        return [bbox]

    gray = _to_gray(image)
    H, W = gray.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    crop = gray[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    if ch < 2 * GUTTER_MIN_THICK or cw < 2 * GUTTER_MIN_THICK:
        return [bbox]

    from .panel_detect import MIN_AREA_RATIO  # existing min-panel-area constant (lazy: avoids import cycle)

    white = crop >= GUTTER_WHITE_THRESH
    row_gutters = _gutter_runs(white.mean(axis=1) >= GUTTER_MIN_RUN_FRAC, GUTTER_MIN_THICK)
    bands = _content_segments(ch, row_gutters)

    cells: list[dict] = []
    for bs, be in bands:
        band = white[bs:be, :]
        col_gutters = _gutter_runs(band.mean(axis=0) >= GUTTER_MIN_RUN_FRAC, GUTTER_MIN_THICK)
        for cs, ce in _content_segments(cw, col_gutters):
            cw_, chh = ce - cs, be - bs
            if (cw_ * chh) / page_area < MIN_AREA_RATIO:
                continue
            cells.append({"x": x0 + cs, "y": y0 + bs, "w": cw_, "h": chh})

    if len(cells) <= 1:
        return [bbox]
    cells.sort(key=lambda c: (c["y"], c["x"]))
    return cells


if __name__ == "__main__":
    # Self-check: a 2x2 grid of dark cells on white with wide gutters → 4 sub-panels;
    # a solid mid-gray splash → 1 (no-op).
    grid = np.full((400, 400), 255, dtype=np.uint8)
    for yy in (slice(20, 180), slice(220, 380)):
        for xx in (slice(20, 180), slice(220, 380)):
            grid[yy, xx] = 30
    assert len(_gutter_split(grid, {"x": 0, "y": 0, "w": 400, "h": 400}, 400, 400)) == 4
    solid = np.full((400, 400), 120, dtype=np.uint8)
    assert len(_gutter_split(solid, {"x": 0, "y": 0, "w": 400, "h": 400}, 400, 400)) == 1
    print("gutter_split self-check OK")
