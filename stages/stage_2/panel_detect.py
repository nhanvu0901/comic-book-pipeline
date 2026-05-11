"""
Panel bounding-box detection via Magi v3 (Florence-2 based, ICCV 2025).

Model: ragavsachdeva/magiv3 (academic-research license)
Inference: whole-page detection with page-level attention (no tiling needed).
Input: RGB color preserved — Magi's default loader desaturates to grayscale,
which loses Western color comic information; we skip that step.

Device selection: CUDA > MPS > CPU. FP16 only on CUDA.
"""
from pathlib import Path
from functools import lru_cache

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor


_HF_REPO = "ragavsachdeva/magiv3"

MIN_AREA_RATIO = 0.01
MAX_ASPECT_RATIO = 6.0


def _pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@lru_cache(maxsize=1)
def _load_model():
    device = _pick_device()
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        _HF_REPO,
        torch_dtype=dtype,
        trust_remote_code=True,
    ).to(device).eval()
    processor = AutoProcessor.from_pretrained(_HF_REPO, trust_remote_code=True)
    return model, processor


def detect_panels(image_path: Path | str) -> list[dict]:
    """
    Run Magi v3 panel detection on a single image.

    Returns:
        List of panel dicts sorted in Western reading order (LTR, top-to-bottom):
            [{"bbox": {"x": int, "y": int, "w": int, "h": int}, "confidence": float}, ...]
    """
    model, processor = _load_model()

    img = Image.open(image_path).convert("RGB")
    img_array = np.array(img)
    page_w, page_h = img.size
    page_area = page_w * page_h

    with torch.no_grad():
        results = model.predict_detections_and_associations([img_array], processor)

    panel_bboxes = results[0].get("panels", []) if results else []

    panels: list[dict] = []
    for box in panel_bboxes:
        x1, y1, x2, y2 = (int(v) for v in box[:4])
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            continue
        area_ratio = (w * h) / page_area
        aspect = max(w / max(h, 1), h / max(w, 1))
        if area_ratio < MIN_AREA_RATIO or aspect > MAX_ASPECT_RATIO:
            continue
        panels.append({
            "bbox": {"x": x1, "y": y1, "w": w, "h": h},
            "confidence": 1.0,
        })

    return sort_western_reading_order(panels)


def sort_western_reading_order(panels: list[dict], row_tolerance_ratio: float = 0.25) -> list[dict]:
    """
    Sort panels in Western reading order: left-to-right within rows,
    rows sorted top-to-bottom.

    Rows are detected by clustering panel vertical centers within
    row_tolerance_ratio of the tallest panel's height.
    """
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
