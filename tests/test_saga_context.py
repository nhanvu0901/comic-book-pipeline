import json
from pathlib import Path

import pytest

import stages.stage_2.url_mode as um


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Keep these unit tests offline + fast: never spawn the real Claude SDK
    plot fallback, and make the summarize step a no-op (it would otherwise hit
    the LLM). Assertions don't depend on either."""
    monkeypatch.setattr("config.ENABLE_SDK_PLOT_FALLBACK", False, raising=False)
    monkeypatch.setattr(
        "stages.stage_1.tools.summarize_context.enrich_with_summary",
        lambda ctx, progress=None: None, raising=False)


def _fake_fandom(query, publisher=""):
    # distinct synopsis per issue so the merge order is observable; ≥600 chars so
    # the SDK plot fallback never triggers even if the flag were on.
    n = query.strip()[-1]
    return {"plot_text": f"Issue {n} synopsis. " * 40, "wiki_url": f"http://w/{n}", "title": f"Saga #{n}"}


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
    assert out["plot_summary"].index("Issue 1") < out["plot_summary"].index("Issue 3")
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
