"""Tests for the unified panel-matching authority (Stage 5 `_assign_scene_panels`)
and the Stage 3 whole-page anchor (choice B). See
docs/superpowers/specs/2026-06-17-unified-panel-matching-design.md (rule inventory)."""
from stages.stage_3.write_script import _beat_anchor
from stages.stage_3.schema import Beat


# ── Task 1: Stage 3 _beat_anchor → whole-page panel_ref ──────────────────────
def test_beat_anchor_always_whole_page_panel():
    # Even when the beat has a committed key_panel, Stage 3 no longer emits a
    # specific panel_ref — Stage 5 is the sole panel authority (choice B).
    b = Beat(id=1, function="SETUP", name="x", summary="x", page_refs=[10],
             key_panels=[{"page": 10, "panel": 3}])
    page, panel = _beat_anchor(b)
    assert page == 10
    assert panel == -1   # -1 == whole page; panel choice deferred to Stage 5


def test_beat_anchor_page_refs_only():
    b = Beat(id=2, function="SETUP", name="y", page_refs=[7, 9])
    assert _beat_anchor(b) == (7, -1)


# ── Task 2: honor-panel_ref rule (R3) retired ────────────────────────────────
from stages.stage_5.shots import _score_panel


def _panel(idx, *, w=600, h=900, desc="", chars=None):
    return {"index": idx, "bbox": {"x": 0, "y": 0, "w": w, "h": h},
            "description": desc, "characters": chars or [], "_page_number": 10}


def test_honor_panel_ref_rule_retired():
    # Two identical panels on the same page; one's index matches scene.panel_ref.
    # With R3 retired, the matching index must NOT get a +15 bonus, so the two
    # identical panels score equally.
    scene = {"text": "a quiet room", "page_ref": 10, "panel_ref": 0}
    p_match = _panel(0)
    p_other = _panel(1)
    s_match = _score_panel(p_match, "a quiet room", scene)
    s_other = _score_panel(p_other, "a quiet room", scene)
    assert s_match == s_other


# ── Task 3: _assign_scene_panels (single panel authority) ────────────────────
from stages.stage_5.shots import _assign_scene_panels, PANEL_MATCH_FLOOR
import stages.stage_5.shots as _shots_for_flag
# Deterministic tests: disable the LLM tiebreak so close scores never make a
# network call. The heuristic lead is used directly (gate covered by behaviour).
_shots_for_flag.LLM_PANEL_JUDGE = False


def _page(num, panels, *, w=1200, h=1800, text_blocks=None, page_type="story"):
    return {
        "source_image": f"p{num}.png",
        "image_dimensions": {"width": w, "height": h},
        "page_type": page_type,
        "panels": panels,
        "text_blocks": text_blocks or [],
    }


def _bigpanel(idx, desc="", chars=None):
    # 600x900 → scale ~2.13 (< 2.5, no upscale penalty), area 540000 → salience on
    return {"index": idx, "bbox": {"x": 0, "y": 0, "w": 600, "h": 900},
            "description": desc, "characters": chars or []}


def _tinypanel(idx):
    # 100x100 → scale 19.2 → huge upscale penalty, empty desc → semantic 0
    return {"index": idx, "bbox": {"x": 0, "y": 0, "w": 100, "h": 100},
            "description": "", "characters": []}


def _dialog(panel_index, text):
    return {"panel_index": panel_index, "type": "speech", "text": text,
            "bbox": {"x": 0, "y": 0, "w": 50, "h": 20}}


def test_page_lock_picks_from_own_page():
    # Scene page_ref=10 with a usable panel; page 11 also has one. Must stay on 10.
    pages = {
        10: _page(10, [_bigpanel(0, "Thor strikes", ["Thor"])],
                  text_blocks=[_dialog(0, "Thor strikes the foe")]),
        11: _page(11, [_bigpanel(0, "someone else", ["Loki"])]),
    }
    scene = {"scene_id": 1, "page_ref": 10, "panel_ref": -1, "text": "Thor strikes"}
    out = _assign_scene_panels(scene=scene, pages_by_number=pages,
                               used_panel_keys=set(), prev_panel=None,
                               cluster_to_name={}, clause_texts=["Thor strikes"])
    assert len(out) == 1
    panel, src = out[0]
    assert panel["_page_number"] == 10
    assert not panel.get("_whole_page")


def test_no_repeat_across_scenes():
    pages = {10: _page(10, [
        _bigpanel(0, "Thor strikes", ["Thor"]),
        _bigpanel(1, "Thor strikes", ["Thor"]),
    ], text_blocks=[_dialog(0, "Thor strikes"), _dialog(1, "Thor strikes")])}
    used = set()
    scene = {"scene_id": 1, "page_ref": 10, "panel_ref": -1, "text": "Thor strikes"}
    a = _assign_scene_panels(scene=scene, pages_by_number=pages, used_panel_keys=used,
                             prev_panel=None, cluster_to_name={}, clause_texts=["Thor strikes"])
    b = _assign_scene_panels(scene={**scene, "scene_id": 2}, pages_by_number=pages,
                             used_panel_keys=used, prev_panel=None,
                             cluster_to_name={}, clause_texts=["Thor strikes"])
    key_a = (a[0][0]["_page_number"], a[0][0]["index"])
    key_b = (b[0][0]["_page_number"], b[0][0]["index"])
    assert key_a != key_b


def test_whole_page_fallback_when_no_panel_depicts():
    # All panels tiny + empty desc + no chars/dialog → score below FLOOR.
    pages = {10: _page(10, [_tinypanel(0), _tinypanel(1)])}
    scene = {"scene_id": 1, "page_ref": 10, "panel_ref": -1, "text": "Thor strikes"}
    out = _assign_scene_panels(scene=scene, pages_by_number=pages, used_panel_keys=set(),
                               prev_panel=None, cluster_to_name={}, clause_texts=["Thor strikes"])
    assert len(out) == 1
    panel, src = out[0]
    assert panel["_whole_page"] is True
    assert panel["bbox"] == {"x": 0, "y": 0, "w": 1200, "h": 1800}


def test_per_clause_panel_match():
    # Each clause must get the panel matching ITS OWN text (by dialog), NOT reading
    # order. Panel 1 depicts clause A ("sonic gun"), panel 0 depicts clause B ("cannon")
    # → result is [panel1, panel0] (indices [1, 0]), proving content beats index order.
    pages = {10: _page(10, [
        _bigpanel(0, "hero with cannon", ["Hero"]),
        _bigpanel(1, "hero with gun", ["Hero"]),
    ], text_blocks=[_dialog(0, "fires the cannon"), _dialog(1, "raises the sonic gun")])}
    scene = {"scene_id": 1, "page_ref": 10, "panel_ref": -1,
             "text": "He raises the sonic gun, then he fires the cannon"}
    out = _assign_scene_panels(scene=scene, pages_by_number=pages, used_panel_keys=set(),
                               prev_panel=None, cluster_to_name={},
                               clause_texts=["He raises the sonic gun", "he fires the cannon"])
    assert [p["index"] for p, _ in out] == [1, 0]


def test_clause_count_drives_panel_count():
    pages = {10: _page(10, [
        _bigpanel(i, "Thor strikes", ["Thor"]) for i in range(4)
    ], text_blocks=[_dialog(i, "Thor strikes") for i in range(4)])}
    scene = {"scene_id": 1, "page_ref": 10, "panel_ref": -1, "text": "Thor strikes again"}
    out = _assign_scene_panels(scene=scene, pages_by_number=pages, used_panel_keys=set(),
                               prev_panel=None, cluster_to_name={},
                               clause_texts=["Thor strikes", "Thor strikes again"])
    assert len(out) == 2     # one panel per clause


def test_intro_returns_cover_full_frame():
    pages = {5: _page(5, [_bigpanel(0, "cover art")], page_type="cover")}
    scene = {"scene_id": 1, "page_ref": 5, "panel_ref": -1, "text": "intro",
             "is_intro": True}
    out = _assign_scene_panels(scene=scene, pages_by_number=pages, used_panel_keys=set(),
                               prev_panel=None, cluster_to_name={}, clause_texts=["Thor strikes"])
    assert len(out) == 1
    panel, src = out[0]
    assert panel["bbox"] == {"x": 0, "y": 0, "w": 1200, "h": 1800}
    assert src == "p5.png"


# ── R10 retired under page-lock (decision A) ─────────────────────────────────
def test_page_locked_skips_backward_progression_penalty():
    # A panel on page 6 with prev_panel on a LATER page (8) must NOT be dragged
    # below FLOOR by the retired R10 page-progression penalty when page_locked.
    panel = {"index": 0, "bbox": {"x": 0, "y": 0, "w": 600, "h": 900},
             "description": "Thor strikes", "characters": ["Thor"], "_page_number": 6}
    scene = {"text": "Thor strikes", "page_ref": 6, "panel_ref": -1}
    prev = {"_page_number": 8, "characters": ["Thor"]}
    locked = _score_panel(panel, "Thor strikes", scene, prev_panel=prev, page_locked=True)
    unlocked = _score_panel(panel, "Thor strikes", scene, prev_panel=prev, page_locked=False)
    assert locked > unlocked          # the -5*(8-6) penalty is skipped when locked
    assert locked >= PANEL_MATCH_FLOOR


def test_backward_narration_scene_still_shows_panel():
    # scene page_ref=6 after a prev panel on page 8 (narration revisits) — with R10
    # retired under page-lock, a depicting panel on page 6 must win, not whole-page.
    pages = {6: _page(6, [_bigpanel(0, "Thor strikes", ["Thor"])],
                      text_blocks=[_dialog(0, "Thor strikes the foe")])}
    scene = {"scene_id": 4, "page_ref": 6, "panel_ref": -1, "text": "Thor strikes"}
    prev = {"_page_number": 8, "characters": ["Thor"]}
    out = _assign_scene_panels(scene=scene, pages_by_number=pages, used_panel_keys=set(),
                               prev_panel=prev, cluster_to_name={}, clause_texts=["Thor strikes"])
    assert len(out) == 1
    assert not out[0][0].get("_whole_page")
    assert out[0][0]["_page_number"] == 6


# ── Task 4: rewire build_shots, delete old pickers ───────────────────────────
import stages.stage_5.shots as shots_mod
from stages.stage_5.shots import build_shots


def test_llm_assign_panels_is_deleted():
    assert not hasattr(shots_mod, "_llm_assign_panels")
    assert not hasattr(shots_mod, "_select_panel_for_chunk")


def test_build_shots_uses_assigned_panels():
    narration = {"scenes": [
        {"scene_id": 1, "page_ref": 10, "panel_ref": -1, "text": "Thor strikes"},
    ], "beats": []}
    caption_chunks = [{"text": "Thor strikes", "start": 0.0, "end": 2.0}]
    scene_timings = [{"scene_id": 1, "start": 0.0, "end": 2.0}]
    pages = {10: {
        "source_image": "p10.png",
        "image_dimensions": {"width": 1200, "height": 1800},
        "page_type": "story",
        "panels": [{"index": 0, "bbox": {"x": 0, "y": 0, "w": 600, "h": 900},
                    "description": "Thor strikes", "characters": ["Thor"]}],
        "text_blocks": [{"panel_index": 0, "type": "speech", "text": "Thor strikes",
                         "bbox": {"x": 0, "y": 0, "w": 50, "h": 20}}],
    }}
    shots = build_shots(narration, scene_timings=scene_timings,
                        caption_chunks=caption_chunks, pages_by_number=pages,
                        cluster_to_name={})
    assert len(shots) == 1
    assert shots[0].source_image == "p10.png"
    assert shots[0].panel_bbox["w"] == 600   # the panel, not a whole page
