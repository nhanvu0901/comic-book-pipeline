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
    """Backwards-compat: returns just the panel bboxes (sorted reading order).
    For full Magi output (characters, texts, cluster_labels, associations, OCR),
    use detect_full()."""
    full = detect_full(image_path)
    return full["panels"]


def detect_full(image_path: Path | str) -> dict:
    """Run Magi v3 FULL detection + OCR on a single image.

    Returns a dict with:
      panels:     [{bbox: {x,y,w,h}, confidence}] — sorted Western reading order
      characters: [{bbox: {x,y,w,h}, cluster_id: int}] — visual identity per page
      texts:      [{bbox: {x,y,w,h}, ocr: str, type, speaker_char_idx, speaker_cluster_id, is_essential}]
                  type ∈ {speech, narration, sfx, caption}
      reading_order_associations: associations from Magi

    Coordinates are pixels, origin top-left. character + text bboxes use the same
    {x,y,w,h} format as panels.
    """
    model, processor = _load_model()

    img = Image.open(image_path).convert("RGB")
    img_array = np.array(img)
    page_w, page_h = img.size
    page_area = page_w * page_h

    with torch.no_grad():
        results = model.predict_detections_and_associations([img_array], processor)
        ocr_results = model.predict_ocr([img_array], processor)

    page_result = results[0] if results else {}
    page_ocr = ocr_results[0].get("ocr_texts", []) if ocr_results else []

    # ── Panels ─────────────────────────────────────────────────────────
    panel_bboxes = page_result.get("panels", []) or []
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
    panels = sort_western_reading_order(panels)

    # ── Characters ─────────────────────────────────────────────────────
    char_bboxes = page_result.get("characters", []) or []
    cluster_labels = page_result.get("character_cluster_labels", []) or []
    characters: list[dict] = []
    for i, box in enumerate(char_bboxes):
        x1, y1, x2, y2 = (int(v) for v in box[:4])
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            continue
        cluster_id = int(cluster_labels[i]) if i < len(cluster_labels) else -1
        characters.append({
            "bbox": {"x": x1, "y": y1, "w": w, "h": h},
            "cluster_id": cluster_id,
            "char_idx": i,
        })

    # ── Texts (with OCR + speaker association) ─────────────────────────
    text_bboxes = page_result.get("texts", []) or []
    text_char_assoc = page_result.get("text_character_associations", []) or []
    is_essential = page_result.get("is_essential_text", []) or []
    # Build text_idx → character_idx map from associations
    text_to_char: dict[int, int] = {}
    for assoc in text_char_assoc:
        if len(assoc) >= 2:
            text_to_char[int(assoc[0])] = int(assoc[1])

    texts: list[dict] = []
    for i, box in enumerate(text_bboxes):
        x1, y1, x2, y2 = (int(v) for v in box[:4])
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            continue
        ocr_text = page_ocr[i] if i < len(page_ocr) else ""
        char_idx = text_to_char.get(i, -1)
        speaker_cluster_id = (
            characters[char_idx]["cluster_id"]
            if 0 <= char_idx < len(characters)
            else None
        )
        essential = bool(is_essential[i]) if i < len(is_essential) else True
        # Heuristic type: if speaker associated → speech; if all-caps short → sfx; else narration
        ttype = "speech" if char_idx >= 0 else (
            "sfx" if ocr_text.isupper() and len(ocr_text.split()) <= 3 else "narration"
        )
        texts.append({
            "bbox": {"x": x1, "y": y1, "w": w, "h": h},
            "ocr": ocr_text,
            "type": ttype,
            "speaker_char_idx": char_idx if char_idx >= 0 else None,
            "speaker_cluster_id": speaker_cluster_id,
            "is_essential": essential,
        })

    return {
        "panels": panels,
        "characters": characters,
        "texts": texts,
        "page_size": {"width": page_w, "height": page_h},
    }


def _bbox_inside(inner: dict, outer: dict, overlap_threshold: float = 0.5) -> bool:
    """Return True if `inner` bbox has >= overlap_threshold of its area inside `outer`."""
    ix0, iy0 = inner["x"], inner["y"]
    ix1, iy1 = ix0 + inner["w"], iy0 + inner["h"]
    ox0, oy0 = outer["x"], outer["y"]
    ox1, oy1 = ox0 + outer["w"], oy0 + outer["h"]
    # Overlap rectangle
    overlap_x = max(0, min(ix1, ox1) - max(ix0, ox0))
    overlap_y = max(0, min(iy1, oy1) - max(iy0, oy0))
    overlap_area = overlap_x * overlap_y
    inner_area = inner["w"] * inner["h"]
    if inner_area <= 0:
        return False
    return overlap_area / inner_area >= overlap_threshold


def assign_to_panels(
    panels: list[dict],
    characters: list[dict],
    texts: list[dict],
) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    """For each panel index, list the character indices and text indices that
    are inside it (>= 50% overlap). Returns (panel_chars, panel_texts) mappings."""
    panel_chars: dict[int, list[int]] = {i: [] for i in range(len(panels))}
    panel_texts: dict[int, list[int]] = {i: [] for i in range(len(panels))}
    for ci, ch in enumerate(characters):
        for pi, panel in enumerate(panels):
            if _bbox_inside(ch["bbox"], panel["bbox"]):
                panel_chars[pi].append(ci)
                break
    for ti, tx in enumerate(texts):
        for pi, panel in enumerate(panels):
            if _bbox_inside(tx["bbox"], panel["bbox"]):
                panel_texts[pi].append(ti)
                break
    return panel_chars, panel_texts


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
