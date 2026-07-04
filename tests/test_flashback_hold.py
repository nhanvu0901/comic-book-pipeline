"""Stage 3 flashback-hold guard: a hidden-origin flashback the outliner emits BEFORE
the climactic confrontation must be moved to sit right before the final beats, so
narration goes present-day fight -> past reveal -> final image, never the reverse."""
from stages.stage_3.write_script import _hold_flashback_beats_late
from stages.stage_3.schema import Beat


def _beat(bid, summary, fn="SETUP"):
    return Beat(id=bid, function=fn, name=f"b{bid}", page_refs=[bid], key_panels=[],
                summary=summary, cause="", characters_active=[])


def _evidence_outline():
    # Mirrors the real bad outline: 10 pre-climax beats, 3 flashback beats emitted
    # too early, then 4 climax/landing beats that should come BEFORE the flashback.
    beats = [_beat(i, f"setup/complication event {i}", "SETUP") for i in range(1, 11)]
    beats.append(_beat(11, "A flashback reveals the true origin, part one", "SETUP"))
    beats.append(_beat(12, "A flashback reveals the true origin, part two", "SETUP"))
    beats.append(_beat(13, "A flashback reveals the true origin, part three", "SETUP"))
    beats.append(_beat(14, "The hero confronts the villain at the castle", "CLIMAX"))
    beats.append(_beat(15, "The villain defeats the hero", "CLIMAX"))
    beats.append(_beat(16, "The villain hurls the hero out the window", "CLIMAX"))
    beats.append(_beat(17, "The hero plummets, his powers becoming visible", "LANDING"))
    return beats


def test_moves_early_flashback_after_confrontation():
    out = _hold_flashback_beats_late(_evidence_outline(), lambda _m: None)
    ids = [b.id for b in out]
    assert ids == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 15, 11, 12, 13, 16, 17]


def test_already_late_flashback_not_moved():
    beats = [_beat(i, f"event {i}", "SETUP") for i in range(1, 8)]
    beats.append(_beat(8, "confront", "CLIMAX"))
    beats.append(_beat(9, "defeat", "CLIMAX"))
    beats.append(_beat(10, "A flashback reveals the true origin", "SETUP"))
    beats.append(_beat(11, "window throw", "CLIMAX"))
    beats.append(_beat(12, "plummets, powers visible", "LANDING"))
    out = _hold_flashback_beats_late(beats, lambda _m: None)
    assert [b.id for b in out] == [b.id for b in beats], "already at/after the target — no move"


def test_no_flashback_unchanged():
    beats = [_beat(i, f"event {i}", "SETUP") for i in range(1, 6)]
    out = _hold_flashback_beats_late(beats, lambda _m: None)
    assert [b.id for b in out] == [1, 2, 3, 4, 5]


def test_relative_order_inside_block_preserved():
    out = _hold_flashback_beats_late(_evidence_outline(), lambda _m: None)
    flash_ids = [b.id for b in out if "flashback" in (b.summary or "").lower()]
    assert flash_ids == [11, 12, 13]


if __name__ == "__main__":
    test_moves_early_flashback_after_confrontation()
    test_already_late_flashback_not_moved()
    test_no_flashback_unchanged()
    test_relative_order_inside_block_preserved()
    print("ok")
