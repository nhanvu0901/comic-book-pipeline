"""
Per-page JSON cache keyed by SHA-256 of the image bytes.

Layout: projects/<slug>/preprocessed/page_NN_<hash16>.json

The hash prefix in the filename means re-scraping a higher-resolution
version of the same page invalidates the cache automatically; unchanged
pages are re-read from disk.
"""
import hashlib
import json
from pathlib import Path


def image_hash(image_path: Path | str) -> str:
    """SHA-256 of image bytes, truncated to 16 hex chars."""
    with open(image_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def cache_path(project_root: Path, page_number: int, h: str) -> Path:
    base = project_root / "preprocessed"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"page_{page_number:03d}_{h}.json"


def load_cached(project_root: Path, page_number: int, h: str) -> dict | None:
    p = cache_path(project_root, page_number, h)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
    return None


def save_cached(project_root: Path, page_number: int, h: str, data: dict) -> Path:
    p = cache_path(project_root, page_number, h)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return p
