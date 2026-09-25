"""art_pipeline/tts.py + longform_tts.py — calm-voice knob injection."""
import json
import wave
from pathlib import Path

import pytest

from art_pipeline import tts


def test_synthesize_art_injects_calm_knobs(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "ART_PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    captured = {}

    import stages.stage_4.pipeline as s4
    monkeypatch.setattr(s4, "synthesize_project",
                        lambda name, **kw: captured.update(kw) or "RESULT")
    calm_calls = []
    monkeypatch.setattr("art_pipeline.audio_fx.apply_calm_filters",
                        lambda p, **kw: calm_calls.append(Path(p)) or p)

    out = tts.synthesize_art("proj")
    assert out == "RESULT"
    assert captured["emotion"] == tts.C.ART_VOICE_EMOTION
    assert captured["speed"] == tts.C.ART_VOICE_SPEED
    assert captured["post_atempo"] == tts.C.ART_POST_ATEMPO
    # audio did not exist → regenerated → calm frequency pass applied once
    assert calm_calls == [tmp_path / "proj" / "audio.wav"]


def test_synthesize_art_no_calm_is_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "ART_PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    captured = {}
    import stages.stage_4.pipeline as s4
    monkeypatch.setattr(s4, "synthesize_project",
                        lambda name, **kw: captured.update(kw) or "R")
    calm_calls = []
    monkeypatch.setattr("art_pipeline.audio_fx.apply_calm_filters",
                        lambda p, **kw: calm_calls.append(p))
    tts.synthesize_art("proj", calm=False)
    assert "emotion" not in captured and "post_atempo" not in captured
    assert calm_calls == []          # no frequency pass in plain mode


def test_synthesize_art_restores_projects_root_after_success(tmp_path, monkeypatch):
    """s4.PROJECTS_ROOT is a shared module attribute; leaving it pointed at
    art_projects/ after we return makes the next comic TTS call in this process
    read/write the wrong tree. Must come back to whatever it was before."""
    monkeypatch.setattr(tts, "ART_PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    import stages.stage_4.pipeline as s4
    comic_root = Path("/comic/projects")  # stand-in for PROJECTS_ROOT before the call
    monkeypatch.setattr(s4, "PROJECTS_ROOT", comic_root)
    monkeypatch.setattr(s4, "synthesize_project", lambda name, **kw: "RESULT")

    out = tts.synthesize_art("proj", calm=False)

    assert out == "RESULT"
    assert s4.PROJECTS_ROOT == comic_root


def test_synthesize_art_restores_projects_root_after_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "ART_PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    import stages.stage_4.pipeline as s4
    comic_root = Path("/comic/projects")
    monkeypatch.setattr(s4, "PROJECTS_ROOT", comic_root)

    def _boom(name, **kw):
        raise RuntimeError("synthesis failed")
    monkeypatch.setattr(s4, "synthesize_project", _boom)

    with pytest.raises(RuntimeError):
        tts.synthesize_art("proj", calm=False)

    assert s4.PROJECTS_ROOT == comic_root


def test_synthesize_art_reuse_skips_calm(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "ART_PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / "audio.wav").write_bytes(b"x")          # already exists, not force
    import stages.stage_4.pipeline as s4
    monkeypatch.setattr(s4, "synthesize_project", lambda name, **kw: "R")
    calm_calls = []
    monkeypatch.setattr("art_pipeline.audio_fx.apply_calm_filters",
                        lambda p, **kw: calm_calls.append(p))
    tts.synthesize_art("proj")                       # reuse path
    assert calm_calls == []          # never double-apply onto a reused WAV


def test_longform_passes_calm_knobs_and_shapes_final(tmp_path, monkeypatch):
    from art_pipeline import longform_tts as lf
    root = tmp_path / "proj"; root.mkdir()
    monkeypatch.setattr(lf, "get_art_project_path", lambda name: root)
    scenes = [{"scene_id": 1, "text": "a", "page_ref": 1, "panel_ref": -1,
               "chapter_id": 1, "is_intro": True, "is_outro": False,
               "word_count": 1, "target_seconds": 1.0, "connective": None,
               "beat_id": 1}]
    (root / "narration.json").write_text(json.dumps(
        {"mode": "painting_story", "title": "T", "hook": "h", "scenes": scenes,
         "total_word_count": 1, "estimated_duration_seconds": 1.0,
         "words_per_second": 2.88, "source_project": "proj", "llm_model": "m"}))
    (root / "chapters.json").write_text(json.dumps(
        [{"chapter_id": 1, "title": "C", "role": "cold_open",
          "scene_ids": [1], "start": None}]))

    captured = {}

    def fake_synth(project_name, **kw):
        captured.update(kw)
        import stages.stage_4.pipeline as s4
        d = Path(s4.PROJECTS_ROOT) / project_name
        with wave.open(str(d / "audio.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
            w.writeframes(b"\x01\x00" * 44100)
        (d / "scene_timings.json").write_text(json.dumps(
            [{"scene_id": 1, "start": 0.0, "end": 1.0}]))
        (d / "word_timestamps.json").write_text(json.dumps(
            [{"word": "a", "start": 0.1, "end": 0.2}]))
    monkeypatch.setattr("stages.stage_4.pipeline.synthesize_project", fake_synth)
    shaped = []
    monkeypatch.setattr("art_pipeline.audio_fx.apply_calm_filters",
                        lambda p, **kw: shaped.append(Path(p)))

    lf.synthesize_longform("proj", calm=True, log=lambda *_: None)
    assert captured["emotion"] == lf.C.ART_VOICE_EMOTION
    assert captured["post_atempo"] == lf.C.ART_POST_ATEMPO
    assert shaped == [root / "audio.wav"]            # final stitched WAV shaped once
