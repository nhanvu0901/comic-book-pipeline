"""Tests for the explore_answer (Q&A) pipeline glue: the identity-repair guard
(stages/stage_2/pipeline.py), the reader-only download wrapper
(stages/stage_2/url_mode.py), and the answer_pipeline CLI orchestrator
(stages/answer_pipeline.py). No network, no render, no real SDK — everything
that touches an external comic source or the SDK is monkeypatched."""
import json

import pytest

import stages.answer_pipeline as ap
import stages.stage_2.pipeline as pipe
import stages.stage_2.url_mode as um


# ── (a) identity-repair guard: plot_source == "answer_research" short-circuits ──


def test_identity_repair_skips_on_answer_research_plot_source(tmp_path, monkeypatch):
    project_root = tmp_path
    ctx_path = project_root / "comic_context.json"
    # identity_suspect=True + plot_status=OK would normally be enough to trigger a
    # rebuild (see tests/test_identity_rebuild.py) — setting it here proves the new
    # guard is an EARLY return for this plot_source, not just "nothing to trigger on".
    ctx_path.write_text(json.dumps({
        "plot_summary": "Q&A: Who has survived the Penance Stare?\n1. Ghost Rider — ...",
        "plot_source": "answer_research",
        "plot_status": "OK",
        "identity_suspect": True,
    }))
    calls = []
    monkeypatch.setattr(
        "stages.stage_2.identity_check.rebuild_plot_from_panels",
        lambda *a, **k: calls.append(1) or True,
    )
    pipe._run_identity_repair(project_root, [], lambda _m: None)
    assert not calls
    # ctx is untouched (identity_suspect not popped, no rewrite happened)
    saved = json.loads(ctx_path.read_text())
    assert saved["identity_suspect"] is True


def test_identity_repair_still_fires_for_other_plot_sources(tmp_path, monkeypatch):
    # Sanity check the guard is scoped to "answer_research" only — a plot_status
    # of MISSING for any OTHER plot_source must still trigger rebuild as before.
    project_root = tmp_path
    (project_root / "comic_context.json").write_text(
        json.dumps({"plot_summary": "", "plot_status": "MISSING"}))
    calls = []
    monkeypatch.setattr(
        "stages.stage_2.identity_check.rebuild_plot_from_panels",
        lambda *a, **k: calls.append(1) or True,
    )
    pipe._run_identity_repair(project_root, [], lambda _m: None)
    assert len(calls) == 1


# ── (b) download_readers_only: ordered chapters, no enrich, ctx untouched ──────


def test_download_readers_only_builds_ordered_chapters_no_enrich(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path, raising=False)

    def _fake_scrape(reader_url, project_root, chapter_index):
        return [f"{project_root}/p{chapter_index}.png"]

    monkeypatch.setattr(um, "scrape_issue_pages", _fake_scrape, raising=False)
    enrich_calls = []
    monkeypatch.setattr(
        um, "_enrich_issues", lambda *a, **k: enrich_calls.append(1), raising=False)

    sentinel = {"title": "sentinel", "plot_source": "answer_research", "untouched": True}
    (tmp_path / "comic_context.json").write_text(json.dumps(sentinel))

    urls = [
        "https://batcave.biz/reader/111/222",
        "https://batcave.biz/reader/333/444",
        "https://batcave.biz/reader/555/666",
    ]
    result = um.download_readers_only("gr_penance", urls, progress=lambda m: None)

    assert not enrich_calls  # never touches enrichment
    assert result["chapters"] == 3
    manifest = json.loads((tmp_path / "raw_comic" / "manifest.json").read_text())
    assert [m["chapter_index"] for m in manifest] == [1, 2, 3]
    assert [m["reader_url"] for m in manifest] == urls

    still = json.loads((tmp_path / "comic_context.json").read_text())
    assert still == sentinel  # byte-identical: download_readers_only wrote nothing to it


def test_download_readers_only_rejects_non_reader_url(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path, raising=False)
    with pytest.raises(ValueError, match="reader URL"):
        um.download_readers_only(
            "gr_penance", ["https://batcave.biz/123-some-series.html"], progress=lambda m: None)


# ── (c) answer_pipeline CLI: arg parsing + --stop-after short-circuits ─────────


def _patch_all_steps(monkeypatch, calls):
    for step in ap.STEPS:
        monkeypatch.setattr(
            ap, f"_step_{step}",
            (lambda name: lambda args, log: calls.append(name) or f"{name} ok")(step))


def test_stop_after_research_short_circuits(monkeypatch):
    calls = []
    _patch_all_steps(monkeypatch, calls)
    rc = ap.main(["--question", "Who survived the Penance Stare?",
                  "--project", "gr_penance", "--stop-after", "research"])
    assert rc == 0
    assert calls == ["research"]  # download/preprocess/narrate/tts/render never called


def test_full_run_calls_every_step_in_order(monkeypatch):
    calls = []
    _patch_all_steps(monkeypatch, calls)
    rc = ap.main(["--question", "Who survived the Penance Stare?", "--project", "gr_penance"])
    assert rc == 0
    assert calls == ap.STEPS


def test_skip_flags_bypass_research_and_download_without_calling_them(monkeypatch):
    calls = []
    _patch_all_steps(monkeypatch, calls)
    rc = ap.main(["--project", "gr_penance", "--skip-research", "--skip-download",
                  "--stop-after", "preprocess"])
    assert rc == 0
    assert calls == ["preprocess"]  # research/download skipped, not invoked


def test_step_failure_reports_fail_and_stops(monkeypatch, capsys):
    calls = []
    _patch_all_steps(monkeypatch, calls)

    def _boom(args, log):
        raise RuntimeError("SDK unavailable")
    monkeypatch.setattr(ap, "_step_research", _boom)

    rc = ap.main(["--question", "Q?", "--project", "gr_penance"])
    assert rc == 1
    assert not calls  # download etc. never reached after research fails
    out = capsys.readouterr().out
    assert "step=research status=fail" in out
    assert "SDK unavailable" in out


def test_question_required_unless_skip_research():
    with pytest.raises(ValueError, match="--question is required"):
        ap._step_research(ap._parse_args(["--project", "gr_penance"]), print)
