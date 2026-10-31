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
    assert processed_shots[0].duration_seconds == 3.0
    assert processed_shots[1].duration_seconds == 3.0
    assert not (project_dir / "music_sync.json").exists()
    assert not (project_dir / "music_sync.txt").exists()
