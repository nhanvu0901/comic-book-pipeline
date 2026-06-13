# Crossover-Saga Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in mode that ingests up to 5 sequential issues of one batcave series (each with its OWN canonical context), then narrates them as one continuous ~60–90s crossover Short — while N=1 and the existing `narrate_1_comic` flow stay byte-for-byte unchanged.

**Architecture:** Reuse the EXISTING multi-issue download (`stages/stage_2/url_mode.download_from_series` → `resolve_chapters` → `scrape_issue_pages`, which already writes a multi-chapter `raw_comic/manifest.json` with pages prefixed `ch{NN}_page_XX.jpg`). Add (1) a per-issue context enrich that builds `comic_context.issues[]` + `is_arc`, and (2) a Stage-3 arc-aware branch that spreads beats across issues and cross-checks each beat against its own issue's context. Stage 2 (VLM), Stage 4 (TTS), Stage 5 (render) are reused unchanged because they already iterate every page/scene/beat. A new pure-function module `stages/_arc.py` holds the page→issue mapping and beat-allocation math (easily unit-tested, shared by Stage 3).

**Tech Stack:** Python 3.14, pytest, existing helpers: `stages/stage_2/url_mode.py`, `stages/stage_2/issue_resolver.py`, `stages/stage_1/tools/fetch_fandom.py`, `stages/stage_1/tools/gather_plot_sdk.py`, `stages/stage_1/tools/summarize_context.py`, `stages/stage_3/write_script.py`.

---

## Spec

Source of truth: `docs/superpowers/specs/2026-06-13-crossover-saga-mode-design.md`.

## File Structure

- **Create** `stages/_arc.py` — pure helpers: `issue_index_of_page(page)`, `allocate_beats_across_issues(total, n_issues, page_counts)`. No I/O, no LLM. Shared by Stage 3.
- **Create** `tests/test_arc.py` — unit tests for `stages/_arc.py`.
- **Create** `tests/test_saga_context.py` — unit tests for the per-issue context merge + N=1 shape.
- **Modify** `config.py` — add `PipelineMode.CROSSOVER_SAGA`.
- **Modify** `stages/stage_2/url_mode.py` — add `download_saga(...)` + `_enrich_issues(...)`; reuse existing chapter resolution + download loop.
- **Modify** `stages/stage_3/write_script.py` — arc-aware branch in `outline_beats` (beat allocation + page-range anchoring) and `_wiki_cross_check` (per-issue).
- **Modify** `stages/stage_2/cli.py` (or wherever url-mode is invoked) — wire `download_saga` behind the new mode.

## Conventions to follow (verified in the codebase)

- Pages are saved by `scrape_issue_pages(reader_url, project_root, chapter_index=k)` as `raw_comic/ch{NN}_page_XX.jpg` (NN = zero-padded `chapter_index`). Each preprocessed page JSON (`preprocessed/page_*.json`) carries `source_image` (that path) and `page_number`.
- `comic_context.json` is read by `stages/stage_3/pipeline.py:25`. `outline_beats` (write_script.py) reads `comic_context["plot_summary"]` and `comic_context["summary"]["story_arc"]`; `_wiki_cross_check` reads the same. KEEP those top-level keys populated (merged) for backward-compat; ADD `issues[]` alongside.
- `fetch_fandom(query, publisher=...)` returns a dict with the synopsis under `plot_text` (+ `wiki_url`, `title`). `gather_plot_sdk(title, issues, publisher, log=...)` returns `{"plot_summary","source_url"}` or falsy.

---

### Task 1: Add the `CROSSOVER_SAGA` pipeline mode

**Files:**
- Modify: `config.py:14-16` (the `PipelineMode` enum)

- [ ] **Step 1: Add the enum value**

In `config.py`, the enum currently is:
```python
class PipelineMode(str, Enum):
    NARRATE_1_COMIC = "narrate_1_comic"
    STORY_ARC = "story_arc"
```
Change to (leave `STORY_ARC` untouched; add the new value):
```python
class PipelineMode(str, Enum):
    NARRATE_1_COMIC = "narrate_1_comic"
    STORY_ARC = "story_arc"
    CROSSOVER_SAGA = "crossover_saga"   # ≤5 sequential issues of one series → one Short
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from config import PipelineMode; print(PipelineMode.CROSSOVER_SAGA.value)"`
Expected: `crossover_saga`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat(saga): add CROSSOVER_SAGA pipeline mode"
```

---

### Task 2: Pure helpers — page→issue mapping + beat allocation

**Files:**
- Create: `stages/_arc.py`
- Test: `tests/test_arc.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arc.py`:
```python
from stages._arc import issue_index_of_page, allocate_beats_across_issues


def test_issue_index_parses_chapter_prefix():
    # source_image carries the ch{NN}_page path written by scrape_issue_pages
    assert issue_index_of_page({"source_image": "/p/raw_comic/ch01_page_05.jpg"}) == 1
    assert issue_index_of_page({"source_image": "/p/raw_comic/ch03_page_12.jpg"}) == 3


def test_issue_index_unknown_returns_zero():
    assert issue_index_of_page({"source_image": "/p/raw_comic/cover.jpg"}) == 0
    assert issue_index_of_page({}) == 0


def test_allocate_even_split_gives_each_issue_a_floor():
    # 20 beats across 5 issues with equal pages → 4 each, every issue ≥ floor
    alloc = allocate_beats_across_issues(total=20, n_issues=5, page_counts=[10, 10, 10, 10, 10])
    assert sum(alloc.values()) == 20
    assert all(c >= 1 for c in alloc.values())
    assert set(alloc.keys()) == {1, 2, 3, 4, 5}


def test_allocate_weights_by_page_count_but_keeps_floor():
    # issue 5 is tiny (2 pages) but still gets the floor of 2
    alloc = allocate_beats_across_issues(total=20, n_issues=5, page_counts=[30, 30, 30, 30, 2])
    assert sum(alloc.values()) == 20
    assert alloc[5] >= 2
    assert alloc[1] > alloc[5]


def test_allocate_single_issue_gets_all():
    assert allocate_beats_across_issues(total=18, n_issues=1, page_counts=[22]) == {1: 18}
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_arc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stages._arc'`

- [ ] **Step 3: Implement `stages/_arc.py`**

```python
"""Pure helpers for crossover-saga (multi-issue) narration. No I/O, no LLM.

`issue_index_of_page` maps a preprocessed page to its 1-based issue number using
the `ch{NN}_page` filename prefix that scrape_issue_pages writes (chapter_index).
`allocate_beats_across_issues` splits a global beat budget across issues,
weighted by each issue's page count but guaranteeing every issue a floor of 2
beats (so no issue is dropped from the Short)."""
from __future__ import annotations

import re

_CH_RE = re.compile(r"ch0*(\d+)_page", re.IGNORECASE)

_FLOOR_PER_ISSUE = 2


def issue_index_of_page(page: dict) -> int:
    """1-based issue number for a preprocessed page, or 0 if it carries no
    chapter prefix (e.g. a cover)."""
    src = str((page or {}).get("source_image", ""))
    m = _CH_RE.search(src)
    return int(m.group(1)) if m else 0


def allocate_beats_across_issues(
    total: int, n_issues: int, page_counts: list[int]
) -> dict[int, int]:
    """Return {issue_index(1-based): beat_count}. Sums to `total`. Each issue gets
    at least min(_FLOOR_PER_ISSUE, fair share) so short issues are still narrated."""
    if n_issues <= 1:
        return {1: total}
    floor = min(_FLOOR_PER_ISSUE, max(1, total // n_issues))
    # Start everyone at the floor, then distribute the remainder by page weight.
    alloc = {i: floor for i in range(1, n_issues + 1)}
    remaining = total - floor * n_issues
    if remaining <= 0:
        # Budget too small for the floor — give 1 each, drop extras from the tail.
        even = {i: 1 for i in range(1, n_issues + 1)}
        # trim from the end until we hit `total`
        for i in range(n_issues, 0, -1):
            if sum(even.values()) <= total:
                break
            even[i] = 0
        return {i: c for i, c in even.items() if c > 0} or {1: total}
    weights = page_counts if (page_counts and len(page_counts) == n_issues and sum(page_counts) > 0) \
        else [1] * n_issues
    wsum = sum(weights)
    # Largest-remainder apportionment of `remaining` by weight.
    exact = [remaining * w / wsum for w in weights]
    base = [int(x) for x in exact]
    leftover = remaining - sum(base)
    order = sorted(range(n_issues), key=lambda i: exact[i] - base[i], reverse=True)
    for j in range(leftover):
        base[order[j]] += 1
    for i in range(1, n_issues + 1):
        alloc[i] += base[i - 1]
    return alloc
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_arc.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add stages/_arc.py tests/test_arc.py
git commit -m "feat(saga): pure page→issue + beat-allocation helpers"
```

---

### Task 3: Per-issue context enrich + merged arc context

**Files:**
- Modify: `stages/stage_2/url_mode.py` (add `_enrich_issues`, near `_enrich_context_silent`)
- Test: `tests/test_saga_context.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_saga_context.py`:
```python
import json
from pathlib import Path

import stages.stage_2.url_mode as um


def _fake_fandom(query, publisher=""):
    # distinct synopsis per issue so the merge order is observable
    n = query.strip()[-1]
    return {"plot_text": f"Issue {n} synopsis. " * 30, "wiki_url": f"http://w/{n}", "title": f"Saga #{n}"}


def test_enrich_issues_builds_arc_context(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "fetch_fandom", _fake_fandom, raising=False)
    ctx = {"title": "Saga", "publisher": "Marvel"}
    chapters = [
        {"label": "#1", "reader_url": "u1", "chapter_index": 1},
        {"label": "#2", "reader_url": "u2", "chapter_index": 2},
        {"label": "#3", "reader_url": "u3", "chapter_index": 3},
    ]
    out = um._enrich_issues(ctx, chapters, project_root=tmp_path, log=lambda m: None)

    assert out["is_arc"] is True
    assert out["issue_count"] == 3
    assert [it["label"] for it in out["issues"]] == ["#1", "#2", "#3"]
    assert [it["chapter_index"] for it in out["issues"]] == [1, 2, 3]
    assert all(it["plot_summary"] for it in out["issues"])
    # merged top-level plot_summary concatenates issue plots in order
    assert out["plot_summary"].index("Issue 1") < out["plot_summary"].index("Issue 3")
    # persisted
    saved = json.loads((tmp_path / "comic_context.json").read_text())
    assert saved["issue_count"] == 3


def test_enrich_issues_n1_emits_single_comic_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "fetch_fandom", _fake_fandom, raising=False)
    ctx = {"title": "Solo", "publisher": "DC"}
    chapters = [{"label": "#1", "reader_url": "u1", "chapter_index": 1}]
    out = um._enrich_issues(ctx, chapters, project_root=tmp_path, log=lambda m: None)

    assert "is_arc" not in out
    assert "issues" not in out
    assert out["plot_summary"].startswith("Issue 1")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_saga_context.py -v`
Expected: FAIL with `AttributeError: module 'stages.stage_2.url_mode' has no attribute '_enrich_issues'`

- [ ] **Step 3: Implement `_enrich_issues` in `stages/stage_2/url_mode.py`**

Add this function (place it right after `_enrich_context_silent`). It reuses the SAME fandom→SDK fallback chain that `_enrich_context_silent` uses, but per issue. Import `fetch_fandom` at module level so the test can monkeypatch `um.fetch_fandom` (add `from stages.stage_1.tools.fetch_fandom import fetch_fandom` near the top of the file, guarded the same way the inline import is today).

```python
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
            fd = fetch_fandom(q, publisher=publisher)
            if isinstance(fd, dict):
                plot = (fd.get("plot_text") or "").strip()
                wiki_url = fd.get("wiki_url") or ""
        except Exception as exc:
            log(f"[saga] fandom miss {label}: {exc}")
        from config import ENABLE_SDK_PLOT_FALLBACK, SDK_PLOT_FALLBACK_MIN_CHARS
        if ENABLE_SDK_PLOT_FALLBACK and len(plot) < SDK_PLOT_FALLBACK_MIN_CHARS:
            try:
                from stages.stage_1.tools.gather_plot_sdk import gather_plot_sdk
                res = gather_plot_sdk(base_title, label, publisher, log=log)
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
        # N==1 → today's single-comic shape (no arc keys).
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

    # Build summary.story_arc/characters from the merged plot (reuse Stage 1 tool).
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_saga_context.py -v`
Expected: PASS (2 passed). (`enrich_with_summary` may no-op without an LLM client in the test — that's fine; the assertions don't depend on it.)

- [ ] **Step 5: Commit**

```bash
git add stages/stage_2/url_mode.py tests/test_saga_context.py
git commit -m "feat(saga): per-issue context enrich + merged arc context"
```

---

### Task 4: `download_saga` entry — resolve ≤N chapters, download, per-issue enrich

**Files:**
- Modify: `stages/stage_2/url_mode.py` (add `download_saga`, reusing `resolve_chapters` + `_run_downloads`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_saga_context.py`:
```python
def test_download_saga_caps_issues_and_enriches(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "fetch_fandom", _fake_fandom, raising=False)
    # 7 chapters available; max_issues=5 → only 5 ingested
    monkeypatch.setattr(um, "resolve_chapters",
        lambda url, issues: [{"label": f"#{i}", "reader_url": f"u{i}", "chapter_index": i}
                             for i in range(1, 8)], raising=False)
    monkeypatch.setattr(um, "_run_downloads",
        lambda proj, root, chapters, log: {"chapters": len(chapters), "total_pages": 22 * len(chapters)},
        raising=False)
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path, raising=False)
    monkeypatch.setattr(um, "get_project_dirs", lambda name: {"root": tmp_path}, raising=False)

    res = um.download_saga("saga_proj", "https://batcave.biz/123-saga.html", max_issues=5,
                           progress=lambda m: None)
    ctx = json.loads((tmp_path / "comic_context.json").read_text())
    assert ctx["issue_count"] == 5
    assert res["chapters"] == 5
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_saga_context.py::test_download_saga_caps_issues_and_enriches -v`
Expected: FAIL with `AttributeError: ... has no attribute 'download_saga'`

- [ ] **Step 3: Implement `download_saga`**

Add to `stages/stage_2/url_mode.py`. It mirrors `download_from_series` but (a) caps chapters to `max_issues`, (b) enriches PER ISSUE, (c) downloads only the capped chapters. Reuses `resolve_chapters`, `_ensure_project_root`, `_write_minimal_context`, `_run_downloads`.

```python
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
    from .issue_resolver import resolve_chapters
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_saga_context.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add stages/stage_2/url_mode.py tests/test_saga_context.py
git commit -m "feat(saga): download_saga entry — cap issues, per-issue enrich, download"
```

---

### Task 5: Stage 3 — arc-aware beat allocation in `outline_beats`

**Files:**
- Modify: `stages/stage_3/write_script.py` (`outline_beats`)
- Test: `tests/test_saga_outline.py`

- [ ] **Step 1: Write the failing test (page→issue grouping used by outline)**

Create `tests/test_saga_outline.py`:
```python
from stages._arc import issue_index_of_page, allocate_beats_across_issues


def test_outline_inputs_group_pages_by_issue():
    pages = [
        {"page_number": 1, "source_image": "/r/ch01_page_01.jpg"},
        {"page_number": 2, "source_image": "/r/ch01_page_02.jpg"},
        {"page_number": 3, "source_image": "/r/ch02_page_01.jpg"},
    ]
    by_issue = {}
    for p in pages:
        by_issue.setdefault(issue_index_of_page(p), []).append(p["page_number"])
    assert by_issue == {1: [1, 2], 2: [3]}
    alloc = allocate_beats_across_issues(20, 2, [2, 1])
    assert sum(alloc.values()) == 20 and alloc[1] >= 2 and alloc[2] >= 2
```

- [ ] **Step 2: Run to verify it passes (helpers already exist)**

Run: `pytest tests/test_saga_outline.py -v`
Expected: PASS (1 passed) — this locks the contract the outline code below relies on.

- [ ] **Step 3: Add the arc branch to `outline_beats`**

In `stages/stage_3/write_script.py`, at the TOP of `outline_beats` (after `plot`/`arc` are read), add an arc-aware instruction block that tells the outliner to cover every issue and anchor beats to each issue's page range. Insert after the `canonical_block` is built (around the existing `page_nums = sorted(...)` line):

```python
    # ── Crossover-saga: spread beats across issues, anchor to each issue's pages ──
    if comic_context.get("is_arc") and comic_context.get("issues"):
        from stages._arc import issue_index_of_page, allocate_beats_across_issues
        issues = comic_context["issues"]
        n_iss = len(issues)
        by_issue: dict[int, list[int]] = {}
        for p in story_pages:
            by_issue.setdefault(issue_index_of_page(p), []).append(int(p.get("page_number", 0) or 0))
        page_counts = [len(by_issue.get(it["chapter_index"], [])) for it in issues]
        # total beat budget = the same upper band the single-comic outline targets
        alloc = allocate_beats_across_issues(total=20, n_issues=n_iss, page_counts=page_counts)
        arc_lines = []
        for it in issues:
            k = it["chapter_index"]
            pgs = sorted(by_issue.get(k, []))
            rng = f"pages {pgs[0]}-{pgs[-1]}" if pgs else "(no pages)"
            arc_lines.append(
                f"  • {it['label']} ({rng}): write ~{alloc.get(k, 2)} beat(s). "
                f"Plot: {(it.get('plot_summary') or '')[:600]}")
        canonical_block += (
            "\n╔══ MULTI-ISSUE SAGA — COVER EVERY ISSUE IN ORDER ══╗\n"
            "This is a crossover of sequential issues. Allocate beats so EACH issue is\n"
            "represented and every beat's page_refs fall INSIDE that issue's page range:\n"
            + "\n".join(arc_lines) + "\n\n"
        )
```

This appends to the existing `canonical_block` that already feeds the outline prompt — no other change to the prompt assembly is required.

- [ ] **Step 4: Smoke-test the branch is reached (no LLM)**

Run:
```bash
python -c "
from stages.stage_3 import write_script as w
import inspect
assert 'is_arc' in inspect.getsource(w.outline_beats)
print('arc branch present')
"
```
Expected: `arc branch present`

- [ ] **Step 5: Commit**

```bash
git add stages/stage_3/write_script.py tests/test_saga_outline.py
git commit -m "feat(saga): Stage 3 outline spreads beats across issues + anchors page ranges"
```

---

### Task 6: Stage 3 — per-issue wiki cross-check

**Files:**
- Modify: `stages/stage_3/write_script.py` (`_wiki_cross_check`)

- [ ] **Step 1: Read the current `_wiki_cross_check` head**

It reads `plot = comic_context.get("plot_summary")` and `arc = comic_context["summary"]["story_arc"]`, builds `wiki_text` (capped 6000), and checks the whole narration against it.

- [ ] **Step 2: Add per-issue grounding text when `is_arc`**

Replace the `wiki_text` construction so, for an arc, EACH issue's plot is labelled in the cross-check reference (the checker already reasons per-claim; labelling by issue lets it match a beat to the right issue's canon). Change the block that builds `wiki_text`:

```python
    plot = (comic_context.get("plot_summary") or "").strip()
    arc = (comic_context.get("summary", {}) or {}).get("story_arc", "").strip()
    if comic_context.get("is_arc") and comic_context.get("issues"):
        # Per-issue canon: label each issue so a beat is checked against ITS issue.
        parts = []
        for it in comic_context["issues"]:
            p = (it.get("plot_summary") or "").strip()
            if p:
                parts.append(f"=== {it['label']} (canon) ===\n{p}")
        wiki_text = "\n\n".join(parts)[:8000] if parts else (arc + "\n\n" + plot)[:6000]
    else:
        if not plot and not arc:
            log("[stage4]   phase E: no wiki plot_summary available — skipping cross-check")
            return []
        wiki_text = ((arc + "\n\n" + plot) if arc else plot)[:6000]
```

Keep the rest of `_wiki_cross_check` (the prompt that consumes `wiki_text`, the false-positive suppression, the return value) unchanged.

- [ ] **Step 3: Smoke-test it imports + branch present**

Run:
```bash
python -c "
from stages.stage_3 import write_script as w
import inspect
src = inspect.getsource(w._wiki_cross_check)
assert 'is_arc' in src and '(canon)' in src
print('per-issue wiki-check present')
"
```
Expected: `per-issue wiki-check present`

- [ ] **Step 4: Commit**

```bash
git add stages/stage_3/write_script.py
git commit -m "feat(saga): Stage 3 wiki cross-check grounds each beat per issue"
```

---

### Task 7: CLI wiring + N=1 regression guard

**Files:**
- Modify: `stages/stage_2/cli.py` (add a `--saga <series_url>` + `--max-issues` path that calls `download_saga`)
- Test: `tests/test_saga_context.py` (add the N=1 regression already covered in Task 3; add a CLI-routing assertion)

- [ ] **Step 1: Read `stages/stage_2/cli.py`**

Find where url-mode entry points (`download_from_series` / `download_from_readers`) are dispatched from CLI args. Mirror that wiring for saga.

- [ ] **Step 2: Add the `--saga` route**

In `stages/stage_2/cli.py`'s argument parser add:
```python
    parser.add_argument("--saga", type=str, default=None,
                        help="batcave SERIES url → crossover-saga mode (≤--max-issues issues, one Short)")
    parser.add_argument("--max-issues", type=int, default=5,
                        help="cap issues ingested in --saga mode (default 5)")
```
And in the dispatch body, BEFORE the existing series/reader handling:
```python
    if args.saga:
        from .url_mode import download_saga
        res = download_saga(args.project, args.saga, max_issues=args.max_issues, progress=print)
        print(f"[saga] ingested {res.get('issue_count', '?')} issue(s), "
              f"{res.get('total_pages', '?')} pages")
        return
```

- [ ] **Step 3: Add the N=1 regression test**

Append to `tests/test_saga_context.py`:
```python
def test_saga_n1_matches_single_comic_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "fetch_fandom", _fake_fandom, raising=False)
    ctx = {"title": "Solo", "publisher": "DC"}
    out = um._enrich_issues(ctx, [{"label": "#1", "reader_url": "u", "chapter_index": 1}],
                            project_root=tmp_path, log=lambda m: None)
    # Must look like a single-comic context: has plot_summary, NO arc keys.
    for k in ("is_arc", "issues", "issue_count"):
        assert k not in out
    assert isinstance(out.get("plot_summary"), str) and out["plot_summary"]
```

- [ ] **Step 4: Run the full saga test file**

Run: `pytest tests/test_saga_context.py tests/test_arc.py tests/test_saga_outline.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add stages/stage_2/cli.py tests/test_saga_context.py
git commit -m "feat(saga): CLI --saga route + N=1 single-comic regression guard"
```

---

### Task 8: End-to-end verification on a real saga

**Files:** none (verification only)

- [ ] **Step 1: Pick a real ≤5-issue saga series URL on batcave** (e.g. a "What If...? Dark" or a short mini-series). Record it.

- [ ] **Step 2: Ingest (Stage 1/2)**

Run: `python -m stages.stage_2 --project saga_test --saga "<series_url>" --max-issues 5`
Expected: logs `[saga] '<title>': N chapter(s) available, ingesting K`; `comic_context.json` has `is_arc: true`, `issue_count: K`, `issues[]` length K; `raw_comic/` has `ch01_page_*`…`ch0K_page_*`.

- [ ] **Step 3: Stage 2 VLM (reused, unchanged)**

Run: `TOKENIZERS_PARALLELISM=false python -m stages.stage_2 --project saga_test --force`
Expected: `✓ Stage 2 complete` over all K issues' pages; `cluster_to_name.json` written.

- [ ] **Step 4: Stage 3 (arc branch)**

Run: `python -m stages.stage_3 --project saga_test --mode panel_walk`
Expected: logs show beats spanning every issue (`MULTI-ISSUE SAGA` block was sent); `phase E: ... ✓`; `narration.json` scenes reference pages from each `ch{NN}`.

- [ ] **Step 5: Stage 4 + Stage 5 (reused) + benchmark**

Run (separate processes — never chain stage_4 && stage_5 in one shell; see CLAUDE notes):
```bash
TOKENIZERS_PARALLELISM=false python -m stages.stage_4 --project saga_test --force
TOKENIZERS_PARALLELISM=false python -m stages.stage_5 --project saga_test --force
TOKENIZERS_PARALLELISM=false python research/scripts/benchmark_score.py "projects/saga_test/final.mp4" --label saga_test --no-vlm
```
Expected: benchmark QUALIFIED; manually confirm each issue appears and the Short reads as one continuous story.

- [ ] **Step 6: N=1 regression (single-comic unchanged)**

Run an existing single-comic project through both paths and diff the narration structure:
```bash
python -m stages.stage_2 --project saga_n1 --saga "<series_url_with_1_chapter_or_max-issues_1>" --max-issues 1
python -c "import json; c=json.load(open('projects/saga_n1/comic_context.json')); assert 'is_arc' not in c; print('N=1 single-comic shape OK')"
```
Expected: `N=1 single-comic shape OK`.

- [ ] **Step 7: Delete throwaway project + commit nothing** (verification task produces no code).

---

## Self-Review

**1. Spec coverage**
- Mode + isolation → Task 1 (enum) + Task 7 (CLI route); `narrate_1_comic` untouched (no edits to its path). ✓
- Series URL → list chapters → download first N → Task 4 (`download_saga` caps + reuses `resolve_chapters`/`_run_downloads`). ✓
- Per-issue context (hard requirement) → Task 3 (`_enrich_issues`). ✓
- Merged arc context shape (`is_arc/issues[]/plot_summary`) → Task 3. ✓
- N=1 = today's behaviour → Task 3 (shape) + Task 7 (regression test) + Task 8 step 6 (E2E). ✓
- Stage 2/4/5 reused unchanged → no tasks modify them; Task 8 runs them as-is. ✓
- Stage 3 beat allocation across issues → Task 2 (math) + Task 5 (outline branch). ✓
- Stage 3 per-issue wiki cross-check → Task 6. ✓
- Short 60–90s (unchanged bands) → outline still targets total≈20 beats; no band change. ✓

**2. Placeholder scan** — no TBD/TODO; every code step has concrete code; commands have expected output. ✓

**3. Type consistency** — `chapter_index` (int, 1-based) is the join key everywhere: written by `download_saga` normalization, stored in `issues[].chapter_index`, parsed from `ch{NN}_page` by `issue_index_of_page`, grouped in Task 5. `allocate_beats_across_issues(total, n_issues, page_counts)` signature identical in Task 2 def and Tasks 5 call. `_enrich_issues(ctx, chapters, *, project_root, log)` identical in Task 3 def and Task 4 call. ✓

## Execution note on commits
`docs/` is gitignored in this repo but specs/plans are tracked via `git add -f` (see prior committed specs). The plan's own commits touch real source under `stages/` and `tests/` (not ignored) — normal `git add` works for those.
