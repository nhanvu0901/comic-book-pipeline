"""Mascot motion: cues → per-frame transform."""
import random

import pytest

from stages.mascot.config import Cue, MascotConfig, Position
from stages.mascot.kit import load_kit
from stages.mascot.motion import build_track, state_at, visible_intervals
from tests.test_mascot_kit import make_kit

W, H = 1080, 1920


@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    root = tmp_path_factory.mktemp("kits")
    make_kit(root)
    return load_kit("fx", root)


def cfg_of(*cues, **kw):
    return MascotConfig(kit="fx", cues=[Cue(**c) if isinstance(c, dict) else c for c in cues], **kw)


def track_of(kit, *cues, w=W, h=H, dur=10.0, **kw):
    return build_track(cfg_of(*cues, **kw), kit, dur, w, h)


def test_hidden_before_first_enter(kit):
    tr = track_of(kit, dict(t=1.0, move="rise"))
    assert state_at(tr, 0.0) is None and state_at(tr, 0.99) is None
    assert state_at(tr, 1.0) is not None and state_at(tr, 3.0) is not None


def test_visible_after_enter_hidden_after_exit(kit):
    tr = track_of(kit, dict(t=1.0, move="rise"), dict(t=4.0, move="sink"), dict(t=7.0, move="peek"))
    assert state_at(tr, 3.9) is not None
    assert state_at(tr, 4.2) is not None             # still sinking
    assert state_at(tr, 4.41) is None and state_at(tr, 6.0) is None
    assert state_at(tr, 7.2) is not None
    assert visible_intervals(tr) == [(1.0, pytest.approx(4.4)), (7.0, 10.0)]


def test_exit_cue_while_hidden_is_ignored(kit):
    tr = track_of(kit, dict(t=1.0, move="sink"), dict(t=2.0, move="hold"))
    assert state_at(tr, 1.5) is None and state_at(tr, 2.5) is not None


def test_non_exit_cue_while_hidden_acts_as_enter(kit):
    tr = track_of(kit, dict(t=1.0, move="hop"))
    s0, s1 = state_at(tr, 1.0), state_at(tr, 2.0)
    assert s0 is not None and s0.feet_y > H + 100        # rising from below the frame
    assert s1.feet_y <= H + 1e-6 and s1.cx == pytest.approx(0.2 * W, abs=10)


def test_pose_swaps_at_cue_time(kit):
    tr = track_of(kit, dict(t=1.0, move="rise", body="a", face="smile"),
                  dict(t=3.0, move="hold", body="b", face="plain"),
                  dict(t=5.0, move="hold"))                       # None parts keep the previous pose
    assert (state_at(tr, 2.99).body, state_at(tr, 2.99).face) == ("a", "smile")
    assert (state_at(tr, 3.0).body, state_at(tr, 3.0).face) == ("b", "plain")
    assert state_at(tr, 6.0).body == "b"
    # the pop peaks at the swap instant
    assert state_at(tr, 3.0).sx > state_at(tr, 2.5).sx + 0.1


def test_unknown_pose_names_fall_back(kit):
    tr = track_of(kit, dict(t=0.5, move="rise", body="ghost", face="ghost"))
    s = state_at(tr, 2.0)
    assert (s.body, s.face) == ("a", "smile")


def test_feet_never_below_the_line(kit):
    rng = random.Random(3)
    for motion in ("calm", "normal", "lively"):
        tr = track_of(kit, dict(t=0.0, move="rise"), dict(t=2.0, move="hop"), dict(t=3.0, move="tilt"),
                      dict(t=4.0, move="shake"), dict(t=5.0, move="pop", body="b"), dict(t=8.0, move="sink"),
                      motion=motion)
        t = 0.5                                                 # after the enter settles
        while t < 8.0:
            s = state_at(tr, t, env=rng.random())
            assert s.feet_y <= H + 1e-6, (motion, t, s.feet_y)
            t += 1 / 30


def test_peek_and_slide_out_pick_nearest_edge(kit):
    for x, sign in ((0.2, -1), (0.8, 1)):
        pos = Position("custom", x, 1.0)
        tr = track_of(kit, dict(t=1.0, move="peek"), dict(t=5.0, move="slide_out"), position=pos)
        s = state_at(tr, 1.0)
        assert (s.cx - x * W) * sign > 200                      # starts off toward its own edge
        assert state_at(tr, 2.0).cx == pytest.approx(x * W, abs=10)
        out = state_at(tr, 5.0 + 0.4 * 0.9)
        assert (out.cx - x * W) * sign > 200                    # leaves toward the same edge


def test_cue_position_glides(kit):
    tr = track_of(kit, dict(t=0.0, move="rise"),
                  dict(t=3.0, move="hold", position=Position.from_preset("bottom_right")))
    assert state_at(tr, 2.9).cx == pytest.approx(0.2 * W, abs=10)
    mid = state_at(tr, 3.0 + 0.175).cx
    assert 0.3 * W < mid < 0.7 * W
    assert state_at(tr, 4.0).cx == pytest.approx(0.8 * W, abs=10)


def test_talk_bounce_follows_env(kit):
    on = track_of(kit, dict(t=0.0, move="rise"))
    off = track_of(kit, dict(t=0.0, move="rise"), talk_bounce=False)
    assert state_at(on, 3.0, 1.0).sy > state_at(on, 3.0, 0.0).sy
    assert state_at(off, 3.0, 1.0).sy == state_at(off, 3.0, 0.0).sy


def test_strength_scales_accents(kit):
    def amp(motion):
        tr = track_of(kit, dict(t=0.0, move="rise"), dict(t=3.0, move="tilt"), motion=motion)
        return abs(state_at(tr, 3.35).rot - state_at(tr, 2.9).rot)
    assert amp("calm") < amp("normal") < amp("lively")


def test_same_motion_at_preview_and_full_size(kit):
    cues = (dict(t=0.5, move="rise"), dict(t=2.0, move="hop"), dict(t=3.0, move="shake", body="b"),
            dict(t=4.0, move="hold", position=Position.from_preset("bottom_center")), dict(t=6.0, move="slide_out"))
    big = track_of(kit, *cues, w=1080, h=1920)
    small = track_of(kit, *cues, w=540, h=960)
    for t in (0.6, 0.8, 2.1, 2.2, 3.05, 3.2, 4.1, 5.0, 6.1, 6.3):
        a, b = state_at(big, t, 0.5), state_at(small, t, 0.5)
        assert a is not None and b is not None
        assert a.cx / 1080 == pytest.approx(b.cx / 540, abs=2e-3)
        assert a.feet_y / 1920 == pytest.approx(b.feet_y / 960, abs=2e-3)
        assert a.sx == pytest.approx(b.sx, abs=1e-6) and a.sy == pytest.approx(b.sy, abs=1e-6)
        assert a.rot == pytest.approx(b.rot, abs=1e-6)
        assert a.blur / 1920 == pytest.approx(b.blur / 960, abs=1e-4)


def test_new_enter_before_previous_exit_finishes(kit):
    tr = track_of(kit, dict(t=1.0, move="rise"), dict(t=3.0, move="sink"), dict(t=3.2, move="rise"))
    assert state_at(tr, 3.25) is not None
    assert visible_intervals(tr)[0][1] == pytest.approx(3.2)
