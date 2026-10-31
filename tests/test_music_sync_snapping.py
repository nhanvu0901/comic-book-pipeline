"""Tests for hit-point selection and beat snapping in stages/music_sync.py."""
from __future__ import annotations

import pytest
from stages.stage_5.schema import Shot
from stages import music_sync


def _make_dummy_shots(durations: list[float], clips: list[int] | None = None, intro_shot: bool = True) -> list[Shot]:
    shots = []
    clips_set = set(clips or [])
    for idx, d in enumerate(durations):
        s = Shot(
            shot_id=idx,
            scene_id=idx + 1,
            duration_seconds=float(d),
            panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100},
            source_image="p1.jpg",
            motion="static",
            is_intro=(idx == 0 and intro_shot),
            clip_path="/dummy/clip.mp4" if idx in clips_set else "",
            clip_id=f"clip_{idx}" if idx in clips_set else "",
        )
        shots.append(s)
    return shots


def test_select_hit_points_general_rule():
    # 8 shots: shot 0 is intro, shot 3 is a clip, shot 5 is climax, shot 7 is final line
    durations = [3.0, 3.0, 3.0, 4.0, 3.0, 4.0, 3.0, 3.0]
    shots = _make_dummy_shots(durations, clips=[3], intro_shot=True)

    narration = {
        "scenes": [
            {"scene_id": 1, "is_intro": True},
            {"scene_id": 2},
            {"scene_id": 3},
            {"scene_id": 4},
            {"scene_id": 5},
            {"scene_id": 6, "importance": "climax"},
            {"scene_id": 7},
            {"scene_id": 8, "is_outro": True},
        ]
    }

    hit_points = music_sync.select_hit_points(shots, narration)

    # Must find at most 3-5 hit points
    assert 2 <= len(hit_points) <= 5
    hit_indices = [hp.shot_index for hp in hit_points]

    # Hook reveal (cut after shot 0, so shot_index 1)
    assert 1 in hit_indices
    # Clip shot (shot 3)
    assert 3 in hit_indices
    # Final line (shot 7)
    assert 7 in hit_indices


def test_snap_shots_to_beats_within_tolerance():
    # Shots: s0=4.0s, s1=4.0s, s2=4.0s (boundaries at 4.0s, 8.0s)
    shots = _make_dummy_shots([4.0, 4.0, 4.0], intro_shot=True)
    hit_points = [
        music_sync.HitPoint(
            shot_index=1,
            time_seconds=4.0,
            name="Hook Reveal",
            strength=0.9,
        )
    ]

    # Music beat at 4.02s in video timeline (with in_point=0)
    # Target cut lands 1.5 frames early at 30fps = 4.02 - (1.5/30) = 4.02 - 0.05 = 3.97s
    # delta = 3.97 - 4.00 = -0.03s (well within ±0.20s)
    strong_beats = [2.0, 4.02, 6.0, 8.0]
    in_point = 0.0

    snapped = music_sync.snap_shots_to_beats(shots, hit_points, in_point=in_point, strong_beats=strong_beats)

    assert snapped[0].shot_index == 1
    assert snapped[0].snapped is True
    # Shot 0 new duration: 4.0 - 0.03 = 3.97s
    assert shots[0].duration_seconds == pytest.approx(3.97, abs=0.01)
    # Shot 1 new duration: 4.0 + 0.03 = 4.03s
    assert shots[1].duration_seconds == pytest.approx(4.03, abs=0.01)
    # Total duration unchanged
    assert sum(s.duration_seconds for s in shots) == pytest.approx(12.0, abs=0.001)
    # Offset from beat is negative (1-2 frames early)
    assert snapped[0].offset_from_beat < 0


def test_snap_shots_rejects_beyond_tolerance():
    shots = _make_dummy_shots([4.0, 4.0, 4.0])
    hit_points = [
        music_sync.HitPoint(
            shot_index=1,
            time_seconds=4.0,
            name="Hook Reveal",
            strength=0.9,
        )
    ]
    # Nearest beat is at 4.40s -> delta = 4.40 - 0.05 - 4.0 = +0.35s > 0.20s
    strong_beats = [1.0, 4.40, 7.0]

    snapped = music_sync.snap_shots_to_beats(shots, hit_points, in_point=0.0, strong_beats=strong_beats, tolerance=0.20)
    assert snapped[0].snapped is False
    # Shots untouched
    assert shots[0].duration_seconds == 4.0
    assert shots[1].duration_seconds == 4.0


def test_snap_shots_respects_min_duration_floor():
    # Shot 0 is 0.45s, delta is -0.10s -> would become 0.35s < 0.40s
    shots = _make_dummy_shots([0.45, 4.0, 4.0])
    hit_points = [
        music_sync.HitPoint(
            shot_index=1,
            time_seconds=0.45,
            name="Hook Reveal",
            strength=0.9,
        )
    ]
    # Beat at 0.40s -> target cut = 0.40 - 0.05 = 0.35s -> delta = -0.10s
    strong_beats = [0.40, 4.0]

    snapped = music_sync.snap_shots_to_beats(shots, hit_points, in_point=0.0, strong_beats=strong_beats, min_duration=0.40)
    # Must NOT reduce shot 0 below 0.40s
    assert shots[0].duration_seconds >= 0.40


def test_snap_shots_respects_clip_fit_speed_limit():
    # Shot 1 is a clip shot with fixed span = 1.0s, fit_to_beat=True
    # If duration increases too much, speed = span / dur falls below 0.8
    shots = _make_dummy_shots([3.0, 1.2, 3.0], clips=[1])
    shots[1].clip_in = 0.0
    shots[1].clip_out = 1.0
    shots[1].clip_fit_to_beat = True

    hit_points = [
        music_sync.HitPoint(
            shot_index=1,
            time_seconds=3.0,
            name="Clip 1",
            strength=1.0,
        )
    ]

    # Delta = -0.15s -> shot 1 duration becomes 1.2 - (-0.15) = 1.35s
    # speed = 1.0 / 1.35 = 0.74 < 0.80 -> should NOT snap!
    strong_beats = [2.90]  # target = 2.90 - 0.05 = 2.85 -> delta = -0.15s

    snapped = music_sync.snap_shots_to_beats(shots, hit_points, in_point=0.0, strong_beats=strong_beats)
    assert snapped[0].snapped is False
    assert shots[1].duration_seconds == 1.2


def test_clip_speed_guard_fixed_window_exceeds_renderer_speed_limit():
    """P2 regression: fixed window with clip_fit_to_beat=False when span > dur
    is planned as fit_to_beat by plan_clip_shot().
    span = 2.0s, initial dur = 1.65s.
    If snapped to dur = 1.50s, speed = 2.0 / 1.50 = 1.33x (> 1.25x CLIP_SPEED_MAX).
    The guard must reject the snap."""
    shots = _make_dummy_shots([3.0, 1.65, 3.0], clips=[1])
    shots[1].clip_in = 0.0
    shots[1].clip_out = 2.0
    shots[1].clip_fit_to_beat = False

    hit_points = [
        music_sync.HitPoint(
            shot_index=2, # Cut into shot 2: shrinking shot 1 duration
            time_seconds=4.65,
            name="Cut after Clip",
            strength=0.9,
        )
    ]

    # Target cut at 4.50s -> delta = 4.50 - 4.65 = -0.15s
    # Prev shot (shot 1) duration would become 1.65 + (-0.15) = 1.50s
    # speed would be 2.0 / 1.50 = 1.333x > 1.25x!
    strong_beats = [4.55] # 4.55 - 0.05 = 4.50s

    snapped = music_sync.snap_shots_to_beats(shots, hit_points, in_point=0.0, strong_beats=strong_beats)
    assert snapped[0].snapped is False
    # Shot 1 duration must stay untouched at 1.65s
    assert shots[1].duration_seconds == 1.65


