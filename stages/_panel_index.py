"""Persist panel embeddings to Qdrant (Stage 2) and read them back (Stage 5).

Panels are embedded ONCE at preprocessing time on their richer text
(description — characters — emotion — dialog) and stored in a per-project Qdrant
collection. Stage 5's matcher reads the vectors back instead of re-embedding every
panel each run. Everything here degrades gracefully: if Qdrant is down or the
embedding backend is unavailable, index_project is a no-op and load_vectors returns
{}, so the matcher falls back to in-memory embedding (its prior behaviour).
"""
from __future__ import annotations

import sys

EMBED_DIM = 3072  # Azure text-embedding-3-large


def panel_embed_text(panel: dict, page_text_blocks: list[dict] | None = None) -> str:
    """The text we embed for a panel — mirrors what the matcher matches against:
    visual description + who is present + dominant emotion + the panel's dialog."""
    _idx = panel.get("index", -999)
    idx = int(_idx) if _idx is not None else -999   # NB: `or` would turn index 0 into -999
    dlg = " ".join(
        str(tb.get("text", "")) for tb in (page_text_blocks or [])
        if int(tb.get("panel_index", -2) if tb.get("panel_index") is not None else -2) == idx
    ).strip()
    chars = " ".join(str(c) for c in (panel.get("characters") or []))
    emo = str(panel.get("dominant_emotion") or "")
    parts = [panel.get("description", "") or "", chars, emo, dlg]
    return " — ".join(x for x in parts if x).strip() or (panel.get("description", "") or "")


def _is_story_page(page: dict) -> bool:
    return bool(page.get("is_story_page")) and not page.get("skip_reason")


def index_project(project: str, pages_by_number: dict[int, dict], *, log=print) -> int:
    """Embed every story panel and upsert to Qdrant. Returns count upserted (0 on
    any failure — never raises into the pipeline)."""
    try:
        from . import _embedding, _qdrant
    except Exception as exc:  # pragma: no cover
        log(f"[panel-index] skipped (import): {exc}")
        return 0

    texts, points = [], []
    for pn in sorted(pages_by_number or {}):
        page = pages_by_number.get(pn) or {}
        if not _is_story_page(page):
            continue
        page_tb = page.get("text_blocks") or []
        src = str(page.get("source_image") or "")
        dims = page.get("image_dimensions") or {}
        parea = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
        for idx, panel in enumerate(page.get("panels") or []):
            bb = panel.get("bbox", {}) or {}
            area = int(bb.get("w", 0) or 0) * int(bb.get("h", 0) or 0)
            texts.append(panel_embed_text(panel, page_tb))
            points.append({
                "id": pn * 1000 + idx,
                "payload": {"page": pn, "index": idx, "source_image": src,
                            "area": area, "page_area": parea},
            })

    if not texts:
        return 0
    try:
        if _embedding.backend_name() == "none":
            log("[panel-index] skipped — no embedding backend")
            return 0
        vecs = _embedding.embed_batch(texts)
        ok = [(p, v) for p, v in zip(points, vecs) if v is not None]
        if not ok:
            log("[panel-index] skipped — embedding produced no vectors")
            return 0
        for p, v in ok:
            p["vector"] = v.tolist() if hasattr(v, "tolist") else list(v)
        _qdrant.ensure_collection(project, EMBED_DIM, recreate=True)
        _qdrant.upsert_panels(project, [p for p, _ in ok])
        n = _qdrant.count(project)
        log(f"[panel-index] {project}: {n} panel vectors → '{_qdrant.collection_name(project)}'")
        return n
    except Exception as exc:
        log(f"[panel-index] skipped (Qdrant/embed unavailable): {exc}")
        return 0


def load_vectors(project: str) -> dict[tuple[int, int], "object"]:
    """Read persisted panel vectors as {(page, index): np.ndarray}. {} if unavailable."""
    try:
        import numpy as np
        from . import _qdrant
        c = _qdrant.client()
        name = _qdrant.collection_name(project)
        if not c.collection_exists(name):
            return {}
        out: dict[tuple[int, int], object] = {}
        offset = None
        while True:
            pts, offset = c.scroll(name, limit=256, with_payload=True,
                                   with_vectors=True, offset=offset)
            for p in pts:
                pl = p.payload or {}
                vec = p.vector
                if vec is None:
                    continue
                out[(int(pl.get("page", 0)), int(pl.get("index", 0)))] = np.asarray(vec, dtype="float32")
            if offset is None:
                break
        return out
    except Exception as exc:  # pragma: no cover
        print(f"[panel-index] load_vectors failed: {exc}", file=sys.stderr)
        return {}
