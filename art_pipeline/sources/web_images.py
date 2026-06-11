# art_pipeline/sources/web_images.py
"""License-safe web image providers for the visuals stage (spec §3.1).

Whitelist filtering happens HERE: any candidate whose license string does not
map cleanly onto VISUAL_LICENSE_WHITELIST is dropped — license is read from API
metadata, never assumed. NC/ND variants are rejected explicitly because
"cc-by-nc-..." startswith "cc-by"."""
import json
import re
import urllib.parse
import urllib.request

from ..config import (
    COMMONS_API, MET_API_BASE, MET_USER_AGENT, OPENVERSE_API,
    VISUAL_LICENSE_WHITELIST,
)

_TAG_RE = re.compile(r"<[^>]+>")
_OK_MIME = ("image/jpeg", "image/png")


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": MET_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _strip_html(s: str) -> str:
    return _TAG_RE.sub("", s or "").strip()


def normalize_license(raw: str | None) -> str | None:
    """Map an API license string to a whitelist code, or None to reject."""
    low = (raw or "").strip().lower()
    if not low:
        return None
    if ("-nc" in low or " nc" in low or "by-nc" in low or "noncommercial" in low
            or "-nd" in low or " nd" in low or "noderiv" in low):
        return None  # non-commercial / no-derivatives — never usable
    if low.startswith("cc0") or low == "cc-0":
        code = "cc0"
    elif low in ("pd", "pdm") or "public domain" in low:
        code = "pd"
    elif low.startswith(("cc-by-sa", "cc by-sa")) or low == "by-sa":
        code = "by-sa"
    elif low.startswith(("cc-by", "cc by")) or low == "by":
        code = "by"
    else:
        return None
    return code if code in VISUAL_LICENSE_WHITELIST else None


def parse_commons_page(page: dict) -> dict | None:
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    if (info.get("mime") or "") not in _OK_MIME:
        return None
    meta = info.get("extmetadata") or {}
    lic = (normalize_license((meta.get("License") or {}).get("value"))
           or normalize_license((meta.get("LicenseShortName") or {}).get("value")))
    if lic is None:
        return None
    title = re.sub(r"^File:|\.\w+$", "", page.get("title") or "").replace("_", " ").strip()
    url = info.get("thumburl") or info.get("url") or ""
    if not url:
        return None
    return {
        "image_url": url,
        "title": title,
        "author": _strip_html((meta.get("Artist") or {}).get("value", "")),
        "license": lic,
        "source_url": info.get("descriptionurl") or "",
        "width": int(info.get("thumbwidth") or info.get("width") or 0),
        "height": int(info.get("thumbheight") or info.get("height") or 0),
    }


def search_commons(query: str, *, limit: int = 8) -> list[dict]:
    q = urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600, "format": "json",
    })
    try:
        data = _get_json(f"{COMMONS_API}?{q}")
    except Exception:
        return []
    pages = ((data.get("query") or {}).get("pages") or {}).values()
    out = []
    for p in pages:
        c = parse_commons_page(p)
        if c:
            out.append(c)
    return out


def parse_openverse_result(r: dict) -> dict | None:
    lic = normalize_license(r.get("license"))
    if lic is None or not r.get("url"):
        return None
    return {
        "image_url": r["url"],
        "title": r.get("title") or "",
        "author": r.get("creator") or "",
        "license": lic,
        "source_url": r.get("foreign_landing_url") or "",
        "width": int(r.get("width") or 0),
        "height": int(r.get("height") or 0),
    }


def search_openverse(query: str, *, limit: int = 8) -> list[dict]:
    q = urllib.parse.urlencode({"q": query, "license": "cc0,pd,by,by-sa",
                                "page_size": limit})
    try:
        data = _get_json(f"{OPENVERSE_API}?{q}")
    except Exception:
        return []
    out = []
    for r in data.get("results") or []:
        c = parse_openverse_result(r)
        if c:
            out.append(c)
    return out


def met_artist_works(artist: str, *, exclude_ids: tuple | set = (), limit: int = 5) -> list[dict]:
    """Other CC0 works by the same artist — strong candidates for technique/
    biography scenes. Bounded to 25 meta lookups."""
    if not artist:
        return []
    from . import met
    q = urllib.parse.urlencode({"artistOrCulture": "true", "hasImages": "true", "q": artist})
    try:
        ids = list(_get_json(f"{MET_API_BASE}/search?{q}").get("objectIDs") or [])
    except Exception:
        return []
    out: list[dict] = []
    for oid in ids[:25]:
        if oid in exclude_ids:
            continue
        try:
            m = met.fetch_meta(oid)
        except Exception:
            continue
        ok, _why = met.validate_cc0(m)
        if not ok:
            continue
        c = met.parse_candidate(m)
        out.append({"image_url": c["image_url"], "title": c["title"],
                    "author": c["artist"], "license": "cc0",
                    "source_url": c["object_url"], "width": 0, "height": 0})
        if len(out) >= limit:
            break
    return out
