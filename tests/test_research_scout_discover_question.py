"""ScoutWorkflow.discover_question — Tier B of the Stage 1 empty-intent fallback.

Tier B used to hand the bare rotated angle ("times a famous power or rule
failed") straight in as the research intent (ui/bridge.py::start_scout_session).
That fed an ANGLE into research_prompts/general_qa.v2.md and general_micro.v1.md,
whose whole premise is ENUMERATING ANSWERS TO A QUESTION — an angle is not a
question, so the run researched the wrong thing (Master 2026-08-28 bug find).

discover_question() fixes this by spending ONE research call to turn the angle
into a real question/moment first, via research_prompts/discover_qa.v1.md and
discover_micro.v1.md (wording copied verbatim from the already-proven inline
prompts in stages/youcom_scout.py's run_discover/run_micro). It must NEVER
raise: an empty intent box has to be able to start a session even when
You.com is down, unauthenticated, or returns nothing usable — every one of
those falls back to returning the angle itself, Tier B's pre-fix behavior.
"""
import pytest

import ui.bridge as bridge
from stages.research_scout.models import ScoutMode
from stages.research_scout.policies import PolicyBundle
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow


class _ScriptedYouCom:
    """A YouComClient stand-in that records the exact call it received and
    returns a scripted candidate list shaped like the real Research API payload
    (output.content.candidates — see stages/research_scout/workflow.py's
    _extract_candidates)."""

    def __init__(self, candidates=None, *, raises=False):
        self.candidates = candidates if candidates is not None else []
        self.raises = raises
        self.calls = 0
        self.seen_prompt = None
        self.seen_schema = None
        self.seen_profile = None
        self.seen_effort = None

    def research(self, prompt, schema, profile, *, effort="standard"):
        self.calls += 1
        if self.raises:
            raise RuntimeError("stub: simulated You.com outage")
        self.seen_prompt = prompt
        self.seen_schema = schema
        self.seen_profile = profile
        self.seen_effort = effort
        payload = {"output": {"content": {"candidates": self.candidates}}}
        return type("RawCall", (), {"api": "research", "payload": payload, "error": None})()


def _workflow(tmp_path, client, digest=""):
    return ScoutWorkflow(store=SessionStore(tmp_path), client=client, digest=digest)


def test_a_discovered_question_is_returned_and_is_not_the_raw_angle(tmp_path):
    angle = PolicyBundle.load(ScoutMode.QA).general_angles["qa"][0]
    client = _ScriptedYouCom(candidates=[
        {"question": "Which villains have exploited Batman's one known weakness?"},
    ])
    workflow = _workflow(tmp_path, client)

    result = workflow.discover_question(ScoutMode.QA)

    assert result == "Which villains have exploited Batman's one known weakness?"
    assert result != angle


def test_the_discover_prompt_actually_contains_the_angle_and_the_digest(tmp_path):
    digest = "- Already produced: some unrelated moment"
    client = _ScriptedYouCom(candidates=[{"question": "A fresh question nobody asked yet"}])
    workflow = _workflow(tmp_path, client, digest=digest)
    angle = workflow.next_angle(ScoutMode.QA)  # same rotation pointer discover_question reads

    workflow.discover_question(ScoutMode.QA)

    assert angle in client.seen_prompt
    assert digest in client.seen_prompt


def test_an_all_burned_result_falls_back_to_the_angle(tmp_path):
    # The digest line is an EXACT copy of the candidate text, guaranteeing is_burned()'s
    # token-overlap check (>=60% containment, >=2 non-format shared tokens) fires.
    burned_text = "Times Superman's invulnerability failed against kryptonite radiation"
    digest = f"- {burned_text}"
    client = _ScriptedYouCom(candidates=[{"question": burned_text}])
    workflow = _workflow(tmp_path, client, digest=digest)
    angle = workflow.next_angle(ScoutMode.QA)

    result = workflow.discover_question(ScoutMode.QA)

    assert result == angle


def test_a_client_exception_falls_back_to_the_angle_and_never_raises(tmp_path):
    client = _ScriptedYouCom(raises=True)
    workflow = _workflow(tmp_path, client)
    angle = workflow.next_angle(ScoutMode.QA)

    result = workflow.discover_question(ScoutMode.QA)  # must not propagate the RuntimeError

    assert result == angle


def test_micro_mode_uses_the_micro_discover_prompt_qa_uses_the_qa_one(tmp_path):
    qa_client = _ScriptedYouCom()
    micro_client = _ScriptedYouCom()
    qa_workflow = _workflow(tmp_path, qa_client)
    micro_workflow = _workflow(tmp_path, micro_client)

    qa_workflow.discover_question(ScoutMode.QA)
    micro_workflow.discover_question(ScoutMode.MICRO)

    # Wording lifted verbatim from run_discover / run_micro (stages/youcom_scout.py) —
    # each mode's distinguishing phrase must land in the prompt actually sent.
    assert "LIST of 3 or more separate moments" in qa_client.seen_prompt
    assert "LIST of 3 or more separate moments" not in micro_client.seen_prompt
    assert "MICRO MOMENT" in micro_client.seen_prompt
    assert "MICRO MOMENT" not in qa_client.seen_prompt


def test_tier_a_still_wins_when_the_bank_has_an_open_question(tmp_path, monkeypatch):
    """Unchanged behaviour: a still-open qa_question_bank.md question must keep
    winning over Tier B, and Tier B's client must never even be touched — bank
    hits are zero-API-cost by design."""
    root = tmp_path / "research_sessions"
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", root)
    bank = tmp_path / "qa_question_bank.md"
    banlist = tmp_path / "qa_question_banlist.md"
    bank.write_text(
        "| Status | Question | Answer items (comic, year) | Notes |\n"
        "|--------|----------|----------------------------|-------|\n"
        "| SAVE-FOR-LATER | A still-open bank question? | item | note |\n",
        encoding="utf-8",
    )
    banlist.write_text(
        "| Date | Question | Reason |\n|------|----------|--------|\n", encoding="utf-8",
    )
    monkeypatch.setattr("stages.research_scout.bank_fallback._REPO_ROOT", tmp_path)

    tripwire_calls = []

    class _Tripwire:
        def research(self, *args, **kwargs):
            tripwire_calls.append(1)
            raise AssertionError("Tier B must not be reached when Tier A has a hit")

    monkeypatch.setattr(
        "stages.research_scout.workflow.YouComClient", lambda *a, **k: _Tripwire()
    )

    session = bridge.start_scout_session("qa", "")

    assert session.user_intent == "A still-open bank question?"
    assert tripwire_calls == []
