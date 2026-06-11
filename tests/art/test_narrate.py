"""Tests for art_pipeline.narrate (Task 7: A4b narration)."""
import json
import pytest
from art_pipeline import narrate

PAGES = [{
    "page_number": 1, "is_story_page": True, "issue_label": "Wheat Field",
    "page_summary": "A wheat field.", "source_image": "/a.jpg",
    "panels": [
        {"index": 0, "bbox": {"x": 0, "y": 0, "w": 100, "h": 100},
         "description": "the full canvas, a golden wheat field under churning clouds"},
        {"index": 1, "bbox": {"x": 0, "y": 0, "w": 50, "h": 50},
         "description": "a dark cypress tree flame-like against the sky"},
    ],
}]
CTX = {"title": "Wheat Field with Cypresses", "plot_summary": "x" * 700,
       "wiki_url": "http://w", "summary": {"characters": []}, "artworks": [],
       "mode": "painting_deep_dive"}

GOOD = json.dumps({
    "title": "The Field Van Gogh Couldn't Leave",
    "hook": "Van Gogh painted this field three times, from inside an asylum.",
    "scenes": (
        [{"text": "Van Gogh painted this field three times, from inside an asylum.",
          "page_ref": 1, "panel_ref": 0, "is_intro": True, "is_outro": False}]
        + [{"text": f"Grounded fact sentence number {i} about the painting here.",
            "page_ref": 1, "panel_ref": -1, "is_intro": False, "is_outro": False}
           for i in range(2, 8)]
        + [{"text": "Wheat Field with Cypresses hangs in the Met today.",
            "page_ref": 1, "panel_ref": 0, "is_intro": False, "is_outro": True}]
    ),
})


def test_parse_and_build_narration_dict():
    n = narrate.build_narration_from_raw(GOOD, PAGES, CTX, "painting_deep_dive",
                                         "proj", "model-x", log=lambda m: None)
    assert n["mode"] == "painting_deep_dive"
    assert n["scenes"][0]["is_intro"] is True
    assert n["scenes"][-1]["is_outro"] is True
    assert n["total_word_count"] > 0
    assert all(s["page_ref"] == 1 for s in n["scenes"])


def test_rejects_bad_page_ref():
    bad = json.loads(GOOD)
    bad["scenes"][2]["page_ref"] = 99
    with pytest.raises(ValueError, match="page_ref"):
        narrate.build_narration_from_raw(json.dumps(bad), PAGES, CTX,
                                         "painting_deep_dive", "p", "m",
                                         log=lambda m: None)


def test_rejects_too_few_scenes():
    bad = json.loads(GOOD)
    bad["scenes"] = bad["scenes"][:3]
    with pytest.raises(ValueError, match="scenes"):
        narrate.build_narration_from_raw(json.dumps(bad), PAGES, CTX,
                                         "painting_deep_dive", "p", "m",
                                         log=lambda m: None)


def test_rejects_non_integer_page_ref():
    """LLM returning "page_ref": "one" must become a contextual ValueError
    (caught by write_narration's retry loop), not a raw TypeError/ValueError."""
    bad = json.loads(GOOD)
    bad["scenes"][2]["page_ref"] = "one"
    with pytest.raises(ValueError, match="non-integer"):
        narrate.build_narration_from_raw(json.dumps(bad), PAGES, CTX,
                                         "painting_deep_dive", "p", "m",
                                         log=lambda m: None)


def test_unknown_mode_raises():
    with pytest.raises(KeyError):
        narrate.mode_prompt_block("not_a_mode")


# ── cap_facts ────────────────────────────────────────────────────────────────

def test_cap_facts_long_input():
    """Input longer than max_chars is capped and a truncation marker is appended."""
    long_text = "A" * 30_000
    result = narrate.cap_facts(long_text, max_chars=24_000)
    assert len(result) > 24_000          # marker adds a few chars
    assert result.startswith("A" * 24_000)
    assert "[... facts truncated for prompt length ...]" in result


def test_cap_facts_short_input():
    """Input shorter than or equal to max_chars is returned unchanged."""
    short_text = "Van Gogh painted this field. " * 100  # ~2800 chars
    result = narrate.cap_facts(short_text)
    assert result == short_text
