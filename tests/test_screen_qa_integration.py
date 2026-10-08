"""Integration test for screen_qa pipeline (Task 5).

Runs CLI research + narrate for:
'How did the Avengers travel back in time in Endgame?'
"""
import json
import subprocess
import sys
from pathlib import Path
import pytest

from config import get_project_dirs
import stages.screen_pipeline as sp


@pytest.mark.integration
def test_endgame_screen_qa_cli_research_and_narrate(tmp_path, monkeypatch):
    """Integration test running CLI research + narrate end to end."""
    project_slug = "endgame_time_travel_integration"
    project_root = tmp_path / project_slug
    project_root.mkdir(parents=True)

    # Point get_project_dirs to our isolated test directory
    monkeypatch.setattr(
        "stages.screen_pipeline.get_project_dirs",
        lambda p: {"root": project_root, "preprocessed": project_root / "preprocessed"},
    )
    monkeypatch.setattr(
        "stages.stage_1.screen_research.get_project_dirs",
        lambda p: {"root": project_root, "preprocessed": project_root / "preprocessed"},
    )
    monkeypatch.setattr(
        "stages.stage_3.screen_qa.get_project_dirs",
        lambda p: {"root": project_root, "preprocessed": project_root / "preprocessed"},
    )

    question = "How did the Avengers travel back in time in Endgame?"
    argv = [
        "--question", question,
        "--project", project_slug,
        "--stop-after", "narrate",
    ]

    rc = sp.main(argv)
    assert rc == 0, f"screen_pipeline failed with exit code {rc}"

    # 1. Verify screen_context.json
    screen_context_path = project_root / "screen_context.json"
    assert screen_context_path.exists(), "screen_context.json was not created"
    s_ctx = json.loads(screen_context_path.read_text())
    assert s_ctx.get("question") == question
    items = s_ctx.get("items")
    assert isinstance(items, list) and len(items) >= 1
    for item in items:
        assert "entity" in item
        assert "event" in item
        assert "adaptation_title" in item
        assert "year" in item
        assert "summary" in item
        assert "visual_query" in item
        assert "source_urls" in item

    # 2. Verify narration.json
    narration_path = project_root / "narration.json"
    assert narration_path.exists(), "narration.json was not created"
    nar = json.loads(narration_path.read_text())
    assert nar.get("mode") == "screen_qa"
    assert nar.get("title")
    assert nar.get("hook")
    scenes = nar.get("scenes")
    assert isinstance(scenes, list) and len(scenes) >= 1

    # Verify visual_beats contract: dicts with {text, query}
    for scene in scenes:
        assert "text" in scene
        assert "#" not in scene["text"]  # Screen QA does not cite comic issue numbers
        vbs = scene.get("visual_beats")
        assert isinstance(vbs, list) and len(vbs) >= 1
        for beat in vbs:
            assert isinstance(beat, dict)
            assert "text" in beat
            assert "query" in beat
