"""Tests for art_pipeline.narrate (Task 7: A4b narration)."""
import json
import pytest
from art_pipeline import narrate

# ── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def pages():
    return [{
        "page_number": 1, "is_story_page": True, "issue_label": "Circus Sideshow",
        "page_summary": "A gas-lit circus stage.", "source_image": "/a.jpg",
        "panels": [
            {"index": 0, "bbox": {"x": 0,   "y": 0,  "w": 100, "h": 100},
             "description": "the showman leaning into the gaslight"},
            {"index": 1, "bbox": {"x": 100, "y": 0,  "w": 100, "h": 100},
             "description": "the trombone player stands dead center"},
            {"index": 2, "bbox": {"x": 0,   "y": 100,"w": 100, "h": 100},
             "description": "gas lamps dots of pure orange"},
            {"index": 3, "bbox": {"x": 100, "y": 100,"w": 100, "h": 100},
             "description": "row of onlookers melts into silhouette"},
        ],
    }]


@pytest.fixture
def ctx():
    return {
        "title": "Circus Sideshow",
        "plot_summary": "x" * 700,
        "wiki_url": "http://w",
        "summary": {"characters": [{"name": "Georges Seurat"}]},
        "artworks": [],
        "mode": "painting_deep_dive",
    }


# ── v2 raw builder ────────────────────────────────────────────────────────────

def _raw_scenes_v2():
    """10 scenes, valid variety: full(intro) r0 x r1 x r2 f r3 x full(outro)."""
    mk = lambda i, text, vis, **kw: {"text": text, "page_ref": 1, "panel_ref": -1,
                                     "visual": vis, **kw}
    return [
        mk(1, "This Circus Sideshow corner hides a riot — look closer.",
           {"kind": "painting_full"}, is_intro=True),
        mk(2, "Here the showman leans into the gaslight.", {"kind": "painting_region", "panel_ref": 0}),
        mk(3, "Seurat painted this in his cramped Paris studio.",
           {"kind": "related", "subject": "photograph portrait of Georges Seurat"}),
        mk(4, "The trombone player stands dead center, frozen.", {"kind": "painting_region", "panel_ref": 1}),
        mk(5, "Critics at the 1888 Salon mocked the stiff figures.",
           {"kind": "related", "subject": "Salon des Independants 1888 Paris photograph"}),
        mk(6, "Look at the gas lamps — dots of pure orange.", {"kind": "painting_region", "panel_ref": 2}),
        mk(7, "The whole canvas glows like a stage set.", {"kind": "painting_full"}),
        mk(8, "A row of onlookers melts into silhouette.", {"kind": "painting_region", "panel_ref": 3}),
        mk(9, "X-rays later revealed he reworked the railing.",
           {"kind": "related", "subject": "Circus Sideshow Seurat x-ray infrared analysis"}),
        mk(10, "Circus Sideshow hangs in The Met today.", {"kind": "painting_full"}, is_outro=True),
    ]


def _raw_v2():
    return json.dumps({"title": "Seurat's Hidden Stage", "hook": "h",
                       "scenes": _raw_scenes_v2()})


# ── v2 tests ──────────────────────────────────────────────────────────────────

def test_build_v2_emits_plan(pages, ctx):
    n, plan = narrate.build_narration_from_raw(_raw_v2(), pages, ctx, "painting_deep_dive",
                                               "proj", "model-x")
    assert len(n["scenes"]) == 10
    by_id = {d["scene_id"]: d for d in plan}
    assert by_id[3]["kind"] == "related"
    assert by_id[3]["motion"] == "pan_right"
    assert by_id[2]["motion"] == "zoom_in" and by_id[4]["motion"] == "zoom_out"
    # related scenes keep panel_ref -1 in narration (hunt re-points later)
    s3 = [s for s in n["scenes"] if s["scene_id"] == 3][0]
    assert s3["panel_ref"] == -1


def test_build_v2_variety_violation_raises(pages, ctx):
    scenes = _raw_scenes_v2()
    scenes[3]["visual"] = {"kind": "painting_region", "panel_ref": 0}  # reuse region 0
    raw = json.dumps({"title": "t", "hook": "h", "scenes": scenes})
    with pytest.raises(ValueError, match="region"):
        narrate.build_narration_from_raw(raw, pages, ctx, "painting_deep_dive", "p", "m")


def test_build_v2_min_scenes(pages, ctx):
    scenes = _raw_scenes_v2()[:8]
    raw = json.dumps({"title": "t", "hook": "h", "scenes": scenes})
    with pytest.raises(ValueError, match="scenes"):
        narrate.build_narration_from_raw(raw, pages, ctx, "painting_deep_dive", "p", "m")


def test_build_v2_string_scene_raises_value_error(pages, ctx):
    """An LLM returning a bare string in "scenes" must become a ValueError
    (caught by write_narration's retry loop), never an AttributeError."""
    scenes = _raw_scenes_v2()
    scenes[2] = "just a string"
    raw = json.dumps({"title": "t", "hook": "h", "scenes": scenes})
    with pytest.raises(ValueError, match="not a JSON object"):
        narrate.build_narration_from_raw(raw, pages, ctx, "painting_deep_dive", "p", "m")


def test_build_v2_string_visual_raises_value_error(pages, ctx):
    """An LLM returning "visual": "painting_full" (string, not object) must
    surface as a ValueError from parse_visual, never an AttributeError."""
    scenes = _raw_scenes_v2()
    scenes[2]["visual"] = "painting_full"
    raw = json.dumps({"title": "t", "hook": "h", "scenes": scenes})
    with pytest.raises(ValueError, match="must be a JSON object"):
        narrate.build_narration_from_raw(raw, pages, ctx, "painting_deep_dive", "p", "m")


def test_hook_concreteness():
    ctx_local = {"title": "Circus Sideshow",
                 "summary": {"characters": [{"name": "Georges Seurat"}]}}
    assert narrate._hook_is_concrete("Seurat hid a riot in this corner", ctx_local)
    assert narrate._hook_is_concrete("This Circus Sideshow detail fooled critics", ctx_local)
    assert not narrate._hook_is_concrete("Ever wonder about art history?", ctx_local)


# ── Legacy / compat tests (still apply after v2 rewrite) ─────────────────────

# Convenience: build a raw v2 payload that passes all validations,
# for tests that only care about one field at a time.
_COMPAT_PAGES = [{
    "page_number": 1, "is_story_page": True, "issue_label": "Circus Sideshow",
    "page_summary": "A gas-lit circus stage.", "source_image": "/a.jpg",
    "panels": [
        {"index": 0, "bbox": {"x": 0,   "y": 0,   "w": 100, "h": 100},
         "description": "showman in gaslight"},
        {"index": 1, "bbox": {"x": 100, "y": 0,   "w": 100, "h": 100},
         "description": "trombone player"},
        {"index": 2, "bbox": {"x": 0,   "y": 100, "w": 100, "h": 100},
         "description": "gas lamps"},
        {"index": 3, "bbox": {"x": 100, "y": 100, "w": 100, "h": 100},
         "description": "onlookers silhouette"},
    ],
}]
_COMPAT_CTX = {
    "title": "Circus Sideshow",
    "plot_summary": "x" * 700,
    "wiki_url": "http://w",
    "summary": {"characters": [{"name": "Georges Seurat"}]},
    "artworks": [],
    "mode": "painting_deep_dive",
}

def _make_good_v2():
    """Minimal valid v2 raw with 10 scenes that pass all gates."""
    return _raw_v2()  # uses _raw_scenes_v2 which is fully valid


def test_parse_and_build_narration_dict():
    n, _plan = narrate.build_narration_from_raw(
        _make_good_v2(), _COMPAT_PAGES, _COMPAT_CTX,
        "painting_deep_dive", "proj", "model-x", log=lambda m: None,
    )
    assert n["mode"] == "painting_deep_dive"
    assert n["scenes"][0]["is_intro"] is True
    assert n["scenes"][-1]["is_outro"] is True
    assert n["total_word_count"] > 0
    assert all(s["page_ref"] == 1 for s in n["scenes"])


def test_rejects_bad_page_ref():
    bad = json.loads(_make_good_v2())
    bad["scenes"][2]["page_ref"] = 99
    with pytest.raises(ValueError, match="page_ref"):
        narrate.build_narration_from_raw(json.dumps(bad), _COMPAT_PAGES, _COMPAT_CTX,
                                         "painting_deep_dive", "p", "m",
                                         log=lambda m: None)


def test_rejects_too_few_scenes():
    bad = json.loads(_make_good_v2())
    bad["scenes"] = bad["scenes"][:3]
    with pytest.raises(ValueError, match="scenes"):
        narrate.build_narration_from_raw(json.dumps(bad), _COMPAT_PAGES, _COMPAT_CTX,
                                         "painting_deep_dive", "p", "m",
                                         log=lambda m: None)


def test_rejects_non_integer_page_ref():
    """LLM returning "page_ref": "one" must become a contextual ValueError."""
    bad = json.loads(_make_good_v2())
    bad["scenes"][2]["page_ref"] = "one"
    with pytest.raises(ValueError, match="non-integer"):
        narrate.build_narration_from_raw(json.dumps(bad), _COMPAT_PAGES, _COMPAT_CTX,
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
    assert len(result) > 24_000
    assert result.startswith("A" * 24_000)
    assert "[... facts truncated for prompt length ...]" in result


def test_cap_facts_short_input():
    """Input shorter than or equal to max_chars is returned unchanged."""
    short_text = "Van Gogh painted this field. " * 100  # ~2800 chars
    result = narrate.cap_facts(short_text)
    assert result == short_text
