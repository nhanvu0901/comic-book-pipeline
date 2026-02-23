"""
Stage 2: Image Picker
Streamlit UI for browsing and selecting comic panels for each scene.
Three tabs: Comic Pages (scraper), Google Search, and Upload.

Usage:
    streamlit run stages/stage_2/app.py
"""
import json
import re
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
from PIL import Image
import requests
from io import BytesIO

from config import (
    GDRIVE_BASE,
    IMAGE_SEARCH_MAX_RESULTS,
    ENABLE_COMIC_SCRAPER,
    ENABLE_VISION_CONFIRM,
    get_project_dirs,
)
from utils.image_search import search_scene_images, download_image


# ─── Helpers ────────────────────────────────────────────────────────────────


def load_projects() -> list[str]:
    if not GDRIVE_BASE.exists():
        return []
    return sorted([
        d.name for d in GDRIVE_BASE.iterdir()
        if d.is_dir() and (d / "script.json").exists()
    ])


def load_script(project_name: str) -> dict:
    script_path = GDRIVE_BASE / project_name / "script.json"
    with open(script_path) as f:
        return json.load(f)


def load_thumbnail(url: str, max_size: tuple = (300, 200)) -> Image.Image | None:
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


def crop_and_save(img: Image.Image, save_path: str) -> None:
    img = img.convert("RGB")
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


def set_source_label(scene_file: Path, label: str) -> None:
    st.session_state[f"source_{scene_file.stem}"] = label


def get_source_label(scene_file: Path) -> str | None:
    return st.session_state.get(f"source_{scene_file.stem}")


def collect_unique_issues(scenes: list[dict]) -> list[str]:
    """Extract unique source_issue values from all scenes, preserving order."""
    seen = set()
    issues = []
    for scene in scenes:
        issue = scene.get("source_issue", "")
        if issue and issue not in seen:
            seen.add(issue)
            issues.append(issue)
    return issues


def get_scraped_pages_key(issue_title: str) -> str:
    """Build a session_state key for cached pages."""
    return f"scraped_{issue_title}"


def get_scraped_pages(issue_title: str) -> list[Path]:
    """Get cached pages for an issue from session_state."""
    return st.session_state.get(get_scraped_pages_key(issue_title), [])


def set_scraped_pages(issue_title: str, pages: list[Path]) -> None:
    st.session_state[get_scraped_pages_key(issue_title)] = pages


def get_discovered_issues() -> list[dict]:
    """Get discovered issues from session_state."""
    return st.session_state.get("discovered_issues", [])


def set_discovered_issues(issues: list[dict]) -> None:
    st.session_state["discovered_issues"] = issues


def match_issue(discovered: list[dict], source_issue: str) -> dict | None:
    """Find the best matching discovered issue for a source_issue like '#1'."""
    if not discovered or not source_issue:
        return None
    # Extract the number from source_issue
    m = re.match(r'#(\d+)', source_issue.strip())
    issue_num = m.group(1) if m else source_issue.strip()
    for item in discovered:
        title = item.get("title", "").lower()
        # Match "issue #1", "issue 1", "#1", "chapter 1", etc.
        if f"#{issue_num}" in title or f"issue {issue_num}" in title \
                or f"issue #{issue_num}" in title or title.endswith(f" {issue_num}"):
            return item
    # Fallback: if only one issue, return it
    if len(discovered) == 1:
        return discovered[0]
    return None


# ─── Comic Scraper: Project-Level Controls ───────────────────────────────────


def render_scraper_controls(script: dict) -> str:
    """
    Render project-level scraper controls above the scene list.
    Returns the batcave_url for use by per-scene tabs.
    """
    from utils.comic_scraper.readcomiconline import discover_issues

    comic_source = script.get("comic_source", {})
    series_name = comic_source.get("series", "")
    saved_url = comic_source.get("batcave_url", "")

    st.markdown("### 📖 Comic Page Scraper (batcave.biz)")

    batcave_url = st.text_input(
        "Series URL on batcave.biz",
        value=saved_url,
        key="project_batcave_url",
        placeholder="https://batcave.biz/6587-what-if-dark-venom-2023.html",
        help=(
            f"From script.json: `{saved_url}`"
            if saved_url
            else f"Search batcave.biz for \"{series_name}\" and paste the URL here"
        ),
    )

    if not batcave_url:
        st.warning("Paste the batcave.biz series URL to enable comic page scraping.")
        return ""

    # Discover issues from the series page
    discovered = get_discovered_issues()

    if not discovered:
        if st.button("📖 Discover Issues", key="discover_btn"):
            with st.spinner(f"Loading issues from {batcave_url}..."):
                try:
                    discovered = discover_issues(batcave_url)
                    set_discovered_issues(discovered)
                    if discovered:
                        st.toast(f"Found {len(discovered)} issue(s)")
                    else:
                        st.error("No issues found. Check the URL or try a different one.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to discover issues: {e}")
        return batcave_url

    # Show discovered issues
    with st.expander(f"Discovered issues ({len(discovered)})", expanded=False):
        for item in discovered:
            st.markdown(f"  - **{item['title']}** → `{item['url']}`")
        if st.button("🔄 Re-discover", key="rediscover_btn"):
            set_discovered_issues([])
            st.rerun()

    return batcave_url


# ─── Tab Renderers ──────────────────────────────────────────────────────────


def render_comic_tab(
    scene: dict, sid: int, scene_file: Path, images_dir: str, batcave_url: str
) -> None:
    """Render the Comic Pages tab — show scraped pages for this scene's issue."""
    from utils.comic_scraper.readcomiconline import scrape_issue_pages

    source_issue = scene.get("source_issue", "")
    source_page = scene.get("source_page")

    if not batcave_url:
        st.warning("Set the batcave.biz URL in the scraper controls above.")
        return

    discovered = get_discovered_issues()
    if not discovered:
        st.info("Click **Discover Issues** above first to find available issues.")
        return

    # Match this scene's source_issue to a discovered issue
    matched = match_issue(discovered, source_issue)

    # Let user pick from all discovered issues
    issue_options = {item["title"]: item for item in discovered}
    default_idx = 0
    if matched:
        titles = list(issue_options.keys())
        if matched["title"] in titles:
            default_idx = titles.index(matched["title"])

    selected_title = st.selectbox(
        f"Issue for Scene {sid}",
        options=list(issue_options.keys()),
        index=default_idx,
        key=f"issue_select_{sid}",
    )
    selected_issue = issue_options[selected_title]
    reader_url = selected_issue["url"]

    # Get pre-scraped pages
    pages = get_scraped_pages(selected_title)

    # Save scraped pages inside the project's images folder
    project_cache = Path(images_dir) / "scraped"

    if not pages:
        st.warning(f"**{selected_title}** not scraped yet.")
        if st.button(f"📖 Scrape {selected_title}", key=f"scrape_single_{sid}"):
            with st.spinner(f"Scraping {selected_title}..."):
                try:
                    pages = scrape_issue_pages(
                        reader_url,
                        issue_slug=selected_title,
                        cache_dir=project_cache,
                    )
                    set_scraped_pages(selected_title, pages)
                    st.rerun()
                except Exception as e:
                    st.error(f"Scraping failed: {e}")
        return

    st.write(f"**{len(pages)} pages** from {selected_title}")

    # Normalize source_page to a list
    suggested_pages = []
    if isinstance(source_page, list):
        suggested_pages = source_page
    elif isinstance(source_page, int):
        suggested_pages = [source_page]

    if suggested_pages:
        st.caption(f"Stage 1 suggested page(s): **{suggested_pages}**")

    # Vision confirm scoring
    scored_pages = None
    if ENABLE_VISION_CONFIRM and scene.get("visual_description"):
        from utils.comic_scraper.vision_confirm import rank_pages as vision_rank
        if st.button("🔬 Score with Vision AI", key=f"vision_btn_{sid}"):
            with st.spinner("Scoring pages with vision model..."):
                scored_pages = vision_rank(pages, scene["visual_description"])
                st.session_state[f"vision_scores_{sid}"] = scored_pages
        scored_pages = st.session_state.get(f"vision_scores_{sid}")

    # Build display order: suggested pages first, then the rest
    display_pages = list(pages)
    if scored_pages:
        display_pages = [p for p, _ in scored_pages]
    elif suggested_pages:
        suggested = []
        others = []
        for p in display_pages:
            page_num = int(p.stem.split("_")[1])
            if page_num in suggested_pages:
                suggested.append(p)
            else:
                others.append(p)
        display_pages = suggested + others

    cols = st.columns(4)
    for i, page_path in enumerate(display_pages):
        page_num = int(page_path.stem.split("_")[1])
        is_suggested = page_num in suggested_pages

        with cols[i % 4]:
            if is_suggested:
                st.markdown(f"**⭐ Page {page_num} (suggested)**")
            else:
                st.markdown(f"Page {page_num}")

            st.image(str(page_path), width=200)

            if scored_pages:
                score = next((s for p, s in scored_pages if p == page_path), None)
                if score is not None:
                    st.caption(f"Confidence: {score:.1%}")

            if st.button("✅ Select", key=f"comic_sel_{sid}_{page_num}"):
                img = Image.open(page_path)
                save_path = str(Path(images_dir) / f"scene_{sid:02d}.jpg")
                crop_and_save(img, save_path)
                set_source_label(scene_file, f"Comic Page {page_num} ({source_issue})")
                st.success(f"Scene {sid} — saved page {page_num}!")
                st.rerun()


def render_search_tab(scene: dict, sid: int, scene_file: Path, images_dir: str) -> None:
    search_key = f"search_{sid}"
    results_key = f"results_{sid}"

    if st.button(f"🔍 Search Images", key=f"search_btn_{sid}"):
        st.session_state[search_key] = True
        st.session_state.pop(results_key, None)

    custom_query = st.text_input(
        "Custom search query (optional)",
        key=f"query_{sid}",
        placeholder="e.g., Amazing Spider-Man 121 bridge scene panel",
    )

    if st.session_state.get(search_key, False):
        if results_key not in st.session_state:
            with st.spinner(f"Searching images for Scene {sid}..."):
                if custom_query:
                    scene_copy = dict(scene)
                    scene_copy["image_search_queries"] = [custom_query]
                    st.session_state[results_key] = search_scene_images(
                        scene_copy, max_results=IMAGE_SEARCH_MAX_RESULTS
                    )
                else:
                    st.session_state[results_key] = search_scene_images(
                        scene, max_results=IMAGE_SEARCH_MAX_RESULTS
                    )

        results = st.session_state.get(results_key, [])

        if not results:
            st.warning("No images found. Try a custom search query.")
        else:
            st.write(f"Found {len(results)} candidates:")

            cols = st.columns(4)
            for i, result in enumerate(results):
                with cols[i % 4]:
                    thumb = load_thumbnail(result.get("thumbnail") or result["url"])
                    if thumb:
                        st.image(thumb, caption=result.get("title", "")[:50], use_container_width=True)
                    else:
                        st.write(f"⚠️ {result.get('title', 'Image')[:40]}")

                    st.caption(f"Source: {result.get('source', 'unknown')[:30]}")

                    if st.button("✅ Select", key=f"sel_{sid}_{i}"):
                        with st.spinner("Downloading and processing..."):
                            save_path = str(Path(images_dir) / f"scene_{sid:02d}.jpg")
                            success = download_image(
                                result["url"],
                                save_path,
                                target_size=(1920, 1080),
                            )
                            if success:
                                set_source_label(scene_file, "Google Search")
                                st.success(f"Scene {sid} image saved!")
                                st.session_state[search_key] = False
                                st.session_state.pop(results_key, None)
                                st.rerun()
                            else:
                                st.error("Download failed. Try another image.")


def render_upload_tab(sid: int, scene_file: Path, images_dir: str) -> None:
    uploaded = st.file_uploader(
        f"Upload an image for Scene {sid}",
        type=["jpg", "jpeg", "png", "webp"],
        key=f"upload_{sid}",
    )
    if uploaded:
        img = Image.open(uploaded)
        st.image(img, caption="Preview", width=400)

        if st.button("💾 Save as scene image", key=f"upload_save_{sid}"):
            save_path = str(Path(images_dir) / f"scene_{sid:02d}.jpg")
            crop_and_save(img, save_path)
            set_source_label(scene_file, "Upload")
            st.success(f"Uploaded image saved for Scene {sid}!")
            st.rerun()


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

# ─── Project-Level Scraper Controls ─────────────────────────────────────────

batcave_url = ""
if ENABLE_COMIC_SCRAPER:
    batcave_url = render_scraper_controls(script)

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
        if scene.get("source_issue"):
            st.markdown(
                f"**Source:** {scene.get('source_issue', '')} — Page {scene.get('source_page', '?')}"
            )

    with col_current:
        if scene_file.exists():
            st.image(str(scene_file), caption="✅ Current selection", width=200)
            source_label = get_source_label(scene_file)
            if source_label:
                st.caption(f"From: {source_label}")
            if st.button("🗑️ Clear", key=f"clear_{sid}"):
                scene_file.unlink()
                st.rerun()
        else:
            st.warning("No image yet")

    # ─── Tabbed Interface ───────────────────────────────────────────────
    tab_names = []
    if ENABLE_COMIC_SCRAPER:
        tab_names.append("📖 Comic Pages")
    tab_names.extend(["🔍 Google Search", "📤 Upload"])

    tabs = st.tabs(tab_names)
    tab_idx = 0

    if ENABLE_COMIC_SCRAPER:
        with tabs[tab_idx]:
            render_comic_tab(scene, sid, scene_file, images_dir, batcave_url)
        tab_idx += 1

    with tabs[tab_idx]:
        render_search_tab(scene, sid, scene_file, images_dir)
    tab_idx += 1

    with tabs[tab_idx]:
        render_upload_tab(sid, scene_file, images_dir)


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
