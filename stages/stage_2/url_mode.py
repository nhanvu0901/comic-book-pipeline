"""
Stage 2 URL-direct mode — bypass Stage 1's identification flow.

Two entry points:
  • from a series URL + issues range:
        download_from_series(project, "https://batcave.biz/6587-what-if-...html", "#1-3")
  • from one or more reader URLs (one issue per URL):
        download_from_readers(project, ["https://batcave.biz/reader/6587/34073", ...])

Both write a minimal `comic_context.json` (so downstream stages can still find
batcave_url / issues) and a full `raw_comic/manifest.json`. If `enrich=True`
(default), the function additionally calls Stage 1's wiki/fandom helpers
silently to enrich the context with a story summary + character roster —
this is what lets Stage 3 produce full-quality narration without the
interactive identification flow.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from config import get_project_dirs, PROJECTS_ROOT
from utils.comic_scraper import discover_issues, scrape_issue_pages
from .issue_resolver import parse_issue_range


_BATCAVE_HOST = "batcave.biz"
_SERIES_URL_RE = re.compile(r"^https?://(?:www\.)?batcave\.biz/(\d+)-([a-z0-9-]+?)\.html")
_READER_URL_RE = re.compile(r"^https?://(?:www\.)?batcave\.biz/reader/(\d+)/(\d+)")


def classify_url(url: str) -> str:
    """Return 'series', 'reader', or 'unknown'."""
    url = url.strip()
    if _READER_URL_RE.match(url):
        return "reader"
    if _SERIES_URL_RE.match(url):
        return "series"
    return "unknown"


def parse_series_slug(series_url: str) -> tuple[str, str]:
    """Extract (news_id, slug) from a series URL like '6587-what-if-dark-venom-2023'."""
    m = _SERIES_URL_RE.match(series_url.strip())
    if not m:
        raise ValueError(f"Not a valid batcave.biz series URL: {series_url}")
    return m.group(1), m.group(2)


def slug_to_title(slug: str) -> str:
    """'what-if-dark-venom-2023' → 'What If Dark Venom 2023'."""
    return " ".join(w.capitalize() for w in slug.split("-") if w)


def slug_to_project_name(slug: str) -> str:
    """'what-if-dark-venom-2023' → 'what_if_dark_venom_2023' (matches Stage 1 storage.slugify)."""
    return re.sub(r"-+", "_", slug.strip("-"))


# ─── Series + range form ────────────────────────────────────────────────────


def download_from_series(
    project_name: str,
    series_url: str,
    issues: str = "",
    *,
    enrich: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Resolve chapters from a series page + range string, then download.

    Returns {"context_path": str, "manifest_path": str, "total_pages": int, "chapters": int}.
    """
    log = progress or print
    series_url = series_url.strip()
    if classify_url(series_url) != "series":
        raise ValueError(f"Expected a batcave.biz series URL, got: {series_url}")

    _news_id, slug = parse_series_slug(series_url)
    title_hint = slug_to_title(slug)

    log(f"[url-mode] series='{title_hint}' issues={issues or '(all)'}")

    project_root = _ensure_project_root(project_name)
    ctx = _write_minimal_context(
        project_root=project_root,
        title_hint=title_hint,
        slug=slug,
        batcave_url=series_url,
        issues=issues,
        log=log,
    )

    if enrich:
        ctx = _enrich_context_silent(ctx, project_root=project_root, log=log)

    return _do_series_download(project_name, series_url, issues, ctx, log=log)


# ─── Reader URLs form ───────────────────────────────────────────────────────


def download_from_readers(
    project_name: str,
    reader_urls: list[str],
    *,
    enrich: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Download each reader URL as one chapter. Series URL is inferred from the first."""
    log = progress or print
    urls = [u.strip() for u in reader_urls if u and u.strip()]
    if not urls:
        raise ValueError("download_from_readers: empty URL list")
    for u in urls:
        if classify_url(u) != "reader":
            raise ValueError(f"Not a batcave.biz reader URL: {u}")

    # Infer series URL from the first reader: /reader/{news_id}/{chap_id}
    m = _READER_URL_RE.match(urls[0])
    news_id = m.group(1) if m else "unknown"
    # We don't know the slug from a reader URL alone — fall back to news_id.
    # discover_issues isn't called, so we can't auto-grab the slug. User-supplied
    # project name is the source of truth.
    project_root = _ensure_project_root(project_name)
    title_hint = project_name.replace("_", " ").title()

    issues_label = ", ".join(f"chap_{m.group(2)}"
                             for m in (_READER_URL_RE.match(u) for u in urls) if m)
    log(f"[url-mode] {len(urls)} reader URL(s) — news_id={news_id}, issues={issues_label}")

    ctx = _write_minimal_context(
        project_root=project_root,
        title_hint=title_hint,
        slug=project_name,
        batcave_url=f"https://{_BATCAVE_HOST}/{news_id}-{project_name}.html",  # best-effort
        issues=issues_label,
        log=log,
        extra={"reader_urls": urls},
    )
    if enrich:
        ctx = _enrich_context_silent(ctx, project_root=project_root, log=log)

    return _do_reader_download(project_name, urls, log=log)


# ─── Internals ──────────────────────────────────────────────────────────────


def _ensure_project_root(project_name: str) -> Path:
    project_root = get_project_dirs(project_name)["root"]
    project_root.mkdir(parents=True, exist_ok=True)
    return project_root


def _write_minimal_context(
    *,
    project_root: Path,
    title_hint: str,
    slug: str,
    batcave_url: str,
    issues: str,
    log: Callable[[str], None],
    extra: dict | None = None,
) -> dict:
    """Write a comic_context.json with just enough fields for downstream stages."""
    ctx_path = project_root / "comic_context.json"
    ctx: dict = {}
    if ctx_path.exists():
        try:
            ctx = json.loads(ctx_path.read_text())
            log(f"[url-mode] reusing existing comic_context.json (will enrich)")
        except json.JSONDecodeError:
            ctx = {}

    ctx.setdefault("title", title_hint)
    ctx.setdefault("series", title_hint)
    ctx.setdefault("issues", issues)
    ctx.setdefault("year", _year_from_slug(slug))
    ctx.setdefault("publisher", "")
    ctx.setdefault("characters", [])
    ctx["batcave_url"] = batcave_url
    if extra:
        ctx.update(extra)

    ctx_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False))
    log(f"[url-mode] wrote {ctx_path.name}")
    return ctx


def _year_from_slug(slug: str) -> str:
    m = re.search(r"(19|20)\d{2}", slug)
    return m.group(0) if m else ""


def _enrich_context_silent(
    ctx: dict, *, project_root: Path, log: Callable[[str], None]
) -> dict:
    """Call Stage 1's wiki + summarize tools silently to fill in `plot_summary` + `summary`."""
    # Stage 1 tools require the LLM client initialized for paraphrase_query (used inside
    # fetch_wiki). Initialize once.
    try:
        from stages.stage_1.llm import create_client
        from stages.stage_1.tools import init as init_tools
        from stages.stage_1.tools.fetch_fandom import fetch_fandom
        from stages.stage_1.tools.fetch_wiki import fetch_wiki
        from stages.stage_1.tools.summarize_context import enrich_with_summary
        from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, LLM_MODELS
    except ImportError as exc:
        log(f"[url-mode] enrichment skipped — Stage 1 modules unavailable: {exc}")
        return ctx

    primary = (LLM_MODELS[0] if LLM_MODELS else "").strip()
    if not OPENROUTER_API_KEY or not primary:
        log("[url-mode] enrichment skipped — OPENROUTER_API_KEY/LLM_MODELS not configured")
        return ctx

    client = create_client(OPENROUTER_API_KEY, OPENROUTER_BASE_URL)
    init_tools(client, primary)

    title = ctx.get("title", "")
    issues = ctx.get("issues", "")
    query = f"{title} {issues}".strip() if issues else title
    log(f"[url-mode] enriching context: querying fandom + wiki for '{query}'")

    plot = ""
    publisher = ctx.get("publisher", "")
    try:
        fandom = fetch_fandom(query, publisher=publisher)
        if isinstance(fandom, dict):
            plot = (fandom.get("text") or fandom.get("synopsis") or "").strip()
            if fandom.get("publisher") and not publisher:
                ctx["publisher"] = str(fandom["publisher"]).strip()
            if plot:
                log(f"[url-mode] fandom hit: {len(plot)} chars")
    except Exception as exc:
        log(f"[url-mode] fandom fetch failed: {exc}")

    if not plot:
        try:
            wiki = fetch_wiki(query, publisher=publisher)
            if isinstance(wiki, dict):
                plot = (wiki.get("text") or "").strip()
                if plot:
                    log(f"[url-mode] wiki hit: {len(plot)} chars")
        except Exception as exc:
            log(f"[url-mode] wiki fetch failed: {exc}")

    if plot:
        ctx["plot_summary"] = plot
        try:
            enrich_with_summary(ctx, progress=log)
        except Exception as exc:
            log(f"[url-mode] summarize failed: {exc}")
    else:
        log("[url-mode] no plot found — narration will run with weaker context")

    (project_root / "comic_context.json").write_text(
        json.dumps(ctx, indent=2, ensure_ascii=False)
    )
    return ctx


def _do_series_download(
    project_name: str,
    series_url: str,
    issues: str,
    ctx: dict,
    *,
    log: Callable[[str], None],
) -> dict:
    """Resolve chapters via discover_issues, download each."""
    from .issue_resolver import resolve_chapters

    project_root = get_project_dirs(project_name)["root"]
    chapters = resolve_chapters(series_url, issues)
    if not chapters:
        raise RuntimeError(f"No chapters resolved for issues={issues!r} at {series_url}")
    log(f"[url-mode] resolved {len(chapters)} chapter(s)")
    return _run_downloads(project_name, project_root, chapters, log)


def _do_reader_download(
    project_name: str,
    reader_urls: list[str],
    *,
    log: Callable[[str], None],
) -> dict:
    """Each reader URL = one chapter; no discover_issues call."""
    project_root = get_project_dirs(project_name)["root"]
    chapters = []
    for i, url in enumerate(reader_urls, start=1):
        m = _READER_URL_RE.match(url)
        chap_id = int(m.group(2)) if m else i
        chapters.append({
            "label": f"#{i}",
            "number": float(i),
            "reader_url": url,
            "chapter_id": chap_id,
        })
    return _run_downloads(project_name, project_root, chapters, log)


def _run_downloads(
    project_name: str,
    project_root: Path,
    chapters: list[dict],
    log: Callable[[str], None],
) -> dict:
    """Shared download loop + manifest writer."""
    manifest: list[dict] = []
    total_pages = 0
    for chapter_idx, chapter in enumerate(chapters, start=1):
        log(f"[url-mode] ▶ {chapter['label']} ({chapter['reader_url']})")
        t0 = time.time()
        try:
            page_paths = scrape_issue_pages(
                chapter["reader_url"],
                project_root=project_root,
                chapter_index=chapter_idx,
            )
        except Exception as exc:
            log(f"[url-mode]   ✗ failed: {exc}")
            continue
        page_strs = [str(p) for p in page_paths]
        manifest.append({
            "chapter_index": chapter_idx,
            "label": chapter["label"],
            "reader_url": chapter["reader_url"],
            "pages": page_strs,
        })
        total_pages += len(page_strs)
        log(f"[url-mode]   ✓ {len(page_strs)} pages in {time.time() - t0:.1f}s")

    manifest_path = project_root / "raw_comic" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    log(f"[url-mode] done — {total_pages} pages across {len(manifest)} chapter(s)")

    return {
        "context_path": str(project_root / "comic_context.json"),
        "manifest_path": str(manifest_path),
        "total_pages": total_pages,
        "chapters": len(manifest),
    }
