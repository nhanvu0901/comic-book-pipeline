"""
Per-page JSON cache keyed by SHA-256 of the image bytes.

Layout: projects/<slug>/preprocessed/page_NN_<hash16>.json

The hash prefix in the filename means re-scraping a higher-resolution
version of the same page invalidates the cache automatically; unchanged
pages are re-read from disk.

page_number in the filename is the GLOBAL running index across chapters
(see pipeline.py's manifest flatten), so it shifts whenever an earlier
chapter's page count changes — a chapter-length change must not invalidate
every later chapter's cache. `load_cached` falls back to a content-hash-only
lookup and migrates ("re-keys") the file to its new expected name on a hit.
"""
import hashlib
import json
from pathlib import Path
from typing import Callable

from utils.atomic_json import write_json_atomic


def image_hash(image_path: Path | str) -> str:
    """SHA-256 of image bytes, truncated to 16 hex chars."""
    with open(image_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def cache_path(project_root: Path, page_number: int, h: str) -> Path:
    base = project_root / "preprocessed"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"page_{page_number:03d}_{h}.json"


def load_cached(
    project_root: Path,
    page_number: int,
    h: str,
    image_path: Path | str | None = None,
    *,
    log: Callable[[str], None] | None = None,
) -> dict | None:
    """Look up a cached page. Exact (page_number, hash) match first; on a miss,
    fall back to hash-only (any page_number) since the same image may simply have
    shifted position because an earlier chapter changed length. A hash-only hit is
    re-keyed on disk to the requested page_number so future lookups hit the fast path.
    """
    p = cache_path(project_root, page_number, h)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None

    base = project_root / "preprocessed"
    if not base.is_dir():
        return None
    matches = sorted(base.glob(f"page_*_{h}.json"))
    if not matches:
        return None

    candidates = []
    for m in matches:
        try:
            candidates.append((m, json.loads(m.read_text())))
        except json.JSONDecodeError:
            continue
    if not candidates:
        return None

    if len(candidates) > 1:
        # Duplicate-content pages (e.g. two blank pages) hash the same — only
        # trust a match whose own source_image is the file we're looking for.
        if image_path is None:
            return None  # can't disambiguate — treat as a miss, don't guess
        resolved = str(Path(image_path).resolve())
        candidates = [(m, d) for m, d in candidates if d.get("source_image") == resolved]
        if len(candidates) != 1:
            return None

    match, data = candidates[0]
    old_pn = data.get("page_number")
    data["page_number"] = page_number
    new_path = cache_path(project_root, page_number, h)
    new_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    if match != new_path:
        match.unlink(missing_ok=True)
    (log or print)(f"[preprocess]   ↻ cache re-key p{old_pn}→p{page_number:03d} ({h})")
    return data


def save_cached(project_root: Path, page_number: int, h: str, data: dict) -> Path:
    # Atomic: an interrupted run used to leave a truncated page JSON here, and BOTH
    # readers (stage_3/pipeline.py, stage_5/pipeline.py) skip unparseable pages with a
    # bare `except: continue` — so the page silently vanished from the panel pool and
    # every lock pointing at it slid onto a different panel, mid-render, with no log.
    return write_json_atomic(cache_path(project_root, page_number, h), data)
