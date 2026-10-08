"""Beat durations from the cached TTS chunks.

The point of stages.stage_4.beat_timing is that a beat's length is the one Stage 5 will really cut
with, so the main test here runs Stage 4's own alignment (align_scenes_to_words / build_caption_chunks)
and Stage 5's own Q&A shot builder over the same audio and compares the numbers."""
import json

import pytest

import config
import stages.review_gate as rg
from stages.stage_4.beat_timing import (
    BeatRow, beat_rows, beat_word_ranges, compute_beat_timings, scene_word_ranges, set_keep_awake,
)
from stages.stage_4.chatterbox_tts import _even_words
from stages.stage_4.chunker import align_scenes_to_words, build_caption_chunks
from stages.stage_5 import shots
from stages.stage_5.shots import build_shots


def test_set_keep_awake_runs_without_error():
    set_keep_awake(True)          # no-op off Windows, must never raise anywhere
    set_keep_awake(False)


def _sc(sid, text, beats=None, **kw):
    return {"scene_id": sid, "text": text, "visual_beats": beats if beats is not None else [], **kw}


# ─── keys: the review gate's, not a private scheme ───────────────────────────

def test_beat_rows_use_the_review_beat_keys_for_string_beats_and_bookends():
    narr = {"scenes": [
        _sc(1, "Hook one. Hook two.", ["Hook one.", "Hook two."], is_intro=True),   # fragmented bookend
        _sc(2, "He fell, then rose.", ["He fell,", "then rose."]),
        _sc(3, "Plain scene with no beats."),                                       # one "<sid>" row
        _sc(4, "Bye now.", is_outro=True),                                          # single-fragment outro
    ]}
    rows = beat_rows(narr)
    assert [(r.beat_key, r.scene_id) for r in rows] == [
        ("1:0", 1), ("1:1", 1), ("2:0", 2), ("2:1", 2), ("3", 3), ("outro", 4)]
    # exactly the keys the custom-image / clip passes (and the review gate) use
    assert [r.beat_key for r in rows] == [k for k, _t in shots._beat_rows_for_custom(narr)]
    assert [r.words for r in rows] == [2, 2, 2, 2, 5, 2]


def test_beat_rows_survive_dict_beats_and_unfragmented_intro():
    narr = {"scenes": [
        _sc(1, "Hook.", ["Hook."], is_intro=True),
        _sc(2, "Frank hunts then wins.", [{"text": "Frank hunts", "page": 3, "panel": 0}, {"text": "then wins."}]),
    ]}
    assert [r.beat_key for r in beat_rows(narr)] == ["intro", "2:0", "2:1"]


# ─── the two word walks ───────────────────────────────────────────────────────

def test_scene_word_ranges_follow_align_scenes_to_words_cursor_rule():
    scenes = [_sc(1, "a b c"), {**_sc(2, "d e"), "word_count": 4}, _sc(3, "f g h i")]
    assert scene_word_ranges(scenes, 100) == {1: (0, 3), 2: (3, 7), 3: (7, 11)}
    # the stream running dry drops later scenes, like Stage 4 (which warns)
    assert scene_word_ranges(scenes, 5) == {1: (0, 3), 2: (3, 5)}


def test_beat_word_ranges_bucket_by_word_position_and_tail_stays_with_the_last_beat():
    rows = [BeatRow("1:0", 1, "a b", 2), BeatRow("1:1", 1, "c d e", 3)]
    assert beat_word_ranges(rows, {1: (10, 15)}) == {"1:0": (10, 12), "1:1": (12, 15)}
    # the scene's stream slice is longer than its beats' text: the last beat takes the rest
    assert beat_word_ranges(rows, {1: (10, 18)}) == {"1:0": (10, 12), "1:1": (12, 18)}
    # shorter: later beats get nothing rather than a negative range
    assert beat_word_ranges(rows, {1: (10, 11)}) == {"1:0": (10, 11)}


# ─── durations ────────────────────────────────────────────────────────────────

def test_a_beat_needs_only_the_chunks_it_overlaps():
    narr = {"scenes": [_sc(1, "One two three.", ["One two three."]), _sc(2, "Four five.", ["Four five."])]}
    chunks = ["One two three.", "Four five."]
    t = compute_beat_timings(narr, chunks, [None, 2.0])          # scene 1 not synthesized yet
    assert t.durations == {"2:0": 2.0} and not t.complete and t.windows == {}
    t = compute_beat_timings(narr, chunks, [3.0, 2.0])
    assert t.complete and t.durations == {"1:0": 3.0, "2:0": 2.0}
    assert t.windows == {"1:0": (0.0, 3.0), "2:0": (3.0, 5.0)}
    assert t.scene_durations == {1: 3.0, 2: 2.0}


def test_a_beat_is_cut_proportionally_by_words_inside_a_chunk():
    narr = {"scenes": [_sc(1, "Frank hunts the giant, then he wins big.",
                           ["Frank hunts the giant,", "then he wins big."])]}
    t = compute_beat_timings(narr, ["Frank hunts the giant, then he wins big."], [4.0])
    assert t.durations["1:0"] == pytest.approx(4.0 * 4 / 8) and t.durations["1:1"] == pytest.approx(4.0 * 4 / 8)


# ─── against Stage 4 + Stage 5 for real ───────────────────────────────────────

def _pg(panels, src, w=600, h=2700):
    return {"panels": panels, "source_image": src, "image_dimensions": {"width": w, "height": h}}


def _panel_at(y, desc):
    return {"bbox": {"x": 0, "y": y, "w": 600, "h": 900}, "description": desc, "characters": []}


def test_beat_durations_match_the_shot_durations_stage5_cuts(tmp_path, monkeypatch):
    """Two scenes, five fragments, chunks of very different pace (one sentence is 3x slower), every
    fragment longer than QA_MIN_SHOT_SECONDS so Stage 5's min-length merge is a no-op. The beat
    durations computed from chunk durations alone must equal the duration of the shot Stage 5 cuts
    for that fragment, from the word stream Stage 4 really builds."""
    scenes = [
        _sc(1, "Frank hunts the giant slowly. Then he strikes it hard.",
            ["Frank hunts the giant slowly.", "Then he strikes it hard."]),
        _sc(2, "The giant falls and the whole city cheers wildly tonight.",
            ["The giant falls and", "the whole city cheers wildly tonight."]),
        _sc(3, "Nobody ever forgets that night again."),
    ]
    narration = {"scenes": scenes}
    chunks = ["Frank hunts the giant slowly.", "Then he strikes it hard.",
              "The giant falls and the whole city cheers wildly tonight.", "Nobody ever forgets that night again."]
    durs = [4.5, 2.0, 5.2, 3.1]

    words, t = [], 0.0
    for c, d in zip(chunks, durs):                 # Stage 4's own timeline (chatterbox_tts._even_words)
        words.extend(_even_words(c, t, d))
        t += d
    scene_timings = [s.to_dict() for s in align_scenes_to_words(scenes, words)]
    caption_chunks = [c.to_dict() for c in build_caption_chunks(scenes, words)]

    timings = compute_beat_timings(narration, chunks, durs)
    assert timings.complete

    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "qa"
    (proj / "review").mkdir(parents=True)
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (proj / "narration.json").write_text(json.dumps(narration))
    keys = [r.beat_key for r in beat_rows(narration)]
    assert keys == ["1:0", "1:1", "2:0", "2:1", "3"]
    (proj / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": {
        k: {"panels": [{"page": 5, "panel": i % 3}], "source": "batcave"} for i, k in enumerate(keys)}}))
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    pages = {5: _pg([_panel_at(0, "a"), _panel_at(900, "b"), _panel_at(1800, "c")], "p5.png")}

    built = build_shots(narration, scene_timings=scene_timings, caption_chunks=caption_chunks,
                        pages_by_number=pages, project="qa")
    assert len(built) == len(keys), [round(s.duration_seconds, 3) for s in built]
    for key, shot in zip(keys, built):
        assert shot.duration_seconds == pytest.approx(timings.durations[key], abs=0.02), (key, shot.caption_text)
    assert sum(s.duration_seconds for s in built) == pytest.approx(sum(durs), abs=0.05)
    # and the absolute windows are contiguous over the whole audio
    ends = [timings.windows[k][1] for k in keys]
    assert ends[-1] == pytest.approx(sum(durs), abs=1e-3)
