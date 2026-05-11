"""
Test Magi v3 panel detection on Western color comics.
Magi v3 (Florence-2 based, ICCV 2025): paper claims it "works surprisingly well
on Western comics". Color is preserved (no grayscale conversion).

Note: academic-research license only.
"""
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoProcessor

_HF_REPO = "ragavsachdeva/magiv3"
_HF_TOKEN = os.getenv("HF_TOKEN", "")

RAW_COMIC = Path(__file__).parent / "projects/the_thing_bond_with_venom_what_if/raw_comic"

OUTPUT_DIR = Path(__file__).parent.parent / "panel_test_output/magiv3"

MIN_AREA_RATIO = 0.01
MAX_ASPECT_RATIO = 6.0

BOX_COLOR = (0, 255, 0)
BOX_WIDTH = 3
LABEL_BG = (0, 255, 0)
LABEL_FG = (0, 0, 0)


def _pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


_model_cache = None
_processor_cache = None


def _get_model():
    global _model_cache, _processor_cache
    if _model_cache is None:
        device = _pick_device()
        dtype = torch.float16 if device == "cuda" else torch.float32
        _model_cache = AutoModelForCausalLM.from_pretrained(
            _HF_REPO,
            torch_dtype=dtype,
            trust_remote_code=True,
            token=_HF_TOKEN,
        ).to(device).eval()
        _processor_cache = AutoProcessor.from_pretrained(
            _HF_REPO,
            trust_remote_code=True,
            token=_HF_TOKEN,
        )
    return _model_cache, _processor_cache


def sort_western_reading_order(panels: list[dict], row_tolerance_ratio: float = 0.25) -> list[dict]:
    if not panels:
        return panels
    max_h = max(p["bbox"]["h"] for p in panels)
    tol = max_h * row_tolerance_ratio
    by_y = sorted(panels, key=lambda p: p["bbox"]["y"] + p["bbox"]["h"] / 2)
    rows: list[list[dict]] = []
    for p in by_y:
        cy = p["bbox"]["y"] + p["bbox"]["h"] / 2
        if rows:
            row_cy = rows[-1][0]["bbox"]["y"] + rows[-1][0]["bbox"]["h"] / 2
            if abs(cy - row_cy) <= tol:
                rows[-1].append(p)
                continue
        rows.append([p])
    ordered: list[dict] = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda p: p["bbox"]["x"]))
    return ordered


def detect_panels(image_path: Path) -> list[dict]:
    model, processor = _get_model()

    img = Image.open(image_path).convert("RGB")
    img_array = np.array(img)
    page_w, page_h = img.size
    page_area = page_w * page_h

    with torch.no_grad():
        results = model.predict_detections_and_associations([img_array], processor)

    panel_bboxes = results[0].get("panels", []) if results else []

    panels = []
    for box in panel_bboxes:
        x1, y1, x2, y2 = [int(v) for v in box[:4]]
        x, y = x1, y1
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            continue
        area_ratio = (w * h) / page_area
        aspect = max(w / max(h, 1), h / max(w, 1))
        if area_ratio < MIN_AREA_RATIO or aspect > MAX_ASPECT_RATIO:
            continue
        panels.append({
            "bbox": {"x": x, "y": y, "w": w, "h": h},
            "confidence": 1.0,
        })

    return sort_western_reading_order(panels)


def _draw_panels(image_path: Path, panels: list[dict], output_path: Path):
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except OSError:
        font = ImageFont.load_default()

    for i, p in enumerate(panels, 1):
        b = p["bbox"]
        x1, y1 = b["x"], b["y"]
        x2, y2 = x1 + b["w"], y1 + b["h"]

        draw.rectangle([x1, y1, x2, y2], outline=BOX_COLOR, width=BOX_WIDTH)

        label = f"{i}  {p['confidence']:.0%}"
        lbox = font.getbbox(label)
        lw, lh = lbox[2] - lbox[0], lbox[3] - lbox[1]
        draw.rectangle([x1, y1 - lh - 6, x1 + lw + 8, y1], fill=LABEL_BG)
        draw.text((x1 + 4, y1 - lh - 4), label, fill=LABEL_FG, font=font)

    img.save(output_path, quality=90)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pages = sorted(RAW_COMIC.glob("*.jpg"))
    print(f"Found {len(pages)} pages")
    print(f"Model: {_HF_REPO}")
    print(f"Device: {_pick_device()}")
    print(f"Output → {OUTPUT_DIR}\n")

    total = 0
    for page in pages:
        panels = detect_panels(page)
        total += len(panels)
        _draw_panels(page, panels, OUTPUT_DIR / page.name)
        print(f"  {page.name}: {len(panels)} panels")

    print(f"\nTotal: {total} panels across {len(pages)} pages")
