"""Qdrant vector store for panel↔narration matching.

Panels are embedded ONCE (at Stage 2, after the VLM produces clean descriptions) and
upserted into a per-project collection; Stage 5 queries it with narration-chunk vectors
instead of re-embedding every panel each run. Each point's payload carries everything the
matcher needs (page, panel index, bbox, source image, description, characters, emotion,
dialog) so a hybrid score (vector cosine + lexical) can be computed without re-reading the
preprocessed JSON. Degrades gracefully: if Qdrant is unreachable, callers fall back to the
in-memory embedding path.
"""
from __future__ import annotations

import re

import config

_client = None


def client():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient
        _client = QdrantClient(url=config.QDRANT_URL, timeout=30)
    return _client


def collection_name(project: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(project).lower()).strip("_")
    return f"panels__{slug}"


def ensure_collection(project: str, dim: int, *, recreate: bool = False) -> str:
    from qdrant_client import models
    c = client()
    name = collection_name(project)
    exists = c.collection_exists(name)
    if exists and recreate:
        c.delete_collection(name)
        exists = False
    if not exists:
        c.create_collection(
            name,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
    return name


def upsert_panels(project: str, points: list[dict]) -> None:
    """points = [{"id": int, "vector": [...], "payload": {...}}, ...]"""
    from qdrant_client import models
    c = client()
    name = collection_name(project)
    c.upsert(name, points=[
        models.PointStruct(id=p["id"], vector=list(p["vector"]), payload=p["payload"])
        for p in points
    ])


def search(project: str, vector, limit: int = 10) -> list[tuple]:
    """Return [(id, cosine_score, payload), ...] best-first for one query vector."""
    c = client()
    name = collection_name(project)
    res = c.query_points(name, query=list(vector), limit=limit, with_payload=True).points
    return [(h.id, float(h.score), h.payload) for h in res]


def count(project: str) -> int:
    try:
        return client().count(collection_name(project)).count
    except Exception:
        return 0
