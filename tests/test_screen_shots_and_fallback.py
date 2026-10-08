import json
import shutil
import subprocess
from pathlib import Path
from PIL import Image
import numpy as np
import pytest

from stages.stage_5.schema import Shot
from utils.text_card import generate_text_card_video, generate_text_card_image
from stages.stage_5.screen_shots import render_screen_shot, build_screen_shots

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe not installed")


def _probe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height",
        "-of", "json", str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


@needs_ffmpeg
def test_text_card_generation(tmp_path):
    img_path = tmp_path / "card.png"
    generate_text_card_image("THE TIME HEIST", "Quantum Realm Navigation", img_path)
    assert img_path.exists()
    im = Image.open(img_path)
    assert im.size == (1080, 1920)

    vid_path = tmp_path / "card.mp4"
    generate_text_card_video("THE TIME HEIST", "Quantum Realm Navigation", 1.5, vid_path)
    assert vid_path.exists()
    info = _probe(vid_path)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert v["codec_name"] == "h264"
    assert v["pix_fmt"] == "yuv420p"
    assert int(v["width"]) == 1080
    assert int(v["height"]) == 1920


@needs_ffmpeg
def test_fallback_chain_level1_primary_clip(tmp_path):
    # Level 1: Primary clip works
    src_mp4 = tmp_path / "primary.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
        "-t", "2.0", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src_mp4)
    ], check=True, capture_output=True)

    shot = Shot(
        shot_id=0, scene_id=1, duration_seconds=1.2,
        panel_bbox={"x": 0, "y": 0, "w": 640, "h": 360},
        source_image="", motion="zoom_in", caption_text="Primary clip test",
        clip_path=str(src_mp4), clip_in=0.0, clip_out=1.2
    )
    out_mp4 = tmp_path / "shot_0.mp4"
    rendered = render_screen_shot(shot, out_mp4)
    assert rendered.exists()
    assert getattr(shot, "screen_fallback_level", "clip") == "clip"


@needs_ffmpeg
def test_fallback_chain_level2_backup_clip(tmp_path):
    # Level 2: Primary clip missing, backup clip provided and works
    backup_mp4 = tmp_path / "backup.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
        "-t", "2.0", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(backup_mp4)
    ], check=True, capture_output=True)

    shot = Shot(
        shot_id=1, scene_id=1, duration_seconds=1.0,
        panel_bbox={"x": 0, "y": 0, "w": 640, "h": 360},
        source_image="", motion="zoom_in", caption_text="Backup test",
        clip_path=str(tmp_path / "missing_primary.mp4"), clip_in=0.0, clip_out=1.0
    )
    out_mp4 = tmp_path / "shot_1.mp4"
    rendered = render_screen_shot(shot, out_mp4, backup_clip_path=backup_mp4)
    assert rendered.exists()
    assert getattr(shot, "screen_fallback_level", None) == "backup_clip"


@needs_ffmpeg
def test_fallback_chain_level3_still_image(tmp_path):
    # Level 3: Both clips missing, custom still image available
    still_img = tmp_path / "still.jpg"
    Image.fromarray(np.full((720, 1280, 3), 80, dtype=np.uint8)).save(still_img)

    shot = Shot(
        shot_id=2, scene_id=1, duration_seconds=1.0,
        panel_bbox={"x": 0, "y": 0, "w": 1280, "h": 720},
        source_image="", motion="zoom_in", caption_text="Still image test",
        clip_path=str(tmp_path / "missing.mp4"), clip_in=0.0, clip_out=1.0,
        custom_image=str(still_img)
    )
    out_mp4 = tmp_path / "shot_2.mp4"
    rendered = render_screen_shot(shot, out_mp4)
    assert rendered.exists()
    assert getattr(shot, "screen_fallback_level", None) == "still_image"


@needs_ffmpeg
def test_fallback_chain_level4_text_card_never_crashes(tmp_path):
    # Level 4: Everything missing -> renders text card without crash
    shot = Shot(
        shot_id=3, scene_id=1, duration_seconds=1.0,
        panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100},
        source_image="", motion="zoom_in", caption_text="Everything is missing",
        clip_path=str(tmp_path / "non_existent.mp4"), clip_in=0.0, clip_out=1.0
    )
    out_mp4 = tmp_path / "shot_3.mp4"
    rendered = render_screen_shot(shot, out_mp4)
    assert rendered.exists()
    assert getattr(shot, "screen_fallback_level", None) == "text_card"
    info = _probe(rendered)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert int(v["width"]) == 1080
    assert int(v["height"]) == 1920
