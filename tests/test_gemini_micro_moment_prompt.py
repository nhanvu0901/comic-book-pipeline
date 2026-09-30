"""Tests for Stage 2 Gemini prompt generation in micro_moment mode and script anchoring."""
import json
from pathlib import Path
import pytest

import stages.stage_3.gemini_prompt as gp


def test_micro_moment_prompt_generation(tmp_path, monkeypatch):
    """Ensure micro_moment mode generates the grounded micro-moment prompt, NOT the Q&A prompt."""
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "batman_test"
    proj_dir.mkdir(parents=True)

    comic_ctx = {
        "title": "Batman #5 (2026)",
        "series": "Batman",
        "issue": "5",
        "year": "2026",
        "pipeline_mode": "micro_moment",
        "characters": ["Batman / Bruce Wayne", "Annika Zeller"],
        "target_moment": "Batman is trapped in a collapsing vault and Annika reveals her true identity.",
    }
    (proj_dir / "comic_context.json").write_text(json.dumps(comic_ctx, indent=2), encoding="utf-8")

    candidate = {
        "character": "Batman / Bruce Wayne",
        "series_issue_year": "Batman #5 (2026)",
        "what_visibly_happens": "Batman is trapped in a collapsing vault and Annika reveals her true identity.",
        "summary": "Dramatic confrontation in the underground vault.",
        "claim_citation": {
            "title": "CBR Review",
            "url": "https://www.cbr.com/batman-5-review/",
            "quote": "The twist ending redefines Annika's motives completely."
        },
        "verbatim_sentence": "The twist ending redefines Annika's motives completely.",
        "source_url": "https://www.cbr.com/batman-5-review/",
        "evidence_urls": ["https://www.cbr.com/batman-5-review/"],
        "verdict": "CONFIRMED"
    }
    (proj_dir / "scout_candidate.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")

    prompt_text, file_path = gp.generate_gemini_writer_prompt("batman_test")

    assert file_path.exists()
    assert "GRIMFRAME MICRO-MOMENT WRITER (GROUNDED)" in prompt_text
    assert "THE QUESTION:" not in prompt_text
    assert "Batman / Bruce Wayne" in prompt_text
    assert "Batman is trapped in a collapsing vault" in prompt_text
    assert "The twist ending redefines Annika's motives completely." in prompt_text
    assert "CONFIRMED" in prompt_text
    assert "HOOK AND FIRST BEAT" in prompt_text
    assert "A question is allowed" in " ".join(prompt_text.split())
    assert "OPENING CHAIN" in prompt_text
    assert "same concrete person-and-event promise" in prompt_text
    assert "NEXT FACT" in prompt_text


def test_micro_moment_script_can_be_approved_before_pages_exist(tmp_path, monkeypatch):
    """Stage 2 must save a draft before Download and Preprocess create any page files."""
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    import stages.stage_3.pipeline as pl
    monkeypatch.setattr(pl, "PROJECTS_ROOT", tmp_path)
    project = tmp_path / "micro_before_download"
    project.mkdir()
    (project / "comic_context.json").write_text(
        json.dumps({"title": "A comic moment", "pipeline_mode": "micro_moment"}),
        encoding="utf-8",
    )

    narration = gp.parse_and_save_script(
        "micro_before_download",
        "A hero stops the machine.\n\nThe machine starts to fall, and the hero holds it long enough for everyone to leave. The hero then lets it go.",
        log=lambda _message: None,
    )

    assert (project / "narration.json").exists()
    assert narration["scenes"]
    assert {scene["page_ref"] for scene in narration["scenes"]} == {1}

    prep = project / "preprocessed"
    prep.mkdir()
    for number in (2, 3, 4):
        (prep / f"page_{number:03d}.json").write_text(json.dumps({
            "page_number": number, "is_story_page": True,
        }), encoding="utf-8")

    assert gp.reanchor_narration_to_pages("micro_before_download", log=lambda _message: None)
    updated = json.loads((project / "narration.json").read_text(encoding="utf-8"))
    assert updated["scenes"][0]["page_ref"] == 2
    assert all(scene["page_ref"] > 1 for scene in updated["scenes"])
    assert [beat["page_refs"] for beat in updated["beats"]] == [
        [scene["page_ref"]] for scene in updated["scenes"]
    ]


def test_micro_moment_reanchor_narration(tmp_path, monkeypatch):
    """Ensure reanchor_narration_to_pages spreads beats across preprocessed story pages."""
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    import stages.stage_3.pipeline as pl
    monkeypatch.setattr(pl, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "micro_reanchor_test"
    proj_dir.mkdir(parents=True)

    comic_ctx = {
        "title": "Batman #5",
        "pipeline_mode": "micro_moment",
        "characters": ["Batman"],
    }
    (proj_dir / "comic_context.json").write_text(json.dumps(comic_ctx), encoding="utf-8")

    # Initial script parsed before downloading pages (all default to page 1)
    narration_data = {
        "mode": "micro_moment",
        "total_word_count": 120,
        "scenes": [
            {"scene_id": 1, "text": "Hook line.", "page_ref": 1, "is_intro": True},
            {"scene_id": 2, "text": "First body beat.", "page_ref": 1},
            {"scene_id": 3, "text": "Second body beat.", "page_ref": 1},
            {"scene_id": 4, "text": "Third body beat.", "page_ref": 1},
            {"scene_id": 5, "text": "Outro punchline.", "page_ref": 1, "is_outro": True},
        ],
        "beats": [
            {"id": 1, "voiceover": "Hook line.", "page_refs": [1]},
            {"id": 2, "voiceover": "First body beat.", "page_refs": [1]},
            {"id": 3, "voiceover": "Second body beat.", "page_refs": [1]},
            {"id": 4, "voiceover": "Third body beat.", "page_refs": [1]},
            {"id": 5, "voiceover": "Outro punchline.", "page_refs": [1]},
        ]
    }
    (proj_dir / "narration.json").write_text(json.dumps(narration_data, indent=2), encoding="utf-8")

    # Mock preprocessed/page_*.json with pages 1..10 (page 1 is cover, 2..9 are story, 10 is ad)
    prep_dir = proj_dir / "preprocessed"
    prep_dir.mkdir()
    pages = [
        {"page_number": 1, "page_type": "cover", "is_story_page": False},
        {"page_number": 2, "page_type": "story", "is_story_page": True},
        {"page_number": 3, "page_type": "story", "is_story_page": True},
        {"page_number": 4, "page_type": "story", "is_story_page": True},
        {"page_number": 5, "page_type": "story", "is_story_page": True},
        {"page_number": 6, "page_type": "story", "is_story_page": True},
        {"page_number": 7, "page_type": "story", "is_story_page": True},
        {"page_number": 8, "page_type": "story", "is_story_page": True},
        {"page_number": 9, "page_type": "story", "is_story_page": True},
        {"page_number": 10, "page_type": "ad", "is_story_page": False},
    ]
    for p in pages:
        (prep_dir / f"page_{p['page_number']:03d}.json").write_text(json.dumps(p), encoding="utf-8")

    # Run reanchor
    ok = gp.reanchor_narration_to_pages("micro_reanchor_test")
    assert ok is True

    updated_nar = json.loads((proj_dir / "narration.json").read_text(encoding="utf-8"))
    scenes = updated_nar["scenes"]
    beats = updated_nar["beats"]

    # Intro should anchor to first story page (2)
    assert scenes[0]["page_ref"] == 2
    assert beats[0]["page_refs"] == [2]

    # Outro should anchor to last story page (9)
    assert scenes[-1]["page_ref"] == 9
    assert beats[-1]["page_refs"] == [9]

    # Body scenes should be spread across story pages, not stuck on 1
    for s in scenes:
        assert s["page_ref"] >= 2
        assert s["page_ref"] <= 9
    for b in beats:
        assert b["page_refs"][0] >= 2
        assert b["page_refs"][0] <= 9
