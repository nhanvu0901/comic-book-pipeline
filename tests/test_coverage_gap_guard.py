"""Guard: the writer must emit exactly one story scene per beat. When it merges or
drops beats, positional anchoring leaves later beats with no scene and every
following panel desyncs from its narration (the Batman/Bane bug — "Bane apologizes"
played over "Porter screaming in fire"). _validate must flag that as a CRITICAL
error so the retry loop re-writes with full beat coverage."""
from stages.stage_3.write_script import _validate, _is_critical_error


def test_coverage_gap_is_reported_and_critical():
    # 17 beats, but the writer's pool only filled 15 → beats 16,17 have no scene.
    parsed = {
        "scenes": [{"text": "When something happens here.", "connective": None,
                    "page_ref": 1, "beat_id": 1} for _ in range(15)],
        "_coverage_gaps": [16, 17],
        "_anchor_pool_count": 15,
    }
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 18)))
    gap_errs = [e for e in errors if "coverage gap" in e.lower()]
    assert gap_errs, f"coverage gap must be reported, got: {errors}"
    assert all(_is_critical_error(e) for e in gap_errs), "coverage gap must be CRITICAL"


def test_surplus_reported_but_not_critical():
    # pool > beats (writer split scenes): _anchor_scenes_to_beats already recovers
    # this via both-ends alignment (gaps==[]), so it must be reported as a soft
    # "scene surplus" warning, NOT a critical coverage gap.
    parsed = {
        "scenes": [{"text": "When something happens here.", "connective": None,
                    "page_ref": 1, "beat_id": 1} for _ in range(15)],
        "_coverage_gaps": [],
        "_anchor_pool_count": 19,
    }
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 18)))
    assert not any("coverage gap" in e.lower() for e in errors), errors
    surplus_errs = [e for e in errors if "scene surplus" in e.lower()]
    assert surplus_errs, f"scene surplus must be reported, got: {errors}"
    assert not any(_is_critical_error(e) for e in surplus_errs), "surplus must not be critical"


def test_pool_less_than_beats_without_gaps_still_critical():
    # pool < beats even if _coverage_gaps somehow came back empty (defensive case) —
    # a genuine shortfall must still be flagged CRITICAL.
    parsed = {
        "scenes": [{"text": "When something happens here.", "connective": None,
                    "page_ref": 1, "beat_id": 1} for _ in range(15)],
        "_coverage_gaps": [],
        "_anchor_pool_count": 15,
    }
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 18)))
    gap_errs = [e for e in errors if "coverage gap" in e.lower()]
    assert gap_errs, f"pool<beats must be reported, got: {errors}"
    assert all(_is_critical_error(e) for e in gap_errs), "pool<beats must be CRITICAL"


def test_no_gap_when_counts_match():
    parsed = {
        "scenes": [{"text": "When something happens here.", "connective": None,
                    "page_ref": 1, "beat_id": 1} for _ in range(17)],
        "_coverage_gaps": [],
        "_anchor_pool_count": 17,
    }
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 18)))
    assert not [e for e in errors if "coverage gap" in e.lower()], errors
