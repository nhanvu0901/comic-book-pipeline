import hashlib
import json
import shutil
from pathlib import Path
from PIL import Image
import numpy as np
import pytest

from stages.stage_5.schema import Shot
from stages.stage_5 import shots, pipeline

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


@needs_ffmpeg
def test_p0_baseline_byte_identity():
    fixture_record = json.loads(Path("tests/fixtures/p0_baseline.json").read_text())
    proj_dir = Path("projects/p0_baseline_fixture")
    proj_dir.mkdir(parents=True, exist_ok=True)
    try:
        p1 = proj_dir / "page_1.png"
        p2 = proj_dir / "page_2.png"
        arr1 = np.full((1800, 1200, 3), 40, dtype=np.uint8)
        arr1[200:800, 200:1000] = [200, 100, 50]
        Image.fromarray(arr1).save(p1)

        arr2 = np.full((1800, 1200, 3), 60, dtype=np.uint8)
        arr2[300:900, 100:900] = [50, 150, 220]
        Image.fromarray(arr2).save(p2)

        s1 = Shot(shot_id=0, scene_id=1, duration_seconds=1.0,
                  panel_bbox={"x": 200, "y": 200, "w": 800, "h": 600},
                  source_image=str(p1), motion="zoom_in", caption_text="Intro beat")
        s2 = Shot(shot_id=1, scene_id=1, duration_seconds=1.0,
                  panel_bbox={"x": 200, "y": 200, "w": 800, "h": 600},
                  source_image=str(p1), motion="pan_down", caption_text="Next beat")
        s3 = Shot(shot_id=2, scene_id=2, duration_seconds=1.0,
                  panel_bbox={"x": 100, "y": 300, "w": 800, "h": 600},
                  source_image=str(p2), motion="zoom_out", caption_text="Climax beat")

        shot_list = [s1, s2, s3]
        shots_dir = proj_dir / "shots"
        shot_paths = []
        for s in shot_list:
            p = shots.render_shot(s, shots_dir / f"shot_{s.shot_id:03d}.mp4")
            shot_paths.append(p)

        shots_json = proj_dir / "shots.json"
        pipeline._write_shots_log(shot_list, [], shots_dir, shots_json, lambda m: None)

        silent_mp4 = proj_dir / "video_silent.mp4"
        pipeline._assemble_video(shot_list, shot_paths, silent_mp4, project=fixture_record["project"])

        h_json = hashlib.sha256(shots_json.read_bytes()).hexdigest()
        h_mp4 = hashlib.sha256(silent_mp4.read_bytes()).hexdigest()

        assert h_json == fixture_record["shots_json_sha256"], (
            f"shots.json sha256 changed: expected {fixture_record['shots_json_sha256']}, got {h_json}"
        )
        assert h_mp4 == fixture_record["video_silent_sha256"], (
            f"video_silent.mp4 sha256 changed: expected {fixture_record['video_silent_sha256']}, got {h_mp4}"
        )
    finally:
        shutil.rmtree(proj_dir, ignore_errors=True)
