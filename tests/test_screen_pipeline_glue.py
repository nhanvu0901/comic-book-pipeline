"""Tests for stages/screen_pipeline.py CLI orchestrator (Task 2)."""
import json
import pytest

import stages.screen_pipeline as sp


def _patch_all_steps(monkeypatch, calls):
    for step in sp.STEPS:
        monkeypatch.setattr(
            sp, f"_step_{step}",
            (lambda name: lambda args, log: calls.append(name) or f"{name} ok")(step),
        )


def test_steps_definition():
    assert sp.STEPS == ["research", "narrate", "tts", "render"]
    assert "download" not in sp.STEPS
    assert "preprocess" not in sp.STEPS


def test_stop_after_research_short_circuits(monkeypatch):
    calls = []
    _patch_all_steps(monkeypatch, calls)
    rc = sp.main([
        "--question", "How did the Avengers travel back in time in Endgame?",
        "--project", "endgame_time_travel",
        "--stop-after", "research",
    ])
    assert rc == 0
    assert calls == ["research"]


def test_stop_after_narrate(monkeypatch):
    calls = []
    _patch_all_steps(monkeypatch, calls)
    rc = sp.main([
        "--question", "How did the Avengers travel back in time in Endgame?",
        "--project", "endgame_time_travel",
        "--stop-after", "narrate",
    ])
    assert rc == 0
    assert calls == ["research", "narrate"]


def test_full_run_calls_all_steps(monkeypatch):
    calls = []
    _patch_all_steps(monkeypatch, calls)
    rc = sp.main([
        "--question", "How did the Avengers travel back in time in Endgame?",
        "--project", "endgame_time_travel",
    ])
    assert rc == 0
    assert calls == ["research", "narrate", "tts", "render"]


def test_skip_research(monkeypatch):
    calls = []
    _patch_all_steps(monkeypatch, calls)
    rc = sp.main([
        "--project", "endgame_time_travel",
        "--skip-research",
        "--stop-after", "narrate",
    ])
    assert rc == 0
    assert calls == ["narrate"]


def test_step_failure_reports_fail_status(monkeypatch, capsys):
    calls = []
    _patch_all_steps(monkeypatch, calls)

    def _fail_research(args, log):
        raise RuntimeError("Research service timeout")

    monkeypatch.setattr(sp, "_step_research", _fail_research)

    rc = sp.main([
        "--question", "How did the Avengers travel back in time in Endgame?",
        "--project", "endgame_time_travel",
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "step=research status=fail" in out
    assert "Research service timeout" in out
    assert "narrate" not in calls


def test_question_required_unless_skip_research():
    args = sp._parse_args(["--project", "test_proj"])
    with pytest.raises(ValueError, match="--question is required"):
        sp._step_research(args, print)


def test_render_stub_contract(monkeypatch):
    args = sp._parse_args(["--project", "test_proj", "--skip-research"])
    res = sp._step_render(args, print)
    assert "stubbed screen_qa render" in res
