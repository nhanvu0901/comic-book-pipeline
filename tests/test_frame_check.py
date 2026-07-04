"""Pure-logic tests for Stage 5.5 frame↔narration verification — _parse_verdict,
_build_summary, _plan_scene_checks. No ffmpeg, no SDK/model calls: verify_frames()
itself is not exercised here (see its module docstring — the checker must never
touch a real render in a unit test)."""
import stages.stage_5.verify_frames as vf


def test_parse_verdict_happy():
    assert vf._parse_verdict('{"match": true, "why": "shows the fight"}') == \
        {"match": True, "why": "shows the fight"}


def test_parse_verdict_fenced():
    raw = '```json\n{"match": false, "why": "wrong panel"}\n```'
    assert vf._parse_verdict(raw) == {"match": False, "why": "wrong panel"}


def test_parse_verdict_garbage():
    assert vf._parse_verdict("not json at all") is None
    assert vf._parse_verdict("") is None
    assert vf._parse_verdict(None) is None


def test_parse_verdict_missing_match_key():
    assert vf._parse_verdict('{"why": "no match field"}') is None


def test_build_summary():
    assert vf._build_summary(3, 5) == "3/5 matched"
    assert vf._build_summary(0, 0) == "0/0 matched"


def test_plan_scene_checks_skips_intro_outro():
    narration = {"scenes": [
        {"scene_id": 1, "is_intro": True, "is_outro": False, "text": "hook"},
        {"scene_id": 2, "is_intro": False, "is_outro": False, "text": "body"},
        {"scene_id": 3, "is_intro": False, "is_outro": True, "text": "outro"},
    ]}
    timings = [
        {"scene_id": 1, "text": "hook", "start": 0.0, "end": 2.0},
        {"scene_id": 2, "text": "body", "start": 2.0, "end": 5.0},
        {"scene_id": 3, "text": "outro", "start": 5.0, "end": 7.0},
    ]
    plan = vf._plan_scene_checks(narration, timings)
    skips = {e["scene_id"]: e["skip"] for e in plan}
    assert skips[1] == "intro/outro"
    assert skips[2] is None
    assert skips[3] == "intro/outro"


def test_plan_scene_checks_missing_scene():
    narration = {"scenes": []}
    timings = [{"scene_id": 9, "text": "orphan", "start": 0.0, "end": 1.0}]
    plan = vf._plan_scene_checks(narration, timings)
    assert plan[0]["skip"] == "scene not found"
