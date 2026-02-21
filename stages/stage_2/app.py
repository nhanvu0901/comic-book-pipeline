"""
Stage 2: Image Picker
Streamlit UI for browsing and selecting comic panels for each scene.
Searches DuckDuckGo Images, displays candidates, lets you pick the best one.

Usage:
    streamlit run stages/stage_2/app.py
"""
import json
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
from PIL import Image
import requests
from io import BytesIO

from config import GDRIVE_BASE, IMAGE_SEARCH_MAX_RESULTS, get_project_dirs
from utils.image_search import search_scene_images, download_image


def load_projects() -> list[str]:
    """List available projects in Google Drive folder."""
    if not GDRIVE_BASE.exists():
        return []
    return sorted([
        d.name for d in GDRIVE_BASE.iterdir()
        if d.is_dir() and (d / "script.json").exists()
    ])


def load_script(project_name: str) -> dict:
    """Load script.json from a project."""
    script_path = GDRIVE_BASE / project_name / "script.json"
    with open(script_path) as f:
        return json.load(f)


def load_thumbnail(url: str, max_size: tuple = (300, 200)) -> Image.Image | None:
    """Download and return a thumbnail image for display."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        img.thumbnail(max_size)
        return img
    except Exception:
        return None


# ─── Streamlit App ──────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Comic Video Pipeline — Image Picker",
    page_icon="🖼️",
    layout="wide",
)

st.title("🖼️ Comic Video Pipeline — Image Picker")
st.caption("Select the best comic panel for each scene in your script.")

# ─── Project Selection ──────────────────────────────────────────────────────

projects = load_projects()

if not projects:
    st.error(
        f"No projects found in `{GDRIVE_BASE}`.\n\n"
        "Run Stage 1 first:\n"
        '```\npython -m stages.stage_1 "Your comic event"\n```'
    )
    st.stop()

project_name = st.selectbox("📁 Select Project", projects)
script = load_script(project_name)
dirs = get_project_dirs(project_name)
images_dir = str(dirs["images"])

st.success(f"**{script.get('title', 'Untitled')}** — {len(script['scenes'])} scenes")

# Show existing images
existing = sorted(Path(images_dir).glob("scene_*.jpg"))
if existing:
    st.info(f"✅ {len(existing)} images already selected. Re-select any scene below to replace.")

# ─── Scene-by-Scene Image Selection ─────────────────────────────────────────

for scene in script["scenes"]:
    sid = scene["scene_id"]
    scene_file = Path(images_dir) / f"scene_{sid:02d}.jpg"

    st.markdown("---")
    col_info, col_current = st.columns([3, 1])

    with col_info:
        st.subheader(f"Scene {sid}")
        st.markdown(f"**Narration:** {scene['narration']}")
        st.markdown(f"**Visual:** {scene.get('visual_description', 'N/A')}")
        st.markdown(f"**Effect:** `{scene.get('effect', 'slow_zoom_in')}`")

    with col_current:
        if scene_file.exists():
            st.image(str(scene_file), caption="✅ Current selection", width=200)
            if st.button("🗑️ Clear", key=f"clear_{sid}"):
                scene_file.unlink()
                st.rerun()
        else:
            st.warning("No image yet")

    # Search button
    search_key = f"search_{sid}"
    results_key = f"results_{sid}"
    if st.button(f"🔍 Search Images for Scene {sid}", key=f"btn_{sid}"):
        st.session_state[search_key] = True
        st.session_state.pop(results_key, None)  # clear cached results to force new search

    # Custom query override
    custom_query = st.text_input(
        "Custom search query (optional)",
        key=f"query_{sid}",
        placeholder="e.g., Amazing Spider-Man 121 bridge scene panel"
    )

    # Show search results
    if st.session_state.get(search_key, False):
        # Only search if we don't already have cached results
        if results_key not in st.session_state:
            with st.spinner(f"Searching images for Scene {sid}..."):
                if custom_query:
                    scene_copy = dict(scene)
                    scene_copy["image_search_queries"] = [custom_query]
                    st.session_state[results_key] = search_scene_images(scene_copy, max_results=IMAGE_SEARCH_MAX_RESULTS)
                else:
                    st.session_state[results_key] = search_scene_images(scene, max_results=IMAGE_SEARCH_MAX_RESULTS)

        results = st.session_state.get(results_key, [])

        if not results:
            st.warning("No images found. Try a custom search query.")
        else:
            st.write(f"Found {len(results)} candidates:")

            # Display in grid
            cols = st.columns(4)
            for i, result in enumerate(results):
                with cols[i % 4]:
                    thumb = load_thumbnail(result.get("thumbnail") or result["url"])
                    if thumb:
                        st.image(thumb, caption=result.get("title", "")[:50], width="stretch")
                    else:
                        st.write(f"⚠️ {result.get('title', 'Image')[:40]}")

                    st.caption(f"Source: {result.get('source', 'unknown')[:30]}")

                    if st.button(f"✅ Select", key=f"sel_{sid}_{i}"):
                        with st.spinner("Downloading and processing..."):
                            save_path = str(Path(images_dir) / f"scene_{sid:02d}.jpg")
                            success = download_image(
                                result["url"],
                                save_path,
                                target_size=(1920, 1080),
                            )
                            if success:
                                st.success(f"Scene {sid} image saved!")
                                st.session_state[search_key] = False
                                st.session_state.pop(results_key, None)
                                st.rerun()
                            else:
                                st.error("Download failed. Try another image.")

    # Manual upload option
    uploaded = st.file_uploader(
        f"Or upload an image manually for Scene {sid}",
        type=["jpg", "jpeg", "png", "webp"],
        key=f"upload_{sid}",
    )
    if uploaded:
        img = Image.open(uploaded).convert("RGB")
        save_path = str(Path(images_dir) / f"scene_{sid:02d}.jpg")
        # Crop and resize
        target_ratio = 1920 / 1080
        img_ratio = img.width / img.height
        if img_ratio > target_ratio:
            new_w = int(img.height * target_ratio)
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, img.height))
        elif img_ratio < target_ratio:
            new_h = int(img.width / target_ratio)
            top = (img.height - new_h) // 2
            img = img.crop((0, top, img.width, top + new_h))
        img = img.resize((1920, 1080), Image.LANCZOS)
        img.save(save_path, "JPEG", quality=95)
        st.success(f"Uploaded image saved for Scene {sid}!")
        st.rerun()


# ─── Status Summary ─────────────────────────────────────────────────────────

st.markdown("---")
st.subheader("📊 Progress")

total_scenes = len(script["scenes"])
completed = len(list(Path(images_dir).glob("scene_*.jpg")))

st.progress(completed / total_scenes)
st.write(f"**{completed}/{total_scenes}** scenes have images")

if completed == total_scenes:
    st.success(
        "🎉 All scenes have images!\n\n"
        "**Next step:** Go to Google Colab and run the TTS notebook.\n\n"
        f"Project folder in Drive: `comic-pipeline/{project_name}/`"
    )
else:
    remaining = [
        s["scene_id"] for s in script["scenes"]
        if not (Path(images_dir) / f"scene_{s['scene_id']:02d}.jpg").exists()
    ]
    st.warning(f"Missing images for scenes: {remaining}")
