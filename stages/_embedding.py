"""Shared local sentence-embedding utility (Stage 3 + Stage 5).

A local sentence-embedding model scores how well two texts match semantically —
far more reliable than lexical word overlap ("mistletoe" vs "spear", "reverted to
mortal form" vs "FIZAPPT"). Model = mxbai-embed-large-v1 (benchmarked best of ~10
local/API models on our panel-match data). Lazy-loaded singleton; if unavailable
every call degrades gracefully to 0.0 / None.

Previously this lived inside stages/stage_5/shots.py. Stage 3's panel-grounding
needs the SAME model + cache, so it was factored out here — one model, one cache,
no Stage-3 → Stage-5 import.
"""

_SENT_MODEL = None
_SENT_MODEL_TRIED = False
_EMBED_CACHE: dict[str, "object"] = {}


def sent_model():
    global _SENT_MODEL, _SENT_MODEL_TRIED
    if _SENT_MODEL_TRIED:
        return _SENT_MODEL
    _SENT_MODEL_TRIED = True
    try:
        from sentence_transformers import SentenceTransformer
        # mxbai-embed-large-v1 (335M) — best on our panel-match benchmark across
        # 3 comics (55% page_ref±1 vs all-MiniLM's 26%); ~5s/render, 100% local.
        _SENT_MODEL = SentenceTransformer("mixedbread-ai/mxbai-embed-large-v1")
    except Exception:
        _SENT_MODEL = None
    return _SENT_MODEL


def embed(text: str):
    """Cached unit-normalized embedding for a piece of text (or None)."""
    text = (text or "").strip()
    if not text:
        return None
    if text in _EMBED_CACHE:
        return _EMBED_CACHE[text]
    m = sent_model()
    if m is None:
        return None
    vec = m.encode(text, convert_to_numpy=True, normalize_embeddings=True)
    _EMBED_CACHE[text] = vec
    return vec


def semantic_sim(a: str, b: str) -> float:
    """Cosine similarity in [0,1] between two texts (0 if model unavailable)."""
    va, vb = embed(a), embed(b)
    if va is None or vb is None:
        return 0.0
    import numpy as np
    return max(0.0, min(1.0, float(np.dot(va, vb))))
