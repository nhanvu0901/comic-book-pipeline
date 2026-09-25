"""
Download comic pages from batcave.biz.

Resolves chapters from comic_context.json, downloads page images to
projects/<slug>/raw_comic/, and writes a manifest.json so that the
preprocessing stage can read pages without re-resolving chapters.
"""
import json
from typing import Callable

from config import get_project_dirs, PROJECTS_ROOT
from stages.user_errors import DownloadIncompleteError, MissingInputError, SourceUrlError
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
        raise MissingInputError(
            f"No comic_context.json for project '{project_name}' yet. Create the project from "
            "Research Scout first (python -m stages.stage_1 from a terminal)."
        )

    ctx = json.loads(ctx_path.read_text())
    batcave_url = ctx.get("batcave_url", "").strip()
    issues = ctx.get("issues", "").strip()
    if not batcave_url:
        # Typical for a project made with Download from URL(s): it has no Stage 1 series
        # link, and the Stage 1 download button still sits above that form.
        raise SourceUrlError(
            f"Project '{project_name}' has no comic link from Research Scout, so there is "
            "nothing to download from here. Use Download from URL(s) with the comic's "
            "batcave link, or approve a selection in Research Scout."
        )

    project_root = get_project_dirs(project_name)["root"]
    log(f"[download] project={project_name} issues={issues!r}")

    chapters = resolve_chapters(batcave_url, issues)
    if not chapters:
        raise DownloadIncompleteError(
            f"Found no issues matching {issues or 'all'!r} at {batcave_url}. Check the "
            "series link and the issue numbers."
        )
    log(f"[download] resolved {len(chapters)} chapter(s)")

    # One download loop for every entry point (url_mode._run_downloads): it fails loud
    # on a missing or empty chapter and drops a chapter's cache when that chapter number
    # held a different comic last time. This copy of the loop used to skip failures
    # silently. Chapters are numbered by position, as they always were here.
    from .url_mode import _run_downloads
    for chapter_idx, chapter in enumerate(chapters, start=1):
        chapter["chapter_index"] = chapter_idx
    _run_downloads(project_name, project_root, chapters, log)
    return load_manifest(project_name)


def load_manifest(project_name: str) -> list[dict]:
    project_root = get_project_dirs(project_name)["root"]
    manifest_path = project_root / "raw_comic" / "manifest.json"
    if not manifest_path.exists():
        return []
    try:
        return json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return []
