"""Transparency FEEDBACK-retry (not a blind re-roll): the critic's flags are
formatted into a FIX block and routed BACK into each mode's writer prompt so it
repairs the exact scenes flagged. Verifies:
  (a) _format_clarity_fixes maps flag types -> concrete repair directives, and
      returns "" for empty / non-actionable input;
  (b) the block reaches every writer prompt (recap / micro / Q&A);
  (c) clarity_fixes="" leaves each prompt BYTE-IDENTICAL to the no-flag path
      (no regression to a tuned writer);
  (d) the pipeline retry keeps the draft with FEWER flags and passes a
      non-empty clarity_fixes into the re-write.
No network: the LLM writer call is monkeypatched everywhere."""
import json
import types

import pytest

import stages.stage_3.write_script as ws
import stages.stage_3.micro_moment as mm
import stages.stage_3.explore_answer as ea
import stages.stage_3.pipeline as pl
from stages.stage_3.schema import Beat, Glossary, CharacterEntry


# ── (a) formatter ────────────────────────────────────────────────────────────
def test_format_clarity_fixes_maps_every_type_to_a_directive():
    flags = [
        "transparency[undefined_character]: scene S2: 'Dawn' appears with no introduction",
        "transparency[overstuffed_sentence]: scene S4: three events chained with dashes",
        "transparency[subplot_dilutes]: scene S5: a herald arc muddies the core point",
        "transparency[off_target]: scene S6: this scene does not serve the focus",
    ]
    block = ws._format_clarity_fixes(flags)
    assert block.startswith("PREVIOUS DRAFT HAD CLARITY ISSUES")
    assert block.endswith("\n\n")
    # every scene id + the type-specific directive language must land in the block
    assert "scene 2" in block and "role tag" in block
    assert "scene 4" in block and "split this sentence" in block
    assert "scene 5" in block and "off-focus thread" in block
    assert "scene 6" in block and "serves the focus" in block


def test_format_clarity_fixes_empty_and_unactionable_return_blank():
    assert ws._format_clarity_fixes([]) == ""
    # unmapped type + malformed string -> nothing actionable -> ""
    assert ws._format_clarity_fixes(["transparency[mystery]: scene S1: ?"]) == ""
    assert ws._format_clarity_fixes(["not a transparency flag at all"]) == ""


_SAMPLE_FLAGS = [
    "transparency[undefined_character]: scene S2: 'Dawn' appears with no introduction",
]
_BLOCK = ws._format_clarity_fixes(_SAMPLE_FLAGS)


def _capture_into(store):
    def _cap(*, system, user, models=None, max_tokens=1600, progress=None,
             label="llm", validator=None):
        store["user"] = user
        # a minimal valid payload so the writer's post-call parse never raises
        raw = json.dumps({
            "title": "t", "hook": "h", "ending_style": "thesis",
            "scenes": store["scenes"],
        })
        return raw, "fake-model"
    return _cap


# ── (b)+(c) recap writer (write_scenes) ──────────────────────────────────────
def _recap_beats():
    return [
        Beat(id=1, function="COLD_OPEN", name="Open", summary="A hero arrives.", page_refs=[1]),
        Beat(id=2, function="LANDING", name="End", summary="The hero leaves.", page_refs=[2]),
    ]


def _run_write_scenes(monkeypatch, clarity_fixes):
    store = {"user": "", "scenes": [
        {"text": "A hero arrives.", "connective": None, "beat_id": 1},
        {"text": "The hero leaves.", "connective": None, "beat_id": 2},
        {"text": "The comic is X.", "connective": None, "beat_id": 2},
    ]}
    monkeypatch.setattr(ws, "call_with_chain", _capture_into(store))
    beats = _recap_beats()
    gloss = Glossary(characters={"Hero": CharacterEntry(canonical_name="Hero")})
    ws.write_scenes(beats, gloss, {"title": "X", "plot_summary": "p"}, [], "recap_summary",
                    clarity_fixes=clarity_fixes)
    return store["user"]


def test_recap_prompt_injects_block_and_is_byte_identical_when_empty(monkeypatch):
    plain = _run_write_scenes(monkeypatch, "")
    withblk = _run_write_scenes(monkeypatch, _BLOCK)
    assert "PREVIOUS DRAFT HAD CLARITY ISSUES" not in plain
    assert _BLOCK in withblk
    # ONLY the block was inserted — removing it reproduces the tuned prompt exactly
    assert withblk.replace(_BLOCK, "", 1) == plain


# ── (b)+(c) micro writer (_call_micro_writer) ────────────────────────────────
def _micro_window():
    return [Beat(id=1, function="SETUP", name="a", summary="s", page_refs=[1],
                 characters_active=["Hulk"]),
            Beat(id=2, function="CLIMAX", name="b", summary="s", page_refs=[2],
                 characters_active=["Hulk"])]


def _run_micro(monkeypatch, clarity_fixes):
    store = {"user": "", "scenes": [
        {"text": "one", "connective": None, "beat_id": 1},
        {"text": "two", "connective": None, "beat_id": 2},
    ]}
    monkeypatch.setattr(mm, "call_with_chain", _capture_into(store))
    mm._call_micro_writer(_micro_window(), {"title": "X", "plot_summary": "p"},
                          "the moment", model=None, progress=None, debug_dump={},
                          story_pages=None, clarity_fixes=clarity_fixes)
    return store["user"]


def test_micro_prompt_injects_block_and_is_byte_identical_when_empty(monkeypatch):
    plain = _run_micro(monkeypatch, "")
    withblk = _run_micro(monkeypatch, _BLOCK)
    assert "PREVIOUS DRAFT HAD CLARITY ISSUES" not in plain
    assert _BLOCK in withblk
    assert withblk.replace(_BLOCK, "", 1) == plain


# ── (b)+(c) Q&A writer (_call_explore_writer) ────────────────────────────────
def _explore_beats():
    return [Beat(id=1, function="COLD_OPEN", name="Wolverine", page_refs=[1], cause="healing"),
            Beat(id=2, function="LANDING", name="Thanos", page_refs=[2], cause="endured")]


def _run_explore(monkeypatch, clarity_fixes):
    store = {"user": "", "scenes": [
        {"text": "Wolverine healed.", "connective": None, "beat_id": 1},
        {"text": "Thanos endured.", "connective": None, "beat_id": 2},
    ]}
    monkeypatch.setattr(ea, "call_with_chain", _capture_into(store))
    items = [{"source_comic": "X #1", "drawable_moment": "m1"},
             {"source_comic": "T #3", "drawable_moment": "m2"}]
    ea._call_explore_writer(_explore_beats(), items, "Who survived?", model=None,
                            progress=None, debug_dump={}, archetype="list",
                            clarity_fixes=clarity_fixes)
    return store["user"]


def test_explore_prompt_injects_block_and_is_byte_identical_when_empty(monkeypatch):
    plain = _run_explore(monkeypatch, "")
    withblk = _run_explore(monkeypatch, _BLOCK)
    assert "PREVIOUS DRAFT HAD CLARITY ISSUES" not in plain
    assert _BLOCK in withblk
    assert withblk.replace(_BLOCK, "", 1) == plain


# ── (d) pipeline orchestration: keep fewer-flag draft + pass real clarity_fixes ─
def _fake_nar():
    n = types.SimpleNamespace()
    n.source_project = ""
    n.to_dict = lambda: {}
    return n


def test_pipeline_retry_feeds_fixes_and_keeps_fewer_flag_draft(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "TRANSPARENCY_RETRY", True)
    monkeypatch.setattr(pl, "load_inputs", lambda p: ({"title": "T"}, [{"is_story_page": True}]))
    monkeypatch.setattr(pl, "_load_direction", lambda p: {})
    monkeypatch.setattr(pl, "_write_run_dump", lambda *a, **k: tmp_path / "x.log")

    original, retried = _fake_nar(), _fake_nar()
    seen = {}

    def _fake_write_script(ctx, story, mode, *, hook_hint, all_pages, direction,
                           progress, debug_dump, clarity_fixes=""):
        seen["clarity_fixes"] = clarity_fixes
        return retried if debug_dump.get("transparency_retry") else original

    monkeypatch.setattr(pl, "_write_script", _fake_write_script)

    heavy = "transparency[undefined_character]: scene S2: 'Dawn' appears with no introduction"
    calls = {"n": 0}

    def _fake_critic(nar, ctx, mode, *, progress=None):
        # first (original) draft: 2 flags incl. a heavy one; retried draft: 0 flags
        calls["n"] += 1
        return [heavy, "transparency[overstuffed_sentence]: scene S3: chained"] if nar is original else []

    monkeypatch.setattr(pl, "_transparency_critic", _fake_critic)

    nar = pl.write_script("proj", "recap_summary")
    # retry fired with a NON-EMPTY, actionable fix block…
    assert seen["clarity_fixes"] and "PREVIOUS DRAFT HAD CLARITY ISSUES" in seen["clarity_fixes"]
    # …and the cleaner (0-flag) re-write was kept over the 2-flag original.
    assert nar is retried


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
