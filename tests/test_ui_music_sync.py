"""Tests for UI CapCut music toggle, file picker, and config bridge."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
import numpy as np
import wave

from ui.bridge import load_music_config, save_music_config, save_capcut_music


def _make_wav_bytes(duration: float = 5.0, sample_rate: int = 22050) -> bytes:
    import io
    num_samples = int(duration * sample_rate)
    t = np.arange(num_samples) / sample_rate
    samples = (0.5 * np.sin(2 * np.pi * 440.0 * t) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def test_save_capcut_music_web_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    save_music_config("proj", {"genre": "ambient horror"})

    wav_data = _make_wav_bytes(duration=5.0)
    res = save_capcut_music("proj", file_name="capcut_ref.wav", data=wav_data, src_path=None)

    assert res["enabled"] is True
    assert res["filename"] == "capcut_reference/capcut_ref.wav"
    assert (proj_dir / "capcut_reference" / "capcut_ref.wav").exists()
    assert (proj_dir / "capcut_reference" / "capcut_ref.wav").stat().st_size == len(wav_data)
    assert len(res["sha256"]) == 64
    assert res["bpm"] > 0
    assert "in_point" in res

    # Verify music.json has both genre and capcut_music
    saved_doc = load_music_config("proj")
    assert saved_doc["genre"] == "ambient horror"
    assert saved_doc["capcut_music"]["enabled"] is True
    assert saved_doc["capcut_music"]["sha256"] == res["sha256"]


def test_save_capcut_music_desktop_path(tmp_path, monkeypatch):
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()

    desktop_src = tmp_path / "outside" / "song.wav"
    desktop_src.parent.mkdir()
    desktop_src.write_bytes(_make_wav_bytes(duration=4.0))

    res = save_capcut_music("proj", file_name="song.wav", data=None, src_path=desktop_src)

    assert res["enabled"] is True
    assert res["filename"] == "capcut_reference/song.wav"
    assert (proj_dir / "capcut_reference" / "song.wav").exists()
    assert (proj_dir / "capcut_reference" / "song.wav").read_bytes() == desktop_src.read_bytes()
    assert res["bpm"] > 0


def test_save_capcut_music_does_not_overwrite_pipeline_artifacts(tmp_path, monkeypatch):
    """P1 regression: uploading a track named audio.wav or final.mp4 must NEVER overwrite
    the project's narration audio or final rendered video."""
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()

    orig_audio = b"ORIGINAL_STAGE4_TTS_AUDIO"
    orig_final = b"ORIGINAL_STAGE5_FINAL_VIDEO"
    (proj_dir / "audio.wav").write_bytes(orig_audio)
    (proj_dir / "final.mp4").write_bytes(orig_final)

    # 1. Upload reference named audio.wav
    wav_data = _make_wav_bytes(duration=3.0)
    res_wav = save_capcut_music("proj", file_name="audio.wav", data=wav_data, src_path=None)
    assert res_wav["filename"] == "capcut_reference/audio.wav"
    assert (proj_dir / "audio.wav").read_bytes() == orig_audio
    assert (proj_dir / "capcut_reference" / "audio.wav").read_bytes() == wav_data

    # 2. Upload reference named final.mp4
    import subprocess
    mp4_temp = tmp_path / "src_track.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=2",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
        "-c:v", "libx264", "-c:a", "aac",
        "-shortest", str(mp4_temp),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mp4_data = mp4_temp.read_bytes()

    res_mp4 = save_capcut_music("proj", file_name="final.mp4", data=mp4_data, src_path=None)
    assert res_mp4["filename"] == "capcut_reference/final.mp4"
    assert (proj_dir / "final.mp4").read_bytes() == orig_final
    assert (proj_dir / "capcut_reference" / "final.mp4").read_bytes() == mp4_data



def test_toggle_capcut_music_preserves_genre_and_track(tmp_path, monkeypatch):
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    save_music_config("proj", {
        "genre": "dark piano",
        "capcut_music": {"enabled": True, "filename": "track.mp3", "sha256": "abc"},
    })

    # Disable toggle
    doc = load_music_config("proj")
    doc["capcut_music"]["enabled"] = False
    save_music_config("proj", doc)

    reloaded = load_music_config("proj")
    assert reloaded["genre"] == "dark piano"
    assert reloaded["capcut_music"]["enabled"] is False
    assert reloaded["capcut_music"]["filename"] == "track.mp3"


def test_review_gate_ui_capcut_controls(tmp_path, monkeypatch):
    import flet as ft
    from unittest.mock import MagicMock
    from ui.state import AppState
    from ui.screens import s_review_gate

    monkeypatch.setattr(s_review_gate, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)

    proj_dir = tmp_path / "test_ui_proj"
    proj_dir.mkdir()
    (proj_dir / "review").mkdir()
    (proj_dir / "narration.json").write_text(json.dumps({"scenes": [{"scene_id": 1, "text": "hello"}]}))
    (proj_dir / "review" / "candidates.json").write_text(json.dumps({"beats": [{"beat_key": "1", "scene_id": 1, "narration_text": "hello"}]}))
    (proj_dir / "review" / "locks.json").write_text(json.dumps({"locks": {}, "approved": True}))

    # Case 1: CapCut music OFF
    save_music_config("test_ui_proj", {"genre": "cinematic"})

    page = MagicMock()
    page.services = []
    page.overlay = []
    state = AppState(project_name="test_ui_proj")

    ctl = s_review_gate.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    assert ctl is not None

    # Walk controls to find Checkbox
    def _find_controls(c, target_type):
        res = []
        if isinstance(c, target_type):
            res.append(c)
        if hasattr(c, "controls") and isinstance(c.controls, list):
            for child in c.controls:
                res.extend(_find_controls(child, target_type))
        if hasattr(c, "content") and c.content:
            res.extend(_find_controls(c.content, target_type))
        return res

    cbs = _find_controls(ctl, ft.Checkbox)
    capcut_cbs = [cb for cb in cbs if "CapCut" in str(cb.label)]
    assert len(capcut_cbs) == 1
    assert capcut_cbs[0].value is False

    # Case 2: CapCut music ON with track
    save_music_config("test_ui_proj", {
        "genre": "cinematic",
        "capcut_music": {
            "enabled": True,
            "filename": "my_beat.mp3",
            "bpm": 128.0,
            "in_point": 3.5,
            "in_point_timecode": "00:03.15",
        }
    })

    ctl2 = s_review_gate.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    cbs2 = _find_controls(ctl2, ft.Checkbox)
    capcut_cbs2 = [cb for cb in cbs2 if "CapCut" in str(cb.label)]
    assert len(capcut_cbs2) == 1
    assert capcut_cbs2[0].value is True


def test_s5_video_ui_shows_sync_sheet(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from ui.state import AppState
    from ui.screens import s5_video

    monkeypatch.setattr(s5_video, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)

    proj_dir = tmp_path / "test_s5_proj"
    proj_dir.mkdir()
    (proj_dir / "final.mp4").write_bytes(b"VIDEO")
    (proj_dir / "music_sync.txt").write_text("CAPCUT MUSIC SYNC SHEET CONTENT")
    (proj_dir / "preview_with_music.mp4").write_bytes(b"PREVIEW")

    page = MagicMock()
    page.services = []
    page.overlay = []
    state = AppState(project_name="test_s5_proj")

    ctl = s5_video.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    assert ctl is not None

    def _find_text(c, search_str):
        for attr in ("text", "value", "content"):
            val = getattr(c, attr, None)
            if val is not None and search_str in str(val):
                return True
        if hasattr(c, "controls") and isinstance(c.controls, list):
            return any(_find_text(child, search_str) for child in c.controls)
        if hasattr(c, "content") and c.content and not isinstance(c.content, str):
            return _find_text(c.content, search_str)
        return False

    assert _find_text(ctl, "Xem Sync Sheet CapCut")
    assert _find_text(ctl, "Preview có nhạc")

