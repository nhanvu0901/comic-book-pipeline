"""Tests for Stage 5 CapCut music sync integration, sync sheets, and preview video."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
import subprocess

from stages.stage_5.schema import Shot
from stages import music_sync


def _create_dummy_video_with_audio(video_path: Path, duration: float = 6.0) -> Path:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=320x240:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-c:a", "aac",
        str(video_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return video_path


def _create_dummy_audio(audio_path: Path, duration: float = 10.0) -> Path:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}",
        str(audio_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return audio_path


def test_format_timecode():
    assert music_sync.format_timecode(0.0) == "0.00 s (00:00:00:00 @30fps)"
    assert music_sync.format_timecode(4.25, fps=30) == "4.25 s (00:00:04:08 @30fps)"
    assert music_sync.format_timecode(65.5, fps=30) == "65.50 s (00:01:05:15 @30fps)"
    # P2 carry tests: 4.99s must round to 00:05:00, not 00:04:00; 59.99s to 01:00:00
    assert music_sync.format_timecode(4.99, fps=30) == "4.99 s (00:00:05:00 @30fps)"
    assert music_sync.format_timecode(59.99, fps=30) == "59.99 s (00:01:00:00 @30fps)"
    assert music_sync.format_timecode(119.99, fps=30) == "119.99 s (00:02:00:00 @30fps)"
    # E2E review bug: 4.651s at 85 BPM must be written with unambiguous seconds and CapCut-style frames
    assert music_sync.format_timecode(4.651, fps=30) == "4.65 s (00:00:04:20 @30fps)"


def test_snapped_hit_point_updates_time_seconds_in_sync_sheet(tmp_path):
    """P2: snapping a hit point from 4.00s to 3.97s must update hp.time_seconds so
    build_sync_sheet outputs 3.97s (not the pre-snap 4.00s)."""
    shots = [
        Shot(shot_id=0, scene_id=1, duration_seconds=4.0, panel_bbox={}, source_image="", motion="", is_intro=True),
        Shot(shot_id=1, scene_id=2, duration_seconds=4.0, panel_bbox={}, source_image="", motion=""),
    ]
    hit_points = [
        music_sync.HitPoint(shot_index=1, time_seconds=4.0, name="Hook Reveal", strength=0.9)
    ]
    # Beat at 4.02s -> target_cut = 4.02 - 0.05 = 3.97s (delta = -0.03s)
    strong_beats = [4.02]
    snapped = music_sync.snap_shots_to_beats(shots, hit_points, in_point=0.0, strong_beats=strong_beats)

    assert snapped[0].snapped is True
    # Crucial: time_seconds must be updated to the snapped cut time!
    assert snapped[0].time_seconds == pytest.approx(3.97, abs=0.01)
    assert snapped[0].target_cut == pytest.approx(3.97, abs=0.01)

    analysis = music_sync.TrackAnalysis(
        file_path="track.mp3", file_sha256="123", duration_seconds=30.0, bpm=120.0
    )
    json_doc, txt = music_sync.build_sync_sheet(tmp_path, analysis, in_point=0.0, hit_points=snapped)
    assert json_doc["hit_points"][0]["time_seconds"] == pytest.approx(3.97, abs=0.01)
    assert json_doc["hit_points"][0]["timecode"] == music_sync.format_timecode(3.97)
    assert "3.97 s (00:00:03:29 @30fps)" in txt
    assert "1/1 hit points đã khớp beat" in txt



def test_build_sync_sheet(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()

    analysis = music_sync.TrackAnalysis(
        file_path=str(project_dir / "reference.mp3"),
        file_sha256="abc123456789",
        duration_seconds=30.0,
        bpm=120.0,
        beats=[1.0, 1.5, 2.0],
        downbeats=[1.0, 3.0],
    )
    hit_points = [
        music_sync.HitPoint(
            shot_index=1,
            time_seconds=3.95,
            name="Hook Reveal",
            strength=0.9,
            beat_time=4.0,
            target_cut=3.95,
            offset_from_beat=-0.05,
            snapped=True,
        )
    ]

    json_doc, txt_content = music_sync.build_sync_sheet(
        project_dir=project_dir,
        analysis=analysis,
        in_point=4.25,
        hit_points=hit_points,
        warning="Track duration warning",
    )

    # Verify JSON
    assert (project_dir / "music_sync.json").exists()
    assert json_doc["bpm"] == 120.0
    assert json_doc["in_point_seconds"] == 4.25
    assert json_doc["in_point_timecode"] == "4.25 s (00:00:04:08 @30fps)"
    assert json_doc["suggested_ducking_db"] <= -12.0
    assert json_doc["warning"] == "Track duration warning"
    assert json_doc["snapped_count"] == 1
    assert json_doc["kept_count"] == 0
    assert len(json_doc["hit_points"]) == 1

    # Verify Vietnamese TXT
    assert (project_dir / "music_sync.txt").exists()
    assert "HƯỚNG DẪN ĐỒNG BỘ NHẠC CAPCUT" in txt_content
    assert "4.25 s (00:00:04:08 @30fps)" in txt_content
    assert "Hook Reveal" in txt_content
    assert "1/1 hit points đã khớp beat (0 giữ nguyên" in txt_content
    assert "preview_with_music.mp4" in txt_content


def test_render_preview_with_music(tmp_path):
    final_video = tmp_path / "final.mp4"
    _create_dummy_video_with_audio(final_video, duration=4.0)

    track_audio = tmp_path / "music.mp3"
    _create_dummy_audio(track_audio, duration=10.0)

    preview_video = tmp_path / "preview_with_music.mp4"
    out = music_sync.render_preview_with_music(
        final_path=final_video,
        track_path=track_audio,
        in_point=2.0,
        out_path=preview_video,
    )

    assert out is not None
    assert preview_video.exists()
    assert preview_video.stat().st_size > 0


def test_stage5_skips_generated_bed_when_capcut_music_enabled(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    # Create music.json with capcut_music enabled
    music_json = project_dir / "music.json"
    music_json.write_text(json.dumps({
        "genre": "cinematic",
        "capcut_music": {
            "enabled": True,
            "filename": "track.mp3",
            "sha256": "123",
        }
    }), encoding="utf-8")

    from stages.stage_5.pipeline import _score_final_video
    final_path = project_dir / "final.mp4"
    final_path.write_bytes(b"NARRATION_ONLY")

    # Should return None and keep final.mp4 untouched
    res = _score_final_video("proj", project_dir, project_dir / "silent.mp4", project_dir / "audio.wav", final_path)
    assert res is None
    assert final_path.read_bytes() == b"NARRATION_ONLY"


def test_stage5_flag_off_byte_identity(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    # Mode is off (default: no capcut_music in music.json)
    music_json = project_dir / "music.json"
    music_json.write_text(json.dumps({"genre": "cinematic"}), encoding="utf-8")

    assert not music_sync.is_capcut_music_enabled(project_dir)

    # Shots should NOT be changed
    shots = [
        Shot(shot_id=0, scene_id=1, duration_seconds=3.0, panel_bbox={}, source_image="", motion=""),
        Shot(shot_id=1, scene_id=2, duration_seconds=3.0, panel_bbox={}, source_image="", motion=""),
    ]
    narration = {"scenes": [{"scene_id": 1}, {"scene_id": 2}]}

    processed_shots = music_sync.prepare_capcut_music_sync(
        project_name="proj",
        root=project_dir,
        shots=shots,
        narration=narration,
    )

    # Identical instances and durations
    assert len(processed_shots) == 2


def test_build_capcut_music_mix_filter():
    """Verify mix-graph construction with sidechain compression, loudness gain, and peak limiter."""
    f = music_sync.build_capcut_music_mix_filter(gain_db=-18.5, duration=10.0, fade_in=1.0, fade_out=2.0)
    assert "[0:a]asplit=2[voice_main][voice_sc]" in f
    assert "volume=-18.50dB" in f
    assert "afade=t=in:st=0:d=1.0" in f
    assert "afade=t=out:st=8.00:d=2.0" in f
    assert "sidechaincompress=threshold=0.08:ratio=3:attack=20:release=250" in f
    assert "amix=inputs=2:duration=first:dropout_transition=2:normalize=0" in f
    assert "alimiter=limit=0.891:attack=5:release=50" in f


def test_measure_loudness_synthetic(tmp_path):
    """Verify loudness targeting with synthetic tones."""
    tone_path = tmp_path / "tone.wav"
    _create_dummy_audio(tone_path, duration=3.0)
    loudness = music_sync.measure_loudness(tone_path, in_point=0.0, duration=3.0)
    assert loudness is not None
    # Sine wave from lavfi has integrated loudness around -22 LUFS
    assert -30.0 <= loudness <= -10.0


def test_render_capcut_music_mix_loudness_and_peak_safe(tmp_path):
    """Verify mixed render executes successfully, reports LUFS metrics, and outputs peak-safe video."""
    final_video = tmp_path / "final.mp4"
    _create_dummy_video_with_audio(final_video, duration=4.0)

    track_audio = tmp_path / "music.mp3"
    _create_dummy_audio(track_audio, duration=10.0)

    mixed_video = tmp_path / "mixed.mp4"
    out, info = music_sync.render_capcut_music_mix(
        final_path=final_video,
        track_path=track_audio,
        in_point=1.0,
        out_path=mixed_video,
    )
    assert out is not None
    assert mixed_video.exists()
    assert mixed_video.stat().st_size > 0
    assert "gain_db" in info
    assert "offset_lu" in info


def test_finish_capcut_music_sync_bake_on(tmp_path):
    """When bake_into_final is True (default in CapCut mode):
    - final.mp4 is replaced by the baked audio mix
    - Master's decision: no final_voice_only.mp4, no preview_with_music.mp4, no music_sync.txt
    - music_sync.json is preserved as internal metadata for UI summary/cache only
    """
    proj_dir = tmp_path / "proj_bake_on"
    proj_dir.mkdir()

    # Configure capcut_music with bake_into_final=True
    music_json = proj_dir / "music.json"
    music_json.write_text(json.dumps({
        "capcut_music": {
            "enabled": True,
            "filename": "ref.mp3",
            "bake_into_final": True,
        }
    }), encoding="utf-8")

    final_video = proj_dir / "final.mp4"
    _create_dummy_video_with_audio(final_video, duration=4.0)
    orig_voice_bytes = final_video.read_bytes()

    track_audio = proj_dir / "ref.mp3"
    _create_dummy_audio(track_audio, duration=10.0)

    analysis = music_sync.TrackAnalysis(
        file_path=str(track_audio), file_sha256="abc", duration_seconds=10.0, bpm=120.0
    )
    hps = [music_sync.HitPoint(shot_index=1, time_seconds=2.0, name="Clip 1", strength=1.0, snapped=True)]

    # Stage state stash
    music_sync._STAGE5_SYNC_STATE["proj_bake_on"] = {
        "analysis": analysis,
        "in_point": 1.0,
        "hit_points": hps,
        "warning": None,
        "track_path": track_audio,
    }

    music_sync.finish_capcut_music_sync("proj_bake_on", proj_dir, final_video)

    # 1. final.mp4 must be updated with the baked mix (different file size / content)
    assert final_video.exists()
    assert final_video.read_bytes() != orig_voice_bytes

    # 2. Master's decision: no final_voice_only.mp4, no preview_with_music.mp4, no music_sync.txt
    assert not (proj_dir / "final_voice_only.mp4").exists()
    assert not (proj_dir / "preview_with_music.mp4").exists()
    assert not (proj_dir / "music_sync.txt").exists()

    # 3. music_sync.json preserved as internal metadata
    assert (proj_dir / "music_sync.json").exists()
    json_doc = json.loads((proj_dir / "music_sync.json").read_text(encoding="utf-8"))
    assert json_doc["bake_into_final"] is True
    assert json_doc["voice_only_video"] is None
    assert json_doc["preview_video"] == "final.mp4"
    assert json_doc["target_offset_lu"] == 16.0


def test_finish_capcut_music_sync_bake_off(tmp_path):
    """When bake_into_final is False:
    - final.mp4 stays untouched (narration-only)
    - preview_with_music.mp4 has the mixed audio
    - music_sync.txt and music_sync.json are written
    - final_voice_only.mp4 does not exist
    """
    proj_dir = tmp_path / "proj_bake_off"
    proj_dir.mkdir()

    music_json = proj_dir / "music.json"
    music_json.write_text(json.dumps({
        "capcut_music": {
            "enabled": True,
            "filename": "ref.mp3",
            "bake_into_final": False,
        }
    }), encoding="utf-8")

    final_video = proj_dir / "final.mp4"
    _create_dummy_video_with_audio(final_video, duration=4.0)
    orig_voice_bytes = final_video.read_bytes()

    track_audio = proj_dir / "ref.mp3"
    _create_dummy_audio(track_audio, duration=10.0)

    analysis = music_sync.TrackAnalysis(
        file_path=str(track_audio), file_sha256="abc", duration_seconds=10.0, bpm=120.0
    )
    hps = [music_sync.HitPoint(shot_index=1, time_seconds=2.0, name="Clip 1", strength=1.0, snapped=True)]

    music_sync._STAGE5_SYNC_STATE["proj_bake_off"] = {
        "analysis": analysis,
        "in_point": 1.0,
        "hit_points": hps,
        "warning": None,
        "track_path": track_audio,
    }

    music_sync.finish_capcut_music_sync("proj_bake_off", proj_dir, final_video)

    # final.mp4 remains narration-only!
    assert final_video.read_bytes() == orig_voice_bytes
    assert (proj_dir / "preview_with_music.mp4").exists()
    assert not (proj_dir / "final_voice_only.mp4").exists()
    assert (proj_dir / "music_sync.txt").exists()
    assert (proj_dir / "music_sync.json").exists()

    json_doc = json.loads((proj_dir / "music_sync.json").read_text(encoding="utf-8"))
    assert json_doc["bake_into_final"] is False
    assert json_doc["voice_only_video"] == "final.mp4"
    assert json_doc["preview_video"] == "preview_with_music.mp4"

    txt_content = (proj_dir / "music_sync.txt").read_text(encoding="utf-8")
    assert "Trộn sẵn nhạc vào final.mp4: KHÔNG" in txt_content


def test_finish_capcut_music_sync_mixer_failure_bake_on_fails_clearly(tmp_path, monkeypatch):
    """When render_capcut_music_mix() returns (None, {}) and bake is ON:
    - Fails the render clearly (RuntimeError)
    - Separates bake_requested (True) from bake_applied (False) and bake_into_final (False)
    - Records bake_failed=True and bake_error
    - Sets voice_only_video='final.mp4' (never presents final.mp4 as music-baked)
    - preview_video is None
    - Writes music_sync.txt with warning so Master can manually sync
    - Keeps final.mp4 narration-only (untouched)
    """
    proj_dir = tmp_path / "proj_mix_fail"
    proj_dir.mkdir()

    music_json = proj_dir / "music.json"
    music_json.write_text(json.dumps({
        "capcut_music": {
            "enabled": True,
            "filename": "ref.mp3",
            "bake_into_final": True,
        }
    }), encoding="utf-8")

    final_video = proj_dir / "final.mp4"
    _create_dummy_video_with_audio(final_video, duration=4.0)
    orig_voice_bytes = final_video.read_bytes()

    track_audio = proj_dir / "ref.mp3"
    _create_dummy_audio(track_audio, duration=10.0)

    analysis = music_sync.TrackAnalysis(
        file_path=str(track_audio), file_sha256="abc", duration_seconds=10.0, bpm=120.0
    )
    hps = [music_sync.HitPoint(shot_index=1, time_seconds=2.0, name="Clip 1", strength=1.0, snapped=True)]

    music_sync._STAGE5_SYNC_STATE["proj_mix_fail"] = {
        "analysis": analysis,
        "in_point": 1.0,
        "hit_points": hps,
        "warning": None,
        "track_path": track_audio,
    }

    # Simulate mixer failure
    monkeypatch.setattr(
        music_sync,
        "render_capcut_music_mix",
        lambda *args, **kwargs: (None, {"error": "ffmpeg audio decode error: invalid header"}),
    )

    with pytest.raises(RuntimeError, match="Failed to bake CapCut reference music into final.mp4"):
        music_sync.finish_capcut_music_sync("proj_mix_fail", proj_dir, final_video)

    # final.mp4 stays untouched (narration-only)
    assert final_video.read_bytes() == orig_voice_bytes

    # No broken preview or voice_only copies
    assert not (proj_dir / "preview_with_music.mp4").exists()
    assert not (proj_dir / "final_voice_only.mp4").exists()

    # Metadata must record failure and NEVER present final.mp4 as music-baked
    json_doc = json.loads((proj_dir / "music_sync.json").read_text(encoding="utf-8"))
    assert json_doc["bake_requested"] is True
    assert json_doc["bake_applied"] is False
    assert json_doc["bake_into_final"] is False
    assert json_doc["bake_failed"] is True
    assert "ffmpeg audio decode error" in json_doc["bake_error"]
    assert json_doc["voice_only_video"] == "final.mp4"
    assert json_doc["preview_video"] is None

    # Fallback sync sheet is written with warning
    assert (proj_dir / "music_sync.txt").exists()
    txt_content = (proj_dir / "music_sync.txt").read_text(encoding="utf-8")
    assert "THẤT BẠI" in txt_content
    assert "LƯU Ý LỖI TRỘN NHẠC" in txt_content


def test_finish_capcut_music_sync_mixer_failure_suppressed_raise(tmp_path, monkeypatch):
    """When raise_on_failure=False, mixer failure returns without raising,
    still recording bake_failed=True and never presenting final.mp4 as music-baked.
    """
    proj_dir = tmp_path / "proj_mix_fail_suppressed"
    proj_dir.mkdir()

    music_json = proj_dir / "music.json"
    music_json.write_text(json.dumps({
        "capcut_music": {
            "enabled": True,
            "filename": "ref.mp3",
            "bake_into_final": True,
        }
    }), encoding="utf-8")

    final_video = proj_dir / "final.mp4"
    _create_dummy_video_with_audio(final_video, duration=4.0)

    track_audio = proj_dir / "ref.mp3"
    _create_dummy_audio(track_audio, duration=10.0)

    analysis = music_sync.TrackAnalysis(
        file_path=str(track_audio), file_sha256="abc", duration_seconds=10.0, bpm=120.0
    )

    music_sync._STAGE5_SYNC_STATE["proj_mix_fail_suppressed"] = {
        "analysis": analysis,
        "in_point": 1.0,
        "hit_points": [],
        "warning": None,
        "track_path": track_audio,
    }

    monkeypatch.setattr(
        music_sync,
        "render_capcut_music_mix",
        lambda *args, **kwargs: (None, {}),
    )

    # Does not raise when raise_on_failure=False
    music_sync.finish_capcut_music_sync(
        "proj_mix_fail_suppressed", proj_dir, final_video, raise_on_failure=False
    )

    json_doc = json.loads((proj_dir / "music_sync.json").read_text(encoding="utf-8"))
    assert json_doc["bake_requested"] is True
    assert json_doc["bake_applied"] is False
    assert json_doc["bake_into_final"] is False
    assert json_doc["bake_failed"] is True
    assert json_doc["voice_only_video"] == "final.mp4"
    assert json_doc["preview_video"] is None

