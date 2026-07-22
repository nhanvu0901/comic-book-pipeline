"""Guard: when the writer emits MORE scenes than beats (splits one beat into two),
front-only truncation dropped the writer's LAST scene — losing the LANDING (the
plummet / final reveal). Both-ends alignment must keep the last beat mapped to the
writer's last scene."""
from stages.stage_3.write_script import _anchor_scenes_to_beats, _dedupe_beat_ids
from stages.stage_3.schema import Beat


def _beat(bid, fn="SETUP"):
    return Beat(id=bid, function=fn, name=f"b{bid}", page_refs=[bid], key_panels=[],
                summary=f"summary {bid}", cause="", characters_active=[])


def test_landing_keeps_writers_last_scene_when_over_emitted():
    beats = [_beat(i) for i in range(1, 17)]           # 16 beats
    beats[0].function = "COLD_OPEN"
    beats[-1].function = "LANDING"
    # 17 scenes: writer split a middle beat → one extra. Last scene = the payoff.
    scenes = [{"text": f"scene {i}", "beat_id": i} for i in range(1, 17)]
    scenes.insert(8, {"text": "EXTRA middle split", "beat_id": 9})
    scenes.append({"text": "PLUMMET stretch reveal", "beat_id": 99})  # writer's true last
    # now 18 scenes for 16 beats — both extras; the LAST must still win the LANDING
    parsed = {"scenes": scenes}
    out = _anchor_scenes_to_beats(parsed, beats, None)
    body = out["scenes"]
    assert body[-1]["text"] == "PLUMMET stretch reveal", \
        f"LANDING must keep the writer's LAST scene, got {body[-1]['text']!r}"
    assert body[0]["text"] == "scene 1", "cold open keeps the first scene"
    assert not out.get("_coverage_gaps"), "no beat should be left without prose"


def test_duplicate_beat_id_does_not_collapse_scenes():
    """Regression (2026-07-16): matched used to be keyed by beat.id. A bridge-retry
    outline bug let two DIFFERENT beats share one id (real case: two distinct
    story beats both tagged id=12) — keying by id silently overwrote the dict
    entry, so BOTH beats fetched the SAME (wrong) scene text and the other
    beat's real prose vanished with no error ("narration only 67 words" bug,
    scene count still matched beat count going in). Position must be the only
    key: each of the 4 beats below keeps its OWN distinct scene text even
    though beat ids 2 and 2 collide."""
    beats = [_beat(1), _beat(2), _beat(2, fn="CLIMAX"), _beat(3)]  # id 2 duplicated
    scenes = [{"text": f"scene {i}"} for i in range(1, 5)]
    out = _anchor_scenes_to_beats({"scenes": scenes}, beats, None)
    texts = [s["text"] for s in out["scenes"]]
    assert texts == ["scene 1", "scene 2", "scene 3", "scene 4"], texts
    assert not out.get("_coverage_gaps"), "no beat should be left without prose"


def test_dedupe_beat_ids_renumbers_collisions():
    """`_dedupe_beat_ids` (called after every bridge-outline merge in
    outline_beats) must keep the first beat with a given id untouched and bump
    every later collision to a fresh id past the current max — the fix at the
    actual SOURCE of the duplicate-id bug (a bridge-retry response echoing an
    existing id onto a new beat)."""
    beats = [_beat(9), _beat(10), _beat(10, fn="CLIMAX"), _beat(10, fn="ESCALATION"), _beat(11)]
    logged = []
    out = _dedupe_beat_ids(beats, logged.append)
    ids = [b.id for b in out]
    assert ids[0] == 9 and ids[1] == 10, "first occurrence of each id is untouched"
    assert len(set(ids)) == 5, f"all ids must be unique after dedupe: {ids}"
    assert any("renumbered 2" in m for m in logged), logged


def test_dedupe_beat_ids_is_a_noop_when_already_unique():
    beats = [_beat(i) for i in range(1, 6)]
    logged = []
    out = _dedupe_beat_ids(beats, logged.append)
    assert [b.id for b in out] == [1, 2, 3, 4, 5]
    assert not logged, "must not log anything when there was nothing to fix"


if __name__ == "__main__":
    test_landing_keeps_writers_last_scene_when_over_emitted()
    test_duplicate_beat_id_does_not_collapse_scenes()
    test_dedupe_beat_ids_renumbers_collisions()
    test_dedupe_beat_ids_is_a_noop_when_already_unique()
    print("ok")
