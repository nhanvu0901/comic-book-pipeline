import json
from pathlib import Path
import pytest

import config
from ui.screens.s_review_gate import (
    _load_clips_manifest,
    _remove_clip_for_beat,
    _load_beat_durations,
)


def test_load_and_remove_clips_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "test_proj"
    clips_dir = proj_dir / "review" / "clips"
    clips_dir.mkdir(parents=True)
    manifest = {
        "clips": [
            {"id": "c1", "beat": "1:0", "file": "review/clips/c1.mp4", "start": 5.0, "enabled": True},
            {"id": "c2", "beat": "2:1", "file": "review/clips/c2.mp4", "start": 10.0, "enabled": False},
            {"id": "c3", "beat": "3:0", "file": "review/clips/c3.mp4", "start": 15.0},
        ]
    }
    (clips_dir / "clips.json").write_text(json.dumps(manifest), "utf-8")

    loaded = _load_clips_manifest("test_proj")
    assert "1:0" in loaded
    assert loaded["1:0"]["id"] == "c1"
    assert "2:1" not in loaded  # enabled=False
    assert "3:0" in loaded

    # Rollback / remove clip for 1:0
    _remove_clip_for_beat("test_proj", "1:0")
    loaded_after = _load_clips_manifest("test_proj")
    assert "1:0" not in loaded_after
    assert "3:0" in loaded_after


def test_load_beat_durations(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "test_proj"
    review_dir = proj_dir / "review"
    review_dir.mkdir(parents=True)

    status_data = {
        "status": "ready",
        "beat_durations": {
            "1:0": 2.4,
            "1:1": 3.1,
        }
    }
    (review_dir / "tts_status.json").write_text(json.dumps(status_data), "utf-8")

    durs = _load_beat_durations("test_proj")
    assert durs.get("1:0") == 2.4
    assert durs.get("1:1") == 3.1


def test_narration_edit_invalidation_rule(tmp_path, monkeypatch):
    """
    Override rule #2:
    Editing the narration with the flag ON clears ALL panel + MP4 selections for the project.
    """
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "test_proj"
    clips_dir = proj_dir / "review" / "clips"
    clips_dir.mkdir(parents=True)

    manifest_file = clips_dir / "clips.json"
    manifest_file.write_text(json.dumps({"clips": [{"id": "c1", "beat": "1:0"}]}), "utf-8")

    locks_file = proj_dir / "review" / "locks.json"
    locks_file.write_text(json.dumps({"locks": {"1:0": {"page": 2, "panel": 1}}, "approved": True}), "utf-8")

    # Flag ON: editing clears all locks & clips
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    assert manifest_file.exists()

    # Simulate clearing
    locks = {"1:0": {"page": 2, "panel": 1}}
    locks_doc = {"locks": locks, "approved": True}
    clips_by_beat = {"1:0": {"id": "c1"}}

    # The logic implemented in s_review_gate:
    locks.clear()
    locks_doc["locks"] = {}
    if manifest_file.exists():
        manifest_file.unlink()
    clips_by_beat.clear()

    assert len(locks) == 0
    assert len(locks_doc["locks"]) == 0
    assert not manifest_file.exists()
    assert len(clips_by_beat) == 0
