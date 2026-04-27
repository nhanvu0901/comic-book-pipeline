"""
Panel bounding-box detection via a YOLO model fine-tuned on Western comics.

Model: mosesb/best-comic-panel-detection (Apache 2.0 weights, YOLOv12x).
Library: ultralytics (AGPL — fine for personal use; user has confirmed
personal-use-only for this pipeline).

Device selection: MPS on Apple Silicon, CUDA on NVIDIA, else CPU.
"""
from pathlib import Path
from functools import lru_cache


_HF_REPO = "mosesb/best-comic-panel-detection"
_WEIGHTS_FILENAME = "best.pt"


def _pick_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


@lru_cache(maxsize=1)
def _load_model():
    """Load YOLO weights from HuggingFace (cached after first call)."""
    from ultralytics import YOLO
    from huggingface_hub import hf_hub_download

    weights_path = hf_hub_download(repo_id=_HF_REPO, filename=_WEIGHTS_FILENAME)
    return YOLO(weights_path)


def detect_panels(image_path: Path | str, conf: float = 0.25, imgsz: int = 1280) -> list[dict]:
    """
    Run YOLO panel detection on a single image.

    Returns:
        List of panel dicts sorted in Western reading order (LTR, top-to-bottom):
            [{"bbox": {"x": int, "y": int, "w": int, "h": int}, "confidence": float}, ...]
    """
    model = _load_model()
    results = model.predict(
        source=str(image_path),
        device=_pick_device(),
        imgsz=imgsz,
        conf=conf,
        verbose=False,
    )
    if not results:
        return []

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return []

    panels: list[dict] = []
    for i in range(len(boxes)):
        x1, y1, x2, y2 = [float(v) for v in boxes.xyxy[i].tolist()]
        conf_i = float(boxes.conf[i].item())
        panels.append({
            "bbox": {
                "x": int(round(x1)),
                "y": int(round(y1)),
                "w": int(round(x2 - x1)),
                "h": int(round(y2 - y1)),
            },
            "confidence": round(conf_i, 3),
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
