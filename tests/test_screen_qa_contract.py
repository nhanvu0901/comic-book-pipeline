"""Tests for Screen Q&A Interface Contract and Shot Builder."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from stages.stage_3.schema import Scene
from stages.stage_5.schema import Shot


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "screen_qa_project"


def test_screen_context_contract_schema():
    """Verify screen_context.json conforms to the contract with video-qa/p3-core:
    {question, items:[{entity, event, adaptation_title, year, summary, visual_query, source_urls}]}
    """
    ctx_file = FIXTURE_DIR / "screen_context.json"
    assert ctx_file.exists()
    data = json.loads(ctx_file.read_text())

    assert "question" in data and isinstance(data["question"], str) and data["question"]
    assert "items" in data and isinstance(data["items"], list) and len(data["items"]) > 0

    required_keys = {"entity", "event", "adaptation_title", "year", "summary", "visual_query", "source_urls"}
    for item in data["items"]:
        assert isinstance(item, dict)
        assert required_keys.issubset(item.keys()), f"Item missing keys: {required_keys - set(item.keys())}"
        assert isinstance(item["entity"], str) and item["entity"]
        assert isinstance(item["event"], str) and item["event"]
        assert isinstance(item["adaptation_title"], str) and item["adaptation_title"]
        assert isinstance(item["year"], int) and item["year"] > 1900
        assert isinstance(item["summary"], str) and item["summary"]
        assert isinstance(item["visual_query"], str) and item["visual_query"]
        assert isinstance(item["source_urls"], list) and len(item["source_urls"]) > 0


def test_narration_contract_schema():
    """Verify narration.json conforms to Scene schema with mode 'screen_qa'
    and visual_beats formatted as {text, query} or string.
    """
    nar_file = FIXTURE_DIR / "narration.json"
    assert nar_file.exists()
    data = json.loads(nar_file.read_text())

    assert data.get("mode") == "screen_qa"
    scenes = data.get("scenes") or []
    assert len(scenes) >= 2

    for sc in scenes:
        scene = Scene(
            scene_id=sc["scene_id"],
            text=sc["text"],
            page_ref=sc.get("page_ref", 0),
            panel_ref=sc.get("panel_ref", 0),
            word_count=sc.get("word_count", len(sc["text"].split())),
            target_seconds=sc.get("target_seconds", 4.0),
            is_intro=sc.get("is_intro", False),
            is_outro=sc.get("is_outro", False),
            visual_beats=sc.get("visual_beats", []),
        )
        assert scene.scene_id > 0
        assert len(scene.text) > 0
        for vb in scene.visual_beats:
            if isinstance(vb, dict):
                assert "text" in vb
                # query is either directly provided or optional visual query
                assert "query" in vb or "visual_query" in vb or "text" in vb
            else:
                assert isinstance(vb, str)


def test_screen_shot_builder_without_comic_pages():
    """Shot builder for screen_qa must build valid shots without comic panels
    (no pages_by_number / _panel_pool).
    """
    from stages.stage_5.screen_shots import build_shots_for_screen_qa

    nar_file = FIXTURE_DIR / "narration.json"
    narration = json.loads(nar_file.read_text())

    scene_timings = [
        {"scene_id": 1, "start": 0.0, "end": 4.2},
        {"scene_id": 2, "start": 4.2, "end": 8.1},
        {"scene_id": 3, "start": 8.1, "end": 12.1},
    ]

    shots = build_shots_for_screen_qa(
        narration=narration,
        scene_timings=scene_timings,
        pages_by_number=None,  # No comic panels!
    )

    assert len(shots) == 6  # 3 scenes * 2 beats each
    for s in shots:
        assert isinstance(s, Shot)
        assert s.duration_seconds >= 0.4
        assert s.source_image == ""  # No comic panel source image
        assert s.caption_text != ""

    # Verify total shot duration covers audio timeline exactly
    total_dur = sum(s.duration_seconds for s in shots)
    expected_dur = 12.1
    assert abs(total_dur - expected_dur) < 0.01


def test_screen_shot_builder_durations_cover_timeline_with_whips_or_gaps():
    """Ensure beat durations cover audio timeline without gaps even with whip deductions."""
    from stages.stage_5.screen_shots import build_shots_for_screen_qa

    nar_file = FIXTURE_DIR / "narration.json"
    narration = json.loads(nar_file.read_text())

    # Uneven timings
    scene_timings = [
        {"scene_id": 1, "start": 0.0, "end": 5.0},
        {"scene_id": 2, "start": 5.0, "end": 9.5},
        {"scene_id": 3, "start": 9.5, "end": 15.0},
    ]

    shots = build_shots_for_screen_qa(
        narration=narration,
        scene_timings=scene_timings,
    )

    assert len(shots) == 6
    total_dur = sum(s.duration_seconds for s in shots)
    assert abs(total_dur - 15.0) < 0.01
