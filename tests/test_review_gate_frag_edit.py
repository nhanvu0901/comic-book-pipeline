"""Review Beats UI — PER-FRAGMENT narration editing.

Every fragment card owns its own TextField now (it used to be ONE "sửa cả câu" box on the
scene's FIRST fragment that rewrote the whole scene for all its siblings). The pure write-back
helper (apply_fragment_edits) is tested standalone for both fragment shapes — str (Q&A/recap)
and dict {"text","page","panel"} (micro, writer-picks-panel) — then the whole screen is built
headless (FakePage) and edits are replayed end-to-end in sandbox project copies.

The invariant under test: after a save, scene["text"] is the concat of its fragments, i.e.
stages.stage_3.beat_split._verbatim_ok(scene["text"], frags) is True — that's what keeps
Stage 5's word-position shot bucketing aligned.
"""
import json
import shutil

import pytest

import ui  # noqa: F401 — patches flet 0.85 compat before screens import it
import flet as ft

from config import PROJECTS_ROOT
import ui.screens.s_review_gate as sg
from stages.stage_3.beat_split import _verbatim_ok
from tests.test_review_gate_grid_hide import _build, _walk


QA_PROJ = "wolverine-killed-his-xmen"        # Q&A: str fragments
MICRO_PROJ = "psylocke-blood-hunt"           # micro_moment: dict fragments (page/panel pins)


def _needs(proj: str):
    return pytest.mark.skipif(
        not (PROJECTS_ROOT / proj / "review" / "candidates.json").exists(),
        reason=f"{proj} export not present",
    )


# ─── pure helper ─────────────────────────────────────────────────────────────

def _str_narration() -> dict:
    return {
        "words_per_second": 3.4, "total_word_count": 9, "estimated_duration_seconds": 2.65,
        "scenes": [
            {"scene_id": 1, "text": "hook line", "is_intro": True,
             "word_count": 2, "target_seconds": 0.59},
            {"scene_id": 2, "text": "alpha beta gamma", "word_count": 3, "target_seconds": 0.88,
             "visual_beats": ["alpha", "beta", "gamma"]},
            {"scene_id": 3, "text": "delta epsilon", "word_count": 2, "target_seconds": 0.59,
             "visual_beats": ["delta", "epsilon"]},
            {"scene_id": 4, "text": "bye now", "is_outro": True,
             "word_count": 2, "target_seconds": 0.59},
        ],
    }


def test_apply_fragment_edits_str_middle_fragment():
    nar = _str_narration()
    out = sg.apply_fragment_edits(nar, {(2, 1): "beta two three"})
    assert out is nar                                    # mutates in place, returns it
    s2 = nar["scenes"][1]
    assert s2["visual_beats"] == ["alpha", "beta two three", "gamma"]
    assert s2["text"] == "alpha beta two three gamma"     # scene text re-derived from frags
    assert _verbatim_ok(s2["text"], s2["visual_beats"])   # the Stage-5 invariant
    assert s2["word_count"] == 5 and s2["target_seconds"] == round(5 / 3.4, 2)
    assert nar["scenes"][2] == _str_narration()["scenes"][2]       # sibling scene untouched
    assert nar["total_word_count"] == 2 + 5 + 2 + 2                # totals recomputed
    assert nar["estimated_duration_seconds"] == round(11 / 3.4, 2)


def test_apply_fragment_edits_multi_scene_and_blank_drop():
    nar = _str_narration()
    sg.apply_fragment_edits(nar, {(2, 0): "  A one  ", (2, 2): "", (3, 1): "epsilon zed"})
    s2, s3 = nar["scenes"][1], nar["scenes"][2]
    assert s2["visual_beats"] == ["A one", "beta"]        # blanked frag dropped, text trimmed
    assert s2["text"] == "A one beta" and _verbatim_ok(s2["text"], s2["visual_beats"])
    assert s3["visual_beats"] == ["delta", "epsilon zed"]
    assert s3["text"] == "delta epsilon zed" and _verbatim_ok(s3["text"], s3["visual_beats"])


def test_apply_fragment_edits_dict_keeps_page_panel_pin():
    nar = {"words_per_second": 3.4, "scenes": [
        {"scene_id": 5, "text": "one two", "word_count": 2, "visual_beats": [
            {"text": "one", "page": 17, "panel": 3},
            {"text": "two", "page": 23, "panel": 0},
        ]},
    ]}
    sg.apply_fragment_edits(nar, {(5, 1): "two edited"})
    s = nar["scenes"][0]
    assert s["visual_beats"] == [{"text": "one", "page": 17, "panel": 3},
                                 {"text": "two edited", "page": 23, "panel": 0}]
    assert s["text"] == "one two edited"
    assert _verbatim_ok(s["text"], [sg._frag_text(v) for v in s["visual_beats"]])


def test_apply_fragment_edits_ignores_missing_targets_and_empty():
    base = _str_narration()
    for edits in ({}, {(99, 0): "ghost scene"}, {(2, 7): "ghost frag"}, {(2, -1): "negative"},
                  {(1, 0): "intro has no fragments"}, {(3, 0): "", (3, 1): "  "}):
        nar = _str_narration()
        assert sg.apply_fragment_edits(nar, edits) == base, edits   # unchanged, never raises


# ─── headless build: one TextField per fragment ──────────────────────────────

def _fields(root) -> dict[str, ft.TextField]:
    """{label: narration TextField}. The page-jump fields aren't multiline and carry no label."""
    return {str(c.label): c for c in _walk(root)
            if isinstance(c, ft.TextField) and c.multiline}


@_needs(QA_PROJ)
def test_headless_build_one_textfield_per_fragment(monkeypatch, capsys):
    """12 rows (intro + 10 fragments + outro) → 12 narration boxes, and a MIDDLE fragment's
    box holds ITS OWN clause, not the whole scene sentence."""
    _page, root = _build(QA_PROJ, monkeypatch)
    boxes = [c for c in _walk(root) if isinstance(c, ft.TextField) and c.multiline]
    labels = [str(c.label) for c in boxes]
    assert len(boxes) == 12, labels
    assert labels == ["INTRO (cold-open)"] + [f"s{sid} · mảnh {i + 1}"
                                              for sid, n in ((2, 4), (3, 3), (4, 3))
                                              for i in range(n)] + ["OUTRO"]
    for box in boxes:                                    # style matches the old scene box
        assert (box.multiline, box.min_lines, box.max_lines, box.text_size) == (True, 2, 5, 13)

    nar = json.loads((PROJECTS_ROOT / QA_PROJ / "narration.json").read_text())
    s3 = next(s for s in nar["scenes"] if s["scene_id"] == 3)
    mid = _fields(root)["s3 · mảnh 2"]
    assert mid.value == s3["visual_beats"][1]            # the FRAGMENT, not the scene
    assert mid.value != s3["text"] and len(mid.value) < len(s3["text"])
    with capsys.disabled():
        print(f"\n  narration_textfields={len(boxes)}  s3·2={mid.value!r}")


# ─── sandbox: edit one fragment → Save → disk ────────────────────────────────

class _Ev:
    def __init__(self, control):
        self.control = control


def _type(field: ft.TextField, text: str):
    field.value = text
    field.on_change(_Ev(field))


def _save_btn(root) -> ft.OutlinedButton:
    return next(c for c in _walk(root) if isinstance(c, ft.OutlinedButton)
                and "Save narration edits" in str(c.content or ""))


def _drop_buttons(root) -> dict[str, ft.IconButton]:
    """{fragment label: its "drop this line" trash button}. The trash lives in the card
    HEADER, i.e. immediately BEFORE that card's narration box in tree order."""
    nodes = list(_walk(root))
    out: dict[str, ft.IconButton] = {}
    for i, c in enumerate(nodes):
        if isinstance(c, ft.IconButton) and str(c.tooltip or "").startswith("Xóa dòng này"):
            label = next((str(n.label) for n in nodes[i:]
                          if isinstance(n, ft.TextField) and n.multiline), "")
            out.setdefault(label, c)
    return out


def _sandbox(proj: str, name: str):
    box = PROJECTS_ROOT / name
    shutil.rmtree(box, ignore_errors=True)
    shutil.copytree(PROJECTS_ROOT / proj, box,
                    ignore=shutil.ignore_patterns("raw_comic", "panel_viz", "*.mp4", "*.wav"))
    return box


def _edit_one_fragment(proj: str, sid: int, frag_no: int, new_text: str, monkeypatch):
    """Replay: build screen → type into that fragment's box → Save. Returns disk state."""
    box = _sandbox(proj, f"_uitest_edit_{sid}")
    try:
        _page, root = _build(box.name, monkeypatch)
        before = json.loads((box / "narration.json").read_text())
        _type(_fields(root)[f"s{sid} · mảnh {frag_no}"], new_text)
        _save_btn(root).on_click(None)
        return (before,
                json.loads((box / "narration.json").read_text()),
                json.loads((box / "review" / "locks.json").read_text()))
    finally:
        shutil.rmtree(box, ignore_errors=True)


@_needs(QA_PROJ)
def test_sandbox_str_fragment_edit_end_to_end(monkeypatch, capsys):
    new = "Logan rams his adamantium claws through the steel skull and buries him under an X."
    before, after, locks = _edit_one_fragment(QA_PROJ, 3, 2, new, monkeypatch)
    s3_before = next(s for s in before["scenes"] if s["scene_id"] == 3)
    s3 = next(s for s in after["scenes"] if s["scene_id"] == 3)
    assert s3["visual_beats"][1] == new
    assert s3["visual_beats"][0] == s3_before["visual_beats"][0]       # siblings untouched
    assert s3["visual_beats"][2] == s3_before["visual_beats"][2]
    assert s3["text"] == " ".join(s3["visual_beats"])
    assert _verbatim_ok(s3["text"], s3["visual_beats"])
    assert s3["word_count"] == len(s3["text"].split()) != s3_before["word_count"]
    assert after["total_word_count"] == sum(s["word_count"] for s in after["scenes"])
    assert [s["visual_beats"] for s in after["scenes"] if s["scene_id"] != 3] == \
           [s["visual_beats"] for s in before["scenes"] if s["scene_id"] != 3]
    assert locks["approved"] is False and locks["approved_at"] is None
    with capsys.disabled():
        print(f"  QA s3: frags={len(s3['visual_beats'])} verbatim=True "
              f"wc {s3_before['word_count']}→{s3['word_count']} approved={locks['approved']}")


@_needs(MICRO_PROJ)
def test_sandbox_dict_fragment_edit_keeps_pin(monkeypatch, capsys):
    new = "and Psylocke answers softly that she believed that for far too long,"
    before, after, locks = _edit_one_fragment(MICRO_PROJ, 7, 2, new, monkeypatch)
    s7_before = next(s for s in before["scenes"] if s["scene_id"] == 7)
    s7 = next(s for s in after["scenes"] if s["scene_id"] == 7)
    assert isinstance(s7["visual_beats"][1], dict)
    assert s7["visual_beats"][1]["text"] == new
    assert [(v["page"], v["panel"]) for v in s7["visual_beats"]] == \
           [(v["page"], v["panel"]) for v in s7_before["visual_beats"]]      # pins intact
    assert s7["text"] == " ".join(v["text"] for v in s7["visual_beats"])
    assert _verbatim_ok(s7["text"], [v["text"] for v in s7["visual_beats"]])
    assert locks["approved"] is False and locks["approved_at"] is None
    with capsys.disabled():
        print(f"  micro s7: pins={[(v['page'], v['panel']) for v in s7['visual_beats']]} "
              f"verbatim=True approved={locks['approved']}")


@_needs(QA_PROJ)
def test_scene_row_still_saves_per_scene_text(monkeypatch):
    """intro/outro rows have no fragments → they keep the per-SCENE write path untouched."""
    box = _sandbox(QA_PROJ, "_uitest_edit_intro")
    try:
        _page, root = _build(box.name, monkeypatch)
        before = json.loads((box / "narration.json").read_text())
        _type(_fields(root)["OUTRO"], "Rewritten outro line.")
        _save_btn(root).on_click(None)
        after = json.loads((box / "narration.json").read_text())
        assert next(s for s in after["scenes"] if s.get("is_outro"))["text"] == \
            "Rewritten outro line."
        assert [s.get("visual_beats") for s in after["scenes"]] == \
               [s.get("visual_beats") for s in before["scenes"]]      # no fragment touched
    finally:
        shutil.rmtree(box, ignore_errors=True)


# ─── op sync: a drop/split must not leave a stale (sid, frag_idx) edit ───────

@_needs(QA_PROJ)
def test_drop_line_clears_that_scenes_pending_fragment_edits(monkeypatch, capsys):
    """frag_edits is keyed by fragment INDEX; dropping a line shifts them, so the op'd scene's
    pending edits must be discarded (rebuilt cards re-seed from disk) while ANOTHER scene's
    pending edit survives."""
    box = _sandbox(QA_PROJ, "_uitest_edit_drop")
    try:
        _page, root = _build(box.name, monkeypatch)
        _type(_fields(root)["s3 · mảnh 3"], "STALE never saved")
        _type(_fields(root)["s2 · mảnh 1"], "OTHER scene edit survives")
        _drop_buttons(root)["s3 · mảnh 1"].on_click(None)     # indices in scene 3 shift by -1
        nar = json.loads((box / "narration.json").read_text())
        s3 = next(s for s in nar["scenes"] if s["scene_id"] == 3)
        assert len(s3["visual_beats"]) == 2
        assert not any("STALE" in f for f in s3["visual_beats"])
        assert _verbatim_ok(s3["text"], s3["visual_beats"])
        boxes = _fields(root)
        assert boxes["s3 · mảnh 1"].value == s3["visual_beats"][0]        # re-seeded from disk
        assert boxes["s2 · mảnh 1"].value == "OTHER scene edit survives"  # untouched scene
        _save_btn(root).on_click(None)
        after = json.loads((box / "narration.json").read_text())
        s2 = next(s for s in after["scenes"] if s["scene_id"] == 2)
        s3b = next(s for s in after["scenes"] if s["scene_id"] == 3)
        assert s2["visual_beats"][0] == "OTHER scene edit survives"
        assert _verbatim_ok(s2["text"], s2["visual_beats"])
        assert s3b["visual_beats"] == s3["visual_beats"]      # no ghost write-back into scene 3
        # …and the dropped words must NOT come back through the per-scene write path: only a
        # scene whose OWN box was typed into gets a per-scene text write.
        assert s3b["text"] == s3["text"] and _verbatim_ok(s3b["text"], s3b["visual_beats"])
        with capsys.disabled():
            print(f"  after drop: s3 frags={s3b['visual_beats']}")
    finally:
        shutil.rmtree(box, ignore_errors=True)


@_needs(QA_PROJ)
def test_approve_autosaves_pending_fragment_edit(monkeypatch, capsys):
    """Approve still auto-saves first: the pending fragment edit lands on disk AND the project
    ends up approved (the save's un-approve must not fight the approve that follows it)."""
    box = _sandbox(QA_PROJ, "_uitest_edit_appr")
    try:
        _page, root = _build(box.name, monkeypatch)
        _type(_fields(root)["s4 · mảnh 1"], "Saved by the Approve click.")
        approve = next(c for c in _walk(root) if isinstance(c, ft.ElevatedButton)
                       and "Approve" in str(c.content or ""))
        if json.loads((box / "review" / "locks.json").read_text())["approved"]:
            approve.on_click(None)          # this project ships approved → un-approve first
        approve.on_click(None)
        nar = json.loads((box / "narration.json").read_text())
        locks = json.loads((box / "review" / "locks.json").read_text())
        s4 = next(s for s in nar["scenes"] if s["scene_id"] == 4)
        assert s4["visual_beats"][0] == "Saved by the Approve click."
        assert _verbatim_ok(s4["text"], s4["visual_beats"])
        assert locks["approved"] is True and locks["approved_at"]
        assert locks["narration_sha1"]                    # hash taken AFTER the save
        with capsys.disabled():
            print(f"  approve autosave: approved={locks['approved']} verbatim=True")
    finally:
        shutil.rmtree(box, ignore_errors=True)


def test_no_page_update_in_rebuild_path():
    """_rebuild must never call page.update() — it patches the whole ~2500-control tree."""
    body = open(sg.__file__).read().split("\n    def _rebuild(", 1)[1].split("\n    def ", 1)[0]
    hits = [ln for ln in body.splitlines()
            if "page.update()" in ln and not ln.strip().startswith("#")]
    assert hits == [], hits


# ─── whole-scene text box (intro/outro/scene rows) ───────────────────────────
# The per-scene box used to write scene["text"] and nothing else. stage_4/chunker.py
# times shots off word_count, never the text, so a stale count silently shifted every
# downstream shot boundary by seconds — the desync that hit two projects.

def test_scene_text_edit_resyncs_word_count_and_totals():
    nar = _str_narration()
    out = sg.apply_scene_text_edits(nar, {1: "a much longer hook line than before"})
    intro = out["scenes"][0]
    assert intro["text"] == "a much longer hook line than before"
    assert intro["word_count"] == 7                      # was 2
    assert intro["target_seconds"] == round(7 / 3.4, 2)
    assert out["total_word_count"] == 7 + 3 + 2 + 2      # recomputed from every scene
    assert out["estimated_duration_seconds"] == round(14 / 3.4, 2)


def test_scene_text_edit_keeps_verbatim_invariant():
    """Editing a fragmented scene through the whole-scene box must leave
    text == " ".join(visual_beats) — Stage 5 buckets shots by word position."""
    nar = _str_narration()
    out = sg.apply_scene_text_edits(nar, {2: "alpha beta gamma delta"})
    scene = out["scenes"][1]
    assert _verbatim_ok(scene["text"], scene["visual_beats"])
    assert scene["visual_beats"] == ["alpha beta gamma delta"]


def test_scene_text_edit_leaves_matching_fragments_alone():
    """Text unchanged from its fragments → per-fragment panel pins survive."""
    nar = _str_narration()
    out = sg.apply_scene_text_edits(nar, {2: "alpha beta gamma"})
    assert out["scenes"][1]["visual_beats"] == ["alpha", "beta", "gamma"]


def test_scene_text_edit_skips_fragment_edited_scenes():
    """A scene with fragment edits belongs to apply_fragment_edits, which re-derives
    its text from the fragments — a per-scene write there would be overwritten."""
    nar = _str_narration()
    out = sg.apply_scene_text_edits(nar, {2: "IGNORED"}, skip={2})
    assert out["scenes"][1]["text"] == "alpha beta gamma"
    assert out["total_word_count"] == 9                  # untouched → no recompute


def test_scene_text_edit_ignores_blank():
    nar = _str_narration()
    out = sg.apply_scene_text_edits(nar, {1: "   "})
    assert out["scenes"][0]["text"] == "hook line"
    assert out["scenes"][0]["word_count"] == 2


def test_scene_text_edit_never_invents_fragments():
    """An unfragmented scene (intro/outro) stays unfragmented — Stage 5 has its own
    path for those, and writing [text] would change the scene's shape."""
    nar = _str_narration()
    out = sg.apply_scene_text_edits(nar, {1: "brand new hook"})
    assert out["scenes"][0].get("visual_beats") in (None, [])
