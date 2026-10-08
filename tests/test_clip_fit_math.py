import subprocess
import shutil
from pathlib import Path
import pytest
import config

from stages.stage_5 import clips, shots, pipeline
from stages.stage_5.schema import Shot

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg required")


def _make_source(path: Path, dur: float = 3.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
        "-t", f"{dur:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)
    ], check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_clip_muted_and_contract_compliant(tmp_path):
    src = _make_source(tmp_path / "src.mp4", dur=2.0)
    shot = Shot(
        shot_id=0, scene_id=1, duration_seconds=1.5,
        panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100},
        source_image="dummy.png", motion="zoom_in",
        clip_path=str(src), clip_in=0.5, clip_out=2.0, clip_id="c1"
    )
    out = tmp_path / "clip_shot.mp4"
    clips.render_clip_shot(shot, out)

    assert out.exists()
    info = clips.probe_video(out)
    assert info["width"] == 1080
    assert info["height"] == 1920
    assert abs(info["duration"] - 1.5) < 0.1

    # Verify muted (no audio stream)
    res = subprocess.run([
        FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", str(out)
    ], capture_output=True, text=True)
    assert res.stdout.strip() == "", "Clip shot must be muted (no audio streams)"


@needs_ffmpeg
def test_fit_math_speed_and_hold_bounds(tmp_path):
    # Source is 1.0s long. Beat requires 1.4s.
    # Available = 1.0s. Needed = 1.4s.
    # Required speed = 1.0 / 1.4 = 0.714 -> clamped to CLIP_SPEED_MIN = 0.8.
    # At speed 0.8: duration is 1.0 / 0.8 = 1.25s.
    # Remaining shortfall: 1.4 - 1.25 = 0.15s <= CLIP_MAX_HOLD = 0.3s.
    src = _make_source(tmp_path / "short_src.mp4", dur=1.0)
    shot = Shot(
        shot_id=1, scene_id=1, duration_seconds=1.4,
        panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100},
        source_image="dummy.png", motion="zoom_in",
        clip_path=str(src), clip_in=0.0, clip_out=1.0, clip_id="c2"
    )
    out = tmp_path / "fitted_clip.mp4"
    clips.render_clip_shot(shot, out)

    # Output matches exact shot contract frames (1.4s * 30 = 42 frames)
    clips.verify_shot_contract(out, 42)


@needs_ffmpeg
def test_frozen_tail_capped_at_max_hold(tmp_path):
    # Source is 1.0s long. Beat requires 2.0s.
    # Shortfall is 2.0 - (1.0 / 0.8) = 0.75s > CLIP_MAX_HOLD (0.3s).
    # Must raise ValueError to trigger panel fallback.
    from PIL import Image
    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1200), (200, 100, 50)).save(page)

    src = _make_source(tmp_path / "too_short.mp4", dur=1.0)
    shot = Shot(
        shot_id=1, scene_id=1, duration_seconds=2.0,
        panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
        source_image=str(page), motion="zoom_in",
        clip_path=str(src), clip_in=0.0, clip_out=1.0, clip_id="c_fail"
    )
    out = tmp_path / "fail_clip.mp4"
    with pytest.raises(ValueError, match="CLIP_MAX_HOLD"):
        clips.render_clip_shot(shot, out)

    # Calling via shots.render_shot safely catches ValueError and falls back to panel
    panel_out = tmp_path / "fallback_shot.mp4"
    res = shots.render_shot(shot, panel_out)
    assert res.exists()
    assert shot.clip_fallback != ""
    assert "CLIP_MAX_HOLD" in shot.clip_fallback


@needs_ffmpeg
def test_hard_cut_before_and_after_clip_shots(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(shots, "PANEL_UPSCALE", False)

    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1200), (40, 100, 160)).save(page)

    src = _make_source(tmp_path / "c.mp4", dur=2.0)

    # 3 shots: Panel 1 (scene 1) -> Clip (scene 2) -> Panel 2 (scene 3)
    s1 = Shot(shot_id=0, scene_id=1, duration_seconds=1.0, panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
              source_image=str(page), motion="zoom_in")
    s2 = Shot(shot_id=1, scene_id=2, duration_seconds=1.0, panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
              source_image="dummy.png", motion="zoom_in",
              clip_path=str(src), clip_in=0.0, clip_out=1.0, clip_id="clip_mid")
    s3 = Shot(shot_id=2, scene_id=3, duration_seconds=1.0, panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
              source_image=str(page), motion="zoom_out")

    p1 = shots.render_shot(s1, tmp_path / "shot_0.mp4")
    p2 = shots.render_shot(s2, tmp_path / "shot_1.mp4")
    p3 = shots.render_shot(s3, tmp_path / "shot_2.mp4")

    out_silent = tmp_path / "assembled.mp4"
    pipeline._assemble_video([s1, s2, s3], [p1, p2, p3], out_silent, project="test_cuts")

    assert out_silent.exists()
    info = clips.probe_video(out_silent)
    assert abs(info["duration"] - 3.0) < 0.2
