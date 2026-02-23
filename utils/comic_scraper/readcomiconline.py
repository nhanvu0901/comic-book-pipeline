"""
Scraper for batcave.biz using nodriver (undetected Chrome via CDP).

How it works:
  batcave.biz embeds a `window.__DATA__` JSON object directly in each page's
  <script> tags. This object contains ALL chapters at once (no pagination)
  on the series page, and ALL image URLs on the reader page.

  Series page:  https://batcave.biz/{id}-{slug}.html
    window.__DATA__ = {
      "news_id": 6587,
      "xhash": "",
      "chapters": [{"id": 34073, "posi": 1.0, "title": "Issue #1", "date": "..."}]
    }

  Reader page:  https://batcave.biz/reader/{news_id}/{chapter_id}{xhash}
    window.__DATA__ = {
      "images": ["https://img.batcave.biz/img/7/6587/34073/1-xxx.jpg", ...]
    }

  Image CDN (img.batcave.biz):
    - Blocks plain requests (403) — hotlink protection checks Referer
    - Blocks JS fetch() — CORS: img.batcave.biz doesn't send ACAO headers
    - Works fine for <img> tags in the browser (no CORS on img tags)
    FIX: Extract cf_clearance + session cookies from the live nodriver browser
    session via CDP, then download with requests using those cookies + the
    reader page URL as Referer. The browser already has all the right cookies.

  Source: confirmed by keiyoushi/extensions-source BatCave.kt and
  KotatsuApp/kotatsu-parsers BatCave.kt
"""
import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

SITE_BASE = "https://batcave.biz"

# ─── JavaScript helper ────────────────────────────────────────────────────────

_JS_EXTRACT_DATA = """
(() => {
    const scripts = document.querySelectorAll('script');
    for (const s of scripts) {
        const text = s.textContent || '';
        const marker = 'window.__DATA__ = ';
        const start = text.indexOf(marker);
        if (start === -1) continue;
        const jsonStart = start + marker.length;
        let depth = 0, end = -1;
        for (let i = jsonStart; i < text.length; i++) {
            if (text[i] === '{') depth++;
            else if (text[i] === '}') {
                depth--;
                if (depth === 0) { end = i + 1; break; }
            }
        }
        if (end !== -1) return text.slice(jsonStart, end);
    }
    return null;
})()
"""

# ─── Async core ───────────────────────────────────────────────────────────────


async def _wait_for_page_load(page, timeout: int = 25) -> bool:
    """Wait for Cloudflare / DDoS-Guard challenge to resolve."""
    for i in range(timeout):
        title = await page.evaluate("document.title")
        if title \
                and "just a moment" not in title.lower() \
                and "attention required" not in title.lower() \
                and "ddos" not in title.lower() \
                and "please wait" not in title.lower():
            return True
        await asyncio.sleep(1)
        if i % 5 == 4:
            print(f"[scraper] Waiting for challenge... ({i + 1}s)")
    return False


async def _open_page(url: str, headless: bool = False):
    """
    Open a batcave.biz page, wait for Cloudflare to clear.
    Returns (browser, page) or (None, None) on failure.
    Caller must call browser.stop() when done.
    """
    import nodriver as uc

    print(f"[scraper] Opening: {url}")
    browser = await uc.start(headless=headless)
    page = await browser.get(url)

    if not await _wait_for_page_load(page):
        title = await page.evaluate("document.title")
        print(f"[scraper] Challenge did NOT resolve (title={title!r}). "
              "Try setting COMIC_SCRAPER_HEADLESS=false in .env.")
        browser.stop()
        return None, None

    title = await page.evaluate("document.title")
    print(f"[scraper] Page loaded: {title!r}")
    return browser, page


async def _extract_data(page) -> dict | None:
    """Extract window.__DATA__ from an already-open page."""
    await asyncio.sleep(5)
    raw = await page.evaluate(_JS_EXTRACT_DATA)
    if not raw:
        n = await page.evaluate("document.querySelectorAll('script').length")
        print(f"[scraper] window.__DATA__ not found ({n} script tags on page).")
        return None
    return json.loads(raw)


async def _get_cookies_via_cdp(page) -> dict:
    """
    Extract all cookies for batcave.biz domains using CDP.
    Returns a dict {name: value} suitable for requests.
    Includes httpOnly cookies like cf_clearance that JS cannot read.
    """
    try:
        import nodriver.cdp.network as cdp_net
        cookie_list = await page.send(
            cdp_net.get_cookies(urls=[
                "https://batcave.biz",
                "https://img.batcave.biz",
            ])
        )
        cookies = {c.name: c.value for c in (cookie_list or [])}
        cf = cookies.get("cf_clearance", "")
        print(f"[scraper] Extracted {len(cookies)} cookies "
              f"(cf_clearance={'✓' if cf else '✗'})")
        return cookies
    except Exception as e:
        print(f"[scraper] CDP cookie extraction failed: {e}")
        # Fallback: try JavaScript (won't get httpOnly cookies)
        try:
            js_cookies_str = await page.evaluate("document.cookie")
            cookies = {}
            for part in (js_cookies_str or "").split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies[k.strip()] = v.strip()
            print(f"[scraper] Fallback JS cookies: {len(cookies)} (no httpOnly)")
            return cookies
        except Exception:
            return {}


# ─── Sync runner ─────────────────────────────────────────────────────────────


def _run_async(coro):
    """Run an async coroutine in a dedicated thread with its own event loop."""
    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(asyncio.sleep(0.5))
            except Exception:
                pass
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_thread).result()


# ─── Download helper ─────────────────────────────────────────────────────────


def _download_with_cookies(
    url: str,
    save_path: Path,
    cookies: dict,
    referer: str,
) -> bool:
    """Download an image using session cookies extracted from the browser."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": referer,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }
        resp = requests.get(url, headers=headers, cookies=cookies, timeout=30)
        resp.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(resp.content)
        return True
    except Exception as e:
        print(f"[scraper] Download failed {url}: {e}")
        return False


# ─── Async implementations ────────────────────────────────────────────────────


async def _async_discover_issues(series_url: str, headless: bool) -> list[dict]:
    browser, page = await _open_page(series_url, headless=headless)
    if not page:
        return []
    try:
        data = await _extract_data(page)
        if not data:
            return []

        news_id = data.get("news_id")
        xhash = data.get("xhash", "")
        chapters = data.get("chapters", [])

        if not news_id or not chapters:
            print(f"[scraper] No chapters in window.__DATA__. Keys: {list(data.keys())}")
            return []

        print(f"[scraper] Found {len(chapters)} chapter(s) for news_id={news_id}")

        issues = []
        for chap in chapters:
            chap_id = chap.get("id")
            if not chap_id:
                continue
            title = chap.get("title") or f"Issue #{int(chap.get('posi', 0))}"
            reader_url = f"{SITE_BASE}/reader/{news_id}/{chap_id}{xhash}"
            issues.append({
                "title": title.strip(),
                "url": reader_url,
                "chapter_id": chap_id,
                "number": chap.get("posi", 0),
                "date": chap.get("date", ""),
            })

        issues.sort(key=lambda x: x["number"])
        for item in issues:
            print(f"[scraper]   #{item['number']} — {item['title']} → {item['url']}")
        return issues

    finally:
        try:
            browser.stop()
            await asyncio.sleep(0.5)
        except Exception:
            pass


async def _async_scrape_issue_pages(
    reader_url: str, issue_dir: Path, headless: bool
) -> list[Path]:
    """
    1. Open the reader page in nodriver (passes Cloudflare, sets cf_clearance)
    2. Extract window.__DATA__.images for the ordered list of page URLs
    3. Wait for <img> tags to load so img.batcave.biz cookies are set
    4. Extract ALL cookies (including httpOnly cf_clearance) via CDP
    5. Download each image with requests using those cookies + reader Referer
    """
    browser, page = await _open_page(reader_url, headless=headless)
    if not page:
        return []
    try:
        data = await _extract_data(page)
        if not data:
            return []

        raw_images = data.get("images", [])
        if not raw_images:
            print(f"[scraper] window.__DATA__.images is empty. Keys: {list(data.keys())}")
            return []

        # Normalize relative → absolute URLs
        image_urls = []
        for img in raw_images:
            img = img.strip()
            if img:
                image_urls.append(img if img.startswith("http") else SITE_BASE + img)

        print(f"[scraper] Found {len(image_urls)} pages.")

        # Wait for the browser to load <img> tags so img.batcave.biz sets cookies
        print("[scraper] Waiting for images to load in browser...")
        await asyncio.sleep(5)

        # Extract cookies from the live browser session via CDP
        cookies = await _get_cookies_via_cdp(page)

        # Done with the browser — close it before starting downloads
        browser.stop()
        await asyncio.sleep(0.5)

        if not cookies:
            print("[scraper] WARNING: No cookies extracted — downloads may fail.")

        print(f"[scraper] Downloading {len(image_urls)} pages with extracted cookies...")

        pages = []
        for i, url in enumerate(image_urls, start=1):
            page_path = issue_dir / f"page_{i:02d}.jpg"
            if page_path.exists():
                pages.append(page_path)
                continue

            print(f"[scraper] Page {i}/{len(image_urls)}...", end=" ", flush=True)
            ok = _download_with_cookies(url, page_path, cookies, referer=reader_url)
            if ok:
                pages.append(page_path)
                print("✓")
            else:
                print("✗")

            time.sleep(0.3)

        return sorted(pages)

    finally:
        try:
            browser.stop()
            await asyncio.sleep(0.5)
        except Exception:
            pass


# ─── Public API ───────────────────────────────────────────────────────────────


def discover_issues(series_url: str, headless: bool | None = None) -> list[dict]:
    """
    Discover all issues/chapters for a series from its batcave.biz page.
    Uses window.__DATA__ — returns ALL chapters at once, no pagination.

    Args:
        series_url: e.g. "https://batcave.biz/6587-what-if-dark-venom-2023.html"
        headless: Run browser headless. None = use config default.

    Returns:
        List of dicts sorted by issue number (ascending):
        [{"title": "Issue #1", "url": "https://batcave.biz/reader/6587/34073",
          "chapter_id": 34073, "number": 1.0, "date": "15.01.2024"}]
    """
    if headless is None:
        from config import COMIC_SCRAPER_HEADLESS
        headless = COMIC_SCRAPER_HEADLESS
    return _run_async(_async_discover_issues(series_url, headless=headless))


def scrape_issue_pages(
    reader_url: str,
    issue_slug: str = "",
    series_slug: str = "",
    cache_dir: str | Path = ".cache/comic_scraper",
    headless: bool | None = None,
) -> list[Path]:
    """
    Scrape all pages from a comic issue reader page.

    Strategy:
      - Open reader in nodriver (gets Cloudflare cookies)
      - Extract image URLs from window.__DATA__
      - Wait for <img> tags to load (triggers img.batcave.biz cookie setting)
      - Extract all cookies via CDP (includes httpOnly cf_clearance)
      - Close browser, download images with requests + those cookies

    Args:
        reader_url: e.g. "https://batcave.biz/reader/6587/34073"
        issue_slug: Cache directory label.
        series_slug: Cache directory label.
        cache_dir: Local cache directory.
        headless: Run browser headless. None = use config default.

    Returns:
        Sorted list of local file paths for each page.
    """
    if headless is None:
        from config import COMIC_SCRAPER_HEADLESS
        headless = COMIC_SCRAPER_HEADLESS

    if not series_slug:
        series_slug = _url_to_cache_key(reader_url, "series")
    if not issue_slug:
        issue_slug = _url_to_cache_key(reader_url, "issue")

    cache_dir = Path(cache_dir)
    issue_dir = cache_dir / series_slug / issue_slug
    issue_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(issue_dir.glob("page_*.jpg"))
    if existing:
        print(f"[scraper] Using cached pages: {len(existing)} pages in {issue_dir}")
        return existing

    return _run_async(_async_scrape_issue_pages(reader_url, issue_dir, headless=headless))


def scrape_single_page(
    reader_url: str,
    page_num: int,
    issue_slug: str = "",
    series_slug: str = "",
    cache_dir: str | Path = ".cache/comic_scraper",
) -> Path | None:
    """Scrape a single page from an issue. Uses cache if available."""
    pages = scrape_issue_pages(reader_url, issue_slug, series_slug, cache_dir)
    if 1 <= page_num <= len(pages):
        return pages[page_num - 1]
    return None


# ─── URL / slug helpers ───────────────────────────────────────────────────────


def _url_to_cache_key(url: str, part: str) -> str:
    """Extract a usable cache key from a batcave.biz URL."""
    m = re.search(r'/reader/(\d+)/(\d+)', url)
    if m:
        return f"series-{m.group(1)}" if part == "series" else f"chapter-{m.group(2)}"
    m = re.search(r'/(\d+-[^/]+?)(?:\.html)?$', url)
    if m:
        return m.group(1)[:60]
    return re.sub(r'[^a-zA-Z0-9-]', '-', url.split('/')[-1])[:60]


def build_issue_slug(source_issue: str) -> str:
    """Convert '#1' → 'Issue-1', 'chapter 5' → 'Chapter-5'."""
    s = source_issue.strip()
    m = re.match(r'#(\d+)', s)
    if m:
        return f"Issue-{m.group(1)}"
    m = re.match(r'chapter\s+(\d+)', s, re.IGNORECASE)
    if m:
        return f"Chapter-{m.group(1)}"
    return re.sub(r'[^a-zA-Z0-9-]', '-', s).strip('-')


def build_series_slug(series_name: str) -> str:
    """
    Convert series name to a slug (display/cache label only).
    For batcave.biz use batcave_url from script.json instead.
    """
    s = series_name.strip().lower()
    s = re.sub(r'\s+', '-', s)
    s = re.sub(r'[?.!\'",;:@#&=+$]+', '', s)
    s = re.sub(r'-{2,}', '-', s)
    return s.strip('-')
