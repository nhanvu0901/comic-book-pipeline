"""
Download comic pages from batcave.biz.

Resolves chapters from comic_context.json, downloads page images to
projects/<slug>/raw_comic/, and writes a manifest.json so that the
preprocessing stage can read pages without re-resolving chapters.
"""
import json
import time
from pathlib import Path
from typing import Callable

from config import get_project_dirs, PROJECTS_ROOT
from utils.comic_scraper import scrape_issue_pages
from .issue_resolver import resolve_chapters


def download_comic(
    project_name: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Download comic pages for a project.
    Returns the manifest (list of chapter dicts with page paths).
    """
    log = progress or print

    ctx_path = PROJECTS_ROOT / project_name / "comic_context.json"
    if not ctx_path.exists():
        raise FileNotFoundError(
            f"comic_context.json not found for project '{project_name}'. "
            "Run Stage 1 first."
        )

    ctx = json.loads(ctx_path.read_text())
    batcave_url = ctx.get("batcave_url", "").strip()
    issues = ctx.get("issues", "").strip()
    if not batcave_url:
        raise ValueError("comic_context.json has no batcave_url — cannot download.")

    project_root = get_project_dirs(project_name)["root"]
    log(f"[download] project={project_name} issues={issues!r}")

    chapters = resolve_chapters(batcave_url, issues)
    if not chapters:
        raise RuntimeError(
            f"No chapters resolved for issues={issues!r} at {batcave_url}"
        )
    log(f"[download] resolved {len(chapters)} chapter(s)")

    manifest: list[dict] = []
    total_pages = 0

    for chapter_idx, chapter in enumerate(chapters, start=1):
        log(f"[download] ▶ downloading {chapter['label']} ({chapter['reader_url']})")
        t0 = time.time()
        try:
            page_paths = scrape_issue_pages(
                chapter["reader_url"],
                project_root=project_root,
                chapter_index=chapter_idx,
            )
        except Exception as e:
            log(f"[download]   ✗ failed: {e}")
            continue

        page_strs = [str(p) for p in page_paths]
        manifest.append({
            "chapter_index": chapter_idx,
            "label": chapter["label"],
            "reader_url": chapter["reader_url"],
            "pages": page_strs,
        })
        total_pages += len(page_strs)
        log(f"[download]   ✓ {len(page_strs)} pages in {time.time() - t0:.1f}s")

    manifest_path = project_root / "raw_comic" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    log(f"[download] done — {total_pages} pages across {len(manifest)} chapter(s)")

    return manifest


def load_manifest(project_name: str) -> list[dict]:
    project_root = get_project_dirs(project_name)["root"]
    manifest_path = project_root / "raw_comic" / "manifest.json"
    if not manifest_path.exists():
        return []
    try:
        return json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return []
