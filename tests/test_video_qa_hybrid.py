import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from PIL import Image
import numpy as np
import pytest

import config
from stages.stage_5.schema import Shot
from stages.stage_5 import shots, pipeline, clips

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe not installed")


def _probe_video(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height,r_frame_rate,nb_frames",
        "-of", "json", str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


@needs_ffmpeg
def test_flag_off_byte_identical_to_baseline(monkeypatch):
    """
    User override rule #1 & #4: with the flag OFF (ENABLE_VIDEO_CLIPS default 0) the stable path stays
    byte-identical. The comparison itself lives in tests/test_p0_baseline.py (per-platform baseline
    captured from 6788212); here the flag is forced OFF explicitly and the same scenarios must agree.
    """
    from tests import p0_scenarios
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    entry = json.loads(Path("tests/fixtures/p0_baseline.json").read_text())["platforms"].get(p0_scenarios.platform_key())
    if entry is None:
        pytest.skip("no 6788212 baseline for this platform (see tests/test_p0_baseline.py)")
    assert p0_scenarios.run_all(Path("projects")) == entry["scenarios"]


@needs_ffmpeg
def test_clip_shot_contract(tmp_path):
    """
    Contract for clip shot:
    h264 yuv420p 1080x1920 30fps, round(dur*30) frames, no audio stream.
    """
    # 1. Create a dummy source video 640x360 2.0s
    src_mp4 = tmp_path / "src.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "testsrc=size=640x360:rate=30",
        "-t", "2.0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(src_mp4)
    ], check=True, capture_output=True)

    out_mp4 = tmp_path / "clip_shot.mp4"
    duration = 1.4  # 1.4 * 30 = 42 frames

    shot = Shot(
        shot_id=0, scene_id=1, duration_seconds=duration,
        panel_bbox={"x": 0, "y": 0, "w": 640, "h": 360},
        source_image="",
        motion="zoom_in",
        clip_path=str(src_mp4),
        clip_in=0.2,
        clip_out=0.2 + duration,
    )

    clips.render_clip_shot(
        shot=shot,
        out_path=out_mp4,
    )

    probe = _probe_video(out_mp4)
    streams = probe.get("streams", [])

    # No audio stream
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    assert len(audio_streams) == 0, "Clip shot must have NO audio stream"

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    assert len(video_streams) == 1, "Must have exactly one video stream"

    v = video_streams[0]
    assert v.get("codec_name") == "h264"
    assert v.get("pix_fmt") == "yuv420p"
    assert int(v.get("width")) == 1080
    assert int(v.get("height")) == 1920

    # Frame count check: round(1.4 * 30) = 42
    expected_frames = round(duration * 30)
    cmd_count = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-count_frames",
        "-show_entries", "stream=nb_read_frames",
        "-of", "csv=p=0", str(out_mp4)
    ]
    count_res = subprocess.run(cmd_count, capture_output=True, text=True, check=True)
    actual_frames = int(count_res.stdout.strip())
    assert actual_frames == expected_frames, f"Expected {expected_frames} frames, got {actual_frames}"


@needs_ffmpeg
def test_clip_fallback_and_rollback_to_panel(tmp_path, monkeypatch):
    """
    When clip file is missing or invalid, fallback cleanly to panel Ken Burns without crash.
    """
    proj_dir = tmp_path / "proj_fallback"
    proj_dir.mkdir(parents=True)
    p_img = proj_dir / "page.png"
    Image.fromarray(np.full((800, 600, 3), 120, dtype=np.uint8)).save(p_img)

    shot = Shot(
        shot_id=0, scene_id=1, duration_seconds=1.0,
        panel_bbox={"x": 50, "y": 50, "w": 400, "h": 400},
        source_image=str(p_img), motion="zoom_in", caption_text="Fallback beat",
        clip_path=str(proj_dir / "non_existent.mp4"),
        clip_in=0.0,
        clip_out=1.0,
    )

    out_mp4 = proj_dir / "shot_000.mp4"
    rendered = shots.render_shot(shot, out_mp4)

    assert rendered.exists()
    assert bool(shot.clip_fallback), "clip_fallback should record the reason why panel rendered"
    assert "error" in shot.clip_fallback.lower() or "not" in shot.clip_fallback.lower() or "failed" in shot.clip_fallback.lower()
