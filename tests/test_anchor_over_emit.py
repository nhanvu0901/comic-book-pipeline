"""Guard: when the writer emits MORE scenes than beats (splits one beat into two),
front-only truncation dropped the writer's LAST scene — losing the LANDING (the
plummet / final reveal). Both-ends alignment must keep the last beat mapped to the
writer's last scene."""
from stages.stage_3.write_script import _anchor_scenes_to_beats
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


if __name__ == "__main__":
    test_landing_keeps_writers_last_scene_when_over_emitted()
    print("ok")
