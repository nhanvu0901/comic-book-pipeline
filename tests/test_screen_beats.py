"""screen_qa beat rows + timeline windows (stages/stage_5/screen_beats.py) — pure, no ffmpeg."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from stages.stage_5 import shots as sh
from stages.stage_5.screen_beats import (
    AUDIO_TAIL_PAD, MIN_SHOT_SECONDS, estimate_beat_seconds, plan_windows, scene_spans,
    screen_beat_rows,
)

FIXTURE = Path(__file__).parent / "fixtures" / "screen_qa_project" / "narration.json"
FPS = 30


@pytest.fixture()
def narration() -> dict:
    return json.loads(FIXTURE.read_text())


def _timings(spans):
    return [{"scene_id": i, "start": a, "end": b} for i, (a, b) in enumerate(spans, start=1)]


def _words(narration, *, per_word=0.3, start=0.5, pause_after=None, pause=0.6):
    """Evenly spaced word_timestamps for the whole narration, optionally with one long pause."""
    words, t, n = [], start, 0
    for sc in narration["scenes"]:
        for w in sc["text"].split():
            words.append({"word": w, "start": round(t, 3), "end": round(t + per_word * 0.8, 3)})
            t += per_word
            n += 1
            if pause_after is not None and n == pause_after:
                t += pause
    return words


# ─── beat keys: the review scheme, 0-based ─────────────────────────────────────

def test_beat_keys_are_exactly_the_review_scheme(narration):
    rows = screen_beat_rows(narration)
    expected = sh._beat_rows_for_custom(narration)
    assert [(r.key, r.text) for r in rows] == expected
    assert [r.key for r in rows] == ["1:0", "1:1", "2:0", "2:1", "3:0", "3:1"]


def test_single_fragment_bookends_keep_the_legacy_keys_and_body_scenes_are_keyed_by_sid():
    nar = {"mode": "screen_qa", "scenes": [
        {"scene_id": 1, "text": "Hook line here.", "is_intro": True, "visual_beats": [
            {"text": "Hook line here.", "query": "hook q"}]},
        {"scene_id": 2, "text": "A body scene without fragments."},
        {"scene_id": 3, "text": "Body two, then three.", "visual_beats": [
            {"text": "Body two,", "query": "q2"}, {"text": "then three.", "query": ""}]},
        {"scene_id": 4, "text": "Closing words.", "is_outro": True},
    ]}
    rows = screen_beat_rows(nar)
    assert [r.key for r in rows] == ["intro", "2", "3:0", "3:1", "outro"]
    assert [r.key for r in rows] == [k for k, _ in sh._beat_rows_for_custom(nar)]
    assert rows[0].unit == "intro" and rows[0].is_intro
    assert rows[-1].unit == "outro" and rows[-1].is_outro
    assert rows[1].unit == "scene"


def test_query_prefers_the_writers_then_the_research_item_then_the_words(narration):
    ctx = {"items": [{"entity": "Tony Stark", "visual_query": "stark hologram query"}]}
    nar = copy.deepcopy(narration)
    nar["scenes"][1]["visual_beats"][0]["query"] = ""        # writer gave none
    nar["scenes"][1]["visual_beats"][1]["query"] = ""
    rows = {r.key: r for r in screen_beat_rows(nar, ctx)}
    assert rows["1:0"].query == "Scott Lang van quantum tunnel"          # the writer's own
    assert rows["2:0"].query == "stark hologram query"                   # entity named in the words
    assert rows["2:1"].query == "solving time travel navigation overnight."  # words (<= 8)


# ─── timeline: gap absorption + exact frame tiling ─────────────────────────────

def test_windows_tile_the_whole_audio_timeline_exactly_even_with_gaps(narration):
    # lead silence before scene 1 AND silences between scenes — all must be absorbed.
    timings = _timings([(0.6, 4.0), (4.5, 8.0), (8.2, 12.0)])
    wins = plan_windows(narration, timings, audio_duration=12.5, merge_short=False)
    assert [w.keys[0] for w in wins] == ["1:0", "1:1", "2:0", "2:1", "3:0", "3:1"]
    total = sum(w.frames for w in wins)
    assert total == math.ceil((12.5 + AUDIO_TAIL_PAD) * FPS - 1e-6)
    assert wins[0].start == 0.0
    # contiguous, no overlap, no gap
    for a, b in zip(wins, wins[1:]):
        assert b.start == pytest.approx(a.start + a.duration, abs=1e-9)
    assert sum(w.duration for w in wins) == pytest.approx(total / FPS, abs=1e-9)
    # every scene's first window starts where the PREVIOUS scene ended (within half a frame)
    starts = {w.keys[0]: w.start for w in wins}
    assert starts["2:0"] == pytest.approx(4.0, abs=0.5 / FPS + 1e-9)
    assert starts["3:0"] == pytest.approx(8.0, abs=0.5 / FPS + 1e-9)


def test_no_drift_the_video_never_ends_before_the_audio(narration):
    timings = _timings([(0.0, 4.2), (4.2, 8.1), (8.1, 12.1)])
    for audio in (12.1, 12.3, 12.9):
        wins = plan_windows(narration, timings, audio_duration=audio)
        end = sum(w.frames for w in wins) / FPS
        assert end >= audio - 1e-9, (audio, end)
        assert end <= max(12.1, audio + AUDIO_TAIL_PAD) + 1 / FPS


def test_scene_timings_may_be_missing_or_an_empty_dict(narration):
    # p3-core calls build_shots_for_screen_qa(nar, {}, {}): no timing at all.
    for t in (None, {}, []):
        wins = plan_windows(narration, t, merge_short=False)
        assert len(wins) == 6
        want = sum(s["target_seconds"] for s in narration["scenes"])
        assert sum(w.duration for w in wins) == pytest.approx(want, abs=1 / FPS)


def test_a_scene_without_timing_follows_on_from_the_previous_end(narration):
    timings = _timings([(0.0, 4.2)])               # scenes 2-3 have no timing
    spans = scene_spans(narration["scenes"], timings)
    assert spans[0] == (1, 0.0, 4.2)
    assert spans[1][1] == 4.2 and spans[1][2] == pytest.approx(4.2 + 3.9)
    assert spans[2][1] == spans[1][2]


def test_fragment_cuts_land_in_the_real_silence_between_words(narration):
    # 10 words in scene 1's first fragment; a 0.6s pause right after word 10.
    words = _words(narration, pause_after=10)
    gap_mid = (words[9]["end"] + words[10]["start"]) / 2
    end1 = words[13]["end"]
    timings = _timings([(words[0]["start"], end1)])
    nar = copy.deepcopy(narration)
    nar["scenes"] = nar["scenes"][:1]
    wins = plan_windows(nar, timings, words, merge_short=False)
    assert wins[1].start == pytest.approx(gap_mid, abs=0.5 / FPS + 1e-9)
    # without word timings the cut is the proportional word share instead
    prop = plan_windows(nar, timings, None, merge_short=False)
    span = end1
    assert prop[1].start == pytest.approx(span * 10 / 14, abs=0.5 / FPS + 1e-9)


# ─── short windows ────────────────────────────────────────────────────────────

def test_short_unpicked_windows_merge_into_the_longer_neighbour_of_the_same_scene(narration):
    timings = _timings([(0.0, 4.2), (4.2, 8.1), (8.1, 12.1)])
    merged = plan_windows(narration, timings, min_seconds=1.5)
    # scene 1: 4 of 14 words of 4.2s = 1.2s < 1.5 -> absorbed into the 3.0s fragment
    assert [w.keys for w in merged][0] == ["1:0", "1:1"]
    assert all(w.scene_id == merged[i].scene_id for i, w in enumerate(merged))
    assert {w.scene_id for w in merged} == {1, 2, 3}                 # never merged across scenes
    assert sum(w.frames for w in merged) == sum(
        w.frames for w in plan_windows(narration, timings, merge_short=False))


def test_a_beat_with_a_master_pick_is_never_absorbed(narration):
    timings = _timings([(0.0, 4.2), (4.2, 8.1), (8.1, 12.1)])
    wins = plan_windows(narration, timings, min_seconds=1.5, protected={"1:1"})
    assert ["1:1"] in [w.keys for w in wins]
    short = next(w for w in wins if w.keys == ["1:1"])
    assert short.duration < 1.5


def test_every_window_keeps_the_renderers_0_4s_floor():
    nar = {"mode": "screen_qa", "scenes": [{"scene_id": 1, "text": "a b c d e f", "visual_beats": [
        {"text": "a", "query": ""}, {"text": "b c d e", "query": ""}, {"text": "f", "query": ""}]}]}
    wins = plan_windows(nar, _timings([(0.0, 2.0)]), merge_short=False)
    assert all(w.frames >= math.ceil(MIN_SHOT_SECONDS * FPS) for w in wins)
    assert sum(w.frames for w in wins) == round(2.0 * FPS)


def test_empty_narration_plans_nothing():
    assert plan_windows({"mode": "screen_qa", "scenes": []}) == []


# ─── UI helper ────────────────────────────────────────────────────────────────

def test_estimate_beat_seconds_splits_each_scene_by_words(narration):
    est = estimate_beat_seconds(narration, {"1": 4.0, "2": 4.0, "3": 4.0})
    assert set(est) == {"1:0", "1:1", "2:0", "2:1", "3:0", "3:1"}
    assert est["1:0"] + est["1:1"] == pytest.approx(4.0, abs=2 / FPS)
    assert est["1:0"] > est["1:1"]                       # 10 words vs 4
    # no TTS status yet -> the writer's target_seconds
    assert sum(estimate_beat_seconds(narration).values()) == pytest.approx(
        sum(s["target_seconds"] for s in narration["scenes"]), abs=3 / FPS)
