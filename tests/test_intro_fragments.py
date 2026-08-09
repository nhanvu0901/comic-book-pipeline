"""The spoken HOOK must be able to cut across panels.

All three modes emitted the intro as one scene with `visual_beats: []`, and stage_5's
bookend branch turns a fragment-less scene into exactly ONE unit — so a 26-word hook sat
~7 seconds on a single frozen drawing, across the 3-second gate where the channel
measurably loses viewers. The hook is now fragmented (verbatim, no LLM) and the bookend
branch only forces one unit when there is nothing to split.
"""
import stages.stage_3.write_script  # noqa: F401 — import order for stage_3 package
from stages.stage_3.beat_split import _verbatim_ok, split_hook_fragments


# ── the splitter ─────────────────────────────────────────────────────────────

def test_hook_splits_verbatim():
    hook = ("Batman walked into an Egyptian tomb with a baby Darkseid strapped to his "
            "chest, and the plan was to shoot himself with it.")
    frags = split_hook_fragments(hook)
    assert len(frags) >= 2
    assert _verbatim_ok(hook, frags), "fragments must concatenate back to the hook"


def test_short_hook_is_left_whole():
    """Below the threshold the hook IS one drawable moment — cutting it would just jitter."""
    assert split_hook_fragments("Adamantium is unbreakable.") == []
    assert split_hook_fragments("Everyone thought he was the hero until the last page.") == []


def test_no_runt_fragments():
    """A 2-word shot cannot hold a panel; runts fold into their neighbour."""
    hook = ("She lied, and the whole city believed her for thirty years, until one "
            "photograph turned up.")
    frags = split_hook_fragments(hook)
    assert frags, "this hook is long enough to split"
    assert min(len(f.split()) for f in frags) >= 4


def test_split_never_returns_a_non_verbatim_result():
    for hook in ["A very long hook that simply has no punctuation in it at all anywhere here",
                 "One, two, three, four, five, six, seven, eight, nine, ten, eleven, twelve.",
                 "He ran — she followed — nobody spoke — and the door closed behind them both."]:
        frags = split_hook_fragments(hook)
        assert not frags or _verbatim_ok(hook, frags), hook


# ── stage_5: a fragmented bookend stops collapsing to one shot ───────────────

def _bookend_units(scene, members):
    """Mirror of build_shots' bookend branch decision — the thing that used to force
    every hook to a single unit."""
    from stages.stage_5.shots import _vb_text
    frags = [c for c in (scene.get("visual_beats") or []) if _vb_text(c)]
    return 1 if len(frags) <= 1 else len(frags)


def test_fragmentless_bookend_still_gets_one_held_panel():
    scene = {"is_intro": True, "text": "one line", "visual_beats": []}
    assert _bookend_units(scene, []) == 1


def test_fragmented_bookend_gets_one_unit_per_fragment():
    scene = {"is_intro": True, "text": "a b c d, and e f g h",
             "visual_beats": ["a b c d,", "and e f g h"]}
    assert _bookend_units(scene, []) == 2


# ── the exporter and the UI must agree on the bookend rows ───────────────────
# They held separate copies of this logic. The copies drifted the moment the hook became
# fragmentable: review_gate wrote 1:0/1:1/1:2 while the UI still rebuilt one hard-coded
# "intro" row, matched nothing, and drew an empty "No candidates" card over three real rows.

def test_bookend_keys_are_fragments_when_the_hook_is_split():
    from stages.review_gate import bookend_row_keys
    scene = {"scene_id": 1, "text": "a b c, and d e f",
             "visual_beats": ["a b c,", "and d e f"]}
    assert bookend_row_keys(scene, "intro") == [("1:0", "fragment", "a b c,"),
                                                ("1:1", "fragment", "and d e f")]


def test_bookend_keeps_the_legacy_key_when_unsplit():
    """Existing locks.json files key the hook as "intro" — they must keep resolving."""
    from stages.review_gate import bookend_row_keys
    for vb in ([], ["one whole line"]):
        scene = {"scene_id": 1, "text": "one whole line", "visual_beats": vb}
        assert bookend_row_keys(scene, "intro") == [("intro", "intro", "one whole line")]
        assert bookend_row_keys(scene, "outro") == [("outro", "outro", "one whole line")]


def test_ui_reconcile_keeps_the_hook_fragment_rows():
    import ui  # noqa: F401 — flet compat
    from ui.screens.s_review_gate import reconcile_beats
    narration = {"scenes": [
        {"scene_id": 1, "is_intro": True, "text": "a b c, and d e f",
         "visual_beats": ["a b c,", "and d e f"]},
        {"scene_id": 2, "text": "body line", "visual_beats": []},
    ]}
    beats = [{"beat_key": "1:0", "candidates": [{"page": 1, "panel": 0}]},
             {"beat_key": "1:1", "candidates": [{"page": 1, "panel": 1}]},
             {"beat_key": "2", "candidates": [{"page": 2, "panel": 0}]}]
    rows = reconcile_beats(beats, narration, qa_mode=False)
    assert [r["beat_key"] for r in rows] == ["1:0", "1:1", "2"]
    assert all(r["candidates"] for r in rows), \
        "a fragment row must keep its panels, not be replaced by an empty 'No candidates' card"


def test_a_fragmented_hook_is_labelled_as_the_cold_open():
    """unit stays "fragment" (own text box, single-select), so the row needs its own
    marker — otherwise the hook reads as body line s1 and the cold open vanishes."""
    import ui  # noqa: F401
    from ui.screens.s_review_gate import _row_label
    row = {"narration_text": "a b c,", "scene_id": 1, "bookend": "intro"}
    assert _row_label(row, "1:0", "fragment").startswith("INTRO (cold-open) · mảnh 1")
    row["bookend"] = "outro"
    assert _row_label(row, "1:1", "fragment").startswith("OUTRO · mảnh 2")
    row.pop("bookend")
    assert _row_label(row, "1:0", "fragment").startswith("s1 · mảnh 1")


def test_reconcile_tags_bookend_rows_from_narration_not_the_export():
    """candidates.json can predate the hook being fragmentable; the tag must come from
    the live narration or the INTRO label is lost on an older export."""
    import ui  # noqa: F401
    from ui.screens.s_review_gate import reconcile_beats
    narration = {"scenes": [
        {"scene_id": 1, "is_intro": True, "text": "a b c, and d e f",
         "visual_beats": ["a b c,", "and d e f"]},
        {"scene_id": 2, "text": "body", "visual_beats": []},
        {"scene_id": 3, "is_outro": True, "text": "the end", "visual_beats": []},
    ]}
    rows = {r["beat_key"]: r for r in reconcile_beats([], narration, qa_mode=False)}
    assert rows["1:0"]["bookend"] == "intro" and rows["1:1"]["bookend"] == "intro"
    assert rows["outro"]["bookend"] == "outro"
    assert not rows["2"].get("bookend")


def test_fragment_locks_reach_the_intro_scenes_visual_beats():
    """The render contract: a "<sid>:<fi>" lock on the HOOK must pin that fragment, the
    same way a body fragment does. stage_5's by_id covers bookend scenes."""
    from stages.stage_5.shots import _apply_visual_beat_locks, _vb_pin
    narration = {"scenes": [
        {"scene_id": 1, "is_intro": True, "text": "a b c, and d e f",
         "visual_beats": ["a b c,", "and d e f"]},
    ]}
    _apply_visual_beat_locks(narration, {
        "1:0": {"panels": [{"page": 22, "panel": 3}]},
        "1:1": {"panels": [{"page": 21, "panel": 0}]},
    })
    vbs = narration["scenes"][0]["visual_beats"]
    assert [_vb_pin(b) for b in vbs] == [(22, 3), (21, 0)]
