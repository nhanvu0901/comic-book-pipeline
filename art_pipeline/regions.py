"""A3: propose story-significant regions of an artwork and emit them in the
comic `PreprocessedPage` schema (one artwork = one page, regions = panels).

Schema parity is the whole trick (spec §4-A3): Stage 5's
_load_preprocessed_pages() globs preprocessed/page_*.json and the embedding
panel-picker reads panel descriptions — both work on art unchanged.

VLM returns PERCENT bboxes (0-100) — far more reliable than raw pixels —
which we convert. Validation guard + grid fallback are day-1 features
(lesson from Stage 2's sloppy-VLM-coords history).
"""
import base64
import io
import json
import re
from pathlib import Path

from PIL import Image

# Read-only imports from the comic pipeline (never modified):
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, VLM_MODELS
from stages.stage_2.cache import image_hash, load_cached, save_cached
from stages.stage_2.schema import PanelInfo, PreprocessedPage

from .config import (
    REGION_IOU_DEDUP, REGION_MAX_AREA_PCT, REGION_MAX_COUNT,
    REGION_MIN_AREA_PCT, REGION_MIN_COUNT, get_art_project_path,
)

# ── Pure geometry ────────────────────────────────────────────────────────────

def clamp_bbox_pct(b: dict) -> dict | None:
    """Clamp a percent bbox into [0,100]; None if malformed or degenerate."""
    try:
        x = max(0.0, min(100.0, float(b["x"])))
        y = max(0.0, min(100.0, float(b["y"])))
        w = min(float(b["w"]), 100.0 - x)
        h = min(float(b["h"]), 100.0 - y)
    except (KeyError, TypeError, ValueError):
        return None
    if w < 1.0 or h < 1.0:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def pct_to_pixels(b: dict, width: int, height: int) -> dict:
    return {
        "x": int(round(b["x"] / 100.0 * width)),
        "y": int(round(b["y"] / 100.0 * height)),
        "w": int(round(b["w"] / 100.0 * width)),
        "h": int(round(b["h"] / 100.0 * height)),
    }


def bbox_iou(a: dict, b: dict) -> float:
    ax1, ay1, ax2, ay2 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx1, by1, bx2, by2 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def filter_regions(
    regs: list[dict],
    *,
    min_area_pct: float = REGION_MIN_AREA_PCT,
    max_area_pct: float = REGION_MAX_AREA_PCT,
    iou_thresh: float = REGION_IOU_DEDUP,
) -> list[dict]:
    """Clamp, area-filter, and IoU-dedup (keep the higher-significance region)."""
    cleaned: list[dict] = []
    for r in regs:
        b = clamp_bbox_pct(r.get("bbox_pct") or {})
        if b is None:
            continue
        area = b["w"] * b["h"] / 100.0  # as % of image area
        if area < min_area_pct or area > max_area_pct:
            continue
        cleaned.append({**r, "bbox_pct": b})
    # dedup: richer significance text wins
    cleaned.sort(key=lambda r: len(r.get("significance") or ""), reverse=True)
    kept: list[dict] = []
    for r in cleaned:
        if all(bbox_iou(r["bbox_pct"], k["bbox_pct"]) <= iou_thresh for k in kept):
            kept.append(r)
    return kept[:REGION_MAX_COUNT]


def grid_fallback_regions() -> list[dict]:
    """Full view + 4 quadrants + center — used when VLM proposals are weak."""
    cells = [
        ({"x": 0, "y": 0, "w": 100, "h": 100}, "the full artwork"),
        ({"x": 0, "y": 0, "w": 50, "h": 50}, "the upper-left quarter"),
        ({"x": 50, "y": 0, "w": 50, "h": 50}, "the upper-right quarter"),
        ({"x": 0, "y": 50, "w": 50, "h": 50}, "the lower-left quarter"),
        ({"x": 50, "y": 50, "w": 50, "h": 50}, "the lower-right quarter"),
        ({"x": 25, "y": 25, "w": 50, "h": 50}, "the center of the composition"),
    ]
    return [{"bbox_pct": b, "description": d, "significance": "grid fallback",
             "dominant_emotion": ""} for b, d in cells]


# ── Page assembly (comic schema) ─────────────────────────────────────────────

def build_page_dict(
    *, page_number: int, image_path: str, width: int, height: int,
    regions: list[dict], page_summary: str, artwork_label: str,
    model_used: str, content_hash: str,
) -> dict:
    panels = [
        PanelInfo(
            index=i,
            bbox=pct_to_pixels(r["bbox_pct"], width, height),
            description=str(r.get("description") or ""),
            characters=[],
            dominant_emotion=str(r.get("dominant_emotion") or ""),
            cluster_ids=[],
        )
        for i, r in enumerate(regions)
    ]
    return PreprocessedPage(
        page_number=page_number,
        source_image=image_path,
        image_dimensions={"width": width, "height": height},
        is_story_page=True,
        page_type="story",
        panels=panels,
        text_blocks=[],
        page_summary=page_summary,
        issue_label=artwork_label,
        vlm_model=VLM_MODELS[0] if VLM_MODELS else "",
        vlm_model_used=model_used,
        content_hash=content_hash,
        preprocessing_method="vlm-regions",
        skip_reason="",
    ).to_dict()


# ── VLM proposer ─────────────────────────────────────────────────────────────

_SYSTEM = """You are an art historian analyzing ONE artwork image. Identify the
4-8 most STORY-SIGNIFICANT regions a narrator would zoom into: faces and their
expressions, symbolic objects, hidden or easily-missed details, signatures or
inscriptions, background scenes, technical passages (brushwork, light).

Rules:
- bbox_pct values are PERCENTAGES (0-100) of image width/height, origin top-left.
- Each region: a concrete one-sentence description of WHAT IS VISIBLE there, a
  one-clause significance (why a narrator would point at it), and a dominant
  emotion word if a figure is present (else empty string).
- Also write page_summary: one neutral sentence describing the whole artwork.
- Do NOT invent details you cannot see. Respond with ONLY valid JSON."""

_USER_TMPL = """Artwork: {label}
Return STRICT JSON only:
{{"page_summary": "<one sentence>",
  "regions": [{{"bbox_pct": {{"x": 0, "y": 0, "w": 0, "h": 0}},
               "description": "<what is visible>",
               "significance": "<why it matters>",
               "dominant_emotion": "<word or empty>"}}]}}"""


def _encode_image_for_vlm(image_path: Path | str, max_dim: int = 2048) -> str:
    """Base64-JPEG for the VLM payload, downscaled to max_dim on the long side.

    Source scans can be ~8MB; huge payloads slow models down and degrade
    proposals. Because bboxes are PERCENT coords, resizing never affects
    coordinates — that's the design advantage of percent coords.
    """
    with Image.open(image_path) as im:
        if max(im.size) > max_dim:
            im = im.copy()
            im.thumbnail((max_dim, max_dim))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _extract_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def propose_regions_vlm(image_path: Path, artwork_label: str, *, log=print) -> tuple[list[dict], str, str]:
    """Returns (regions, page_summary, model_used). Empty list on total failure.

    A model whose proposals survive filter_regions below REGION_MIN_COUNT is
    treated as weak: we advance to the next model in the chain, keeping the
    best attempt so far as a fallback if no model reaches the threshold
    (the orchestrator's grid fallback remains the final backstop)."""
    from openai import OpenAI
    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    b64 = _encode_image_for_vlm(image_path)
    user = _USER_TMPL.format(label=artwork_label)
    best: tuple[list[dict], str, str] = ([], "", "")
    best_usable = -1
    for model in VLM_MODELS:
        try:
            log(f"[regions] {model} …")
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": user},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]},
                ],
                max_tokens=2000, timeout=120,
            )
            data = _extract_json(resp.choices[0].message.content or "")
            if data and isinstance(data.get("regions"), list):
                attempt = (data["regions"], str(data.get("page_summary") or ""), model)
                usable = len(filter_regions(data["regions"]))
                if usable >= REGION_MIN_COUNT:
                    return attempt
                log(f"[regions]   {model}: only {usable} usable region(s) — next model")
                if usable > best_usable:
                    best, best_usable = attempt, usable
                continue
            log(f"[regions]   {model}: unparseable JSON — next model")
        except Exception as exc:
            log(f"[regions]   {model}: {type(exc).__name__}: {exc} — next model")
    return best


# ── Orchestrator ─────────────────────────────────────────────────────────────

def process_artworks(project_name: str, *, force: bool = False, log=print) -> list[dict]:
    """Manifest → per artwork: VLM regions → guard → fallback → page_NNN JSON.
    Cache layout identical to comic Stage 2 (preprocessed/page_NNN_<hash>.json)."""
    root = get_art_project_path(project_name)
    manifest_path = root / "raw_art" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest: {manifest_path}. Run fetch first.")
    manifest = json.loads(manifest_path.read_text())

    results: list[dict] = []
    page_number = 0
    for chapter in manifest:
        label = chapter["label"]
        for img_str in chapter["pages"]:
            page_number += 1
            img = Path(img_str)
            h = image_hash(img)
            cached = None if force else load_cached(root, page_number, h)
            if cached is not None:
                log(f"[regions] ✓ cache hit p{page_number:03d} ({img.name})")
                results.append(cached)
                continue
            with Image.open(img) as im:
                width, height = im.size
            raw_regs, summary, model_used = propose_regions_vlm(img, label, log=log)
            kept = filter_regions(raw_regs)
            if len(kept) < REGION_MIN_COUNT:
                log(f"[regions] p{page_number:03d}: only {len(kept)} valid region(s) "
                    f"→ grid fallback (spec §4-A3)")
                kept = grid_fallback_regions()
                model_used = model_used or "grid-fallback"
                summary = summary or f"The artwork {label}."
            page = build_page_dict(
                page_number=page_number, image_path=str(img.resolve()),
                width=width, height=height, regions=kept,
                page_summary=summary, artwork_label=label,
                model_used=model_used, content_hash=h,
            )
            save_cached(root, page_number, h, page)
            log(f"[regions] p{page_number:03d}: {len(kept)} region(s) via {model_used}")
            results.append(page)
    return results
