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


def test_count_mismatch_reported_even_without_gaps():
    # pool > beats (writer added/split scenes): no missing beat, but still a mismatch
    # that drifts panels — must still flag.
    parsed = {
        "scenes": [{"text": "When something happens here.", "connective": None,
                    "page_ref": 1, "beat_id": 1} for _ in range(15)],
        "_coverage_gaps": [],
        "_anchor_pool_count": 19,
    }
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 18)))
    assert any("coverage gap" in e.lower() for e in errors), errors


def test_no_gap_when_counts_match():
    parsed = {
        "scenes": [{"text": "When something happens here.", "connective": None,
                    "page_ref": 1, "beat_id": 1} for _ in range(17)],
        "_coverage_gaps": [],
        "_anchor_pool_count": 17,
    }
    errors = _validate(parsed, valid_pages={1}, valid_beat_ids=set(range(1, 18)))
    assert not [e for e in errors if "coverage gap" in e.lower()], errors
