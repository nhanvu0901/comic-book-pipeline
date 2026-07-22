"""STORY-FIRST recap writer (2026-07-17 port from micro_moment).

The recap writer's source of truth is the STORY, not the VLM panel art. Verifies the
`write_scenes` user prompt:
  (a) carries NO VLM panel description / page-summary prose (the VLM-tilt source);
  (b) carries the STORY sources — story_meaning + notable_moments;
  (c) STILL carries verbatim dialog (OCR speech is story material the writer may quote);
  (d) degrades softly on an OLD project with neither story_meaning nor notable_moments
      (additive Stage-1 fields) — no crash, no story-meaning header, plot still there.

No network: the LLM writer call is monkeypatched to capture the prompt.
"""
import json

import stages.stage_3.write_script as ws
from stages.stage_3.schema import Beat, Glossary


_DESC = "NEON_ROBOT_SMASHES_GLASS_desc"       # a VLM panel description → must NOT leak
_PSUM = "PAGE_SUMMARY_visual_prose_marker"     # a VLM page summary → must NOT leak
_MEANING = "THEME_no_one_escapes_their_past_marker"
_MOMENT = "MOMENT_hero_burns_the_letter_marker"
_OCR = "OCR_I_NEVER_ASKED_FOR_THIS_marker"


def _page(pn):
    return {"page_number": pn, "page_summary": _PSUM, "is_story_page": True,
            "image_dimensions": {"width": 1000, "height": 1500},
            "panels": [{"index": 0, "bbox": {"x": 0, "y": 0, "w": 900, "h": 900},
                        "description": f"{_DESC} {pn}",
                        "dialog": [{"speaker": "Frank", "ocr": f"{_OCR} {pn}", "text": "x"}]}]}


def _beat(bid, page):
    return Beat(id=bid, function="SETUP", name=f"beat {bid}", summary=f"summary {bid}",
                page_refs=[page], key_panels=[], cause="", characters_active=["Frank"])


def _run_and_capture(ctx):
    captured = {}

    def _fake(*, system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return json.dumps({"scenes": [{"text": "x", "connective": None, "beat_id": 1},
                                       {"text": "y", "connective": None, "beat_id": 2}]}), "m"

    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(ws, "call_with_chain", _fake)
    try:
        ws.write_scenes([_beat(1, 3), _beat(2, 4)], Glossary(characters={}), ctx,
                        [_page(3), _page(4)], "recap_summary")
    finally:
        mp.undo()
    return captured


def test_recap_writer_prompt_is_story_first():
    ctx = {"title": "Frank vs the Giant",
           "plot_summary": "Frank Castle hunts a giant through the docks and ends it.",
           "story_meaning": _MEANING, "notable_moments": [_MOMENT]}
    cap = _run_and_capture(ctx)
    user, system = cap["user"], cap["system"]

    # (a) NO VLM visual prose reaches the writer
    assert _DESC not in user, "VLM panel description leaked into the writer prompt"
    assert _PSUM not in user, "VLM page summary leaked into the writer prompt"
    assert "PAGE DETAIL" not in user

    # (b) STORY sources present
    assert _MEANING in user and "STORY MEANING" in user
    assert _MOMENT in user and "KEY STORY MOMENTS" in user

    # (c) verbatim dialog kept (story material the writer may quote)
    assert _OCR in user and "VERBATIM DIALOG" in user

    # the system prompt now roots fidelity in the STORY, not the panel art
    assert "STORY FIDELITY" in system and "PANEL FIDELITY" not in system


def test_recap_writer_degrades_without_new_fields():
    """OLD project: no story_meaning / notable_moments → still runs, plot still there."""
    ctx = {"title": "Old Comic",
           "plot_summary": "PLOT_ONLY_grounding_marker for a legacy project."}
    cap = _run_and_capture(ctx)
    user = cap["user"]
    assert "STORY MEANING" not in user and "KEY STORY MOMENTS" not in user
    assert "PLOT_ONLY_grounding_marker" in user     # plot_summary still grounds the writer
    assert _DESC not in user                          # and still no VLM visual prose


if __name__ == "__main__":
    test_recap_writer_prompt_is_story_first()
    test_recap_writer_degrades_without_new_fields()
    print("ok")
