"""
Gap 4 + Gap 6: two multi-issue entry paths used to skip arc handling —

  * download_from_series / download_from_readers called the single-issue
    _enrich_context_silent even when they downloaded >1 chapter, so Stage 3
    never saw is_arc/issue_count/issues[] unless you went through --saga.
  * The CLI's --saga only accepted ONE series URL; N --reader-urls always
    routed to the non-arc download_from_readers, so download_saga_from_readers
    (which the UI already reaches, see ui/bridge.py) was unreachable from CLI.

These tests pin: >1 chapter → _enrich_issues (arc context); ==1 chapter →
today's single-comic behavior unchanged; --saga + N reader URLs → routes to
download_saga_from_readers. No network: same fixture approach as
tests/test_saga_context.py (fake fetch_fandom, no-op summarize).
"""
import json
import sys

import pytest

import stages.stage_2.url_mode as um


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Keep these unit tests offline + fast — see tests/test_saga_context.py."""
    monkeypatch.setattr("config.ENABLE_SDK_PLOT_FALLBACK", False, raising=False)
    monkeypatch.setattr(
        "stages.stage_1.tools.summarize_context.enrich_with_summary",
        lambda ctx, progress=None: None, raising=False)
    # download_from_readers/download_saga_from_readers hit batcave's reader
    # __DATA__ for the canonical title — stub it so tests never touch the network.
    monkeypatch.setattr(um, "_chapter_meta_from_reader",
        lambda url, log: {"title": "Test Saga", "issues": "", "year": "", "source_title": ""},
        raising=False)
    # Never actually scrape/download pages.
    monkeypatch.setattr(um, "_run_downloads",
        lambda proj, root, chapters, log: {
            "context_path": "c", "manifest_path": "m",
            "total_pages": 22 * len(chapters), "chapters": len(chapters)},
        raising=False)


def _fake_fandom(query, publisher=""):
    n = query.strip()[-1]
    return {"plot_text": f"Issue {n} synopsis. " * 40, "wiki_url": f"http://w/{n}", "title": f"Saga #{n}"}


def _use_tmp_project(monkeypatch, tmp_path):
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path, raising=False)
    monkeypatch.setattr(um, "get_project_dirs", lambda name: {"root": tmp_path}, raising=False)


def test_download_from_readers_multi_url_builds_arc_context(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "fetch_fandom", _fake_fandom, raising=False)
    _use_tmp_project(monkeypatch, tmp_path)

    urls = ["https://batcave.biz/reader/100/1", "https://batcave.biz/reader/100/2"]
    um.download_from_readers("mega", urls, enrich=True)

    ctx = json.loads((tmp_path / "comic_context.json").read_text())
    assert ctx["is_arc"] is True
    assert ctx["issue_count"] == 2
    assert len(ctx["issues"]) == 2


def test_download_from_readers_single_url_stays_single_comic(tmp_path, monkeypatch):
    # Force _enrich_context_silent's early-return branch (no API key configured)
    # instead of hitting the real Stage 1 LLM/wiki chain — keeps this hermetic
    # while still exercising the real single-issue code path (not a mock).
    monkeypatch.setattr("config.OPENROUTER_API_KEY", "", raising=False)
    _use_tmp_project(monkeypatch, tmp_path)

    urls = ["https://batcave.biz/reader/100/1"]
    um.download_from_readers("solo", urls, enrich=True)

    ctx = json.loads((tmp_path / "comic_context.json").read_text())
    # "issues" itself is always present as the issues-string field (e.g.
    # "chap_1") — only is_arc/issue_count, and "issues" becoming a per-issue
    # LIST, would mean this got routed through the arc path.
    assert "is_arc" not in ctx
    assert "issue_count" not in ctx
    assert isinstance(ctx["issues"], str)


def test_download_from_series_multi_chapter_builds_arc_context(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "fetch_fandom", _fake_fandom, raising=False)
    _use_tmp_project(monkeypatch, tmp_path)
    monkeypatch.setattr(um, "resolve_chapters",
        lambda url, issues: [{"label": f"#{i}", "reader_url": f"u{i}"} for i in range(1, 4)],
        raising=False)

    um.download_from_series("venom", "https://batcave.biz/6587-venom.html", "#1-3", enrich=True)

    ctx = json.loads((tmp_path / "comic_context.json").read_text())
    assert ctx["is_arc"] is True
    assert ctx["issue_count"] == 3


def test_download_from_series_single_chapter_stays_single_comic(tmp_path, monkeypatch):
    monkeypatch.setattr("config.OPENROUTER_API_KEY", "", raising=False)
    _use_tmp_project(monkeypatch, tmp_path)
    monkeypatch.setattr(um, "resolve_chapters",
        lambda url, issues: [{"label": "#1", "reader_url": "u1"}], raising=False)

    um.download_from_series("venom", "https://batcave.biz/6587-venom.html", "#1", enrich=True)

    ctx = json.loads((tmp_path / "comic_context.json").read_text())
    assert "is_arc" not in ctx


def test_cli_saga_with_multiple_reader_urls_routes_to_saga_from_readers(monkeypatch):
    import stages.stage_2.cli as cli

    calls = {}

    def fake_saga_from_readers(project, urls, progress=None):
        calls["urls"] = urls
        return {"context_path": "c", "manifest_path": "m", "total_pages": 44,
                "chapters": len(urls), "issue_count": len(urls)}

    def fail_if_called(*a, **k):
        pytest.fail("--saga with N reader URLs must not call the series form")

    monkeypatch.setattr(cli, "download_saga_from_readers", fake_saga_from_readers, raising=False)
    monkeypatch.setattr(cli, "download_saga", fail_if_called, raising=False)

    urls = ["https://batcave.biz/reader/100/1", "https://batcave.biz/reader/100/2"]
    monkeypatch.setattr(sys, "argv",
        ["stage_2", "--project", "proj", "--saga", *urls, "--download-only"])

    cli.main()

    assert calls["urls"] == urls


def test_cli_saga_with_single_series_url_still_uses_download_saga(monkeypatch):
    import stages.stage_2.cli as cli

    calls = {}

    def fake_saga(project, series_url, max_issues=5, progress=None):
        calls["series_url"] = series_url
        return {"context_path": "c", "manifest_path": "m", "total_pages": 22,
                "chapters": 1, "issue_count": 1}

    def fail_if_called(*a, **k):
        pytest.fail("single --saga series URL must not call the reader-URL form")

    monkeypatch.setattr(cli, "download_saga", fake_saga, raising=False)
    monkeypatch.setattr(cli, "download_saga_from_readers", fail_if_called, raising=False)

    url = "https://batcave.biz/6587-what-if-dark-venom-2023.html"
    monkeypatch.setattr(sys, "argv",
        ["stage_2", "--project", "proj", "--saga", url, "--download-only"])

    cli.main()

    assert calls["series_url"] == url
