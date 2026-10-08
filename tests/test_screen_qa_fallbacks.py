"""Tests for the 4-level never-crash fallback chain in screen_qa:
Level 1: Primary Clip (from manifest)
Level 2: Backup Candidate Clip
Level 3: HD Still / Custom Image (Ken Burns)
Level 4: Text Card (utils/text_card.py) -> NEVER CRASHES
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest
from PIL import Image

from stages.stage_5.schema import Shot
from stages.stage_5.screen_shots import (
    render_screen_shot,
    build_shots_for_screen_qa,
    run_screen_qa_pipeline,
)
from stages.stage_5.clips import verify_shot_contract


def _make_dummy_mp4(out_path: Path, duration: float = 1.0, color: str = "blue") -> Path:
    """Generate a valid contract MP4 (1080x1920, 30fps, h264, yuv420p) using ffmpeg."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(duration * 30)))
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920:r=30",
        "-t", f"{duration:.3f}",
        "-frames:v", str(frames),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _make_dummy_image(out_path: Path, color: tuple = (200, 50, 50)) -> Path:
    """Generate a test image."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1080, 1920), color)
    img.save(out_path)
    return out_path


def test_fallback_level_1_primary_clip(tmp_path):
    """Level 1: Valid primary clip renders successfully."""
    clip_file = _make_dummy_mp4(tmp_path / "primary.mp4", duration=2.0, color="green")
    out_file = tmp_path / "shot_001.mp4"

    shot = Shot(
        shot_id=1,
        scene_id=1,
        duration_seconds=1.5,
        panel_bbox={},
        source_image="",
        motion="push_in",
        caption_text="Testing primary clip",
        clip_path=str(clip_file),
        clip_in=0.0,
        clip_out=1.5,
        clip_id="c1",
    )

    rendered = render_screen_shot(shot, out_file)
    assert rendered.exists()
    assert getattr(shot, "screen_render_level", 0) == 1
    verify_shot_contract(out_file, int(round(1.5 * 30)))


def test_fallback_level_2_backup_candidate(tmp_path):
    """Level 2: Primary clip missing/broken, backup clip succeeds."""
    backup_file = _make_dummy_mp4(tmp_path / "backup.mp4", duration=2.0, color="orange")
    out_file = tmp_path / "shot_002.mp4"

    shot = Shot(
        shot_id=2,
        scene_id=1,
        duration_seconds=1.2,
        panel_bbox={},
        source_image="",
        motion="push_in",
        caption_text="Testing backup clip fallback",
        clip_path=str(tmp_path / "non_existent_clip.mp4"),  # Level 1 fails!
        clip_id="broken_c1",
    )
    # Set backup candidate
    shot.backup_clip_path = str(backup_file)
    shot.backup_clip_in = 0.0
    shot.backup_clip_out = 1.2
    shot.backup_clip_id = "backup_c1"

    rendered = render_screen_shot(shot, out_file)
    assert rendered.exists()
    assert getattr(shot, "screen_render_level", 0) == 2
    assert "fallback" in (shot.clip_fallback or "").lower()
    verify_shot_contract(out_file, int(round(1.2 * 30)))


def test_fallback_level_3_hd_still_ken_burns(tmp_path):
    """Level 3: Both primary and backup clips fail/absent, custom still succeeds with Ken Burns."""
    still_file = _make_dummy_image(tmp_path / "still.png", color=(30, 100, 200))
    out_file = tmp_path / "shot_003.mp4"

    shot = Shot(
        shot_id=3,
        scene_id=1,
        duration_seconds=1.0,
        panel_bbox={},
        source_image="",
        motion="push_in",
        caption_text="Testing HD still Ken Burns fallback",
        clip_path=str(tmp_path / "missing1.mp4"),
        custom_image=str(still_file),  # Level 3 candidate!
    )
    shot.backup_clip_path = str(tmp_path / "missing2.mp4")  # Level 2 fails!

    rendered = render_screen_shot(shot, out_file)
    assert rendered.exists()
    assert getattr(shot, "screen_render_level", 0) == 3
    verify_shot_contract(out_file, int(round(1.0 * 30)))


def test_fallback_level_4_text_card_never_crashes(tmp_path):
    """Level 4: Everything missing -> renders text card using Pillow -> NEVER CRASHES."""
    out_file = tmp_path / "shot_004.mp4"

    shot = Shot(
        shot_id=4,
        scene_id=1,
        duration_seconds=1.0,
        panel_bbox={},
        source_image="",
        motion="push_in",
        caption_text="Quantum navigation solved using mobius strip",
        clip_path=str(tmp_path / "missing1.mp4"),
        custom_image=str(tmp_path / "missing_still.png"),
    )
    shot.backup_clip_path = str(tmp_path / "missing2.mp4")

    # This MUST NOT raise!
    rendered = render_screen_shot(shot, out_file, work_dir=tmp_path / "_work")
    assert rendered.exists()
    assert getattr(shot, "screen_render_level", 0) == 4
    verify_shot_contract(out_file, int(round(1.0 * 30)))


def test_run_screen_qa_pipeline_full_assembly(tmp_path):
    """Test full screen_qa pipeline producing final.mp4 with audio and exact contract."""
    proj_dir = tmp_path / "screen_qa_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    narration = {
        "mode": "screen_qa",
        "title": "Quantum Travel",
        "scenes": [
            {
                "scene_id": 1,
                "text": "Scott Lang proposes the quantum realm for time travel.",
                "word_count": 9,
                "target_seconds": 2.0,
                "visual_beats": [
                    {"beat_id": "1:1", "text": "Scott Lang proposes the quantum realm", "query": "Scott Lang van"},
                    {"beat_id": "1:2", "text": "for time travel.", "query": "Avengers compound"},
                ],
            }
        ],
    }
    (proj_dir / "narration.json").write_text(json.dumps(narration))

    # Generate dummy audio
    audio_path = proj_dir / "audio.wav"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
        "-ar", "24000", "-ac", "1",
        str(audio_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    scene_timings = [{"scene_id": 1, "start": 0.0, "end": 2.0}]

    res = run_screen_qa_pipeline(
        project_name="screen_qa_proj",
        project_root=proj_dir,
        narration=narration,
        audio_path=audio_path,
        audio_duration=2.0,
        scene_timings=scene_timings,
    )

    assert Path(res.final_path).exists()
    assert res.shot_count == 2
    assert res.duration_seconds >= 1.9
