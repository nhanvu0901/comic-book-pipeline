"""
Image search utility using DuckDuckGo.
Returns candidate images for each scene in the script.
"""
import os
import requests
import time
from pathlib import Path
from PIL import Image
from io import BytesIO
from ddgs import DDGS


def search_images(query: str, max_results: int = 12) -> list[dict]:
    """
    Search for images using DuckDuckGo.
    
    Returns list of dicts with: title, url, thumbnail, source, width, height
    """
    print(f"  🔍 Searching: {query}")
    try:
        ddgs = DDGS()
        results = list(ddgs.images(query, max_results=max_results))
        print(f"  ✅ Found {len(results)} images")
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
        print(f"  ❌ Search error: {e}")
        return []


def search_scene_images(scene: dict, max_results: int = 12) -> list[dict]:
    """
    Search images for a single scene using its search queries.
    Deduplicates results across multiple queries.
    """
    all_results = []
    seen_urls = set()

    queries = scene.get("image_search_queries", [])
    if not queries:
        # Fallback: build query from visual description
        queries = [scene.get("visual_description", "comic book panel")]

    for query in queries:
        results = search_images(query, max_results=max_results)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)
        time.sleep(0.5)  # Rate limiting

    return all_results


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

        # Center-crop to target aspect ratio, then resize
        img = _crop_to_aspect(img, target_size[0], target_size[1])
        img = img.resize(target_size, Image.LANCZOS)

        # Save
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
        # Image is wider — crop sides
        new_w = int(img_h * target_ratio)
        left = (img_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, img_h))
    elif img_ratio < target_ratio:
        # Image is taller — crop top/bottom
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
