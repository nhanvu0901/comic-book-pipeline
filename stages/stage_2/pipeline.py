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

from config import VLM_BATCH_SIZE, VLM_MODEL, get_project_dirs
from .cache import image_hash, load_cached, save_cached
from .panel_detect import detect_panels
from .schema import PanelInfo, PreprocessedPage, TextBlock
from .vlm_extract import extract_page, extract_pages_batch


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

    # Flatten manifest into a single ordered list of (page_number, label, path).
    # Continuity in reading flow > chapter boundaries — we batch across chapters
    # only if they're adjacent in the manifest, which they always are.
    flat: list[tuple[int, str, Path]] = []
    global_page_num = 0
    for chapter in manifest:
        label = chapter["label"]
        for img_path_str in chapter["pages"]:
            img_path = Path(img_path_str)
            if not img_path.exists():
                log(f"[preprocess]   ⚠ missing: {img_path.name} — skipping")
                continue
            global_page_num += 1
            flat.append((global_page_num, label, img_path))

    log(f"[preprocess] {len(flat)} total page(s); batch_size={VLM_BATCH_SIZE}")

    # ── Phase 1: hash every page, separate cached vs uncached (preserve order) ──
    page_states: list[dict] = []  # parallel to flat; carries "cached" dict OR None
    for pn, label, img_path in flat:
        h = image_hash(img_path)
        cached = None if force_refresh else load_cached(project_root, pn, h)
        if cached is not None and cached.get("skip_reason") == "vlm_failure":
            cached = None  # invalidate prior failures so we retry with batch
        page_states.append({"pn": pn, "label": label, "img": img_path, "hash": h, "cached": cached})

    cached_count = sum(1 for s in page_states if s["cached"] is not None)
    log(f"[preprocess] cache: {cached_count}/{len(page_states)} pages have valid results — "
        f"{len(page_states) - cached_count} need VLM")

    # ── Phase 2: walk pages in order, batching uncached runs ──
    # Overlap pattern: each batch carries the IMMEDIATELY PRIOR page (its full extracted
    # data + image) as context. The prior page is NOT re-processed (first-wins lock-in)
    # — if VLM ignores instructions and emits an entry for it, vlm_extract drops it.
    results: list[dict] = []
    running_state = ""  # Fallback memory used when no prior page is available (first batch only)
    prev_page_dict: dict | None = None
    prev_image_path: Path | None = None
    i = 0
    n = len(page_states)
    while i < n:
        s = page_states[i]
        if s["cached"] is not None:
            log(f"[preprocess]   ✓ cache hit p{s['pn']:03d} ({s['img'].name})")
            results.append(s["cached"])
            # Cached page becomes the prior-context for whatever batch comes next.
            prev_page_dict = s["cached"]
            prev_image_path = s["img"]
            summary = (s["cached"].get("page_summary") or "").strip()
            if summary and not running_state:
                running_state = summary[:240]
            i += 1
            continue

        # Collect a contiguous run of uncached pages up to VLM_BATCH_SIZE.
        batch_end = i
        while batch_end < n and page_states[batch_end]["cached"] is None and (batch_end - i) < VLM_BATCH_SIZE:
            batch_end += 1
        batch = page_states[i:batch_end]
        batch_pns = [b["pn"] for b in batch]
        overlap_note = f" + prior p{prev_page_dict['page_number']:03d}" if prev_page_dict is not None else ""
        log(f"[preprocess] ▶ VLM batch of {len(batch)} fresh page(s): {batch_pns}{overlap_note}")

        # Magi panel-detect every page in the batch (Magi for prior page not needed — its data is cached).
        batch_panels: list[list[dict]] = []
        batch_dims: list[tuple[int, int]] = []
        for b in batch:
            t_panel = time.time()
            with Image.open(b["img"]) as im:
                batch_dims.append(im.size)
            panels_raw = detect_panels(b["img"])
            log(f"[preprocess]   p{b['pn']:03d}: Magi → {len(panels_raw)} panel(s) "
                f"in {time.time() - t_panel:.1f}s")
            batch_panels.append(panels_raw)

        # Call multi-image VLM with overlap. Returns None on total failure → fall back per-page.
        t_vlm = time.time()
        vlm_pages, new_state, model_used = extract_pages_batch(
            [b["img"] for b in batch],
            batch_panels,
            progress=log,
            story_context=story_context,
            running_state=running_state,
            prior_page=prev_page_dict,
            prior_image_path=prev_image_path,
        )
        vlm_dt = time.time() - t_vlm

        if vlm_pages is None:
            log(f"[preprocess]   ✗ batch failed — falling back to per-page extract_page()")
            for b, panels_raw, dims in zip(batch, batch_panels, batch_dims):
                page_dict = _build_page_from_single(
                    page_number=b["pn"], issue_label=b["label"], image_path=b["img"],
                    panels_raw=panels_raw, dimensions=dims, project_root=project_root,
                    log=log, story_context=story_context, content_hash=b["hash"],
                )
                results.append(page_dict)
                prev_page_dict = page_dict
                prev_image_path = b["img"]
        else:
            log(f"[preprocess]   ✓ batch ok in {vlm_dt:.1f}s via {model_used}")
            running_state = new_state or running_state
            for b, panels_raw, dims, vlm_page in zip(batch, batch_panels, batch_dims, vlm_pages):
                page_dict = _assemble_page_dict(
                    page_number=b["pn"], issue_label=b["label"], image_path=b["img"],
                    panels_raw=panels_raw, dimensions=dims, vlm_data=vlm_page,
                    content_hash=b["hash"], vlm_model_used=vlm_page.get("_vlm_model_used", model_used),
                )
                save_cached(project_root, b["pn"], b["hash"], page_dict)
                results.append(page_dict)
                # The LAST page of this batch becomes prior-context for next batch.
                prev_page_dict = page_dict
                prev_image_path = b["img"]

        i = batch_end

    log(f"[preprocess] running_state final: {running_state[:200]}")
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


def _assemble_page_dict(
    *,
    page_number: int,
    issue_label: str,
    image_path: Path,
    panels_raw: list[dict],
    dimensions: tuple[int, int],
    vlm_data: dict,
    content_hash: str,
    vlm_model_used: str,
) -> dict:
    """Turn raw VLM output + Magi bboxes into a PreprocessedPage dict. Pure builder, no I/O."""
    width, height = dimensions

    # Cover shortcut: first page, no panels detected, no VLM data → mark as cover.
    if not panels_raw and page_number == 1 and not vlm_data:
        return PreprocessedPage(
            page_number=page_number,
            source_image=str(image_path.resolve()),
            image_dimensions={"width": width, "height": height},
            is_story_page=False, page_type="cover", panels=[], text_blocks=[],
            page_summary="Cover page", issue_label=issue_label,
            vlm_model="", vlm_model_used="", content_hash=content_hash,
            preprocessing_method="magi+vlm", skip_reason="",
        ).to_dict()

    page_type = str(vlm_data.get("page_type", "story")).lower()
    if page_type not in ("cover", "story", "skip"):
        page_type = "story"
    skip_reason = str(vlm_data.get("skip_reason", ""))
    vlm_text_blocks = vlm_data.get("text_blocks") or []

    if page_type == "skip":
        panel_infos: list[PanelInfo] = []
        text_blocks: list[TextBlock] = []
        page_summary = ""
    else:
        panel_infos = [
            PanelInfo(
                index=i, bbox=p["bbox"],
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

    return PreprocessedPage(
        page_number=page_number,
        source_image=str(image_path.resolve()),
        image_dimensions={"width": width, "height": height},
        is_story_page=(page_type == "story"),
        page_type=page_type, panels=panel_infos, text_blocks=text_blocks,
        page_summary=page_summary, issue_label=issue_label,
        vlm_model=VLM_MODEL, vlm_model_used=vlm_model_used,
        content_hash=content_hash, preprocessing_method="magi+vlm",
        skip_reason=skip_reason,
    ).to_dict()


def _build_page_from_single(
    *,
    page_number: int,
    issue_label: str,
    image_path: Path,
    panels_raw: list[dict],
    dimensions: tuple[int, int],
    project_root: Path,
    log: Callable[[str], None],
    story_context: str,
    content_hash: str,
) -> dict:
    """Single-image fallback: called when a multi-image batch fails."""
    # First-page-no-panels cover shortcut.
    if not panels_raw and page_number == 1:
        log(f"[stage2]     p{page_number:03d} no panels + first page → COVER shortcut")
        out = _assemble_page_dict(
            page_number=page_number, issue_label=issue_label, image_path=image_path,
            panels_raw=[], dimensions=dimensions, vlm_data={},
            content_hash=content_hash, vlm_model_used="",
        )
        save_cached(project_root, page_number, content_hash, out)
        return out

    log(f"[stage2]     p{page_number:03d} fallback single-image VLM ({len(panels_raw)} panels)…")
    t_vlm = time.time()
    vlm_data = extract_page(image_path, panels_raw, progress=log, story_context=story_context)
    log(f"[stage2]     p{page_number:03d} fallback done in {time.time() - t_vlm:.1f}s")

    out = _assemble_page_dict(
        page_number=page_number, issue_label=issue_label, image_path=image_path,
        panels_raw=panels_raw, dimensions=dimensions, vlm_data=vlm_data,
        content_hash=content_hash,
        vlm_model_used=str(vlm_data.get("_vlm_model_used", "")),
    )
    save_cached(project_root, page_number, content_hash, out)
    return out


def _panel_field(vlm_data: dict, index: int, key: str, default=""):
    panels = vlm_data.get("panels") or []
    for p in panels:
        if int(p.get("index", -1)) == index:
            v = p.get(key)
            if v is not None:
                return v
    return default
