"""Tests for Screen Q&A Select-Beat UI (ui/screens/s_screen_gate.py)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

import config
from ui.state import AppState
from ui.screens import s_screen_gate
from ui.web_routes import _broadcast_moment_picked


def _setup_project(tmp_path: Path, project_name: str = "proj_screen"):
    proj_dir = tmp_path / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    narration = {
        "mode": "screen_qa",
        "title": "Quantum Travel",
        "scenes": [
            {
                "scene_id": 1,
                "text": "Scott Lang discovers the quantum time slip.",
                "word_count": 8,
                "target_seconds": 3.0,
                "visual_beats": [
                    {"beat_id": "1:1", "text": "Scott Lang discovers", "query": "Scott Lang van"},
                    {"beat_id": "1:2", "text": "the quantum time slip.", "query": "Avengers compound quantum"},
                ],
            },
            {
                "scene_id": 2,
                "text": "Tony Stark solves the problem with a Mobius strip.",
                "word_count": 9,
                "target_seconds": 3.5,
                "visual_beats": [
                    {"beat_id": "2:1", "text": "Tony Stark solves the problem", "query": "Tony Stark holographic mobius"},
                    {"beat_id": "2:2", "text": "with a Mobius strip.", "query": "Quantum GPS device test"},
                ],
            },
        ],
    }
    (proj_dir / "narration.json").write_text(json.dumps(narration), "utf-8")
    return proj_dir


def test_screen_gate_load_beats(tmp_path, monkeypatch):
    """Test loading beats from narration.json without comic panels."""
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    _setup_project(tmp_path, "proj1")

    beats = s_screen_gate.load_screen_beats("proj1")
    assert len(beats) == 4
    assert beats[0]["beat_id"] == "1:1"
    assert beats[0]["query"] == "Scott Lang van"
    assert beats[1]["beat_id"] == "1:2"
    assert beats[2]["beat_id"] == "2:1"
    assert beats[3]["beat_id"] == "2:2"


def test_screen_gate_clip_and_card_management(tmp_path, monkeypatch):
    """Test setting and removing clips, stills, and cards for beats."""
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    proj_dir = _setup_project(tmp_path, "proj2")

    # Pick MP4 for beat 1:1
    s_screen_gate.set_beat_clip("proj2", "1:1", video_id="vid123", start=14.5)
    clips = s_screen_gate.load_beat_clips("proj2")
    assert "1:1" in clips
    assert clips["1:1"]["id"] == "vid123"
    assert clips["1:1"]["start"] == 14.5

    # Pick Still for beat 1:2
    s_screen_gate.set_beat_still("proj2", "1:2", image_path="review/custom/still.png")
    stills = s_screen_gate.load_beat_stills("proj2")
    assert stills.get("1:2") == "review/custom/still.png"

    # Set beat 1:1 to Card (clears clip)
    s_screen_gate.set_beat_card("proj2", "1:1")
    clips = s_screen_gate.load_beat_clips("proj2")
    assert "1:1" not in clips

    # Reset beat 1:2 (clears still)
    s_screen_gate.clear_beat_selection("proj2", "1:2")
    stills = s_screen_gate.load_beat_stills("proj2")
    assert "1:2" not in stills


def test_screen_gate_pubsub_listener(tmp_path, monkeypatch):
    """Test that picking a moment via web routes notifies s_screen_gate."""
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    _setup_project(tmp_path, "proj3")

    notified = []

    def _cb(payload):
        notified.append(payload)

    s_screen_gate.add_screen_listener(_cb)

    payload = {"project": "proj3", "beat": "2:1", "video_id": "test_vid", "start": 10.0}
    _broadcast_moment_picked(payload)

    assert len(notified) == 1
    assert notified[0]["beat"] == "2:1"
    assert notified[0]["video_id"] == "test_vid"


def test_screen_gate_build_control(tmp_path, monkeypatch):
    """Test building Flet Control tree for screen_gate screen."""
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    _setup_project(tmp_path, "proj4")

    mock_page = MagicMock()
    mock_page.pubsub = MagicMock()
    state = AppState(project_name="proj4")
    state.pipeline_mode = "screen_qa"

    control = s_screen_gate.build(
        mock_page,
        state,
        on_go=lambda stage: None,
        on_state_change=lambda: None,
    )

    assert control is not None
