"""Decompose a long-form source into N ready-to-render sub-project slugs.

See LONGFORM_DESIGN.md. The whole long-form idea is SEGMENT-AND-STITCH: the tuned
core pipeline (Stage 1-5) still makes one tight ~60-90s segment; long-form just
runs it N times over N normal sub-projects, then concats. This module produces
those sub-projects WITHOUT touching any Stage core code.

- decompose_recap: an already-downloaded+preprocessed SAGA project (raw_comic
  ch01..chN + preprocessed/ + comic_context.issues[]) -> one single-issue
  sub-project per issue. Pure file-ops + reuse of the existing single-comic
  context shape. NO Stage-2 re-run.
- decompose_qa: a QUESTION -> research a LARGER answer set, split into K countdown
  segments (~3-4 items each), and materialise each as a normal Q&A sub-project by
  reusing answer_research.build_contexts + url_mode.download_readers_only.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from config import get_project_dirs
from stages._arc import issue_index_of_page


# ─── Recap: saga → one single-issue sub-project per issue ────────────────────


def decompose_recap(saga_project: str, *, log=None) -> list[str]:
    """Split a preprocessed saga project into one single-issue sub-project per
    issue. Returns the ordered sub-project slugs. Pure file-ops — no Stage 2."""
    log = log or print
    saga_root = get_project_dirs(saga_project)["root"]
    ctx_path = saga_root / "comic_context.json"
    ctx = json.loads(ctx_path.read_text())
    issues = ctx.get("issues")
    if not isinstance(issues, list) or not issues:
        raise ValueError(
            f"decompose_recap: {saga_project} comic_context has no issues[] list — "
            "not a saga project (download it with --saga first)"
        )

    # Group every preprocessed page by its issue index (from the chNN_ prefix of
    # source_image, via _arc.issue_index_of_page). sorted() keeps reading order,
    # and a contiguous issue slice stays correctly ordered under the same sort.
    prep_dir = saga_root / "preprocessed"
    by_issue: dict[int, list[tuple[Path, dict]]] = {}
    for p in sorted(prep_dir.glob("page_*.json")):
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        by_issue.setdefault(issue_index_of_page(data), []).append((p, data))

    cluster_map = saga_root / "cluster_to_name.json"
    slugs: list[str] = []
    for issue in issues:
        ci = int(issue.get("chapter_index") or 0)
        seg_pages = by_issue.get(ci, [])
        if not seg_pages:
            log(f"[longform] recap: issue chapter_index={ci} has no preprocessed pages — skip")
            continue

        seg_slug = f"{saga_project}__seg{ci:02d}"
        seg_root = get_project_dirs(seg_slug)["root"]
        seg_raw = seg_root / "raw_comic"
        seg_prep = seg_root / "preprocessed"
        seg_raw.mkdir(parents=True, exist_ok=True)
        seg_prep.mkdir(parents=True, exist_ok=True)

        for src_path, data in seg_pages:
            # Copy the raw page and repoint source_image at the sub-project's copy
            # (the ONLY field Stage 3/5 dereference for the panel image). Everything
            # else in the page JSON is kept byte-identical.
            src_img = Path(str(data.get("source_image", "")))
            if src_img.name:
                dst_img = seg_raw / src_img.name
                if src_img.exists():
                    shutil.copy2(src_img, dst_img)
                data = {**data, "source_image": str(dst_img)}
            (seg_prep / src_path.name).write_text(
                json.dumps(data, indent=2, ensure_ascii=False)
            )

        (seg_root / "comic_context.json").write_text(
            json.dumps(_single_issue_context(ctx, issue), indent=2, ensure_ascii=False)
        )
        if cluster_map.exists():
            shutil.copy2(cluster_map, seg_root / "cluster_to_name.json")

        slugs.append(seg_slug)
        log(f"[longform] recap seg {seg_slug}: {len(seg_pages)} page(s)")

    return slugs


def _single_issue_context(saga_ctx: dict, issue: dict) -> dict:
    """Build a SINGLE-ISSUE comic_context from one saga issue dict.

    Reuses the plain single-comic shape (title/series/issue/plot_summary/...) —
    deliberately NOT is_arc and NO issues[] list, so Stage 3 runs the normal tuned
    recap path (not the arc path)."""
    label = str(issue.get("label", "") or "").strip()
    series = saga_ctx.get("series") or saga_ctx.get("title") or ""
    title = f"{series} {label}".strip() if label else (series or saga_ctx.get("title", ""))
    plot = str(issue.get("plot_summary", "") or "")
    return {
        "title": title,
        "series": series,
        "issue": label,
        "year": saga_ctx.get("year", ""),
        "publisher": saga_ctx.get("publisher", ""),
        "characters": saga_ctx.get("characters", []),
        "plot_summary": plot,
        "plot_status": "OK" if plot.strip() else "MISSING",
        "plot_source": "recap",
        "wiki_url": issue.get("wiki_url", ""),
        "batcave_url": saga_ctx.get("batcave_url", ""),
    }


# ─── Q&A: question → K countdown segments ────────────────────────────────────


def decompose_qa(question: str, project: str, *, max_items: int = 15, log=None) -> list[str]:
    """Research a big answer set for `question`, split into K countdown segments
    (~3-4 items each), and materialise each as a normal Q&A sub-project via the
    existing build_contexts + download_readers_only. Returns the segment slugs
    that shipped (a segment that fails to build/download is logged and skipped)."""
    log = log or print
    # Lazy imports: keep this module cheap to import (the recap path needs none of
    # these) and let the Q&A test monkeypatch them on their source modules.
    from stages.stage_1.answer_research import build_contexts, research_answer
    from stages.stage_1.storage import slugify
    from stages.stage_2.url_mode import download_readers_only

    research = research_answer(question, max_items=max_items, log=log)
    groups = _chunk_items(research.get("items") or [])
    log(f"[longform] qa: {sum(len(g) for g in groups)} item(s) → {len(groups)} segment(s)")

    slugs: list[str] = []
    for k, group in enumerate(groups, start=1):
        seg_slug = slugify(f"{project}__seg{k:02d}")
        seg_research = {**research, "items": group}
        try:
            # build_contexts verifies + resolves each item and (in place) fills any
            # empty reader_url, then writes answer_context.json + a saga-shape
            # comic_context.json. Read the reader_urls AFTER it returns so resolved
            # ones are included; it raises fail-loud if a URL is truly undownloadable.
            build_contexts(question, seg_research, seg_slug, log=log)
            download_readers_only(
                seg_slug, [it.get("reader_url", "") for it in group], progress=log
            )
        except Exception as exc:  # noqa: BLE001 - fail-loud per segment, keep the rest
            log(f"[longform] qa seg {seg_slug} FAILED ({type(exc).__name__}: {exc}) — skipping")
            continue
        slugs.append(seg_slug)
        log(f"[longform] qa seg {seg_slug}: {len(group)} item(s)")

    return slugs


def _chunk_items(items: list, *, target: int = 4, floor: int = 3) -> list[list]:
    """Split N items into near-even groups of ~`target`, each >= `floor` where
    possible (a countdown listicle needs >= 3). One group when N <= target."""
    n = len(items)
    if n <= target:
        return [list(items)]
    k = max(1, math.ceil(n / target))
    while k > 1 and n / k < floor:  # don't leave a runt group below the listicle floor
        k -= 1
    base, extra = divmod(n, k)
    out, idx = [], 0
    for g in range(k):
        size = base + (1 if g < extra else 0)
        out.append(list(items[idx:idx + size]))
        idx += size
    return out
