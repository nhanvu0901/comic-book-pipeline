"""
Scraper for batcave.biz using pure HTTP (curl_cffi for TLS fingerprinting).

How the site works:
  batcave.biz gates every HTML page behind a custom SHA-256 proof-of-work
  challenge (not Cloudflare). Flow:

    1. GET any page → returns a small HTML with a <script> containing:
           p.token = "<base64-ish string>"
       and JS that finds `nonce` where SHA-256("{token}:{nonce}") starts with
       "00" in hex (avg ~128 iterations, under 1 ms in Python).
    2. POST /_v with (token, nonce, fake browser fingerprint) → server sets
       two session cookies: __guard_token and __guard_trust.
    3. Subsequent GETs return the real HTML, which embeds
           window.__DATA__ = {...}
       with everything we need:
         • Series page: chapters list (no pagination, all at once)
         • Reader page: images list (all URLs on img.batcave.biz)
    4. Image CDN img.batcave.biz just needs the guard cookies + a
       `Referer: https://batcave.biz/` header. No cf_clearance, no
       Cloudflare clearance, no browser.

Reference: keiyoushi/extensions-source BatCave.kt uses the same flow
(just Referer + session cookies, no special Cloudflare handling).
"""
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests as cf_req

SITE_BASE = "https://batcave.biz"
_POW_PREFIX = "00"  # hex prefix the SHA-256 hash must start with

# Module-level cached session — solve the challenge once per process.
_session: cf_req.Session | None = None


# ─── Challenge solver ────────────────────────────────────────────────────────


def _solve_pow(token: str) -> tuple[int, float]:
    """Find nonce such that SHA-256(f'{token}:{nonce}') starts with '00' (hex)."""
    t0 = time.time()
    nonce = 0
    while True:
        if hashlib.sha256(f"{token}:{nonce}".encode()).hexdigest().startswith(_POW_PREFIX):
            return nonce, time.time() - t0
        nonce += 1


def _new_session() -> cf_req.Session:
    """Create a session and solve the batcave.biz challenge. Returns a Session
    with __guard_token/__guard_trust cookies ready to use."""
    sess = cf_req.Session(impersonate="chrome")

    print("[scraper] Solving batcave.biz challenge...")
    r = sess.get(f"{SITE_BASE}/", timeout=15)
    m = re.search(r'token:\s*"([^"]+)"', r.text)
    if not m:
        raise RuntimeError(
            f"Could not find challenge token on {SITE_BASE}/ (status={r.status_code}). "
            "Site may have changed its anti-bot system."
        )
    token = m.group(1)

    nonce, dt = _solve_pow(token)
    sess.post(
        f"{SITE_BASE}/_v",
        data={
            "token": token,
            "mode": "modern",
            "workTime": int(dt * 1000),
            "iterations": nonce,
            "webdriver": "0",
            "touch": "0",
            "screen_w": "1920",
            "screen_h": "1080",
            "screen_cd": "24",
            "wgv": "Apple Inc.",
            "wgr": "Apple M2",
            "tz": "-420",
            "dpr": "2",
        },
        timeout=15,
    )

    if "__guard_token" not in sess.cookies:
        raise RuntimeError(
            "POST /_v did not set guard cookies — challenge solver may be broken."
        )
    print(f"[scraper] Challenge solved: nonce={nonce} in {dt * 1000:.0f}ms")
    return sess


def _get_session() -> cf_req.Session:
    """Return a cached session; re-solve the challenge if it was dropped."""
    global _session
    if _session is None or "__guard_token" not in _session.cookies:
        _session = _new_session()
    return _session


# ─── HTML → window.__DATA__ extraction ────────────────────────────────────────


def _fetch_data(url: str) -> dict[str, Any] | None:
    """Fetch a batcave.biz page and return its window.__DATA__ JSON, or None."""
    sess = _get_session()
    r = sess.get(url, timeout=20)

    # If guard cookies expired, retry once with a fresh session.
    if r.status_code == 404 and "token:" in r.text:
        print("[scraper] Guard cookies appear expired — re-solving challenge...")
        global _session
        _session = None
        sess = _get_session()
        r = sess.get(url, timeout=20)

    if r.status_code != 200:
        print(f"[scraper] GET {url} → status={r.status_code}")
        return None

    m = re.search(r"window\.__DATA__\s*=\s*({.*?});", r.text, re.DOTALL)
    if not m:
        print(f"[scraper] window.__DATA__ not found on {url}")
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"[scraper] Failed to parse __DATA__: {e}")
        return None


def _ajax_chapter_images(reader_url: str, news_id, chapter_id) -> list[str]:
    """Fetch a chapter's image list via the reader's AJAX API.

    Site update ~2026-07: reader pages ship an empty __DATA__.images and the Vue
    app fetches them with sendAjax("reader/getChapterData") instead (libs.min.js
    v1.2.8). Mirror that call. Returns [] on any failure."""
    if not news_id or not chapter_id:
        return []
    sess = _get_session()
    try:
        r = sess.post(
            f"{SITE_BASE}/engine/ajax/controller.php?mod=api&action=reader/getChapterData",
            data={"news_id": news_id, "chapter_id": chapter_id},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": reader_url},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[scraper] getChapterData → status={r.status_code}")
            return []
        return (r.json().get("data") or {}).get("images") or []
    except Exception as e:  # noqa: BLE001 — network/JSON errors both mean "no images"
        print(f"[scraper] getChapterData failed: {e}")
        return []


# ─── Image download ──────────────────────────────────────────────────────────


def _download_image(url: str, save_path: Path) -> bool:
    """Download one image via the shared session with Referer header."""
    sess = _get_session()
    try:
        r = sess.get(
            url,
            headers={"Referer": f"{SITE_BASE}/"},
            timeout=30,
        )
        r.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(r.content)
        return True
    except Exception as e:
        print(f"[scraper] Download failed {url}: {e}")
        return False


# ─── Public API ───────────────────────────────────────────────────────────────


def discover_issues(series_url: str, headless: bool | None = None) -> list[dict]:
    """
    Discover all issues/chapters for a series from its batcave.biz page.
    Uses window.__DATA__ — returns ALL chapters at once, no pagination.

    Args:
        series_url: e.g. "https://batcave.biz/6587-what-if-dark-venom-2023.html"
        headless: Ignored (kept for backwards compatibility — scraper is headless by design now).

    Returns:
        List of dicts sorted by issue number (ascending):
        [{"title": "Issue #1", "url": "https://batcave.biz/reader/6587/34073",
          "chapter_id": 34073, "number": 1.0, "date": "15.01.2024"}]
    """
    data = _fetch_data(series_url)
    if not data:
        return []

    news_id = data.get("news_id")
    xhash = data.get("xhash", "")
    chapters = data.get("chapters", [])
    if not news_id or not chapters:
        print(f"[scraper] No chapters in __DATA__. Keys: {list(data.keys())}")
        return []

    print(f"[scraper] Found {len(chapters)} chapter(s) for news_id={news_id}")

    issues = []
    for chap in chapters:
        chap_id = chap.get("id")
        if not chap_id:
            continue
        posi = chap.get("posi", 0)
        title = (chap.get("title") or f"Issue #{int(posi)}").strip()
        reader_url = f"{SITE_BASE}/reader/{news_id}/{chap_id}{xhash}"
        # `number` = the ACTUAL issue number parsed from the title, NOT batcave's posi.
        # posi is a display slot and can disagree with the issue order (Planet Hulk 2015
        # lists #5 at posi 4 and #4 at posi 5) — sorting by posi then swaps issues #4/#5
        # in a saga ingest. Parse "#N" from the title (decimals kept: #33.1); fall back to
        # posi only when the title has no number.
        m = re.search(r"#\s*(\d+(?:\.\d+)?)", title)
        number = float(m.group(1)) if m else float(posi or 0)
        issues.append({
            "title": title,
            "url": reader_url,
            "chapter_id": chap_id,
            "number": number,
            "date": chap.get("date", ""),
        })

    issues.sort(key=lambda x: x["number"])
    for item in issues:
        print(f"[scraper]   #{item['number']} — {item['title']} → {item['url']}")
    return issues


def scrape_issue_pages(
    reader_url: str,
    *,
    project_root: Path,
    chapter_index: int = 1,
    headless: bool | None = None,
) -> list[Path]:
    """
    Scrape all pages from a comic issue reader page into the project's
    raw_comic/ folder, prefixed with chXX_ so multiple chapters can coexist.

    Args:
        reader_url: e.g. "https://batcave.biz/reader/6587/34073"
        project_root: Path to projects/<slug>/.
        chapter_index: 1-based chapter number, used as filename prefix.
        headless: Ignored (kept for backwards compatibility).

    Returns:
        Sorted list of local file paths for each downloaded page.
    """
    raw_dir = Path(project_root) / "raw_comic"
    raw_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"ch{chapter_index:02d}_"
    existing = sorted(raw_dir.glob(f"{prefix}page_*.jpg"))

    data = _fetch_data(reader_url)
    if not data:
        # Offline / reader down: a cached set is better than nothing, but say so —
        # we cannot verify completeness without the page list.
        if existing:
            print(f"[scraper] reader unreachable — using {len(existing)} cached page(s) "
                  f"UNVERIFIED for completeness ({prefix}*)")
            return existing
        return []

    raw_images = data.get("images") or []
    if not raw_images and data.get("rdr_ajax"):
        raw_images = _ajax_chapter_images(
            reader_url, data.get("news_id"), data.get("chapter_id"))
    if not raw_images:
        # Fail loud: a silent [] used to produce a 0-page manifest the rest of the
        # pipeline happily "succeeded" on (status=ok, 0 pages).
        raise RuntimeError(
            f"no images for {reader_url} (rdr_ajax={data.get('rdr_ajax')!r}, "
            f"pages={data.get('pages')!r}) — reader layout may have changed again"
        )

    image_urls = [
        img if img.startswith("http") else SITE_BASE + img
        for img in (i.strip() for i in raw_images) if img
    ]
    # Cache short-circuit ONLY when the chapter is COMPLETE. The old "any file
    # exists → return" froze partially-downloaded chapters forever (a guard/network
    # failure mid-chapter left 20/40 pages that every later run happily reused,
    # silently shipping a comic with missing pages). The per-page loop below already
    # skips files that exist, so an incomplete chapter resumes instead of re-fetching.
    if len(existing) >= len(image_urls):
        print(f"[scraper] Using cached pages: {len(existing)}/{len(image_urls)} in {raw_dir} ({prefix}*)")
        return existing
    if existing:
        print(f"[scraper] cached {len(existing)}/{len(image_urls)} page(s) — resuming download of the rest")
    print(f"[scraper] Found {len(image_urls)} pages — downloading...")

    pages: list[Path] = []
    for i, url in enumerate(image_urls, start=1):
        page_path = raw_dir / f"{prefix}page_{i:02d}.jpg"
        if page_path.exists():
            pages.append(page_path)
            continue

        print(f"[scraper] Page {i}/{len(image_urls)}...", end=" ", flush=True)
        if _download_image(url, page_path):
            pages.append(page_path)
            print("✓")
        else:
            print("✗")
        time.sleep(0.2)

    return sorted(pages)


def scrape_single_page(
    reader_url: str,
    page_num: int,
    *,
    project_root: Path,
    chapter_index: int = 1,
) -> Path | None:
    """Scrape a single page from an issue. Uses cache if available."""
    pages = scrape_issue_pages(
        reader_url, project_root=project_root, chapter_index=chapter_index,
    )
    if 1 <= page_num <= len(pages):
        return pages[page_num - 1]
    return None
