import json
from pathlib import Path
import pytest

import config
from ui import bridge
from ui.screens import s_review_gate
from ui.screens.s_review_gate import (
    _load_clips_manifest,
    _remove_clip_for_beat,
    _load_beat_durations,
    clear_project_locks_and_clips,
)


def _patch_roots(monkeypatch, root: Path):
    monkeypatch.setattr(config, "PROJECTS_ROOT", root)
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", root)
    monkeypatch.setattr(s_review_gate, "PROJECTS_ROOT", root)


def test_load_and_remove_clips_manifest(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
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


def test_load_beat_durations_from_both_paths(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    proj_dir = tmp_path / "test_proj"

    # 1. From cache/tts/status.json (where BackgroundTTSRunner writes)
    cache_tts_dir = proj_dir / "cache" / "tts"
    cache_tts_dir.mkdir(parents=True)
    status_data = {
        "status": "ready",
        "beat_durations": {
            "1:0": 2.4,
            "1:1": 3.1,
        }
    }
    (cache_tts_dir / "status.json").write_text(json.dumps(status_data), "utf-8")

    durs = _load_beat_durations("test_proj")
    assert durs.get("1:0") == 2.4
    assert durs.get("1:1") == 3.1

    # 2. From review/tts_status.json
    review_dir = proj_dir / "review"
    review_dir.mkdir(parents=True)
    status_data_override = {
        "status": "ready",
        "beat_durations": {
            "1:0": 2.9,
            "1:1": 3.5,
        }
    }
    (review_dir / "tts_status.json").write_text(json.dumps(status_data_override), "utf-8")

    durs_review = _load_beat_durations("test_proj")
    assert durs_review.get("1:0") == 2.9
    assert durs_review.get("1:1") == 3.5


def test_narration_edit_invalidation_rule(tmp_path, monkeypatch):
    """
    Binding correction / spec rule:
    Editing the narration with the flag ON clears ALL panel + MP4 selections for the project.
    Directly tests clear_project_locks_and_clips.
    """
    _patch_roots(monkeypatch, tmp_path)
    proj_dir = tmp_path / "test_proj"
    review_dir = proj_dir / "review"
    clips_dir = review_dir / "clips"
    clips_dir.mkdir(parents=True)

    manifest_file = clips_dir / "clips.json"
    manifest_file.write_text(json.dumps({"clips": [{"id": "c1", "beat": "1:0"}]}), "utf-8")

    locks_file = review_dir / "locks.json"
    initial_locks_doc = {
        "locks": {"1:0": [{"page": 2, "panel": 1}]},
        "approved": True,
        "approved_at": "2026-10-08T10:00:00",
    }
    locks_file.write_text(json.dumps(initial_locks_doc), "utf-8")

    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    assert manifest_file.exists()

    # Call actual module function
    updated_doc = clear_project_locks_and_clips("test_proj", initial_locks_doc)

    # 1. Manifest file deleted
    assert not manifest_file.exists()

    # 2. In-memory locks doc cleared
    assert updated_doc["locks"] == {}

    # 3. Disk locks.json cleared
    persisted_doc = json.loads(locks_file.read_text("utf-8"))
    assert persisted_doc["locks"] == {}
