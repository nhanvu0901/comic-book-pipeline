"""Shared sentence-embedding utility (Stage 3 + Stage 5).

A sentence-embedding model scores how well two texts match semantically — far
more reliable than lexical word overlap ("mistletoe" vs "spear", "reverted to
mortal form" vs "FIZAPPT").

Backend (resolved once, lazily):
  1. Azure OpenAI text-embedding-3-large  — when AZURE_OPENAI_EMBEDDING_* env is
     set (network, but Cloudflare-independent). Best quality.
  2. local mxbai-embed-large-v1 (sentence-transformers) — offline fallback.
  3. None — every call degrades gracefully to 0.0 / None.

Stage 3's panel-grounding and Stage 5's panel-match share the SAME model + cache
(one model, one cache, no Stage-3 → Stage-5 import).
"""
from __future__ import annotations

import sys

_BACKEND = None          # "azure" | "local" | "none"
_AZURE_CLIENT = None
_SENT_MODEL = None
_RESOLVED = False
_EMBED_CACHE: dict[str, "object"] = {}


def _resolve_backend():
    """Pick the embedding backend once. Never raises."""
    global _BACKEND, _AZURE_CLIENT, _SENT_MODEL, _RESOLVED
    if _RESOLVED:
        return
    _RESOLVED = True
    import config
    if config._azure_embed_ready():
        try:
            from openai import AzureOpenAI
            _AZURE_CLIENT = AzureOpenAI(
                api_key=config.AZURE_OPENAI_EMBEDDING_API_KEY,
                azure_endpoint=config.AZURE_OPENAI_EMBEDDING_ENDPOINT,
                api_version=config.AZURE_OPENAI_EMBEDDING_MODEL_API_VERSION,
            )
            _BACKEND = "azure"
            print(f"[embedding] backend=Azure ({config.AZURE_OPENAI_EMBEDDING_MODEL_NAME})", file=sys.stderr)
            return
        except Exception as exc:  # bad key/endpoint/lib → fall through to local
            print(f"[embedding] Azure init failed ({exc}); falling back to local", file=sys.stderr)
    try:
        from sentence_transformers import SentenceTransformer
        # mxbai-embed-large-v1 (335M) — best on our panel-match benchmark.
        _SENT_MODEL = SentenceTransformer("mixedbread-ai/mxbai-embed-large-v1")
        _BACKEND = "local"
        print("[embedding] backend=local (mxbai-embed-large-v1)", file=sys.stderr)
    except Exception:
        _BACKEND = "none"
        print("[embedding] no backend available — semantic match disabled", file=sys.stderr)


def backend_name() -> str:
    _resolve_backend()
    return _BACKEND or "none"


def _normalize(vec):
    import numpy as np
    v = np.asarray(vec, dtype="float32")
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _azure_embed(texts: list[str]):
    """Batch-embed via Azure; returns list of normalized vectors (or [] on failure)."""
    import config
    try:
        resp = _AZURE_CLIENT.embeddings.create(
            model=config.AZURE_OPENAI_EMBEDDING_MODEL_NAME, input=texts)
        return [_normalize(d.embedding) for d in resp.data]
    except Exception as exc:
        print(f"[embedding] Azure embed call failed: {exc}", file=sys.stderr)
        return []


def embed_batch(texts: list[str]) -> list:
    """Embed many texts at once (one Azure call). Returns a list aligned with
    `texts`; entries are normalized vectors or None. Caches each result."""
    _resolve_backend()
    out: list = [None] * len(texts)
    miss_idx, miss_txt = [], []
    for i, t in enumerate(texts):
        t = (t or "").strip()
        if not t:
            continue
        if t in _EMBED_CACHE:
            out[i] = _EMBED_CACHE[t]
        else:
            miss_idx.append(i)
            miss_txt.append(t)
    if not miss_txt or _BACKEND == "none":
        return out
    if _BACKEND == "azure":
        vecs = _azure_embed(miss_txt)
        if len(vecs) != len(miss_txt):
            vecs = []  # partial/failed → leave as None (graceful)
    else:  # local
        try:
            arr = _SENT_MODEL.encode(miss_txt, convert_to_numpy=True, normalize_embeddings=True)
            vecs = list(arr)
        except Exception as exc:
            print(f"[embedding] local encode failed: {exc}", file=sys.stderr)
            vecs = []
    for j, i in enumerate(miss_idx):
        if j < len(vecs):
            v = vecs[j]
            _EMBED_CACHE[miss_txt[j]] = v
            out[i] = v
    return out


def embed(text: str):
    """Cached unit-normalized embedding for one text (or None)."""
    return embed_batch([text])[0]


def semantic_sim(a: str, b: str) -> float:
    """Cosine similarity in [0,1] between two texts (0 if model unavailable)."""
    va, vb = embed(a), embed(b)
    if va is None or vb is None:
        return 0.0
    import numpy as np
    return max(0.0, min(1.0, float(np.dot(va, vb))))
