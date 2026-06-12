import json
import wave
from pathlib import Path

import pytest

from art_pipeline import longform_tts
from art_pipeline.config import ART_LF_CHAPTER_GAP_S

SR = 44100


def _write_wav(path: Path, seconds: float):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"\x01\x00" * int(SR * seconds))


def _make_longform_project(tmp_path, monkeypatch, chapter_secs=(2.0, 3.0)):
    root = tmp_path / "proj"; root.mkdir()
    monkeypatch.setattr("art_pipeline.longform_tts.get_art_project_path",
                        lambda name: root)
    scenes, chapters = [], []
    sid = 0
    for ci, secs in enumerate(chapter_secs, start=1):
        ids = []
        for _ in range(2):
            sid += 1; ids.append(sid)
            scenes.append({"scene_id": sid, "text": f"scene {sid} text",
                           "page_ref": 1, "panel_ref": -1,
                           "chapter_id": ci, "is_intro": sid == 1,
                           "is_outro": False, "word_count": 3,
                           "target_seconds": 1.0, "connective": None,
                           "beat_id": sid})
        chapters.append({"chapter_id": ci, "title": f"Ch {ci}",
                         "role": "backfill", "scene_ids": ids, "start": None})
    (root / "narration.json").write_text(json.dumps(
        {"mode": "painting_story", "title": "T", "hook": "h",
         "scenes": scenes, "total_word_count": 12,
         "estimated_duration_seconds": 9.0, "words_per_second": 2.88,
         "source_project": "proj", "llm_model": "m"}))
    (root / "chapters.json").write_text(json.dumps(chapters))

    def fake_synthesize(project_name, **kwargs):
        import stages.stage_4.pipeline as s4
        ch_dir = Path(s4.PROJECTS_ROOT) / project_name
        idx = int(project_name.split("_")[1]) - 1
        secs = chapter_secs[idx]
        _write_wav(ch_dir / "audio.wav", secs)
        ch_scenes = [s for s in scenes if s["chapter_id"] == idx + 1]
        (ch_dir / "scene_timings.json").write_text(json.dumps(
            [{"scene_id": s["scene_id"],
              "start": k * secs / 2, "end": (k + 1) * secs / 2}
             for k, s in enumerate(ch_scenes)]))
        (ch_dir / "word_timestamps.json").write_text(json.dumps(
            [{"text": "w", "start": 0.1, "end": 0.2},
             {"text": "x", "start": secs - 0.2, "end": secs - 0.1}]))
    monkeypatch.setattr("stages.stage_4.pipeline.synthesize_project",
                        fake_synthesize)
    return root


def test_stitch_offsets_exact(tmp_path, monkeypatch):
    root = _make_longform_project(tmp_path, monkeypatch, (2.0, 3.0))
    out = longform_tts.synthesize_longform("proj", log=lambda *_: None)
    with wave.open(str(root / "audio.wav"), "rb") as w:
        total = w.getnframes() / w.getframerate()
    assert total == pytest.approx(2.0 + ART_LF_CHAPTER_GAP_S + 3.0, abs=0.01)
    timings = json.loads((root / "scene_timings.json").read_text())
    assert len(timings) == 4
    # chapter 2's first scene starts exactly after ch1 audio + gap
    t3 = next(t for t in timings if t["scene_id"] == 3)
    assert t3["start"] == pytest.approx(2.0 + ART_LF_CHAPTER_GAP_S, abs=0.01)
    chapters = json.loads((root / "chapters.json").read_text())
    assert chapters[0]["start"] == pytest.approx(0.0, abs=0.01)
    assert chapters[1]["start"] == pytest.approx(2.0 + ART_LF_CHAPTER_GAP_S, abs=0.01)
    words = json.loads((root / "word_timestamps.json").read_text())
    assert words[2]["start"] == pytest.approx(2.0 + ART_LF_CHAPTER_GAP_S + 0.1, abs=0.01)
    assert out["chapters"] == 2


def test_skip_when_audio_exists(tmp_path, monkeypatch):
    root = _make_longform_project(tmp_path, monkeypatch)
    _write_wav(root / "audio.wav", 1.0)
    out = longform_tts.synthesize_longform("proj", log=lambda *_: None)
    assert out.get("skipped") is True


def test_missing_chapters_json_errors(tmp_path, monkeypatch):
    root = _make_longform_project(tmp_path, monkeypatch)
    (root / "chapters.json").unlink()
    with pytest.raises(FileNotFoundError):
        longform_tts.synthesize_longform("proj", log=lambda *_: None)
