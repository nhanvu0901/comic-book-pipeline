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


def test_skip_narrate_resumes_at_tts_without_rewriting_the_approved_narration(monkeypatch):
    """Narration is approved in the review gate by SHA; re-running narrate would rewrite
    narration.json and make the approval stale, so a resumed run must be able to skip it."""
    calls = []
    _patch_all_steps(monkeypatch, calls)
    rc = sp.main(["--project", "endgame_time_travel", "--skip-research", "--skip-narrate"])
    assert rc == 0
    assert calls == ["tts", "render"]


def test_start_at_runs_from_that_step_on(monkeypatch, capsys):
    """Re-render after picking clips must not re-run TTS (force-regenerates audio); re-TTS after a
    narration approval must not rewrite the narration."""
    calls = []
    _patch_all_steps(monkeypatch, calls)
    assert sp.main(["--project", "p", "--start-at", "render"]) == 0
    assert calls == ["render"]
    out = capsys.readouterr().out
    assert "step=research status=ok detail=skipped" in out and "step=tts status=ok detail=skipped" in out
    calls.clear()
    assert sp.main(["--project", "p", "--start-at", "tts", "--stop-after", "tts"]) == 0
    assert calls == ["tts"]


def test_start_at_after_stop_after_is_rejected(monkeypatch, capsys):
    _patch_all_steps(monkeypatch, [])
    with pytest.raises(SystemExit) as exc:
        sp.main(["--project", "p", "--start-at", "render", "--stop-after", "narrate"])
    assert exc.value.code == 2
    assert "comes after --stop-after" in capsys.readouterr().err


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


def _screen_project(tmp_path, monkeypatch, mode="screen_qa"):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "narration.json").write_text(json.dumps({"mode": mode, "scenes": [{"scene_id": 1, "text": "x"}]}))
    monkeypatch.setattr(sp, "get_project_dirs", lambda p: {"root": root})
    return root


def _fake_screen_shots(monkeypatch):
    """Stand-in for video-qa/p3-visual's stages.stage_5.screen_shots (not on this branch)."""
    import sys
    import types
    mod = types.ModuleType("stages.stage_5.screen_shots")
    mod.run_screen_qa_pipeline = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "stages.stage_5.screen_shots", mod)
    return mod


def test_render_goes_through_stage5_assemble_project(tmp_path, monkeypatch):
    """Stage 5 dispatches mode=='screen_qa' to p3-visual's run_screen_qa_pipeline; the CLI must
    use that entry (review gate, narration-hash guard, audio/timing loading) — not call the shot
    builder with guessed arguments."""
    import types
    import stages.stage_5.pipeline as s5
    _screen_project(tmp_path, monkeypatch)
    _fake_screen_shots(monkeypatch)
    seen = {}

    def fake_assemble(project, *, force=False, skip_review=False, panels_only=False, progress=None):
        seen.update(project=project, force=force, skip_review=skip_review, panels_only=panels_only)
        progress("[stage5] rendering")
        return types.SimpleNamespace(final_path="/x/final.mp4", duration_seconds=51.3, shot_count=17, scene_count=7)

    monkeypatch.setattr(s5, "assemble_project", fake_assemble)
    logs = []
    args = sp._parse_args(["--project", "proj", "--skip-research", "--skip-review"])
    detail = sp._step_render(args, logs.append)
    assert seen == {"project": "proj", "force": True, "skip_review": True, "panels_only": False}
    assert "17 shot(s)" in detail and "51.3s" in detail
    assert "[stage5] rendering" in logs


def test_render_fails_clearly_when_the_screen_builder_is_missing(tmp_path, monkeypatch):
    import sys
    _screen_project(tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "stages.stage_5.screen_shots", None)     # -> ImportError
    args = sp._parse_args(["--project", "proj", "--skip-research"])
    with pytest.raises(RuntimeError, match="p3-visual"):
        sp._step_render(args, print)


def test_render_refuses_a_non_screen_narration(tmp_path, monkeypatch):
    import stages.stage_5.pipeline as s5
    _screen_project(tmp_path, monkeypatch, mode="explore_answer")
    _fake_screen_shots(monkeypatch)
    monkeypatch.setattr(s5, "assemble_project", lambda *a, **k: pytest.fail("must not render a comic narration"))
    args = sp._parse_args(["--project", "proj", "--skip-research"])
    with pytest.raises(ValueError, match="screen_qa"):
        sp._step_render(args, print)


def test_render_without_narration_is_a_clear_error(tmp_path, monkeypatch):
    root = _screen_project(tmp_path, monkeypatch)
    (root / "narration.json").unlink()
    _fake_screen_shots(monkeypatch)
    args = sp._parse_args(["--project", "proj", "--skip-research"])
    with pytest.raises(FileNotFoundError, match="narration.json"):
        sp._step_render(args, print)


def test_review_gate_block_is_reported_as_a_status_line(monkeypatch, capsys):
    """Stage 4/5 raise SystemExit while the project is unapproved; an agent driving the CLI
    still gets one machine-parseable line, exactly like answer_pipeline."""
    calls = []
    _patch_all_steps(monkeypatch, calls)

    def _blocked(args, log):
        raise SystemExit("[review-gate] BLOCKED: 'proj' is not approved.")

    monkeypatch.setattr(sp, "_step_tts", _blocked)
    rc = sp.main(["--project", "proj", "--skip-research"])
    assert rc == 1
    assert "[screen-pipeline] step=tts status=fail detail=ReviewGateBlocked" in capsys.readouterr().out
    assert calls == ["narrate"]


def test_narrate_step_flags_the_deterministic_fallback(monkeypatch):
    import stages.stage_3.screen_qa as sq
    from stages.stage_3.schema import Narration

    nar = Narration(mode="screen_qa", title="t", hook="h", scenes=[], llm_model="")
    monkeypatch.setattr(sq, "write_screen_qa", lambda *a, **k: nar)
    monkeypatch.setattr(sq, "save_screen_narration", lambda *a, **k: None)
    args = sp._parse_args(["--project", "proj", "--skip-research"])
    assert "deterministic fallback" in sp._step_narrate(args, print)
    nar.llm_model = "some-model"
    assert "deterministic fallback" not in sp._step_narrate(args, print)


def test_tts_step_uses_the_unchanged_post_atempo_default(monkeypatch):
    import config
    args = sp._parse_args(["--project", "proj", "--skip-research"])
    assert args.atempo == config.POST_ATEMPO
