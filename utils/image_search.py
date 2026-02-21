"""
Image search utility.
Priority: Serper.dev (Google Images) → SerpAPI (Google Images) → DuckDuckGo.
"""
import os
import sys
import requests
import time
from pathlib import Path
from PIL import Image
from io import BytesIO
from ddgs import DDGS

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SERPAPI_KEY, SERPER_API_KEY


# ─── Serper.dev (Google Images) ──────────────────────────────────────────────

def _search_serper(query: str, max_results: int = 12) -> list[dict]:
    """Search Google Images via Serper.dev. Returns [] on failure."""
    if not SERPER_API_KEY:
        return []

    print(f"  🔍 [Serper] Searching: {query}")
    try:
        resp = requests.post(
            "https://google.serper.dev/images",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "q": query,
                "num": max_results,
                "gl": "us",
                "hl": "en",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        images = data.get("images", [])
        print(f"  ✅ Found {len(images)} images via Serper")
        return [
            {
                "title": img.get("title", ""),
                "url": img.get("imageUrl", ""),
                "thumbnail": img.get("thumbnailUrl", ""),
                "source": img.get("source", ""),
                "width": img.get("imageWidth", 0),
                "height": img.get("imageHeight", 0),
            }
            for img in images[:max_results]
        ]
    except Exception as e:
        print(f"  ⚠️  Serper error: {e}")
        return []


# ─── SerpAPI (Google Images) — Fallback #1 ──────────────────────────────────

def _search_serpapi(query: str, max_results: int = 12) -> list[dict]:
    """Search Google Images via SerpAPI. Returns [] on failure."""
    if not SERPAPI_KEY:
        return []

    print(f"  🔍 [SerpAPI] Searching: {query}")
    try:
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_images",
                "q": query,
                "num": max_results,
                "api_key": SERPAPI_KEY,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        images = data.get("images_results", [])
        print(f"  ✅ Found {len(images)} images via SerpAPI")
        return [
            {
                "title": img.get("title", ""),
                "url": img.get("original", ""),
                "thumbnail": img.get("thumbnail", ""),
                "source": img.get("source", ""),
                "width": img.get("original_width", 0),
                "height": img.get("original_height", 0),
            }
            for img in images[:max_results]
        ]
    except Exception as e:
        print(f"  ⚠️  SerpAPI error: {e}")
        return []


# ─── DuckDuckGo — Fallback #2 ───────────────────────────────────────────────

def _search_ddg(query: str, max_results: int = 12) -> list[dict]:
    """Search images via DuckDuckGo. Retries up to 3 times with backoff."""
    print(f"  🔍 [DDG] Searching: {query}")
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            ddgs = DDGS()
            results = list(ddgs.images(query, max_results=max_results))
            print(f"  ✅ Found {len(results)} images via DDG")
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("image", ""),
                    "thumbnail": r.get("thumbnail", ""),
                    "source": r.get("source", ""),
                    "width": r.get("width", 0),
                    "height": r.get("height", 0),
                }
                for r in results
            ]
        except Exception as e:
            print(f"  ❌ DDG error (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    return []


# ─── Public API ──────────────────────────────────────────────────────────────

def search_images(query: str, max_results: int = 12) -> list[dict]:
    """
    Search for images. Tries Serper.dev → SerpAPI → DuckDuckGo.

    Returns list of dicts with: title, url, thumbnail, source, width, height
    """
    # 1. Serper.dev (2,500 free queries)
    results = _search_serper(query, max_results)
    if results:
        return results

    # 2. SerpAPI (250/month free)
    results = _search_serpapi(query, max_results)
    if results:
        return results

    # 3. DuckDuckGo (free, but often 403 on images)
    return _search_ddg(query, max_results)


def search_scene_images(scene: dict, max_results: int = 12) -> list[dict]:
    """
    Search images for a single scene using its search queries.
    Deduplicates results across multiple queries.
    """
    all_results = []
    seen_urls = set()

    queries = scene.get("image_search_queries", [])
    if not queries:
        queries = [scene.get("visual_description", "comic book panel")]

    for query in queries:
        results = search_images(query, max_results=max_results)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)
        time.sleep(0.5)

    return all_results


# ─── Download & Processing ───────────────────────────────────────────────────

def download_image(url: str, save_path: str, target_size: tuple = (1920, 1080)) -> bool:
    """
    Download an image and resize/crop to target dimensions.
    Uses center-crop to fill the frame without distortion.

    Returns True if successful.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        resp.raise_for_status()

        img = Image.open(BytesIO(resp.content))
        img = img.convert("RGB")

        img = _crop_to_aspect(img, target_size[0], target_size[1])
        img = img.resize(target_size, Image.LANCZOS)

        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(save_path, "JPEG", quality=95)
        print(f"  💾 Saved: {save_path}")
        return True

    except Exception as e:
        print(f"  ❌ Download failed ({url[:60]}...): {e}")
        return False


def _crop_to_aspect(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center-crop image to match target aspect ratio."""
    img_w, img_h = img.size
    target_ratio = target_w / target_h
    img_ratio = img_w / img_h

    if img_ratio > target_ratio:
        new_w = int(img_h * target_ratio)
        left = (img_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, img_h))
    elif img_ratio < target_ratio:
        new_h = int(img_w / target_ratio)
        top = (img_h - new_h) // 2
        img = img.crop((0, top, img_w, top + new_h))

    return img


def download_scene_image(url: str, scene_id: int, images_dir: str) -> str | None:
    """Download and save an image for a specific scene."""
    filename = f"scene_{scene_id:02d}.jpg"
    save_path = os.path.join(images_dir, filename)
    if download_image(url, save_path):
        return save_path
    return None
