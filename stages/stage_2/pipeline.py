"""
Stage 2 orchestrator: preprocess downloaded comic pages.

Reads the download manifest written by download.py, then for each page:
  SHA-256 cache check → Magi panel detect → VLM enrich → persist JSON.

Sequential processing keeps things simple and well under OpenRouter's
20 RPM / 50 RPD free-tier limits for a typical 22-page issue.
"""
import json
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from config import VLM_MODEL, get_project_dirs
from .cache import image_hash, load_cached, save_cached
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
    Run preprocessing on already-downloaded comic pages.
    Reads raw_comic/manifest.json written by the download stage.
    Returns list of page dicts (also written to disk as individual JSON files).
    """
    log = progress or print

    project_root = get_project_dirs(project_name)["root"]
    manifest_path = project_root / "raw_comic" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No download manifest found for project '{project_name}'. "
            "Run the Download stage first."
        )

    manifest = json.loads(manifest_path.read_text())
    total_chapters = len(manifest)
    log(f"[preprocess] project={project_name} — {total_chapters} chapter(s) from manifest")

    story_context = _load_story_context(project_root, log)

    results: list[dict] = []
    global_page_num = 0

    for chapter in manifest:
        label = chapter["label"]
        pages = chapter["pages"]
        total = len(pages)
        log(f"[preprocess] ▶ {label}: {total} page(s)")
        t_chapter = time.time()

        for local_idx, img_path_str in enumerate(pages, start=1):
            img_path = Path(img_path_str)
            if not img_path.exists():
                log(f"[preprocess]   ⚠ missing: {img_path.name} — skipping")
                continue
            global_page_num += 1
            log(f"[preprocess]   ── page {local_idx}/{total} (global p{global_page_num:03d}) "
                f"{img_path.name}")
            page_dict = _process_one_page(
                page_number=global_page_num,
                issue_label=label,
                image_path=img_path,
                project_root=project_root,
                force_refresh=force_refresh,
                log=log,
                story_context=story_context,
            )
            results.append(page_dict)
        log(f"[preprocess]   ✓ {label} done in {time.time() - t_chapter:.1f}s")

    _reclassify_mid_doc_covers(results, project_root, log)

    story_count = sum(1 for r in results if r.get("is_story_page"))
    log(f"[preprocess] done — {len(results)} pages processed, {story_count} story pages")
    return results


def _reclassify_mid_doc_covers(
    pages: list[dict], project_root: Path, log: Callable[[str], None]
) -> None:
    """A real cover sits at the edges of the issue. A page tagged 'cover' in the middle is almost always a misclassified splash — flip it to story so Narration can use it."""
    total = max((int(p.get("page_number", 0) or 0) for p in pages), default=0)
    if total < 5:
        return
    for p in pages:
        if p.get("page_type") != "cover":
            continue
        pn = int(p.get("page_number", 0) or 0)
        if pn <= 2 or pn >= total:
            continue
        log(f"[preprocess] mid-doc cover at p{pn:03d}/{total} → reclassifying to story (Option 1 heuristic)")
        p["page_type"] = "story"
        p["is_story_page"] = True
        h = str(p.get("content_hash", "") or "")
        if h:
            try:
                save_cached(project_root, pn, h, p)
            except Exception as exc:
                log(f"[preprocess]   ⚠ couldn't persist reclassification for p{pn}: {exc}")


def _load_story_context(project_root: Path, log: Callable[[str], None]) -> str:
    ctx_path = project_root / "comic_context.json"
    if not ctx_path.exists():
        log("[preprocess] no comic_context.json — VLM runs without story context")
        return ""
    try:
        ctx = json.loads(ctx_path.read_text())
    except json.JSONDecodeError:
        log("[preprocess] comic_context.json unreadable — VLM runs without story context")
        return ""
    summary = ctx.get("summary") or {}
    if not summary:
        log("[preprocess] comic_context.summary missing — VLM runs without story context")
        return ""
    from stages.stage_1.tools.summarize_context import format_for_vlm
    block = format_for_vlm(summary)
    log(f"[preprocess] story context loaded: {len(block)} chars, {len(summary.get('characters') or [])} characters")
    return block


def _process_one_page(
    *,
    page_number: int,
    issue_label: str,
    image_path: Path,
    project_root: Path,
    force_refresh: bool,
    log: Callable[[str], None],
    story_context: str = "",
) -> dict:
    log(f"[stage2]     computing hash for {image_path.name}…")
    h = image_hash(image_path)
    log(f"[stage2]     hash={h[:16]}…")

    if not force_refresh:
        cached = load_cached(project_root, page_number, h)
        if cached is not None:
            if cached.get("skip_reason") == "vlm_failure":
                log(f"[stage2]     ⚠ cache had vlm_failure — invalidating and re-running with fallback chain")
            else:
                cached_type = cached.get("page_type", "?")
                cached_panels = len(cached.get("panels", []))
                log(f"[stage2]     ✓ cache hit — type={cached_type}, {cached_panels} panels — skipping panel detect + VLM")
                return cached
    log(f"[stage2]     no cache — running full pipeline")

    t0 = time.time()
    with Image.open(image_path) as im:
        width, height = im.size
    log(f"[stage2]     image loaded: {width}×{height} px, "
        f"{image_path.stat().st_size / 1024:.0f} KB")

    # ── Magi panel detection ──
    log(f"[stage2]     running Magi v3 panel detection…")
    t_panel = time.time()
    panels_raw = detect_panels(image_path)
    panel_dt = time.time() - t_panel
    log(f"[stage2]     Magi found {len(panels_raw)} panel(s) in {panel_dt:.1f}s")
    for i, p in enumerate(panels_raw):
        b = p["bbox"]
        log(f"[stage2]       panel {i}: {b['w']}×{b['h']} @ ({b['x']},{b['y']}) conf={p['confidence']}")

    # ── Cover shortcut (first page, no panels) ──
    if not panels_raw and page_number == 1:
        log(f"[stage2]     first page, no panels detected — marking as COVER, skipping VLM")
        page = PreprocessedPage(
            page_number=page_number,
            source_image=str(image_path.resolve()),
            image_dimensions={"width": width, "height": height},
            is_story_page=False,
            page_type="cover",
            panels=[],
            text_blocks=[],
            page_summary="Cover page",
            issue_label=issue_label,
            vlm_model="",
            vlm_model_used="",
            content_hash=h,
            preprocessing_method="magi+vlm",
            skip_reason="",
        )
        out = page.to_dict()
        save_cached(project_root, page_number, h, out)
        log(f"[stage2]     → COVER  0 panels, {time.time() - t0:.1f}s total")
        return out

    if not panels_raw:
        log(f"[stage2]     no panels on non-first page — VLM will classify (story vs skip)")

    # ── VLM enrichment ──
    log(f"[stage2]     calling VLM with fallback chain (primary={VLM_MODEL})")
    log(f"[stage2]     sending {len(panels_raw)} panel bboxes + full page image…")
    t_vlm = time.time()
    vlm_data = extract_page(image_path, panels_raw, progress=log, story_context=story_context)
    vlm_dt = time.time() - t_vlm
    vlm_model_used = str(vlm_data.get("_vlm_model_used", ""))

    page_type = str(vlm_data.get("page_type", "story")).lower()
    if page_type not in ("cover", "story", "skip"):
        page_type = "story"
    skip_reason = str(vlm_data.get("skip_reason", ""))

    vlm_panels = vlm_data.get("panels") or []
    vlm_text_blocks = vlm_data.get("text_blocks") or []

    if skip_reason == "vlm_failure":
        log(f"[stage2]     ✗ VLM FAILED in {vlm_dt:.1f}s")
        log(f"[stage2]       error: {vlm_data.get('error','?')[:200]}")
    else:
        log(f"[stage2]     ✓ VLM done in {vlm_dt:.1f}s")
        log(f"[stage2]       page_type={page_type.upper()}"
            + (f"  skip_reason={skip_reason}" if skip_reason else ""))
        log(f"[stage2]       {len(vlm_panels)} panel descriptions, "
            f"{len(vlm_text_blocks)} text blocks")
        for vp in vlm_panels:
            desc = str(vp.get("description", ""))[:80]
            log(f"[stage2]       panel {vp.get('index', '?')}: {desc}")
        for tb in vlm_text_blocks:
            txt = str(tb.get("text", ""))[:60]
            log(f"[stage2]       text [{tb.get('type','?')}] p{tb.get('panel_index','?')}: \"{txt}\"")

    # ── Build output ──
    if page_type == "skip":
        panel_infos: list[PanelInfo] = []
        text_blocks: list[TextBlock] = []
        page_summary = ""
        log(f"[stage2]     page marked SKIP — clearing panels + text blocks")
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
            for tb in vlm_text_blocks
        ]
        page_summary = str(vlm_data.get("page_summary", ""))
        log(f"[stage2]     built {len(panel_infos)} PanelInfo + {len(text_blocks)} TextBlock objects")
        if page_summary:
            log(f"[stage2]     summary: {page_summary[:120]}")

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
        vlm_model_used=vlm_model_used,
        content_hash=h,
        preprocessing_method="magi+vlm",
        skip_reason=skip_reason,
    )
    out = page.to_dict()
    cache_file = save_cached(project_root, page_number, h, out)

    total_dt = time.time() - t0
    flag = page_type.upper() + (f" ({skip_reason})" if page_type == "skip" and skip_reason else "")
    log(f"[stage2]     → {flag}  {len(panel_infos)} panels, "
        f"{len(text_blocks)} text blocks")
    log(f"[stage2]     timing: Magi={panel_dt:.1f}s  VLM={vlm_dt:.1f}s  total={total_dt:.1f}s")
    log(f"[stage2]     saved → {Path(cache_file).name}")
    return out


def _panel_field(vlm_data: dict, index: int, key: str, default=""):
    panels = vlm_data.get("panels") or []
    for p in panels:
        if int(p.get("index", -1)) == index:
            v = p.get(key)
            if v is not None:
                return v
    return default
