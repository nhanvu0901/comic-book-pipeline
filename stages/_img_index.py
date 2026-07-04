"""Image-space panel matching — embed panel PIXELS (Stage 2) + narration text (Stage 5)
into ONE joint SigLIP space. A desc-FREE second signal for the Stage-5 matcher.

WHY (the failure mode this defends against):
    Today the matcher compares the narration TEXT to the VLM-written panel DESCRIPTION,
    embedded by Qwen and stored in the TEXT Qdrant collection (see _panel_index). Those
    VLM descriptions are the pipeline's #1 error source: the batch VLM fabricates a
    description from STORY CONTEXT rather than the pixels, so a poisoned description scores
    a FAKE-HIGH text cosine for the WRONG panel (doom-rocket-raccoon #13 got mis-anchored on
    a hallucinated desc, and every text-only signal happily agreed with itself). This module
    adds a signal that NEVER reads the VLM's words: it embeds the panel's actual ART crop and
    the narration line into SigLIP's joint image-text space, so a fabricated description can
    poison the text cosine but not the image cosine.

Everything degrades to EXACTLY the text-only path. When transformers ships no SigLIP, the
model can't load, Qdrant is down, or PANEL_IMG_EMBED=0: index_project_images is a no-op and
load_image_vectors returns {}, so Stage 5 blends nothing and behaves as it does today.

Mirrors _panel_index.py's shape (index at Stage 2 → per-project Qdrant collection → read back
at Stage 5) but with its OWN collection name (panels_img__{slug}) and its OWN encoder. The id
scheme (page*1000 + index) matches the text collection so (page, panel_index) keys line up.
"""
from __future__ import annotations

import os
import re
import sys

# ── Env gates (repo convention: module-level constants read live so tests can monkeypatch).
# Default ON; PANEL_IMG_EMBED=0/false/no skips the image channel entirely (pure text-only,
# byte-identical to the pre-Feature-A behaviour). PANEL_IMG_WEIGHT is the blend weight w in
# Stage 5's (1-w)*text + w*image ranking — 0.35 = image is a strong MINORITY vote that flips
# near-ties / low-confidence text picks but cannot override a confidently-agreeing text lead.
PANEL_IMG_EMBED = os.getenv("PANEL_IMG_EMBED", "1").strip().lower() not in ("0", "false", "no", "")
PANEL_IMG_WEIGHT = float(os.getenv("PANEL_IMG_WEIGHT", "0.35"))


def _model_id_and_class():
    """Best available SigLIP (model_id, ModelClass), or (None, None) when transformers ships
    no SigLIP at all. Prefer siglip2 (stronger zero-shot) when the installed transformers has
    it; else siglip v1. NOTE: this repo's transformers 4.49 has only v1 (no Siglip2Model), so
    v1 `google/siglip-base-patch16-224` (768-dim) is what actually loads — the siglip2 branch
    auto-activates for free if transformers is later upgraded."""
    try:
        import transformers as _tf
    except Exception:
        return (None, None)
    if hasattr(_tf, "Siglip2Model"):
        return ("google/siglip2-base-patch16-256", _tf.Siglip2Model)
    if hasattr(_tf, "SiglipModel"):
        return ("google/siglip-base-patch16-224", _tf.SiglipModel)
    return (None, None)


def img_embed_available() -> bool:
    """True only when the image channel CAN run: PANEL_IMG_EMBED on AND transformers ships a
    SigLIP class. Deliberately does NOT download/load the model (that hits the network); a
    load failure at encode time is caught in _encoder(), which then no-ops too. Every entry
    point checks this and degrades to the text-only path when False."""
    if not PANEL_IMG_EMBED:
        return False
    _mid, cls = _model_id_and_class()
    return cls is not None


# Lazy encoder singleton — loaded once, freed via release(). _ENC_FAILED short-circuits repeat
# attempts after a load failure so we don't re-pay the (failing) import/download every call.
_ENC = None
_ENC_FAILED = False


def _encoder():
    """Lazy-load the SigLIP model+processor once (device MPS→CPU). Returns a dict
    {model, processor, device, dim} or None on any failure (cached as failed → no retry)."""
    global _ENC, _ENC_FAILED
    if _ENC is not None:
        return _ENC
    if _ENC_FAILED or not img_embed_available():
        return None
    try:
        import torch
        from transformers import AutoProcessor
        mid, cls = _model_id_and_class()
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = cls.from_pretrained(mid).to(device).eval()
        proc = AutoProcessor.from_pretrained(mid)
        _ENC = {"model": model, "processor": proc, "device": device, "dim": None}
        return _ENC
    except Exception as exc:
        _ENC_FAILED = True
        print(f"[img-index] SigLIP load failed — image channel OFF (text-only): {exc}",
              file=sys.stderr)
        return None


def _l2(mat):
    """Row-wise L2-normalize so a plain dot product IS the cosine (SigLIP towers are not
    unit-norm by default)."""
    import numpy as np
    norm = np.linalg.norm(mat, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return (mat / norm).astype("float32")


def embed_images(images, *, batch: int = 16):
    """L2-normalized image embeddings [N, dim] (float32 np.ndarray) for a list of PIL images,
    or None when the encoder is unavailable. Batched to bound peak memory."""
    enc = _encoder()
    if enc is None or not images:
        return None
    import numpy as np
    import torch
    model, proc, device = enc["model"], enc["processor"], enc["device"]
    out = []
    with torch.no_grad():
        for i in range(0, len(images), batch):
            inp = proc(images=images[i:i + batch], return_tensors="pt").to(device)
            out.append(model.get_image_features(**inp).float().cpu().numpy())
    mat = _l2(np.concatenate(out, axis=0))
    enc["dim"] = int(mat.shape[1])
    return mat


def embed_texts(texts, *, batch: int = 32):
    """L2-normalized text embeddings [N, dim] (float32 np.ndarray) via the SAME model's TEXT
    tower, or None when unavailable. SigLIP's text tower is trained with padding to a fixed
    length (64), so pad to max_length + truncate."""
    enc = _encoder()
    if enc is None or not texts:
        return None
    import numpy as np
    import torch
    model, proc, device = enc["model"], enc["processor"], enc["device"]
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            chunk = [str(t or "") for t in texts[i:i + batch]]
            inp = proc(text=chunk, padding="max_length", truncation=True,
                       return_tensors="pt").to(device)
            out.append(model.get_text_features(**inp).float().cpu().numpy())
    mat = _l2(np.concatenate(out, axis=0))
    enc["dim"] = int(mat.shape[1])
    return mat


def release():
    """Free the model + MPS/CPU cache — mirror panel_detect.release_model so SigLIP does not
    stay co-resident with the rest of Stage 2 / Stage 5 on a 16GB Mac. No-op if never loaded."""
    global _ENC
    if _ENC is None:
        return
    try:
        import gc
        import torch
        _ENC = None
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        _ENC = None


def _img_collection_name(project: str) -> str:
    """panels_img__{slug} — same slug rule as _qdrant.collection_name so it lines up 1:1 with
    the text collection, but a DISTINCT collection (image vectors have a different dim)."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(project).lower()).strip("_")
    return f"panels_img__{slug}"


def index_project_images(project: str, pages_by_number: dict, *, log=print) -> int:
    """Crop every STORY panel from its page image, embed the CROP with SigLIP, and upsert to
    the per-project image collection. Same id scheme as the text index (page*1000 + index) so
    (page, idx) keys line up. Recreates the collection each run (no stale points). Never raises
    — returns the count upserted (0 on any failure or when the channel is off)."""
    if not img_embed_available():
        log("[img-index] skipped — image embedding unavailable "
            "(no SigLIP in transformers or PANEL_IMG_EMBED=0)")
        return 0
    try:
        from PIL import Image
        from qdrant_client import models
        from . import _qdrant
        from ._panel_index import _is_story_page
    except Exception as exc:  # pragma: no cover
        log(f"[img-index] skipped (import): {exc}")
        return 0

    pts: list[dict] = []      # {"id", "payload"} aligned with `crops`
    crops = []                # PIL.Image, one per pts entry
    for pn in sorted(pages_by_number or {}):
        page = pages_by_number.get(pn) or {}
        if not _is_story_page(page):
            continue
        src = str(page.get("source_image") or "")
        if not src or not os.path.exists(src):
            continue
        try:
            page_img = Image.open(src).convert("RGB")
        except Exception as exc:
            log(f"[img-index] page {pn}: open failed ({exc}) — skipped")
            continue
        W, H = page_img.size
        for idx, panel in enumerate(page.get("panels") or []):
            bb = panel.get("bbox") or {}
            x = int(bb.get("x", 0) or 0); y = int(bb.get("y", 0) or 0)
            w = int(bb.get("w", 0) or 0); h = int(bb.get("h", 0) or 0)
            if w <= 0 or h <= 0:
                x, y, w, h = 0, 0, W, H     # whole-page / missing bbox → embed the full page
            box = (max(0, x), max(0, y), min(W, x + w), min(H, y + h))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            # Raw crop of the ORIGINAL art — no inpaint/mirror. The pixels ARE the ground
            # truth we're adding; altering them would defeat the purpose.
            crops.append(page_img.crop(box))
            pts.append({"id": pn * 1000 + idx,
                        "payload": {"page": pn, "index": idx, "source_image": src}})

    if not pts:
        return 0

    vecs = embed_images(crops)   # lazy-loads the model
    release()                    # free SigLIP right after encoding (memory budget)
    if vecs is None:
        log("[img-index] skipped — encoder unavailable at encode time")
        return 0
    dim = int(vecs.shape[1])

    name = _img_collection_name(project)
    try:
        c = _qdrant.client()
        if c.collection_exists(name):
            c.delete_collection(name)
        c.create_collection(
            name, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))
        c.upsert(name, points=[
            models.PointStruct(id=p["id"], vector=vecs[k].tolist(), payload=p["payload"])
            for k, p in enumerate(pts)
        ])
    except Exception as exc:
        log(f"[img-index] skipped (Qdrant unavailable): {exc}")
        return 0
    log(f"[img-index] {project}: {len(pts)} panel-image vectors (dim {dim}) → '{name}'")
    return len(pts)


def load_image_vectors(project: str) -> dict:
    """Read persisted panel-IMAGE vectors as {(page, index): np.ndarray}. {} when the channel
    is off, the collection is missing, or Qdrant is down. Mirror of _panel_index.load_vectors
    but with NO cross-backend dim check: image vectors only ever DOT against SigLIP text-tower
    vectors from the same model, and Stage 5 guards that dim match at blend time."""
    if not PANEL_IMG_EMBED:
        return {}
    try:
        import numpy as np
        from . import _qdrant
        c = _qdrant.client()
        name = _img_collection_name(project)
        if not c.collection_exists(name):
            return {}
        out: dict = {}
        offset = None
        while True:
            recs, offset = c.scroll(name, limit=256, with_payload=True,
                                    with_vectors=True, offset=offset)
            for p in recs:
                pl = p.payload or {}
                if p.vector is None:
                    continue
                out[(int(pl.get("page", 0)), int(pl.get("index", 0)))] = np.asarray(
                    p.vector, dtype="float32")
            if offset is None:
                break
        return out
    except Exception as exc:  # pragma: no cover
        print(f"[img-index] load_image_vectors failed: {exc}", file=sys.stderr)
        return {}
