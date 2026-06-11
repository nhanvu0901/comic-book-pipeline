"""The Met Open Access adapter. Clean REST JSON — eliminates the whole
Cloudflare/PoW scraping failure class the comic pipeline fights (spec §6.1).
API docs: https://metmuseum.github.io/"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

from ..config import MET_API_BASE, MET_USER_AGENT


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": MET_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_meta(object_id: int) -> dict:
    return _get_json(f"{MET_API_BASE}/objects/{int(object_id)}")


def search(query: str, *, has_images: bool = True, limit: int = 50) -> list[int]:
    q = urllib.parse.urlencode({"hasImages": str(has_images).lower(), "q": query})
    data = _get_json(f"{MET_API_BASE}/search?{q}")
    return list(data.get("objectIDs") or [])[:limit]


def validate_cc0(meta: dict) -> tuple[bool, str]:
    """Hard gate (spec §4-A2): refuse anything not isPublicDomain or imageless."""
    if not meta.get("isPublicDomain"):
        return False, f"objectID {meta.get('objectID')} is NOT public domain — refused"
    if not (meta.get("primaryImage") or "").strip():
        return False, f"objectID {meta.get('objectID')} has no primaryImage"
    return True, ""


def parse_candidate(meta: dict) -> dict:
    return {
        "object_id": meta.get("objectID"),
        "title": meta.get("title") or "",
        "artist": meta.get("artistDisplayName") or "",
        "year": meta.get("objectDate") or "",
        "department": meta.get("department") or "",
        "image_url": meta.get("primaryImage") or "",
        "object_url": meta.get("objectURL") or "",
        "credit_line": meta.get("creditLine") or "",
        "medium": meta.get("medium") or "",
    }


def fetch_image(meta: dict, dest: Path) -> Path:
    url = (meta.get("primaryImage") or "").strip()
    if not url:
        raise ValueError(f"objectID {meta.get('objectID')}: no primaryImage")
    req = urllib.request.Request(url, headers={"User-Agent": MET_USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.read())
    return dest
