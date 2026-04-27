"""
Stage 2 orchestrator: for a project, scrape pages and enrich each one.

Flow per project:
  1. Load projects/<slug>/comic_context.json.
  2. Resolve requested issues to batcave.biz chapter reader URLs.
  3. Scrape each chapter's pages (cached by the scraper itself).
  4. For each page: SHA-256 cache check → YOLO panels → (if >0 panels) VLM enrich.
  5. Persist a JSON per page to projects/<slug>/preprocessed/page_NNN_<hash>.json.

Sequential processing keeps things simple and well under OpenRouter's
20 RPM / 50 RPD free-tier limits for a typical 22-page issue.
"""
import json
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from config import VLM_MODEL, get_project_dirs, GDRIVE_BASE
from utils.comic_scraper import scrape_issue_pages
from .cache import image_hash, load_cached, save_cached
from .issue_resolver import resolve_chapters
from .panel_detect import detect_panels
from .schema import PanelInfo, PreprocessedPage, TextBlock
from .vlm_extract import extract_page


def preprocess_project(
    project_name: str,
    *,
    progress: Callable[[str], None] | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    """
    Run the full Stage 2 pipeline for a project. Returns list of page dicts
    (also written to disk as individual JSON files).
    """
    log = progress or print

    ctx_path = GDRIVE_BASE / project_name / "comic_context.json"
    if not ctx_path.exists():
        raise FileNotFoundError(f"comic_context.json not found for project '{project_name}' "
                                f"(looked at {ctx_path}). Run Stage 1 first.")

    ctx = json.loads(ctx_path.read_text())
    batcave_url = ctx.get("batcave_url", "").strip()
    issues = ctx.get("issues", "").strip()
    if not batcave_url:
        raise ValueError("comic_context.json has no batcave_url — cannot scrape pages.")

    project_root = get_project_dirs(project_name)["root"]
    log(f"[stage2] project={project_name} issues={issues!r} batcave_url={batcave_url}")

    chapters = resolve_chapters(batcave_url, issues)
    if not chapters:
        raise RuntimeError(f"No chapters resolved for issues={issues!r} at {batcave_url}")
    log(f"[stage2] resolved {len(chapters)} chapter(s) to scrape")

    results: list[dict] = []
    global_page_num = 0

    for chapter in chapters:
        log(f"[stage2] ▶ scraping {chapter['label']} ({chapter['reader_url']})")
        try:
            page_paths = scrape_issue_pages(chapter["reader_url"])
        except Exception as e:
            log(f"[stage2]   scrape failed: {e}")
            continue

        total = len(page_paths)
        log(f"[stage2]   got {total} page(s) — starting preprocessing")
        t_chapter = time.time()
        for local_idx, img_path in enumerate(page_paths, start=1):
            global_page_num += 1
            log(f"[stage2]   ── page {local_idx}/{total} (global p{global_page_num:03d}) "
                f"{Path(img_path).name}")
            page_dict = _process_one_page(
                page_number=global_page_num,
                issue_label=chapter["label"],
                image_path=img_path,
                project_root=project_root,
                force_refresh=force_refresh,
                log=log,
            )
            results.append(page_dict)
        log(f"[stage2]   ✓ chapter {chapter['label']} done in {time.time()-t_chapter:.1f}s")

    story_count = sum(1 for r in results if r.get("is_story_page"))
    log(f"[stage2] done — {len(results)} pages processed, {story_count} marked as story pages")
    return results


def _process_one_page(
    *,
    page_number: int,
    issue_label: str,
    image_path: Path,
    project_root: Path,
    force_refresh: bool,
    log: Callable[[str], None],
) -> dict:
    h = image_hash(image_path)

    if not force_refresh:
        cached = load_cached(project_root, page_number, h)
        if cached is not None:
            log(f"[stage2]     ✓ cache hit ({h}) — skipping YOLO + VLM")
            return cached

    t0 = time.time()
    with Image.open(image_path) as im:
        width, height = im.size
    log(f"[stage2]     size={width}×{height} hash={h}")

    t_yolo = time.time()
    panels_raw = detect_panels(image_path)
    log(f"[stage2]     YOLO found {len(panels_raw)} panel(s) in {time.time()-t_yolo:.1f}s")

    if not panels_raw:
        # YOLO found no panels. Could be a cover (single splash) or an ad — ask the
        # VLM anyway so covers still get metadata; it'll classify as skip if promo.
        log(f"[stage2]     no panels — asking VLM to classify (cover vs skip)")

    t_vlm = time.time()
    log(f"[stage2]     calling VLM ({VLM_MODEL}) with {len(panels_raw)} panel bboxes…")
    vlm_data = extract_page(image_path, panels_raw, model=VLM_MODEL)
    vlm_dt = time.time() - t_vlm

    page_type = str(vlm_data.get("page_type", "story")).lower()
    if page_type not in ("cover", "story", "skip"):
        page_type = "story"
    skip_reason = str(vlm_data.get("skip_reason", ""))

    if skip_reason == "vlm_failure":
        log(f"[stage2]     ✗ VLM failed in {vlm_dt:.1f}s: {vlm_data.get('error','?')[:120]}")
    else:
        log(f"[stage2]     ✓ VLM classified as {page_type.upper()}"
            + (f" ({skip_reason})" if skip_reason else "")
            + f" — {len(vlm_data.get('panels') or [])} panel descriptions, "
            f"{len(vlm_data.get('text_blocks') or [])} text blocks in {vlm_dt:.1f}s")

    # Skip pages get no metadata — enforce empty arrays regardless of VLM output.
    if page_type == "skip":
        panel_infos: list[PanelInfo] = []
        text_blocks: list[TextBlock] = []
        page_summary = ""
    else:
        panel_infos = [
            PanelInfo(
                index=i,
                bbox=p["bbox"],
                description=_panel_field(vlm_data, i, "description"),
                characters=_panel_field(vlm_data, i, "characters", default=[]),
                dominant_emotion=_panel_field(vlm_data, i, "dominant_emotion"),
            )
            for i, p in enumerate(panels_raw)
        ]
        text_blocks = [
            TextBlock(
                panel_index=int(tb.get("panel_index", -1)),
                text=str(tb.get("text", "")),
                type=str(tb.get("type", "speech")),
                speaker=tb.get("speaker") or None,
            )
            for tb in (vlm_data.get("text_blocks") or [])
        ]
        page_summary = str(vlm_data.get("page_summary", ""))

    page = PreprocessedPage(
        page_number=page_number,
        source_image=str(image_path.resolve()),
        image_dimensions={"width": width, "height": height},
        is_story_page=(page_type == "story"),
        page_type=page_type,
        panels=panel_infos,
        text_blocks=text_blocks,
        page_summary=page_summary,
        issue_label=issue_label,
        vlm_model=VLM_MODEL,
        content_hash=h,
        preprocessing_method="yolo+vlm",
        skip_reason=skip_reason,
    )
    out = page.to_dict()
    cache_file = save_cached(project_root, page_number, h, out)

    flag = page_type.upper() + (f" ({skip_reason})" if page_type == "skip" and skip_reason else "")
    log(f"[stage2]     → {flag}  {len(panel_infos)} panels, "
        f"{len(text_blocks)} text blocks, {time.time() - t0:.1f}s total "
        f"→ {Path(cache_file).name}")
    return out


def _panel_field(vlm_data: dict, index: int, key: str, default=""):
    panels = vlm_data.get("panels") or []
    for p in panels:
        if int(p.get("index", -1)) == index:
            v = p.get(key)
            if v is not None:
                return v
    return default
