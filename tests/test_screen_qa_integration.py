"""LIVE integration test for screen_qa: CLI research + narrate for a real screen question.

Hits You.com web search and the writer LLM chain, so it needs YDC_API_KEY and OPENROUTER_API_KEY
(skipped without them) and costs a few cents:

    pytest -m integration tests/test_screen_qa_integration.py -s

The assertions are on the CONTRACT and structure (shared with video-qa/p3-visual), not on exact
wording: the question is a normal "How did ..." trivia question about a famous film.
"""
import json
import os

import pytest

import config  # noqa: F401  (loads .env so the skipif below sees the keys)
import stages.screen_pipeline as sp
from stages.stage_3 import screen_qa as sq

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.getenv("YDC_API_KEY") and os.getenv("OPENROUTER_API_KEY")),
        reason="live screen_qa test needs YDC_API_KEY and OPENROUTER_API_KEY",
    ),
]

QUESTION = "How did the Avengers travel back in time in Endgame?"


def test_endgame_screen_qa_cli_research_and_narrate(tmp_path, monkeypatch):
    project_slug = "endgame_time_travel_integration"
    project_root = tmp_path / project_slug
    project_root.mkdir(parents=True)

    dirs = lambda p: {"root": project_root, "preprocessed": project_root / "preprocessed"}  # noqa: E731
    for module in ("stages.screen_pipeline", "stages.stage_1.screen_research", "stages.stage_3.screen_qa"):
        monkeypatch.setattr(f"{module}.get_project_dirs", dirs)

    rc = sp.main(["--question", QUESTION, "--project", project_slug, "--stop-after", "narrate"])
    assert rc == 0, f"screen_pipeline failed with exit code {rc}"

    # ── screen_context.json: the contract p3-visual reads ──
    ctx = json.loads((project_root / "screen_context.json").read_text())
    assert ctx["question"] == QUESTION
    items = ctx["items"]
    assert items, "research produced no items"
    for item in items:
        for key in ("entity", "event", "adaptation_title", "year", "summary", "visual_query", "source_urls"):
            assert key in item, key
        assert isinstance(item["year"], int)
        assert item["source_urls"], f"{item['entity']} has no grounded source URL"
    assert not (project_root / "answer_context.json").exists()    # comic research never ran
    assert not (project_root / "comic_context.json").exists()

    # ── narration.json: Q&A structure, title + year cited, {text, query} beats ──
    nar = json.loads((project_root / "narration.json").read_text())
    assert nar["mode"] == "screen_qa"
    assert nar["title"] and nar["hook"]
    scenes = nar["scenes"]
    assert len(scenes) >= 2 * len(items) + 2
    assert scenes[0]["is_intro"] and scenes[0]["text"] == nar["hook"]
    assert scenes[-1]["is_outro"]

    body_text = " ".join(s["text"] for s in scenes)
    assert "#" not in body_text                                   # no comic issue numbers
    first = items[0]
    assert sq._cites(body_text, first["adaptation_title"], first["year"]), (
        f"narration never cites {first['adaptation_title']!r} ({first['year']})")

    for scene in scenes:
        beats = scene["visual_beats"]
        assert beats, scene["text"]
        for beat in beats:
            assert isinstance(beat, dict) and beat["text"].strip() and beat["query"].strip()
        assert sq._tokens(" ".join(b["text"] for b in beats)) == sq._tokens(scene["text"])
