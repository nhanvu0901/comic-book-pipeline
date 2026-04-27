"""
Resolve each scene_timing into a concrete visual plan: source image,
9:16 crop rect, and Ken Burns animation bounds.

The 9:16 crop is centered on the target panel (from Stage 2's panel bbox),
clamped to image bounds. Ken Burns adds a 1.00 → 1.08 zoom over the
scene's duration for motion.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


OUTPUT_W = 1080
OUTPUT_H = 1920
TARGET_ASPECT = OUTPUT_W / OUTPUT_H   # 9/16 = 0.5625

ZOOM_START = 1.00
ZOOM_END = 1.08


@dataclass
class VisualPlan:
    scene_id: int
    text: str
    image_path: str          # absolute path to source page JPG
    image_width: int
    image_height: int
    crop_x: int              # 9:16 crop (px)
    crop_y: int
    crop_w: int
    crop_h: int
    duration: float          # seconds
    start: float             # absolute start in narration audio
    zoom_start: float = ZOOM_START
    zoom_end: float = ZOOM_END


def plan_scenes(
    scene_timings: list[dict],
    preprocessed_pages: dict[int, dict],
) -> list[VisualPlan]:
    """
    Build a VisualPlan for each scene timing.

    Args:
        scene_timings: list of dicts loaded from scene_timings.json
        preprocessed_pages: page_number -> page dict (from preprocessed/*.json)
    """
    plans: list[VisualPlan] = []
    for s in scene_timings:
        page_num = int(s.get("page_ref") or 0)
        panel_ref = int(s.get("panel_ref") if s.get("panel_ref") is not None else -1)
        start = float(s.get("start") or 0.0)
        end = float(s.get("end") or 0.0)
        dur = max(0.3, end - start)

        page = preprocessed_pages.get(page_num)
        if not page:
            # fall back to the first preprocessed story page we have
            page = _first_story_page(preprocessed_pages)
            if page is None:
                continue

        image_path = page.get("source_image") or ""
        if not image_path or not Path(image_path).exists():
            continue

        img_w, img_h = _image_dimensions(page, image_path)
        panel_bbox = _resolve_panel_bbox(page, panel_ref)
        crop = _fit_9_16_crop(img_w, img_h, panel_bbox)

        plans.append(VisualPlan(
            scene_id=int(s.get("scene_id") or len(plans) + 1),
            text=str(s.get("text") or ""),
            image_path=str(Path(image_path).resolve()),
            image_width=img_w,
            image_height=img_h,
            crop_x=crop[0], crop_y=crop[1], crop_w=crop[2], crop_h=crop[3],
            duration=round(dur, 3),
            start=round(start, 3),
        ))
    return plans


def _image_dimensions(page: dict, image_path: str) -> tuple[int, int]:
    d = page.get("image_dimensions") or {}
    w, h = int(d.get("width") or 0), int(d.get("height") or 0)
    if w > 0 and h > 0:
        return w, h
    with Image.open(image_path) as im:
        return im.size


def _resolve_panel_bbox(page: dict, panel_ref: int) -> tuple[int, int, int, int] | None:
    panels = page.get("panels") or []
    if panel_ref >= 0:
        for p in panels:
            if int(p.get("index", -1)) == panel_ref:
                b = p.get("bbox") or {}
                return (int(b.get("x", 0)), int(b.get("y", 0)),
                        int(b.get("w", 0)), int(b.get("h", 0)))
    return None


def _fit_9_16_crop(
    img_w: int,
    img_h: int,
    panel: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    """
    Compute a 9:16 crop rect within the image, centered on the panel bbox
    if provided (else the image center). Clamped to image bounds.
    """
    # Largest 9:16 rect that fits in the image.
    if img_w / img_h < TARGET_ASPECT:
        crop_w = img_w
        crop_h = int(img_w / TARGET_ASPECT)
    else:
        crop_h = img_h
        crop_w = int(img_h * TARGET_ASPECT)

    if panel:
        px, py, pw, ph = panel
        cx = px + pw / 2
        cy = py + ph / 2
    else:
        cx = img_w / 2
        cy = img_h / 2

    crop_x = int(round(max(0, min(cx - crop_w / 2, img_w - crop_w))))
    crop_y = int(round(max(0, min(cy - crop_h / 2, img_h - crop_h))))
    return crop_x, crop_y, crop_w, crop_h


def _first_story_page(pages: dict[int, dict]) -> dict | None:
    for num in sorted(pages):
        p = pages[num]
        if p.get("is_story_page"):
            return p
    return None


def load_preprocessed_pages(project_root: Path) -> dict[int, dict]:
    """
    Load every preprocessed/*.json into a dict keyed by page_number.
    """
    prep_dir = project_root / "preprocessed"
    out: dict[int, dict] = {}
    if not prep_dir.exists():
        return out
    for p in sorted(prep_dir.glob("page_*.json")):
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        n = int(d.get("page_number") or 0)
        if n > 0:
            out[n] = d
    return out
