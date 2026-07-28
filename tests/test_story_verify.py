"""Unit tests for the STORY_VERIFY critic (no network — call_with_chain is mocked)."""
import json

import pytest

from stages.stage_3 import story_verify as sv
from stages.stage_3.schema import Narration, Scene


def _page(pn, ch, dialog, desc="a panel"):
    return {
        "page_number": pn,
        "source_image": f"/x/ch{ch:02d}_page_{pn:02d}.jpg",
        "panels": [{"description": desc, "dialog": [{"text": d, "speaker": "X"} for d in dialog]}],
    }


def _narration(text):
    return Narration(
        mode="explore_answer", title="Q?", hook="h",
        scenes=[
            Scene(scene_id=1, text="hook", page_ref=1, panel_ref=-1, is_intro=True),
            Scene(scene_id=2, text=text, page_ref=2, panel_ref=-1, visual_beats=[text]),
            Scene(scene_id=3, text="outro", page_ref=2, panel_ref=-1, is_outro=True),
        ],
    )


@pytest.fixture(autouse=True)
def _pages(monkeypatch):
    """One chapter, page 2 with dialogue — every test scene anchors to page_ref=2."""
    monkeypatch.setattr(sv, "_load_preprocessed",
                        lambda project: [_page(2, 1, ["their minds will switch bodies"])])
    monkeypatch.setenv("STORY_VERIFY", "1")


def _scripted(monkeypatch, responses):
    """Mock call_with_chain: return the next JSON per `label`. `responses` maps
    label -> list of dict payloads consumed in order. Records call labels."""
    calls = []

    def fake(*, system, user, models=None, max_tokens=2000, progress=None, label="llm", validator=None):
        calls.append(label)
        queue = responses.get(label) or []
        payload = queue.pop(0) if queue else {}
        return json.dumps(payload), "mock-model"

    monkeypatch.setattr(sv, "call_with_chain", fake)
    return calls


def test_contradicted_triggers_rewrite_and_issue(monkeypatch):
    # verify -> contradicted; rewrite returns new text; re-verify -> STILL contradicted
    # => original kept + issue emitted, and the rewrite path WAS invoked.
    calls = _scripted(monkeypatch, {
        "story_verify": [
            {"claims": [{"claim": "an accident swaps their minds", "verdict": "CONTRADICTED",
                         "evidence": "minds will switch bodies (deliberate)"}]},
            {"claims": [{"claim": "still wrong", "verdict": "CONTRADICTED", "evidence": "x"}]},
        ],
        "story_rewrite": [{"text": "A deliberate trick swaps their minds.",
                           "visual_beats": ["A deliberate trick swaps their minds."]}],
    })
    nar = _narration("An accident swaps their minds.")
    issues = sv.run_story_verify(nar, "proj")

    assert len(issues) == 1 and "scene 2 contradicted" in issues[0]
    assert "story_rewrite" in calls  # retry path exercised
    assert nar.scenes[1].text == "An accident swaps their minds."  # original kept (rewrite failed re-verify)


def test_rewrite_resolves_contradiction(monkeypatch):
    # verify -> contradicted; rewrite; re-verify -> clean => scene mutated, no issue.
    _scripted(monkeypatch, {
        "story_verify": [
            {"claims": [{"claim": "wrong", "verdict": "CONTRADICTED", "evidence": "x"}]},
            {"claims": [{"claim": "right", "verdict": "SUPPORTED", "evidence": "y"}]},
        ],
        "story_rewrite": [{"text": "A deliberate trick swaps their minds.",
                           "visual_beats": ["A deliberate trick", "swaps their minds."]}],
    })
    nar = _narration("An accident swaps their minds.")
    issues = sv.run_story_verify(nar, "proj")

    assert issues == []
    assert nar.scenes[1].text == "A deliberate trick swaps their minds."
    assert nar.scenes[1].visual_beats == ["A deliberate trick", "swaps their minds."]


def test_supported_no_issue_no_rewrite(monkeypatch):
    calls = _scripted(monkeypatch, {
        "story_verify": [{"claims": [{"claim": "ok", "verdict": "SUPPORTED", "evidence": "z"}]}],
    })
    nar = _narration("Doom saves the day.")
    issues = sv.run_story_verify(nar, "proj")

    assert issues == []
    assert "story_rewrite" not in calls
    assert nar.scenes[1].text == "Doom saves the day."


def test_broken_json_degrades_to_not_found(monkeypatch):
    # Model returns garbage (no parseable claims) -> degrade to NOT_FOUND, no crash, no issue.
    def fake(*, system, user, models=None, max_tokens=2000, progress=None, label="llm", validator=None):
        return "totally not json {{{", "mock-model"
    monkeypatch.setattr(sv, "call_with_chain", fake)

    nar = _narration("Some claim.")
    issues = sv.run_story_verify(nar, "proj")

    assert issues == []  # NOT_FOUND never blocks


def test_evidence_cap_and_dialog_first(monkeypatch):
    monkeypatch.setenv("STORY_VERIFY_MAX_CHARS", "600")
    pages = [_page(2, 1, ["HELLO THERE " * 40], desc="ZZZDESC " * 400)]
    ev = sv.gather_evidence("proj", pages)

    assert len(ev) <= 600
    assert "DIALOGUE" in ev  # dialogue section leads; description truncated off the tail
    assert "ZZZDESC" not in ev  # long dialogue front-loaded, description content falls off the cap


def test_knob_off_skips(monkeypatch):
    monkeypatch.setenv("STORY_VERIFY", "0")
    called = {"n": 0}

    def fake(**kw):
        called["n"] += 1
        return "{}", "m"
    monkeypatch.setattr(sv, "call_with_chain", fake)

    nar = _narration("Anything.")
    assert sv.run_story_verify(nar, "proj") == []
    assert called["n"] == 0  # no LLM calls when the knob is off
