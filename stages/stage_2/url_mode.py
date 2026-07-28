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

from config import get_project_dirs
from utils.comic_scraper import scrape_issue_pages
from .issue_resolver import resolve_chapters

try:
    from stages.stage_1.tools.fetch_fandom import fetch_fandom
except Exception:  # pragma: no cover - keeps module importable if stage_1 deps missing
    fetch_fandom = None


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

    chapters = resolve_chapters(series_url, issues)
    if not chapters:
        raise RuntimeError(f"No chapters resolved for issues={issues!r} at {series_url}")
    log(f"[url-mode] resolved {len(chapters)} chapter(s)")

    if enrich:
        if len(chapters) > 1:
            # >1 chapter downloaded here IS an arc, same as --saga — route through
            # _enrich_issues (not the single-issue enrich) so Stage 3 gets
            # is_arc/issue_count/issues[]. Without this, anyone using
            # --url ... --issues "#1-3" (instead of --saga) silently lost all
            # arc handling downstream.
            for i, ch in enumerate(chapters, start=1):
                ch["chapter_index"] = i
            ctx = _enrich_issues(ctx, chapters, project_root=project_root, log=log)
        else:
            ctx = _enrich_context_silent(ctx, project_root=project_root, log=log)

    return _run_downloads(project_name, project_root, chapters, log)


# ─── Reader URLs form ───────────────────────────────────────────────────────


def _chapter_meta_from_reader(reader_url: str, log: Callable[[str], None]) -> dict:
    """Ask batcave's reader-page window.__DATA__ for the REAL chapter title.

    The reader __DATA__ carries `chapters: [{id, title}]` with entries like
    'Power Rangers: Ranger Slayer (2020-) #1' — the canonical series name,
    year, and issue number. We use that (NOT the user-typed project name) as
    the wiki query. Returns {"title", "issues", "year", "source_title"} or {}.
    """
    try:
        from utils.comic_scraper.readcomiconline import _fetch_data
        m = _READER_URL_RE.match(reader_url)
        chap_id = int(m.group(2)) if m else None
        data = _fetch_data(reader_url) or {}
        chapters = data.get("chapters") or []
        raw = ""
        for c in chapters:
            if c.get("id") == chap_id:
                raw = (c.get("title") or c.get("title_en") or "").strip()
                break
        if not raw and chapters:
            raw = (chapters[0].get("title") or chapters[0].get("title_en") or "").strip()
        if not raw:
            return {}
        # 'Power Rangers: Ranger Slayer (2020-) #1' → title / year / issue
        ym = re.search(r"\((\d{4})", raw)
        im = re.search(r"(#\d+(?:\.\d+)?)\s*$", raw)
        title = re.sub(r"\s*\(\d{4}[^)]*\)", "", raw)
        if im:
            title = title[: title.rfind(im.group(1))]
        title = title.strip(" -–")
        # batcave often appends a bare "Issue" token (e.g. "Ghost Racers Issue #1");
        # drop a trailing standalone Issue/Issues so the title reads cleanly.
        title = re.sub(r"\s+[Ii]ssues?\s*$", "", title).strip(" -–")
        return {
            "title": title,
            "issues": im.group(1) if im else "",
            "year": ym.group(1) if ym else "",
            "source_title": raw,
        }
    except Exception as exc:
        log(f"[url-mode] reader meta fetch failed ({type(exc).__name__}: {exc}) — using project-name hint")
        return {}


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
    project_root = _ensure_project_root(project_name)

    # Source of truth for title/issue/year is batcave's own reader __DATA__
    # (canonical chapter title) — NOT the user-typed project name. Project name
    # only remains as a fallback hint when the meta fetch fails.
    meta = _chapter_meta_from_reader(urls[0], log)
    title_hint = meta.get("title") or project_name.replace("_", " ").title()
    if meta.get("source_title"):
        log(f"[url-mode] real title from batcave: {meta['source_title']!r}")

    issues_label = meta.get("issues") or ", ".join(
        f"chap_{m.group(2)}" for m in (_READER_URL_RE.match(u) for u in urls) if m)
    log(f"[url-mode] {len(urls)} reader URL(s) — news_id={news_id}, issues={issues_label}")

    extra: dict = {"reader_urls": urls}
    if meta.get("title"):
        # Override (not setdefault) any hint-derived fields from a previous run.
        extra.update({"title": meta["title"], "series": meta["title"],
                      "issues": issues_label})
        if meta.get("year"):
            extra["year"] = meta["year"]

    ctx = _write_minimal_context(
        project_root=project_root,
        title_hint=title_hint,
        slug=project_name,
        batcave_url=f"https://{_BATCAVE_HOST}/{news_id}-{project_name}.html",  # best-effort
        issues=issues_label,
        log=log,
        extra=extra,
    )

    chapters = _reader_url_chapters(urls)
    if enrich:
        if len(urls) > 1:
            # >1 reader URL IS an arc, same as --saga-from-readers — see
            # download_from_series for why this must be _enrich_issues, not
            # the single-issue enrich.
            ctx = _enrich_issues(ctx, chapters, project_root=project_root, log=log)
        else:
            ctx = _enrich_context_silent(ctx, project_root=project_root, log=log)

    return _run_downloads(project_name, project_root, chapters, log)


def download_readers_only(
    project_name: str,
    reader_urls: list[str],
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Download reader URLs as ordered chapters, WITHOUT any enrich step.

    Built for explore_answer (Q&A) mode: the calling orchestrator has already
    built comic_context.json itself (question + researched countdown items,
    reader_urls in order) via stages.stage_1.answer_research.build_contexts.
    Every other entry point in this module calls _enrich_issues /
    _enrich_context_silent afterward, which OVERWRITES plot_summary/summary
    with a single comic's wiki plot — that would clobber the researched
    answer context (a Q&A digest has no single issue's "plot" to fetch, it
    cites N different comics). This wrapper only downloads pages and writes
    raw_comic/manifest.json; it never touches comic_context.json.
    """
    log = progress or print
    urls = [u.strip() for u in reader_urls if u and u.strip()]
    if not urls:
        raise ValueError("download_readers_only: empty URL list")
    for u in urls:
        if classify_url(u) != "reader":
            raise ValueError(f"Not a batcave.biz reader URL: {u}")

    # Dedup exact-duplicate URLs (two answer items citing the SAME issue) so batcave
    # isn't scraped twice — but keep each unique URL's FIRST-OCCURRENCE rank as its
    # chapter label/index. A positional relabel after dropping a duplicate would shift
    # every later chapter's "#N" and break the beat→item→panel-pool mapping downstream
    # (review_gate._beat_source / explore_answer.build_answer_beats key on "#N" = item N).
    uniq: list[str] = []
    ranks: list[int] = []
    seen: set[str] = set()
    for rank, u in enumerate(urls, start=1):
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
        ranks.append(rank)
    if len(uniq) < len(urls):
        log(f"[url-mode] {len(urls) - len(uniq)} duplicate reader URL(s) share a chapter — "
            f"labels keep first-occurrence ranks {ranks} (items citing the same issue share its panels)")

    project_root = _ensure_project_root(project_name)
    chapters = _reader_url_chapters(uniq, ranks=ranks)
    log(f"[url-mode] explore_answer: {len(uniq)} reader URL(s), no enrich")
    return _run_downloads(project_name, project_root, chapters, log)


# ─── Crossover-saga ingest ──────────────────────────────────────────────────


def download_saga(
    project_name: str,
    series_url: str,
    *,
    max_issues: int = 5,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Crossover-saga ingest: resolve a series' chapters, keep the first
    min(len, max_issues), download them, and build a per-issue arc context.
    N==1 collapses to the single-comic shape (see _enrich_issues)."""
    log = progress or print
    series_url = series_url.strip()
    if classify_url(series_url) != "series":
        raise ValueError(f"Expected a batcave.biz series URL, got: {series_url}")

    _news_id, slug = parse_series_slug(series_url)
    title_hint = slug_to_title(slug)
    project_root = _ensure_project_root(project_name)

    all_chapters = resolve_chapters(series_url, "")
    if not all_chapters:
        raise RuntimeError(f"No chapters resolved at {series_url}")
    chapters = all_chapters[: max(1, int(max_issues))]
    # normalize chapter_index to 1..N so page prefixes / issue mapping line up
    for i, ch in enumerate(chapters, start=1):
        ch["chapter_index"] = i
    log(f"[saga] '{title_hint}': {len(all_chapters)} chapter(s) available, ingesting {len(chapters)}")

    ctx = _write_minimal_context(
        project_root=project_root, title_hint=title_hint, slug=slug,
        batcave_url=series_url, issues="", log=log,
    )
    ctx = _enrich_issues(ctx, chapters, project_root=project_root, log=log)

    dl = _run_downloads(project_name, get_project_dirs(project_name)["root"], chapters, log)
    return {**dl, "issue_count": ctx.get("issue_count", 1)}


def download_saga_from_readers(
    project_name: str,
    reader_urls: list[str],
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Crossover-saga from explicit reader URLs (one issue each): fetch a
    SEPARATE per-issue context for each URL + weave into one arc context, then
    download. N==1 collapses to today's single-comic shape (see _enrich_issues).
    This is the reader-URL twin of download_saga (which takes a series URL)."""
    log = progress or print
    urls = [u.strip() for u in reader_urls if u and u.strip()]
    if not urls:
        raise ValueError("download_saga_from_readers: empty URL list")
    if any(classify_url(u) != "reader" for u in urls):
        raise ValueError("download_saga_from_readers expects batcave.biz reader URLs (one per issue)")

    project_root = _ensure_project_root(project_name)
    # Derive a series title from the first reader's batcave metadata (best wiki query).
    meta = _chapter_meta_from_reader(urls[0], log)
    title_hint = (meta.get("title") or "").strip() or project_name.replace("_", " ").title()

    chapters = _reader_url_chapters(urls)
    # Overwrite the positional labels (#1..#N) with each chapter's REAL issue
    # number from its own reader metadata. Positional labels made _enrich_issues
    # research the WRONG issues whenever the given readers aren't a series' first
    # N chapters (real case: Bedford Falls #6-#10 was enriched with the plots of
    # #1-#5 — the recap would have narrated the wrong half of the series).
    # chapter_index stays positional (it drives the chNN_ page prefixes on disk).
    for ch in chapters:
        ch_meta = meta if ch["reader_url"] == urls[0] else _chapter_meta_from_reader(ch["reader_url"], log)
        real = str(ch_meta.get("issues") or "").strip()
        m_num = re.search(r"#\s*(\d+(?:\.\d+)?)", real)
        if m_num:
            ch["label"] = f"#{m_num.group(1)}"
            ch["number"] = float(m_num.group(1))
    log(f"[saga] '{title_hint}': {len(chapters)} reader URL(s) → per-issue arc "
        f"({', '.join(c['label'] for c in chapters)})")

    ctx = _write_minimal_context(
        project_root=project_root, title_hint=title_hint,
        slug=slug_to_project_name(title_hint.lower().replace(" ", "-")),
        batcave_url=urls[0], issues="", log=log,
    )
    ctx = _enrich_issues(ctx, chapters, project_root=project_root, log=log)

    dl = _run_downloads(project_name, get_project_dirs(project_name)["root"], chapters, log)
    return {**dl, "issue_count": ctx.get("issue_count", 1)}


# ─── Internals ──────────────────────────────────────────────────────────────


def _ensure_project_root(project_name: str) -> Path:
    project_root = get_project_dirs(project_name)["root"]
    project_root.mkdir(parents=True, exist_ok=True)
    return project_root


def _reader_url_chapters(urls: list[str], ranks: list[int] | None = None) -> list[dict]:
    """One chapter dict per reader URL — shared by download_from_readers and
    download_saga_from_readers (both treat each reader URL as one issue).

    `ranks` (optional, same length as urls) pins each chapter's label/number/
    chapter_index to the caller's ORIGINAL 1-based position instead of the list
    position here. Q&A dedup needs this: dropping a duplicate URL must NOT shift
    the labels of the URLs after it, or every beat→item lookup downstream
    (issue_label "#N" → answer item N) goes off by one."""
    chapters = []
    for i, url in enumerate(urls, start=1):
        rank = ranks[i - 1] if ranks else i
        m = _READER_URL_RE.match(url)
        chap_id = int(m.group(2)) if m else rank
        chapters.append({
            "label": f"#{rank}", "number": float(rank),
            "reader_url": url, "chapter_id": chap_id, "chapter_index": rank,
        })
    return chapters


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
    # enrich_with_summary needs the OpenRouter client initialized once.
    try:
        from stages.stage_1.llm import create_client
        from stages.stage_1.tools import init as init_tools
        from stages.stage_1.tools.fetch_fandom import fetch_fandom
        from stages.stage_1.tools.summarize_context import enrich_with_summary
        from config import (OPENROUTER_API_KEY, OPENROUTER_BASE_URL, LLM_MODELS,
                            ENABLE_SDK_PLOT_FALLBACK, SDK_PLOT_FALLBACK_MIN_CHARS)
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
    # batcave chapter ids ("chap_21727") mean nothing to any wiki — strip them.
    issues_q = re.sub(r"chap_?\d+", "", issues, flags=re.IGNORECASE).strip(" ,")
    query = f"{title} {issues_q}".strip() if issues_q else title
    log(f"[url-mode] enriching context: querying fandom + wiki for '{query}'")

    plot = ""
    publisher = ctx.get("publisher", "")
    try:
        fandom = fetch_fandom(query, publisher=publisher)
        if (isinstance(fandom, dict) and not (fandom.get("plot_text") or "").strip()
                and query != title):
            # Whole chain missed with "<title> <issue>" — retry once with the bare title.
            log(f"[url-mode] fandom miss for {query!r} — retrying with bare title {title!r}")
            fandom = fetch_fandom(title, publisher=publisher)
        if isinstance(fandom, dict):
            # fetch_fandom returns the synopsis under "plot_text" (see fetch_fandom.py)
            plot = (fandom.get("plot_text") or "").strip()
            if plot:
                if fandom.get("wiki_url"):
                    ctx["wiki_url"] = fandom["wiki_url"]
                if fandom.get("title"):
                    ctx["wiki_page_title"] = fandom["title"]
                log(f"[url-mode] fandom hit: {len(plot)} chars ({fandom.get('source','')})")
    except Exception as exc:
        log(f"[url-mode] fandom fetch failed: {exc}")

    # Fallback: fandom (MediaWiki) gave nothing or only a stub → spawn a web-enabled
    # Claude SDK agent to research the plot from a real source. No source → no plot.
    # (The old Tavily review-site search via fetch_wiki was removed — SDK is the sole fallback.)
    if ENABLE_SDK_PLOT_FALLBACK and len(plot or "") < SDK_PLOT_FALLBACK_MIN_CHARS:
        try:
            from stages.stage_1.tools.gather_plot_sdk import gather_plot_sdk
            res = gather_plot_sdk(title, issues_q, publisher,
                                  year=str(ctx.get("year", "") or ""), log=log)
            # don't downgrade: only adopt the SDK plot if it beats the existing weak one
            if res and res.get("plot_summary") and len(res["plot_summary"]) > len(plot or ""):
                plot = res["plot_summary"]
                ctx["wiki_url"] = res.get("source_url") or ctx.get("wiki_url", "")
                ctx["plot_source"] = "claude-sdk-web"
                log(f"[url-mode] SDK web-research grounded {len(plot)} chars ← {res.get('source_url')}")
        except Exception as exc:
            log(f"[url-mode] SDK plot fallback failed: {exc}")

    if plot:
        ctx["plot_summary"] = plot
        ctx["plot_status"] = "OK"
        try:
            enrich_with_summary(ctx, progress=log)
        except Exception as exc:
            log(f"[url-mode] summarize failed: {exc}")
    else:
        # No plot grounded (Fandom miss + SDK web fallback failed). Mark it so
        # Stage 3 can warn LOUDLY instead of silently shipping a hallucinated
        # draft. Do NOT pretend the context is ready.
        ctx["plot_status"] = "MISSING"
        log("⚠️  [url-mode] NO PLOT GROUNDED — fandom missed AND the SDK web "
            "fallback returned nothing (check the log above for the reason: turns/"
            "throttle/no-source). plot_status=MISSING; Stage 3 will run with WEAK "
            "context and likely invent events. Hand-populate plot_summary or re-run enrichment.")

    (project_root / "comic_context.json").write_text(
        json.dumps(ctx, indent=2, ensure_ascii=False)
    )
    return ctx


def _enrich_issues(
    ctx: dict, chapters: list[dict], *, project_root, log
) -> dict:
    """Fetch a SEPARATE canonical context for EACH issue and merge them in arc
    order into ctx. For N==1 this reduces to the existing single-comic shape
    (no is_arc / issues[]). Reuses fetch_fandom + the SDK web fallback per issue.

    Writes comic_context.json and returns the updated ctx."""
    from pathlib import Path as _Path
    publisher = ctx.get("publisher", "")
    base_title = ctx.get("title", "")

    def _one_issue_plot(label: str) -> dict:
        q = f"{base_title} {label}".strip()
        plot, wiki_url, src = "", "", ""
        try:
            fd = fetch_fandom(q, publisher=publisher) if fetch_fandom else None
            if isinstance(fd, dict):
                plot = (fd.get("plot_text") or "").strip()
                wiki_url = fd.get("wiki_url") or ""
        except Exception as exc:
            log(f"[saga] fandom miss {label}: {exc}")
        from config import ENABLE_SDK_PLOT_FALLBACK, SDK_PLOT_FALLBACK_MIN_CHARS
        if ENABLE_SDK_PLOT_FALLBACK and len(plot) < SDK_PLOT_FALLBACK_MIN_CHARS:
            try:
                from stages.stage_1.tools.gather_plot_sdk import gather_plot_sdk
                res = gather_plot_sdk(base_title, label, publisher,
                                      year=str(ctx.get("year", "") or ""), log=log)
                if res and len(res.get("plot_summary", "")) > len(plot):
                    plot = res["plot_summary"]
                    wiki_url = res.get("source_url") or wiki_url
                    src = "claude-sdk-web"
            except Exception as exc:
                log(f"[saga] SDK fallback {label}: {exc}")
        return {"plot_summary": plot, "wiki_url": wiki_url, "plot_source": src}

    issues_meta: list[dict] = []
    for ch in chapters:
        label = ch.get("label", f"#{ch.get('chapter_index','?')}")
        log(f"[saga] enriching issue {label} …")
        info = _one_issue_plot(label)
        issues_meta.append({
            "label": label,
            "chapter_index": int(ch.get("chapter_index", len(issues_meta) + 1)),
            "plot_summary": info["plot_summary"],
            "wiki_url": info["wiki_url"],
            "plot_source": info["plot_source"],
        })

    if len(issues_meta) <= 1:
        only = issues_meta[0] if issues_meta else {"plot_summary": ""}
        ctx["plot_summary"] = only.get("plot_summary", "")
        if only.get("wiki_url"):
            ctx["wiki_url"] = only["wiki_url"]
    else:
        ctx["is_arc"] = True
        ctx["issue_count"] = len(issues_meta)
        ctx["issues"] = issues_meta
        ctx["plot_summary"] = "\n\n".join(
            f"[{it['label']}] {it['plot_summary']}".strip()
            for it in issues_meta if it["plot_summary"]
        )

    if str(ctx.get("plot_summary", "")).strip():
        ctx["plot_status"] = "OK"
    else:
        ctx["plot_status"] = "MISSING"
        log("⚠️  [saga] NO PLOT GROUNDED for any issue — fandom missed AND the SDK "
            "web fallback returned nothing. plot_status=MISSING; Stage 3 will run "
            "with WEAK context and likely invent events.")

    try:
        from stages.stage_1.tools.summarize_context import enrich_with_summary
        if ctx.get("plot_summary"):
            enrich_with_summary(ctx, progress=log)
    except Exception as exc:
        log(f"[saga] summarize failed: {exc}")

    (_Path(project_root) / "comic_context.json").write_text(
        json.dumps(ctx, indent=2, ensure_ascii=False)
    )
    return ctx


def _run_downloads(
    project_name: str,
    project_root: Path,
    chapters: list[dict],
    log: Callable[[str], None],
) -> dict:
    """Shared download loop + manifest writer."""
    manifest: list[dict] = []
    total_pages = 0
    for pos, chapter in enumerate(chapters, start=1):
        # Honor the chapter's OWN chapter_index (falling back to list position).
        # The Q&A dedup path hands us chapters whose index = the answer item's
        # first-occurrence RANK (possibly sparse, e.g. [1,2,4]); naming the files
        # positionally (ch03 for index 4) would desync the chNN filename prefix
        # from the "#N" label and break issue_index_of_page → beat anchoring.
        # Saga/reader callers pass dense 1..N indices, so nothing changes for them.
        chapter_idx = int(chapter.get("chapter_index") or pos)
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
