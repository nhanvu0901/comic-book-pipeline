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
import threading
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests as cf_req

SITE_BASE = "https://batcave.biz"
_POW_PREFIX = "00"  # hex prefix the SHA-256 hash must start with

# Module-level cached session — solve the challenge once per process.
_session: cf_req.Session | None = None
_session_lock = threading.Lock()

# A flaky network (the production box's DNS answered only about half of its lookups) used to
# fail a page after ONE attempt, so a 24-page chapter nearly always came back with holes.
# Retry what looks transient, give up fast on what cannot get better (a 404).
_ATTEMPTS = 5            # first try + 4 retries
_RETRY_BASE_DELAY = 1.5  # seconds; grows with the attempt number
# Pages in a row that ran out of retries on a network error before the chapter is abandoned:
# with the network down, retrying every remaining page would hang the download for an hour.
_GIVE_UP_AFTER = 3
_failure = threading.local()   # why the last page/list fetch failed, for the error message


def _short_reason(exc: BaseException) -> str:
    """One status-line reason: the exception kind and message without libcurl's docs link."""
    text = str(exc).split(" See https://", 1)[0].strip()
    return f"{type(exc).__name__}: {text}"[:240]


def _network_looks_down() -> bool:
    """The last failed fetch on this thread was a transient error that outlasted every retry."""
    return bool(getattr(_failure, "transient", False))


def last_download_error() -> str:
    """The reason the most recent failed fetch on this thread gave up, or ''."""
    return getattr(_failure, "reason", "")


def _is_transient(exc: BaseException) -> bool:
    """True for a failure a retry can plausibly cure: no HTTP answer at all (DNS, connect,
    timeout, reset), a throttled/overloaded server, or a file the OS had locked for a moment."""
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", 0) or 0
        return status in (408, 425, 429) or status >= 500
    return isinstance(exc, (cf_req.RequestsError, OSError))


def _retry_transient(call, what: str):
    """Run call(); on a transient failure wait a little and try again, up to _ATTEMPTS
    times. The last exception is re-raised so the caller still decides what a failure means."""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — classified below, re-raised when final
            if attempt == _ATTEMPTS or not _is_transient(exc):
                raise
            delay = _RETRY_BASE_DELAY * attempt
            print(f"[scraper] {what}: {_short_reason(exc)} — retry {attempt}/{_ATTEMPTS - 1} "
                  f"in {delay:.1f}s")
            time.sleep(delay)


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
    r = _retry_transient(lambda: sess.get(f"{SITE_BASE}/", timeout=15), "challenge page")
    m = re.search(r'token:\s*"([^"]+)"', r.text)
    if not m:
        raise RuntimeError(
            f"Could not find challenge token on {SITE_BASE}/ (status={r.status_code}). "
            "Site may have changed its anti-bot system."
        )
    token = m.group(1)

    nonce, dt = _solve_pow(token)
    _retry_transient(lambda: sess.post(
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
    ), "challenge answer")

    if not any(k in sess.cookies for k in ("__guard_trust", "__guard_token", "__guard_id")):
        raise RuntimeError(
            f"POST /_v did not set guard cookies (got: {list(sess.cookies.keys())}) — challenge solver may be broken."
        )
    print(f"[scraper] Challenge solved: nonce={nonce} in {dt * 1000:.0f}ms")
    return sess


def _get_session() -> cf_req.Session:
    """Return a cached session; re-solve the challenge if it was dropped."""
    global _session
    with _session_lock:
        if _session is None or not any(k in _session.cookies for k in ("__guard_trust", "__guard_token", "__guard_id")):
            _session = _new_session()
        return _session


# ─── HTML → window.__DATA__ extraction ────────────────────────────────────────


def _fetch_data(url: str) -> dict[str, Any] | None:
    """Fetch a batcave.biz page and return its window.__DATA__ JSON, or None."""
    sess = _get_session()
    r = _retry_transient(lambda: sess.get(url, timeout=20), "reader page")

    # If guard cookies expired, retry once with a fresh session.
    if r.status_code == 404 and "token:" in r.text:
        print("[scraper] Guard cookies appear expired — re-solving challenge...")
        global _session
        _session = None
        sess = _get_session()
        r = _retry_transient(lambda: sess.get(url, timeout=20), "reader page")

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
        r = _retry_transient(lambda: sess.post(
            f"{SITE_BASE}/engine/ajax/controller.php?mod=api&action=reader/getChapterData",
            data={"news_id": news_id, "chapter_id": chapter_id},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": reader_url},
            timeout=20,
        ), "chapter page list")
        if r.status_code != 200:
            print(f"[scraper] getChapterData → status={r.status_code}")
            return []
        return (r.json().get("data") or {}).get("images") or []
    except Exception as e:  # noqa: BLE001 — network/JSON errors both mean "no images"
        print(f"[scraper] getChapterData failed: {e}")
        _failure.reason = _short_reason(e)
        return []


# ─── Image download ──────────────────────────────────────────────────────────


def _download_image(url: str, save_path: Path) -> bool:
    """Download one image via the shared session with Referer header."""
    sess = _get_session()

    def _fetch_and_save() -> None:
        r = sess.get(
            url,
            headers={"Referer": f"{SITE_BASE}/"},
            timeout=30,
        )
        r.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(r.content)

    try:
        _retry_transient(_fetch_and_save, "image")
        _failure.reason = ""
        _failure.transient = False
        return True
    except Exception as e:
        print(f"[scraper] Download failed {url}: {e}")
        _failure.reason = _short_reason(e)
        _failure.transient = _is_transient(e)
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
    existing = sorted(raw_dir.glob(f"{prefix}page_*.jpg"), key=_page_number_of)

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
        _failure.reason = ""
        raw_images = _ajax_chapter_images(
            reader_url, data.get("news_id"), data.get("chapter_id"))
        if not raw_images and last_download_error():
            # A network failure, not a layout change — do not send anyone chasing the site.
            raise RuntimeError(
                f"could not load the page list for {reader_url}: {last_download_error()}")
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
    # Return exactly THIS chapter's page names, in reading order: extra files left by a
    # longer earlier download are not part of it, and a plain sort put page_100 before
    # page_11.
    expected = [raw_dir / f"{prefix}page_{i:02d}.jpg" for i in range(1, len(image_urls) + 1)]
    cached = [p for p in expected if p.exists()]
    if len(cached) == len(expected):
        print(f"[scraper] Using cached pages: {len(cached)}/{len(image_urls)} in {raw_dir} ({prefix}*)")
        return expected
    if cached:
        print(f"[scraper] cached {len(cached)}/{len(image_urls)} page(s) — resuming download of the rest")
    print(f"[scraper] Found {len(image_urls)} pages — downloading...")

    missing: list[int] = []
    reasons: list[str] = []
    stalled = 0       # pages in a row that ran out of retries on a network error
    gave_up = False
    for i, (url, page_path) in enumerate(zip(image_urls, expected), start=1):
        if page_path.exists():
            continue

        print(f"[scraper] Page {i}/{len(image_urls)}...", end=" ", flush=True)
        _failure.reason = ""
        _failure.transient = False
        if _download_image(url, page_path):
            print("✓")
            stalled = 0
        else:
            missing.append(i)
            reasons.append(last_download_error())
            print("✗")
            # A permanent hole (a 404) says nothing about the pages after it; a network
            # that stayed down through every retry says the same about all of them.
            stalled = stalled + 1 if _network_looks_down() else 0
            if stalled >= _GIVE_UP_AFTER:
                missing.extend(j for j in range(i + 1, len(image_urls) + 1)
                               if not expected[j - 1].exists())
                gave_up = True
                break
        time.sleep(0.2)

    if missing:
        # Fail loud: a chapter with holes used to be returned as if complete, and the
        # comic shipped with pages missing. What did arrive stays cached for the retry.
        # Say WHY too — "page(s) 5, 8" alone hid a DNS outage behind a missing-pages report.
        why = next((r for r in reasons if r), "")
        raise RuntimeError(
            f"{len(missing)} of {len(image_urls)} page(s) did not download for {reader_url}: "
            f"page(s) {', '.join(str(n) for n in missing)}"
            + (f" — {why}" if why else "")
            + (f" — stopped after {_GIVE_UP_AFTER} pages in a row failed: the network looks "
               "down, the rest were not tried" if gave_up else "")
        )
    return expected


def _page_number_of(path: Path) -> int:
    """Numeric page from a chNN_page_MM.jpg name (0 when the name has none)."""
    m = re.search(r"page_(\d+)", path.name)
    return int(m.group(1)) if m else 0


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
