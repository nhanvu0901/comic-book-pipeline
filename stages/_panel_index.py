"""Persist panel embeddings to Qdrant (Stage 2) and read them back (Stage 5).

Panels are embedded ONCE at preprocessing time on their richer text
(description — characters — emotion — dialog) and stored in a per-project Qdrant
collection. Stage 5's matcher reads the vectors back instead of re-embedding every
panel each run. Everything here degrades gracefully: if Qdrant is down or the
embedding backend is unavailable, index_project is a no-op and load_vectors returns
{}, so the matcher falls back to in-memory embedding (its prior behaviour).
"""
from __future__ import annotations

import os
import sys

EMBED_DIM = 3072  # Azure text-embedding-3-large

# DIALOG_TRUTH: use Magi's pixel-OCR (the `ocr` field on each dialog block) as the
# AUTHORITATIVE dialog when embedding a panel — the batch VLM fabricates the `text`
# field from story flow (real case: doom-rocket-raccoon p28 panel 1 pixels read
# "SO NOW WHAT DO WE DO?" but the VLM wrote "WE'VE REACHED THE BIG BANG"), which
# poisons the panel embedding and mis-grounds Stage 3/5. Default ON; DIALOG_TRUTH=0/
# false/no reverts to VLM-text embedding. Old cached pages carry no `ocr`, so the flag
# is a no-op for them regardless — their persisted Qdrant vectors stay valid.
DIALOG_TRUTH = os.getenv("DIALOG_TRUTH", "1").strip().lower() not in ("0", "false", "no", "")


def panel_dialog(panel: dict, page_text_blocks: list[dict] | None = None) -> list[dict]:
    """A panel's dialog lines. New schema: nested panel['dialog']. Old cached pages:
    filter the page-level text_blocks by panel_index (backward-compat)."""
    d = panel.get("dialog")
    if d is not None:
        return d
    _idx = panel.get("index", -999)
    idx = int(_idx) if _idx is not None else -999   # NB: `or` would turn index 0 into -999
    return [tb for tb in (page_text_blocks or [])
            if (int(tb.get("panel_index", -2)) if tb.get("panel_index") is not None else -2) == idx]


def page_dialog(page: dict) -> list[dict]:
    """ALL dialog on a page. New schema: flattened from panels[].dialog. Old cached
    pages: the page-level text_blocks (backward-compat)."""
    tb = page.get("text_blocks")
    if tb:   # non-empty page-level list = old cached schema; new comic output leaves it []
        return tb
    out: list[dict] = []
    for p in page.get("panels") or []:
        out.extend(p.get("dialog") or [])
    return out


def _panel_dialog_text(panel: dict, page_text_blocks: list[dict] | None = None) -> str:
    """A panel's dialog as ONE string for embedding. Magi's pixel-OCR (`ocr` on each
    block) is deterministic ground truth and OVERRIDES the VLM's `text` field, which the
    batch VLM fabricates from story flow. Per the DIALOG_TRUTH contract, prefer OCR
    whenever ANY block on the panel carries it; fall back to the VLM `text` only when no
    OCR exists for the panel (old cached pages have no `ocr` → identical to the prior
    behaviour, so their persisted Qdrant vectors stay valid)."""
    blocks = panel_dialog(panel, page_text_blocks)
    if DIALOG_TRUTH:
        ocr = " ".join(str(b.get("ocr", "")).strip() for b in blocks
                       if str(b.get("ocr", "")).strip())
        if ocr:
            return ocr
    return " ".join(str(b.get("text", "")).strip() for b in blocks
                    if str(b.get("text", "")).strip())


def panel_embed_text(panel: dict, page_text_blocks: list[dict] | None = None) -> str:
    """The text we embed for a panel — mirrors what the matcher matches against:
    visual description + who is present + dominant emotion + the panel's dialog
    (Magi-OCR ground truth preferred over VLM transcription — see _panel_dialog_text)."""
    dlg = _panel_dialog_text(panel, page_text_blocks)
    chars = " ".join(str(c) for c in (panel.get("characters") or []))
    emo = str(panel.get("dominant_emotion") or "")
    parts = [panel.get("description", "") or "", chars, emo, dlg]
    return " — ".join(x for x in parts if x).strip() or (panel.get("description", "") or "")


def _is_story_page(page: dict) -> bool:
    return bool(page.get("is_story_page")) and not page.get("skip_reason")


def _embed_with_retry(embed_batch, texts: list[str], *, tries: int = 4, log=print) -> list:
    """Embed one batch, retrying while any entry is still None (Azure 'Connection error'
    is usually transient). embed_batch caches successes, so each retry only re-fetches the
    still-missing texts. Exponential backoff between tries."""
    import time
    vecs = embed_batch(texts)
    k = 1
    while any(v is None for v in vecs) and k < tries:
        time.sleep(min(2.0 ** k, 20.0))
        log(f"[panel-index] retry embed (attempt {k + 1}/{tries}) — {sum(v is None for v in vecs)} text(s) still missing")
        vecs = embed_batch(texts)
        k += 1
    return vecs


def index_project(project: str, pages_by_number: dict[int, dict], *, log=print,
                  batch_pages: int = 5) -> int:
    """Embed every story panel and upsert to Qdrant, in batches of `batch_pages` pages so
    a transient network error kills only one batch (which is retried), not the whole run.
    The collection is RECREATED first so every re-run replaces the project's vectors (no
    stale points). Returns the count upserted (0 on total failure — never raises)."""
    # Panel TEXT-embed master switch. OFF (default) → Master picks panels by hand in review,
    # so building the Qwen text index + `panels__<slug>` collection is dead work. Skip it
    # entirely (no embedding API, no collection). SigLIP image index is separate (see caller).
    from config import PANEL_TEXT_EMBED
    if not PANEL_TEXT_EMBED:
        log("[panel-index] SKIPPED (PANEL_TEXT_EMBED=0)")
        return 0

    try:
        from . import _embedding, _qdrant
    except Exception as exc:  # pragma: no cover
        log(f"[panel-index] skipped (import): {exc}")
        return 0

    # Group points+texts BY PAGE so we can batch a few pages at a time.
    per_page: list[tuple[int, list[dict], list[str]]] = []
    for pn in sorted(pages_by_number or {}):
        page = pages_by_number.get(pn) or {}
        if not _is_story_page(page):
            continue
        page_tb = page.get("text_blocks")  # None on new schema → panel_dialog uses nested
        src = str(page.get("source_image") or "")
        dims = page.get("image_dimensions") or {}
        parea = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
        pts, txts = [], []
        for idx, panel in enumerate(page.get("panels") or []):
            bb = panel.get("bbox", {}) or {}
            area = int(bb.get("w", 0) or 0) * int(bb.get("h", 0) or 0)
            txts.append(panel_embed_text(panel, page_tb))
            pts.append({
                "id": pn * 1000 + idx,
                "payload": {"page": pn, "index": idx, "source_image": src,
                            "area": area, "page_area": parea},
            })
        if pts:
            per_page.append((pn, pts, txts))

    if not per_page:
        return 0
    if _embedding.backend_name() == "none":
        log("[panel-index] skipped — no embedding backend")
        return 0

    # DON'T drop the old index up-front: embed FIRST and recreate the collection only once we
    # actually have vectors (see `created` below). A backend that passes the "none" check above
    # but then fails EVERY embed call (API flaky/timeout) would otherwise leave the collection
    # dropped-and-empty — silently degrading every later render to in-memory embed with nobody
    # the wiser. Sized to the ACTIVE backend (Gemini 3072 / Qwen 4096 / mxbai 1024); the first
    # recreate sets the dim.
    total, failed_pages, created = 0, [], False
    for i in range(0, len(per_page), batch_pages):
        chunk = per_page[i:i + batch_pages]
        chunk_pns = [pn for pn, _p, _t in chunk]
        pts = [p for _pn, ps, _t in chunk for p in ps]
        txts = [t for _pn, _p, ts in chunk for t in ts]
        try:
            vecs = _embed_with_retry(_embedding.embed_batch, txts, log=log)
            ok = [(p, v) for p, v in zip(pts, vecs) if v is not None]
            for p, v in ok:
                p["vector"] = v.tolist() if hasattr(v, "tolist") else list(v)
            if ok:
                if not created:      # first real vectors → NOW it's safe to drop the stale index
                    _qdrant.ensure_collection(project, _embedding.embed_dim(), recreate=True)
                    created = True
                _qdrant.upsert_panels(project, [p for p, _ in ok])
                total += len(ok)
            if len(ok) != len(pts):
                failed_pages.extend(chunk_pns)
                log(f"[panel-index] pages {chunk_pns}: {len(pts) - len(ok)}/{len(pts)} panels failed to embed")
        except Exception as exc:
            failed_pages.extend(chunk_pns)
            log(f"[panel-index] pages {chunk_pns} batch failed: {exc}")

    coll = _qdrant.collection_name(project)
    if failed_pages:
        log(f"[panel-index] {project}: {total} vectors → '{coll}' "
            f"(⚠ pages with missing embeds: {sorted(set(failed_pages))})")
    else:
        log(f"[panel-index] {project}: {total} panel vectors → '{coll}'")
    return total


def _dim_mismatch(vecs: dict, expected_dim: int) -> bool:
    """True when `vecs` is non-empty and its vector length differs from
    `expected_dim` — a project indexed under one embedding backend (e.g. Gemini
    3072-dim) read back under a different live backend (e.g. Qwen 4096-dim) would
    otherwise crash the matcher's np.dot. Pure/testable without a Qdrant client."""
    if not vecs:
        return False
    got = len(next(iter(vecs.values())))
    return got != expected_dim


def load_vectors(project: str) -> dict[tuple[int, int], "object"]:
    """Read persisted panel vectors as {(page, index): np.ndarray}. {} if unavailable
    or if the persisted dim doesn't match the current embedding backend's dim."""
    try:
        import numpy as np
        from . import _embedding, _qdrant
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
        expected_dim = _embedding.embed_dim()
        if _dim_mismatch(out, expected_dim):
            got = len(next(iter(out.values())))
            print(f"[panel-index] panel index dim {got} != current embed dim "
                  f"{expected_dim} — ignoring persisted vectors, falling back to "
                  f"in-memory embed")
            return {}
        return out
    except Exception as exc:  # pragma: no cover
        print(f"[panel-index] load_vectors failed: {exc}", file=sys.stderr)
        return {}
