"""suggest_cues on hand-made timeline data (no ffmpeg needed) and on a tiny synthetic project."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stages.mascot import render, suggest
from stages.mascot.config import MOVES_EXIT, Cue, MascotConfig
from stages.mascot.kit import Kit, Preset

PRESETS = {n: Preset(n, f"body_{n}", f"face_{n}", n) for n in ("point", "shock", "think", "sly")}


def make_kit(presets=PRESETS) -> Kit:
    bodies = {"body_default": None} | {p.body: None for p in presets.values()}
    faces = {"face_default": None} | {p.face: None for p in presets.values()}
    return Kit("k", "k", Path("."), (10, 10), bodies, faces, {}, dict(presets), "body_default", "face_default")


def sample_data(duration=60.0, functions=("COLD_OPEN", "SETUP", "ESCALATION", "MIDPOINT", "CLIMAX", "LANDING")) -> dict:
    n = len(functions)
    starts = [round(0.2 + i * (duration - 8) / n, 3) for i in range(n)]
    cuts = [0.0] + [round(i * 3.7, 3) for i in range(1, int(duration / 3.7) + 1)] + [duration]
    return {
        "duration": duration, "cuts": sorted(set(cuts)),
        "scenes": [{"scene_id": i + 1, "t": t, "end": t + 5} for i, t in enumerate(starts)],
        "beats": [{"id": i + 1, "function": f, "name": f.lower(), "t": t}
                  for i, (f, t) in enumerate(zip(functions, starts))],
        "words": [{"w": "w", "t": 0.15, "end": 0.4}] + [{"w": "x", "t": t + 0.05, "end": t + 0.3} for t in starts[1:]],
    }


def key(cues):
    return [(round(c.t, 3), c.move, c.body, c.face, c.locked) for c in cues]


def suggest_for(data, cfg=None, kit=None):
    return suggest.suggest_cues(Path("."), cfg or MascotConfig(), kit or make_kit(), data)


def ratio(cues, data):
    return suggest.visible_ratio(cues, data["duration"])


# ── role mapping ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fn,role", [
    ("COLD_OPEN", "hook"), ("cold_open", "hook"), ("Hook", "hook"),
    ("CLIMAX", "peak"), ("twist", "peak"), ("Reveal", "peak"),
    ("LANDING", "landing"), ("RESOLUTION", "landing"), ("payoff", "landing"),
    ("ESCALATION", "build"), ("MIDPOINT", "build"), ("COMPLICATION", "build"),
    ("SETUP", "other"), ("", "other"), (None, "other")])
def test_function_to_role_is_case_insensitive_and_general(fn, role):
    assert suggest._role(fn) == role


# ── rules ────────────────────────────────────────────────────────────────────────────────
def test_rules_hook_peak_landing_end():
    data = sample_data()
    cues = suggest_for(data)
    first = cues[0]
    assert (first.move, first.body, first.face) == ("rise", "body_point", "face_point") and first.t >= 0.2
    shock = [c for c in cues if c.face == "face_shock"]
    assert shock and shock[0].t == pytest.approx(data["beats"][4]["t"], abs=0.3)
    assert shock[0].move in ("shake", "peek")                  # peek only when it had to enter
    landing = [c for c in cues if c.face == "face_sly"]          # sly preferred when the kit has it
    assert landing and landing[0].move in ("pop", "peek")
    assert cues[-1].move == "slide_out" and cues[-1].t == pytest.approx(data["duration"] - 0.5, abs=0.3)
    assert all(not c.locked for c in cues)
    assert len({c.id for c in cues}) == len(cues)


def test_landing_uses_think_when_kit_has_no_sly():
    kit = make_kit({k: v for k, v in PRESETS.items() if k != "sly"})
    cues = suggest_for(sample_data(), kit=kit)
    assert any(c.face == "face_think" for c in cues)


def test_unknown_presets_fall_back_to_kit_defaults():
    kit = make_kit({})
    cues = suggest_for(sample_data(), kit=kit)
    posed = [c for c in cues if c.move not in MOVES_EXIT]
    assert posed and all((c.body, c.face) == ("body_default", "face_default") for c in posed)


def test_times_are_snapped_to_the_timeline():
    data = sample_data()
    for c in suggest_for(data):
        pts = list(data["cuts"]) + [b["t"] for b in data["beats"]] + [w["t"] for w in data["words"]]
        assert any(abs(c.t - p) < 1e-6 for p in pts) or c.t in (0.2, data["duration"] - 0.5) \
            or c.t > 0, c


def test_beats_missing_falls_back_to_scene_starts():
    data = sample_data()
    data["beats"] = []
    cues = suggest_for(data)
    assert cues[0].move == "rise" and cues[-1].move == "slide_out" and len(cues) >= 3


def test_empty_project_enter_at_0_2_and_exit_at_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "source_video", lambda p: None)
    cues = suggest.suggest_cues(tmp_path, MascotConfig(), make_kit())
    assert [c.move for c in cues] == ["rise", "slide_out"] and cues[0].t == 0.2 and cues[1].t > cues[0].t
    data = {"duration": 30.0, "cuts": [0.0, 30.0], "scenes": [], "beats": [], "words": []}
    cues = suggest_for(data)
    assert (cues[0].t, cues[0].move) == (0.2, "rise") and (cues[-1].t, cues[-1].move) == (29.5, "slide_out")
    assert all(c.move not in suggest.MOVES_EXIT for c in cues[:-1])      # only pose changes between


def test_project_dir_path_reads_the_timeline(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "source_video", lambda p: None)
    (tmp_path / "scene_timings.json").write_text(json.dumps(
        [{"scene_id": i, "start": i * 6.0, "end": i * 6.0 + 5.5} for i in range(1, 8)]))
    cues = suggest.suggest_cues(tmp_path, MascotConfig(), make_kit())
    assert cues[0].move == "rise" and cues[-1].move == "slide_out"


# ── spacing, locks, ratio ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fns", [
    ("COLD_OPEN", "SETUP", "ESCALATION", "MIDPOINT", "CLIMAX", "LANDING"),
    ("COLD_OPEN", "SETUP", "SETUP", "SETUP", "SETUP", "SETUP", "LANDING"),       # Q&A shape
    ("COLD_OPEN", "CLIMAX", "CLIMAX", "TWIST", "LANDING")])
@pytest.mark.parametrize("duration", [30.0, 50.0, 75.0])
def test_visible_ratio_in_band_and_spacing(fns, duration):
    data = sample_data(duration, fns)
    cues = suggest_for(data)
    lo, hi = suggest.VISIBLE_BAND
    assert lo <= ratio(cues, data) <= hi
    assert all(b.t - a.t >= suggest.MIN_GAP - 1e-6 for a, b in zip(cues, cues[1:]))
    assert all(0 < c.t < duration for c in cues)


def test_locked_cues_kept_exactly_and_gaps_respected():
    data = sample_data()
    locked = [Cue(t=data["beats"][4]["t"] + 0.3, move="tilt", body="body_think", face="face_think", locked=True),
              Cue(t=20.0, move="sink", locked=True)]
    old = Cue(t=12.34, move="hop")                                   # earlier suggestion: replaced
    cfg = MascotConfig(cues=[locked[1], old, locked[0]])
    cues = suggest_for(data, cfg)
    assert [c for c in cues if c.locked] == sorted(locked, key=lambda c: c.t)
    assert all(a is b for a, b in zip([c for c in cues if c.locked], sorted(locked, key=lambda c: c.t)))
    assert all(c.id != old.id for c in cues)
    for fresh in (c for c in cues if not c.locked):
        assert all(abs(fresh.t - lk.t) >= suggest.LOCK_GAP - 1e-6 for lk in locked), fresh
    assert 0.3 <= ratio(cues, data) <= 0.8                           # locks bend the band a little


def test_locked_exit_is_respected_in_the_state_machine():
    data = sample_data()
    cfg = MascotConfig(cues=[Cue(t=1.5, move="sink", locked=True)])
    cues = suggest_for(data, cfg)
    assert any(c.locked and c.move == "sink" for c in cues)
    assert not any(not c.locked and abs(c.t - 1.5) < suggest.LOCK_GAP for c in cues)


def test_deterministic():
    data = sample_data()
    cfg = MascotConfig(cues=[Cue(t=33.0, move="hop", locked=True)])
    a, b = suggest_for(data, cfg), suggest_for(data, cfg)
    assert key(a) == key(b)


def test_clear_suggestions_keeps_only_locked():
    cfg = MascotConfig(cues=[Cue(t=1, locked=True), Cue(t=2), Cue(t=3, locked=True)])
    assert [c.t for c in suggest.clear_suggestions(cfg)] == [1, 3]


# ── pose changes inside visible stretches ────────────────────────────────────────────────
ALL_PRESETS = {n: Preset(n, f"body_{n}", f"face_{n}", n) for n in
               ("neutral", "point", "shock", "think", "sly", "explain", "shrug")}


def _stretches(cues, duration):
    """[(start, end, [(t, pose)...])] per visible stretch, carrying the pose across cues."""
    out, cur, pose = [], None, None
    for c in sorted(cues, key=lambda c: c.t):
        if c.move in MOVES_EXIT:
            if cur:
                out.append((cur[0], c.t, cur[1]))
            cur = None
            continue
        if c.body or c.face:
            pose = (c.body or pose[0], c.face or pose[1]) if pose else (c.body, c.face)
        if cur is None:
            cur = (c.t, [])
        if c.body or c.face:
            cur[1].append((c.t, pose))
    if cur:
        out.append((cur[0], duration, cur[1]))
    return out


@pytest.mark.parametrize("fns", [
    ("COLD_OPEN", "SETUP", "ESCALATION", "MIDPOINT", "CLIMAX", "LANDING"),
    ("COLD_OPEN", "SETUP", "SETUP", "SETUP", "SETUP", "LANDING")])
def test_no_pose_held_longer_than_max_and_consecutive_poses_differ(fns):
    data = sample_data(50.0, fns)
    cues = suggest_for(data, kit=make_kit(ALL_PRESETS))
    for start, end, poses in _stretches(cues, 50.0):
        times = [start] + [t for t, _ in poses[1:]] + [end]
        assert all(b - a <= suggest.POSE_MAX_HOLD + 1e-6 for a, b in zip(times, times[1:])), (start, end, times)
        assert all(a[1] != b[1] for a, b in zip(poses, poses[1:])), poses
    assert sum(1 for c in cues if c.move in ("pop", "hop", "tilt", "shake")) >= 3
    assert any(c.move in suggest.ACCENT_MOVES for c in cues)       # now and then, not never
    assert all(b.t - a.t >= suggest.MIN_GAP - 1e-6 for a, b in zip(cues, cues[1:]))
    assert suggest.VISIBLE_BAND[0] <= ratio(cues, data) <= suggest.VISIBLE_BAND[1]


def test_pose_changes_respect_locks_and_are_deterministic():
    data = sample_data(50.0)
    cfg = MascotConfig(cues=[Cue(t=12.0, move="hold", body="body_shrug", face="face_shrug", locked=True)])
    kit = make_kit(ALL_PRESETS)
    a, b = suggest_for(data, cfg, kit), suggest_for(data, cfg, kit)
    assert key(a) == key(b)
    assert [c for c in a if c.locked] == cfg.cues
    assert all(abs(c.t - 12.0) >= suggest.LOCK_GAP for c in a if not c.locked)


def test_pose_changes_without_presets_do_not_crash():
    cues = suggest_for(sample_data(50.0), kit=make_kit({}))
    assert cues[0].move == "rise" and cues[-1].move == "slide_out"
