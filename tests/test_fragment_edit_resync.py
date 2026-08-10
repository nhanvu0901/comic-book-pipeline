"""Every path that rewrites a scene's text must resync word_count and remap locks
(2026-08-10).

Stage 4 (stages/stage_4/chunker.align_scenes_to_words) walks the TTS word-timestamp
stream consuming EXACTLY word_count words per scene, in order. A scene that claims more
words than it speaks eats the head of the next scene's words, so every later scene
drifts. Two projects shipped with exactly that: asm-kraven-buried scene 5 claimed 30
words for 24, asm-spider-goblin scene 6 claimed 19 for 9 — both from drop_fragment,
the one text-rewriting path that never resynced.

Separately, blanking a fragment's text box DELETES that fragment, renumbering the ones
after it. "<sid>:<frag>" locks had to be remapped or every later line inherited the
panel of the line below it.
"""
import pytest

from ui.screens.s_review_gate import (
    apply_fragment_edits,
    drop_fragment,
    remap_fragment_locks,
)


def _scene(sid=2, frags=("one two", "three four", "five six")):
    text = " ".join(frags)
    return {
        "scene_id": sid,
        "text": text,
        "visual_beats": list(frags),
        "word_count": len(text.split()),
        "target_seconds": round(len(text.split()) / 3.4, 2),
    }


# ── drop_fragment ────────────────────────────────────────────────────────────

def test_drop_resyncs_word_count_to_the_surviving_text():
    s = _scene()
    assert s["word_count"] == 6
    assert drop_fragment(s, 1) is True
    assert s["text"] == "one two five six"
    assert s["word_count"] == 4, "stale word_count is what desynced Stage 4"


def test_drop_resyncs_target_seconds_with_the_given_rate():
    s = _scene()
    drop_fragment(s, 1, wps=2.0)
    assert s["word_count"] == 4
    assert s["target_seconds"] == 2.0


def test_drop_word_count_always_matches_the_text_it_leaves_behind():
    for idx in (0, 1, 2):
        s = _scene()
        drop_fragment(s, idx)
        assert s["word_count"] == len(s["text"].split())


def test_last_fragment_is_refused_without_mutating():
    s = _scene(frags=("only line",))
    before = dict(s)
    assert drop_fragment(s, 0) is False
    assert s == before, "the caller deletes the whole scene instead; nothing may change here"


def test_out_of_range_raises():
    with pytest.raises(ValueError):
        drop_fragment(_scene(), 9)


# ── apply_fragment_edits: blanking a box deletes the fragment ────────────────

def test_blanked_fragment_is_reported_so_the_caller_can_remap():
    narration = {"words_per_second": 3.4, "scenes": [_scene()]}
    dropped: list = []
    apply_fragment_edits(narration, {(2, 1): "   "}, dropped_out=dropped)

    assert narration["scenes"][0]["visual_beats"] == ["one two", "five six"]
    assert dropped == [(2, 1)], "silently dropping it is what shifted every later lock"


def test_dropped_indices_come_back_highest_first():
    """The caller applies shifts one at a time; low-first would invalidate the rest."""
    narration = {"words_per_second": 3.4,
                 "scenes": [_scene(frags=("a", "b", "c", "d"))]}
    dropped: list = []
    apply_fragment_edits(narration, {(2, 1): "", (2, 2): ""}, dropped_out=dropped)
    assert dropped == [(2, 2), (2, 1)]


def test_remapping_the_reported_drop_keeps_locks_on_their_own_lines():
    """End-to-end: the panel picked for 'five six' must still be on 'five six'."""
    narration = {"words_per_second": 3.4, "scenes": [_scene()]}
    locks = {
        "2:0": {"panels": [{"page": 3, "panel": 0}]},   # one two
        "2:1": {"panels": [{"page": 5, "panel": 0}]},   # three four  <- being blanked
        "2:2": {"panels": [{"page": 9, "panel": 1}]},   # five six
    }
    dropped: list = []
    apply_fragment_edits(narration, {(2, 1): ""}, dropped_out=dropped)
    for sid, idx in dropped:
        locks = remap_fragment_locks(locks, sid, idx, -1)

    assert narration["scenes"][0]["visual_beats"] == ["one two", "five six"]
    assert locks["2:0"]["panels"] == [{"page": 3, "panel": 0}]
    assert locks["2:1"]["panels"] == [{"page": 9, "panel": 1}], "'five six' keeps its own panel"
    assert "2:2" not in locks, "no lock may point past the end of visual_beats"


def test_word_count_resynced_when_a_fragment_is_blanked():
    narration = {"words_per_second": 3.4, "scenes": [_scene()]}
    apply_fragment_edits(narration, {(2, 1): ""})
    s = narration["scenes"][0]
    assert s["word_count"] == len(s["text"].split()) == 4
    assert narration["total_word_count"] == 4


def test_blanking_every_fragment_is_ignored():
    narration = {"words_per_second": 3.4, "scenes": [_scene()]}
    dropped: list = []
    apply_fragment_edits(narration, {(2, 0): "", (2, 1): "", (2, 2): ""}, dropped_out=dropped)
    assert narration["scenes"][0]["visual_beats"] == ["one two", "three four", "five six"]
    assert dropped == [], "deleting a whole beat is the trash icon's job"


def test_dropped_out_is_optional():
    narration = {"words_per_second": 3.4, "scenes": [_scene()]}
    apply_fragment_edits(narration, {(2, 0): "edited words here"})   # must not raise
    assert narration["scenes"][0]["visual_beats"][0] == "edited words here"
