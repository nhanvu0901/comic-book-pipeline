"""Whip-blur wipe transition: boundary selection + duration-preserving borrow.
Pure logic only — no ffmpeg/rendering (see the manual smoke-render for real output)."""
from dataclasses import dataclass

import config
from stages.stage_5 import pipeline as P


@dataclass
class FakeShot:
    scene_id: int
    duration_seconds: float


def _shots():
    return [
        FakeShot(1, 1.0), FakeShot(1, 1.2),   # scene 1: last shot 1.2s
        FakeShot(2, 0.9),                      # scene 2: single shot 0.9s
        FakeShot(3, 0.3), FakeShot(3, 1.1),    # scene 3: FIRST shot 0.3s (<0.6 guard)
        FakeShot(4, 1.3),                      # scene 4
    ]
    # boundaries: 0 (scene1->2, both sides >=0.6 -> eligible)
    #             1 (scene2->3, first shot of scene3 = 0.3s -> always skipped)
    #             2 (scene3->4, both sides >=0.6 -> eligible)


def test_prob_zero_never_whips(monkeypatch):
    monkeypatch.setattr(config, "TRANSITION_WHIP_PROB", 0.0)
    assert P._pick_whip_boundaries("proj", _shots()) == {}


def test_prob_one_whips_every_valid_boundary_skips_short_shots(monkeypatch):
    monkeypatch.setattr(config, "TRANSITION_WHIP_PROB", 1.0)
    monkeypatch.setattr(config, "TRANSITION_WHIP_SECONDS", 0.24)
    whip = P._pick_whip_boundaries("proj", _shots())
    assert whip == {0: 0.24, 2: 0.24}  # boundary 1 skipped: adjacent 0.3s shot


def test_same_seed_same_selection_across_calls(monkeypatch):
    monkeypatch.setattr(config, "TRANSITION_WHIP_PROB", 0.5)
    a = P._pick_whip_boundaries("my-project", _shots())
    b = P._pick_whip_boundaries("my-project", _shots())
    assert a == b
    # a different project (different seed) is allowed to differ — just confirm the
    # function is a pure function of its inputs, not calling-order dependent.
    c = P._pick_whip_boundaries("my-project", _shots())
    assert c == a


def test_borrow_preserves_total_duration(monkeypatch):
    monkeypatch.setattr(config, "TRANSITION_WHIP_PROB", 1.0)
    monkeypatch.setattr(config, "TRANSITION_WHIP_SECONDS", 0.24)
    shots = _shots()
    whip = P._pick_whip_boundaries("proj", shots)
    assert whip  # sanity: at least one boundary chosen
    new_durs = P._whip_borrowed_durations(shots, whip)
    total_before = sum(s.duration_seconds for s in shots)
    total_after = sum(new_durs) + sum(whip.values())
    assert abs(total_before - total_after) < 1e-9


def test_no_whip_returns_original_durations():
    shots = _shots()
    durs = P._whip_borrowed_durations(shots, {})
    assert durs == [s.duration_seconds for s in shots]


def test_empty_project_disables_whip_in_assemble_video(monkeypatch, tmp_path):
    """_assemble_video with project="" (the default, used by callers/tests that
    predate this feature) must skip whip selection entirely — old behavior stays
    byte-identical regardless of TRANSITION_WHIP_PROB's default."""
    monkeypatch.setattr(config, "XFADE_TRANSITION", "cut")
    monkeypatch.setattr(config, "XFADE_SOFT_EDGES", False)
    monkeypatch.setattr(config, "FLASH_ACCENTS", False)
    monkeypatch.setattr(config, "TRANSITION_WHIP_PROB", 1.0)  # would whip everything if enabled
    calls = {"concat": []}
    monkeypatch.setattr(P, "_concat", lambda paths, out: (calls["concat"].append(paths), out)[1])
    shots = [FakeShot(1, 2.0), FakeShot(2, 2.0), FakeShot(3, 2.0)]
    paths = [tmp_path / f"s{i}.mp4" for i in range(3)]
    P._assemble_video(shots, paths, tmp_path / "out.mp4")
    assert len(calls["concat"]) == 1
    assert calls["concat"][0] == paths  # untouched — no bridges, no trims
