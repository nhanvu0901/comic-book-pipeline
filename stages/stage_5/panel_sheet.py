"""Contact sheet: one JPEG grid of every panel a Stage 5 render will use, in
render order — so a wrong/blurry panel pick can be caught before the ffmpeg
render (which takes far longer) even starts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_PAD = 8
_LABEL_H = 28
_BG = (255, 255, 255)
_PLACEHOLDER = (200, 200, 200)


def _get(shot: Any, key: str, default=None):
    return shot.get(key, default) if isinstance(shot, dict) else getattr(shot, key, default)


def _page_of(source_image: str) -> int | None:
    m = re.search(r"page[_-]?(\d+)", Path(source_image).name)
    return int(m.group(1)) if m else None


def _font(size: int):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except OSError:
        return ImageFont.load_default()


def _crop_thumb(source_image: str, bbox: dict | None, thumb_h: int) -> Image.Image:
    img = Image.open(source_image).convert("RGB")
    if bbox and bbox.get("w") and bbox.get("h"):
        x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
        img = img.crop((x, y, x + w, y + h))
    w, h = img.size
    scale = thumb_h / h
    return img.resize((max(1, round(w * scale)), thumb_h))


def build_panel_sheet(shots: list, out_path, cols: int = 4, thumb_h: int = 320) -> Path:
    """Build the sheet at `out_path` (JPEG). `shots` is a list of Shot dataclasses
    or plain dicts (both shots list and shots.json entries work) in render order.
    A shot whose source_image can't be opened/cropped gets a gray placeholder tile
    instead of aborting the whole sheet — Master needs to see the full sequence
    even if one page went missing.

    # ponytail: shots carry no panel-index-within-page field, so the label is
    # "s{scene}#{i} p{page} {dur}s", not a page/panel ratio — add panel index if
    # a real per-page counter ever gets tracked upstream.
    """
    out_path = Path(out_path)
    font = _font(20)
    tiles: list[tuple[Image.Image, str]] = []
    thumb_w = 0
    for i, s in enumerate(shots, 1):
        src = _get(s, "source_image", "") or ""
        bbox = _get(s, "panel_bbox", None)
        try:
            thumb = _crop_thumb(src, bbox, thumb_h)
        except Exception:
            thumb = Image.new("RGB", (round(thumb_h * 0.75), thumb_h), _PLACEHOLDER)
        thumb_w = max(thumb_w, thumb.width)
        label = f"s{_get(s, 'scene_id', '?')}#{i}"
        page = _page_of(src)
        if page is not None:
            label += f" p{page}"
        dur = _get(s, "duration_seconds", None)
        if dur is not None:
            label += f" {float(dur):.1f}s"
        tiles.append((thumb, label))

    rows = (len(tiles) + cols - 1) // cols if tiles else 0
    cell_w = thumb_w + _PAD
    cell_h = thumb_h + _LABEL_H + _PAD
    sheet = Image.new("RGB", (cols * cell_w + _PAD, max(rows, 1) * cell_h + _PAD), _BG)
    draw = ImageDraw.Draw(sheet)
    for idx, (thumb, label) in enumerate(tiles):
        r, c = divmod(idx, cols)
        x, y = _PAD + c * cell_w, _PAD + r * cell_h
        sheet.paste(thumb, (x, y))
        draw.text((x + 2, y + thumb_h + 4), label, fill=(0, 0, 0), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=90)
    return out_path
