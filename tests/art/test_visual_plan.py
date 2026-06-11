import pytest

from art_pipeline.visual_plan import (
    KINDS, assign_motions, derive_trivial_plan, parse_visual,
    validate_variety, visual_target,
)


def _scene(i, page=1, panel=-1, intro=False, outro=False):
    return {"scene_id": i, "page_ref": page, "panel_ref": panel,
            "is_intro": intro, "is_outro": outro, "text": f"s{i}"}


def _decl(i, kind, panel_ref=-1, subject=""):
    return {"scene_id": i, "kind": kind, "panel_ref": panel_ref, "subject": subject}


# ── parse_visual ─────────────────────────────────────────────────────────────

def test_parse_visual_region():
    d = parse_visual({"kind": "painting_region", "panel_ref": 2}, scene_id=3)
    assert d == {"scene_id": 3, "kind": "painting_region", "panel_ref": 2,
                 "subject": "", "motion": "", "fallback": ""}


def test_parse_visual_related_requires_subject():
    with pytest.raises(ValueError, match="scene 4"):
        parse_visual({"kind": "related", "subject": "  "}, scene_id=4)


def test_parse_visual_unknown_kind():
    with pytest.raises(ValueError, match="scene 2"):
        parse_visual({"kind": "collage"}, scene_id=2)


def test_parse_visual_region_requires_panel():
    with pytest.raises(ValueError, match="panel_ref"):
        parse_visual({"kind": "painting_region", "panel_ref": -1}, scene_id=5)


# ── assign_motions ───────────────────────────────────────────────────────────

def test_assign_motions_alternates_zoom_and_maps_kinds():
    plan = [_decl(1, "painting_full"), _decl(2, "painting_region", 0),
            _decl(3, "related", subject="artist portrait"),
            _decl(4, "painting_region", 1), _decl(5, "painting_full")]
    assign_motions(plan, intro_scene_id=1)
    motions = [d["motion"] for d in plan]
    assert motions == ["static", "zoom_in", "pan_right", "zoom_out", "zoom_out"]
    # intro full = static; later full = zoom_out (never a dead static mid-video)


# ── validate_variety (4 hard rules) ─────────────────────────────────────────

def _ok_fixture():
    scenes = [_scene(1, intro=True), _scene(2, panel=0), _scene(3),
              _scene(4, panel=1), _scene(5, outro=True)]
    plan = {1: _decl(1, "painting_full"), 2: _decl(2, "painting_region", 0),
            3: _decl(3, "related", subject="portrait of the artist"),
            4: _decl(4, "painting_region", 1), 5: _decl(5, "painting_full")}
    return scenes, plan


def test_variety_ok():
    scenes, plan = _ok_fixture()
    validate_variety(scenes, plan)  # no raise


def test_variety_rule1_consecutive_same_target():
    scenes, plan = _ok_fixture()
    plan[3] = _decl(3, "painting_region", 0)  # same region as scene 2
    with pytest.raises(ValueError, match="consecutive"):
        validate_variety(scenes, plan)


def test_variety_rule2_region_reuse():
    scenes, plan = _ok_fixture()
    plan[4] = _decl(4, "painting_region", 0)  # region 0 again (non-consecutive)
    with pytest.raises(ValueError, match="region"):
        validate_variety(scenes, plan)


def test_variety_rule3_full_only_intro_outro_one_mid():
    scenes = [_scene(1, intro=True), _scene(2), _scene(3, panel=0),
              _scene(4), _scene(5, outro=True)]
    plan = {1: _decl(1, "painting_full"), 2: _decl(2, "painting_full"),
            3: _decl(3, "painting_region", 0), 4: _decl(4, "painting_full"),
            5: _decl(5, "painting_full")}  # 2 mid fulls (scenes 2 and 4)
    with pytest.raises(ValueError, match="painting_full"):
        validate_variety(scenes, plan)


def test_variety_rule4_duplicate_subjects():
    scenes, plan = _ok_fixture()
    plan[3] = _decl(3, "related", subject="Portrait of the Artist")
    scenes.insert(3, _scene(6))
    plan[6] = _decl(6, "related", subject="portrait of the artist ")
    with pytest.raises(ValueError, match="subject"):
        validate_variety(scenes, plan)


# ── visual_target / derive_trivial_plan ─────────────────────────────────────

def test_visual_target_identity():
    assert visual_target(_scene(1, page=2), _decl(1, "painting_region", 3)) == ("r", 2, 3)
    assert visual_target(_scene(1, page=2), _decl(1, "painting_full")) == ("f", 2)
    assert visual_target(_scene(1), _decl(1, "related", subject=" X-Ray ")) == ("x", "x-ray")


def test_derive_trivial_plan_for_legacy_projects():
    narration = {"scenes": [_scene(1, panel=2, intro=True), _scene(2, panel=-1)]}
    plan = derive_trivial_plan(narration)
    assert [d["kind"] for d in plan] == ["painting_region", "painting_full"]
    assert all(d["motion"] for d in plan)
    assert set(KINDS) == {"painting_region", "painting_full", "related"}
