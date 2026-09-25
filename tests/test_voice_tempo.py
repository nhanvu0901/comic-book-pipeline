"""The reading pace comes from config (POST_ATEMPO for Shorts, POST_ATEMPO_LONGFORM for
longform), set per machine in .env.

The UI's Synthesize button and Stage 7's re-render hard-coded atempo 1.35 — written before
config split the pace by format — so the server's POST_ATEMPO=1.15 was ignored, and a
longform narration was read at the Shorts race pace config warns against."""
import sys
import types

import stages.stage_4.cli as s4_cli
import stages.stage_4.pipeline as s4_pipeline
import ui.bridge as bridge


def _fake_result():
    return types.SimpleNamespace(
        to_dict=lambda: {}, audio_path="a.wav", audio_duration_seconds=1.0,
        scene_timings=[], caption_chunks=[], mode="explore_answer")


def test_synthesize_in_the_ui_lets_the_narration_mode_choose_the_pace(monkeypatch):
    seen = {}

    def _synth(project, **kwargs):
        seen.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(s4_pipeline, "synthesize_project", _synth)
    bridge.run_stage_4("p", None, None, lambda _m: None, provider="chatterbox")

    assert seen["post_atempo"] is None


def test_the_stage4_command_defaults_to_the_mode_pace_too(monkeypatch):
    seen = {}

    def _synth(project, **kwargs):
        seen.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(s4_cli, "synthesize_project", _synth)
    monkeypatch.setattr(sys, "argv", ["stages.stage_4", "--project", "p"])
    s4_cli.main()
    assert seen["post_atempo"] is None

    monkeypatch.setattr(sys, "argv", ["stages.stage_4", "--project", "p", "--atempo", "1.2"])
    s4_cli.main()
    assert seen["post_atempo"] == 1.2


def test_stage7_rerender_does_not_force_a_pace(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects" / "p").mkdir(parents=True)
    (tmp_path / "projects" / "p" / "final.mp4").write_bytes(b"x")
    commands = []

    class _Proc:
        stdout = []

        def __init__(self, cmd, **_kw):
            commands.append(cmd)

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", _Proc)
    bridge.run_stage6_render("p", lambda _m: None)

    stage4 = next(c for c in commands if "stages.stage_4" in c)
    assert "--atempo" not in stage4
