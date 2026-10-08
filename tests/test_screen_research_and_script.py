import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

import config
from stages.stage_1.screen_research import (
    research_screen,
    build_screen_context,
)
from stages.stage_3.screen_narration import (
    write_screen_narration,
)


def test_screen_research_creates_context_without_comic_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "test_screen_proj"
    proj_dir.mkdir(parents=True)

    mock_llm_data = {
        "work_title": "Avengers: Endgame",
        "release_year": "2019",
        "citation": "Avengers: Endgame (2019)",
        "summary": "The Avengers used the Quantum Realm to travel back in time.",
        "items": [
            {
                "title": "Quantum Tunnel Discovery",
                "description": "Ant-Man returns from the Quantum Realm and suggests time travel.",
                "drawable_moment": "Scott Lang explains Quantum Realm at Avengers facility",
                "clip_query": "Scott Lang Quantum Realm Avengers Endgame clip"
            },
            {
                "title": "Stark Navigates Space-Time",
                "description": "Tony Stark designs the Mobius strip GPS device.",
                "drawable_moment": "Tony Stark solves time travel Mobius strip simulation",
                "clip_query": "Tony Stark discovers time travel simulation Endgame clip"
            }
        ]
    }

    with patch("stages.stage_1.screen_research._call_screen_research_llm", return_value=mock_llm_data):
        res = research_screen("How did the Avengers travel back in time in Endgame?")
        ctx_file = build_screen_context("How did the Avengers travel back in time in Endgame?", res, "test_screen_proj")

        assert ctx_file.exists()
        saved = json.loads(ctx_file.read_text("utf-8"))
        assert saved["mode"] == "screen_qa"
        assert saved["citation"] == "Avengers: Endgame (2019)"
        assert len(saved["items"]) == 2
        # Never contains comic reader_url
        assert "reader_urls" not in saved
        assert "reader_url" not in saved["items"][0]


def test_screen_narration_cites_movie_and_year(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "test_screen_proj"
    proj_dir.mkdir(parents=True)

    ctx_data = {
        "mode": "screen_qa",
        "question": "How did the Avengers travel back in time in Endgame?",
        "work_title": "Avengers: Endgame",
        "release_year": "2019",
        "citation": "Avengers: Endgame (2019)",
        "items": [
            {
                "title": "Quantum Realm",
                "description": "Using Pym Particles through the Quantum Realm.",
                "drawable_moment": "Avengers walking in white Quantum suits",
                "clip_query": "Avengers Endgame white suits walk scene clip"
            }
        ]
    }
    (proj_dir / "screen_context.json").write_text(json.dumps(ctx_data), "utf-8")

    mock_narration_dict = {
        "title": "How Avengers Traveled Back in Time",
        "mode": "screen_qa",
        "scenes": [
            {
                "scene_id": 1,
                "text": "In Avengers: Endgame (2019), Earth's mightiest heroes ventured into the Quantum Realm to reverse the snap.",
                "visual_beats": [
                    "In Avengers: Endgame (2019),",
                    "Earth's mightiest heroes ventured into the Quantum Realm to reverse the snap."
                ]
            }
        ]
    }

    with patch("stages.stage_3.screen_narration._call_screen_narration_llm", return_value=mock_narration_dict):
        nar_file = write_screen_narration("test_screen_proj")
        assert nar_file.exists()
        saved = json.loads(nar_file.read_text("utf-8"))
        assert saved["mode"] == "screen_qa"
        scene0_text = saved["scenes"][0]["text"]
        assert "Avengers: Endgame (2019)" in scene0_text
        # Verbatim invariant check
        vbeats = saved["scenes"][0]["visual_beats"]
        assert " ".join(vbeats) == scene0_text or "".join(vbeats).replace(" ", "") == scene0_text.replace(" ", "")
