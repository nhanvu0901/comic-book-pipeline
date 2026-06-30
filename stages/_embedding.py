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

_BACKEND = None          # "gemini" | "azure" | "openai" | "local" | "none"
_AZURE_CLIENT = None
_GENAI_CLIENT = None
_GENAI_TYPES = None
_SENT_MODEL = None
_OPENAI_URL = None       # OpenAI-compatible /v1/embeddings endpoint (e.g. local llama-server)
_RESOLVED = False
_EMBED_CACHE: dict[str, "object"] = {}


def _init_gemini() -> bool:
    global _BACKEND, _GENAI_CLIENT, _GENAI_TYPES
    import config
    if not getattr(config, "GEMINI_API_KEY", ""):
        return False
    try:
        from google import genai
        from google.genai import types
        _GENAI_CLIENT = genai.Client(api_key=config.GEMINI_API_KEY,
                                     http_options=types.HttpOptions(timeout=60000))
        _GENAI_TYPES = types
        _BACKEND = "gemini"
        print(f"[embedding] backend=Gemini ({config.GEMINI_EMBED_MODEL})", file=sys.stderr)
        return True
    except Exception as exc:
        print(f"[embedding] Gemini init failed ({exc})", file=sys.stderr)
        return False


def _init_openai() -> bool:
    """OpenAI-compatible /v1/embeddings server (e.g. local llama-server running
    Qwen3-Embedding with --embedding --pooling last). Just records the URL — failures
    surface per-call (graceful None), so a down server doesn't crash resolution."""
    global _BACKEND, _OPENAI_URL
    import config
    url = getattr(config, "EMBED_OPENAI_URL", "")
    if not url:
        return False
    _OPENAI_URL = url
    _BACKEND = "openai"
    print(f"[embedding] backend=openai ({config.EMBED_OPENAI_MODEL} @ {url}, "
          f"dim={config.EMBED_OPENAI_DIM})", file=sys.stderr)
    return True


def _init_azure() -> bool:
    global _BACKEND, _AZURE_CLIENT
    import config
    if not config._azure_embed_ready():
        return False
    try:
        from openai import AzureOpenAI
        _AZURE_CLIENT = AzureOpenAI(
            api_key=config.AZURE_OPENAI_EMBEDDING_API_KEY,
            azure_endpoint=config.AZURE_OPENAI_EMBEDDING_ENDPOINT,
            api_version=config.AZURE_OPENAI_EMBEDDING_MODEL_API_VERSION,
        )
        _BACKEND = "azure"
        print(f"[embedding] backend=Azure ({config.AZURE_OPENAI_EMBEDDING_MODEL_NAME})", file=sys.stderr)
        return True
    except Exception as exc:
        print(f"[embedding] Azure init failed ({exc})", file=sys.stderr)
        return False


def _init_local() -> bool:
    global _BACKEND, _SENT_MODEL
    try:
        from sentence_transformers import SentenceTransformer
        # mxbai-embed-large-v1 (335M) — best of the OFFLINE options on our benchmark.
        _SENT_MODEL = SentenceTransformer("mixedbread-ai/mxbai-embed-large-v1")
        _BACKEND = "local"
        print("[embedding] backend=local (mxbai-embed-large-v1)", file=sys.stderr)
        return True
    except Exception:
        return False


def _resolve_backend():
    """Pick the embedding backend once. Never raises. EMBED_BACKEND forces one
    (google/qwen/azure/local); "auto" (default) chains Gemini → Azure → local."""
    global _BACKEND, _RESOLVED
    if _RESOLVED:
        return
    _RESOLVED = True
    import config
    pref = (getattr(config, "EMBED_BACKEND", "auto") or "auto").lower()
    forced = {"google": _init_gemini, "gemini": _init_gemini,
              "qwen": _init_openai, "openai": _init_openai,
              "azure": _init_azure, "local": _init_local}
    if pref in forced:
        if forced[pref]():
            return
        print(f"[embedding] EMBED_BACKEND={pref!r} unavailable — falling back to auto chain",
              file=sys.stderr)
    # auto chain (NB: openai is opt-in only — never auto-picked, the local server may be down)
    if _init_gemini() or _init_azure() or _init_local():
        return
    _BACKEND = "none"
    print("[embedding] no backend available — semantic match disabled", file=sys.stderr)


def embed_dim() -> int:
    """Vector dimensionality of the ACTIVE backend — for sizing the Qdrant collection."""
    import config
    return {"gemini": 3072, "azure": 3072,
            "openai": int(getattr(config, "EMBED_OPENAI_DIM", 4096) or 4096),
            "local": 1024}.get(backend_name(), 3072)


def backend_name() -> str:
    _resolve_backend()
    return _BACKEND or "none"


def _normalize(vec):
    import numpy as np
    v = np.asarray(vec, dtype="float32")
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


_GEMINI_RPM = 95              # stay under the free-tier 100 embed-requests/min quota
_GEMINI_CALLS: list = []     # sliding window of (monotonic_ts, n_contents)


def _gemini_pace(n: int):
    """Block until embedding `n` more contents keeps us under _GEMINI_RPM in any 60s
    window (each content counts as one request on the free tier). Prevents the 429
    RESOURCE_EXHAUSTED that otherwise kills a batch."""
    import time
    global _GEMINI_CALLS
    while True:
        now = time.monotonic()
        _GEMINI_CALLS = [(t, c) for t, c in _GEMINI_CALLS if now - t < 60.0]
        used = sum(c for _, c in _GEMINI_CALLS)
        if used + n <= _GEMINI_RPM or not _GEMINI_CALLS:
            _GEMINI_CALLS.append((time.monotonic(), n))
            return
        wait = 60.0 - (now - _GEMINI_CALLS[0][0]) + 0.5
        print(f"[embedding] Gemini pacing: sleep {wait:.0f}s ({used}+{n} > {_GEMINI_RPM}/min)", file=sys.stderr)
        time.sleep(max(0.5, wait))


def _gemini_embed(texts: list[str]):
    """Batch-embed via Gemini (gemini-embedding-001, 3072-dim); normalized vectors or []."""
    import config
    _gemini_pace(len(texts))
    try:
        resp = _GENAI_CLIENT.models.embed_content(
            model=config.GEMINI_EMBED_MODEL,
            contents=texts,
            config=_GENAI_TYPES.EmbedContentConfig(
                task_type="SEMANTIC_SIMILARITY", output_dimensionality=3072),
        )
        return [_normalize(e.values) for e in resp.embeddings]
    except Exception as exc:
        print(f"[embedding] Gemini embed call failed: {exc}", file=sys.stderr)
        return []


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


def _openai_embed(texts: list[str]):
    """Batch-embed via an OpenAI-compatible /v1/embeddings server (local llama-server,
    LM Studio, etc.). Returns normalized vectors or [] on any failure (graceful)."""
    import json, urllib.request, config
    try:
        body = json.dumps({"model": config.EMBED_OPENAI_MODEL, "input": texts}).encode()
        req = urllib.request.Request(
            _OPENAI_URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.load(r)
        data = sorted(resp["data"], key=lambda d: d.get("index", 0))
        return [_normalize(d["embedding"]) for d in data]
    except Exception as exc:
        print(f"[embedding] OpenAI-compatible embed call failed: {exc}", file=sys.stderr)
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
    if _BACKEND == "gemini":
        vecs = _gemini_embed(miss_txt)
        if len(vecs) != len(miss_txt):
            vecs = []  # partial/failed → leave as None (graceful)
    elif _BACKEND == "openai":
        vecs = _openai_embed(miss_txt)
        if len(vecs) != len(miss_txt):
            vecs = []  # partial/failed → leave as None (graceful)
    elif _BACKEND == "azure":
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
