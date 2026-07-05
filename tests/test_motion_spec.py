"""MOTION CORE overhaul (2026-07-04) — pure-logic checks, no ffmpeg / no embeddings.

Covers the pacing + amplitude spec:
  • sub-shot SPLIT of a long hold into competitor-cadence clips (durations sum EXACTLY),
  • the tightening framing cadence emitted per sub-shot (same scene_id, different framings),
  • intro / outro stay a SINGLE deliberate shot (no split), un-mirrored intro, zoom_out outro,
  • zoom amplitude never near-static (>= 0.06).
The landscape smart-cover-crop is covered pixel-level in test_landscape_contain.py.
"""
import re

import pytest

from stages.stage_5 import shots as S
from stages.stage_5.shots import (
    _split_shot_durations, _zoompan_expr, _build_shots_per_chunk,
    MAX_SHOT_SECONDS, ZOOM_AMPLITUDE, ZOOM_AMPLITUDE_ACTION, _SUBSHOT_FRAMINGS,
)


# ── sub-shot split ───────────────────────────────────────────────────────────
def test_long_shot_splits_into_capped_subshots():
    durs = _split_shot_durations(5.4)
    assert len(durs) == 3                              # ~1.8s each
    assert all(1.2 <= d <= MAX_SHOT_SECONDS for d in durs), durs
    assert abs(sum(durs) - 5.4) < 1e-9                 # EXACT sum → audio sync preserved


def test_short_shot_is_untouched():
    assert _split_shot_durations(2.0) == [2.0]
    assert _split_shot_durations(MAX_SHOT_SECONDS) == [MAX_SHOT_SECONDS]  # threshold: no split


def test_split_never_exceeds_cap_and_sums_exact():
    for dur in (2.7, 3.0, 4.9, 7.0, 8.0, 12.3):
        durs = _split_shot_durations(dur)
        assert len(durs) >= 2
        assert all(d <= MAX_SHOT_SECONDS + 1e-9 for d in durs), (dur, durs)
        assert abs(sum(durs) - dur) < 1e-9, (dur, durs)


# ── build path: framings + intro/outro handling ─────────────────────────────
def _fixture():
    panel = {"bbox": {"x": 10, "y": 10, "w": 400, "h": 600},
             "_page_number": 1, "_page_area": 400 * 600}
    narration = {"scenes": [
        {"scene_id": 1, "text": "Intro hook", "is_intro": True},
        {"scene_id": 2, "text": "Story beat"},
        {"scene_id": 3, "text": "Outro line", "is_outro": True},
    ]}
    caption_chunks = [
        {"text": "intro hook words", "start": 0.0, "end": 2.0},
        {"text": "story beat words go here", "start": 2.0, "end": 7.4},   # 5.4s → splits
        {"text": "outro closing line", "start": 7.4, "end": 9.4},
    ]
    scene_timings = [
        {"scene_id": 1, "start": 0.0, "end": 2.0},
        {"scene_id": 2, "start": 2.0, "end": 7.4},
        {"scene_id": 3, "start": 7.4, "end": 9.4},
    ]
    return panel, narration, caption_chunks, scene_timings


def _shots_by_scene(monkeypatch):
    panel, narration, caption_chunks, scene_timings = _fixture()
    monkeypatch.setattr(S, "_match_panels",
                        lambda units, *a, **k: [(panel, "page_1.png")] * len(units))
    out = _build_shots_per_chunk(narration, caption_chunks, {}, scene_timings)
    by_scene = {}
    for sh in out:
        by_scene.setdefault(sh.scene_id, []).append(sh)
    return by_scene


def test_story_scene_splits_into_subshots_same_scene_diff_framings(monkeypatch):
    by_scene = _shots_by_scene(monkeypatch)
    story = by_scene[2]
    assert len(story) == 3                                   # 5.4s → 3 sub-shots
    assert all(sh.scene_id == 2 for sh in story)             # same scene → hard cuts
    motions = [sh.motion for sh in story]
    assert motions == list(_SUBSHOT_FRAMINGS[:3])            # wide → closer → detail
    assert len(set(motions)) == 3                            # different framings
    assert abs(sum(sh.duration_seconds for sh in story) - 5.4) < 1e-9


def test_intro_single_shot_unmirrored_zoom_in(monkeypatch):
    by_scene = _shots_by_scene(monkeypatch)
    intro = by_scene[1]
    assert len(intro) == 1 and intro[0].is_intro            # cold open never splits
    assert intro[0].motion == "zoom_in"
    assert intro[0].no_mirror is True                        # frame 1 never mirrored


def test_outro_single_shot_zoom_out(monkeypatch):
    by_scene = _shots_by_scene(monkeypatch)
    outro = by_scene[3]
    assert len(outro) == 1                                   # loop-close never splits
    assert outro[0].motion == "zoom_out"                     # ends at z=1.0 (loop framing)


# ── amplitude: never a freeze ────────────────────────────────────────────────
def _push_amplitude(expr: str) -> float:
    """The signed push coefficient in a zoom-family z expr, e.g. z='1.5+0.1*...' → 0.1."""
    m = re.search(r"z='[\d.]+([+-][\d.]+)\*", expr)
    assert m, expr
    return abs(float(m.group(1)))


def test_zoom_family_amplitude_never_near_static():
    for action in (False, True):
        for motion in ("zoom_in", "zoom_out", "push_top", "push_detail"):
            amp = _push_amplitude(_zoompan_expr(motion, 45, action=action))
            assert amp >= 0.06, (motion, action, amp)


def test_amplitude_defaults_match_spec():
    assert ZOOM_AMPLITUDE == pytest.approx(0.10)
    assert ZOOM_AMPLITUDE_ACTION == pytest.approx(0.15)
    assert _push_amplitude(_zoompan_expr("zoom_in", 30)) == pytest.approx(0.10)
    assert _push_amplitude(_zoompan_expr("zoom_in", 30, action=True)) == pytest.approx(0.15)
