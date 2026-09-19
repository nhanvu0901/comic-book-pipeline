"""ScoutWorkflow.discover_questions — Tier B of the Stage 1 empty-intent fallback.

Tier B used to hand the bare rotated angle ("times a famous power or rule
failed") straight in as the research intent (ui/bridge.py::start_scout_session).
That fed an ANGLE into research_prompts/general_qa.v2.md and general_micro.v1.md,
whose whole premise is ENUMERATING ANSWERS TO A QUESTION — an angle is not a
question, so the run researched the wrong thing (Master 2026-08-28 bug find).

Discovery fixes that by spending ONE research call to turn angles into real
questions/moments first, via research_prompts/discover_qa.v2.md and
discover_micro.v2.md. It used to return exactly ONE question and throw the rest
of the batch away; now it returns the whole is_burned-filtered batch so the UI
can offer a CHOICE (and a re-roll) instead of a single take-it-or-leave-it
question.

It must NEVER raise: an empty intent box has to be able to start a session even
when You.com is down, unauthenticated, or returns nothing usable — every one of
those falls back to a single-entry batch holding the angle itself, Tier B's
pre-fix behavior.
"""
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


def _qa_angles():
    return PolicyBundle.load(ScoutMode.QA).general_angles["qa"]


# ─── the whole batch, not just the first survivor ───────────────────────────


def test_every_discovered_question_comes_back_not_just_the_first(tmp_path):
    """The bug this replaces: discover_question() returned the first non-burned
    candidate and discarded the other four, so one research call bought one
    question."""
    asked = [f"Question number {n}?" for n in range(1, 6)]
    client = _ScriptedYouCom(candidates=[{"question": q} for q in asked])
    workflow = _workflow(tmp_path, client)

    batch = workflow.discover_questions(ScoutMode.QA)

    assert [entry["question"] for entry in batch] == asked
    assert client.calls == 1  # a batch costs exactly what one question used to


def test_each_entry_names_the_angle_it_came_from(tmp_path):
    """The chat bubble labels every choice with its angle, so the angle has to
    survive the trip. The model echoes it; when it forgets, the angle is
    recovered from the candidate's position in the requested one-per-angle run."""
    angles = _qa_angles()
    client = _ScriptedYouCom(candidates=[
        {"question": "Echoed angle question?", "angle": angles[3]},
        {"question": "Forgot to echo the angle?"},
    ])
    workflow = _workflow(tmp_path, client)

    batch = workflow.discover_questions(ScoutMode.QA)

    assert batch[0]["angle"] == angles[3]
    assert batch[1]["angle"] == angles[1]  # second slot of the one-per-angle run


def test_count_caps_the_batch(tmp_path):
    client = _ScriptedYouCom(candidates=[{"question": f"Q{n}?"} for n in range(1, 9)])
    workflow = _workflow(tmp_path, client)

    batch = workflow.discover_questions(ScoutMode.QA, count=3)

    assert [entry["question"] for entry in batch] == ["Q1?", "Q2?", "Q3?"]


# ─── filtering: burned lanes and already-shown questions ────────────────────


def test_burned_candidates_are_dropped_and_the_rest_survive(tmp_path):
    # The digest line is an EXACT copy of the candidate text, guaranteeing
    # is_burned()'s token-overlap check (>=60% containment, >=2 non-format
    # shared tokens) fires on that one and only that one.
    burned = "Times Superman's invulnerability failed against kryptonite radiation"
    client = _ScriptedYouCom(candidates=[
        {"question": burned},
        {"question": "Which villains have escaped Arkham through the front door?"},
    ])
    workflow = _workflow(tmp_path, client, digest=f"- {burned}")

    batch = workflow.discover_questions(ScoutMode.QA)

    assert [entry["question"] for entry in batch] == [
        "Which villains have escaped Arkham through the front door?"
    ]


def test_exclude_suppresses_the_batch_the_user_has_already_seen(tmp_path):
    """A re-roll must not hand back the same five questions, so the questions
    already on screen are passed in as `exclude`."""
    already_shown = "Which villains have escaped Arkham through the front door?"
    client = _ScriptedYouCom(candidates=[
        {"question": already_shown},
        {"question": "Who has out-lasted Deadpool in a healing contest?"},
    ])
    workflow = _workflow(tmp_path, client)

    batch = workflow.discover_questions(ScoutMode.QA, exclude=[already_shown])

    assert [entry["question"] for entry in batch] == [
        "Who has out-lasted Deadpool in a healing contest?"
    ]


def test_the_same_question_twice_in_one_batch_is_only_offered_once(tmp_path):
    client = _ScriptedYouCom(candidates=[
        {"question": "Who has lifted Mjolnir?"},
        {"question": "  Who has lifted Mjolnir?  "},
    ])
    workflow = _workflow(tmp_path, client)

    batch = workflow.discover_questions(ScoutMode.QA)

    assert len(batch) == 1


# ─── the never-raises guarantee ─────────────────────────────────────────────


def test_a_client_exception_falls_back_to_the_angle_and_never_raises(tmp_path):
    client = _ScriptedYouCom(raises=True)
    workflow = _workflow(tmp_path, client)
    angle = workflow.next_angle(ScoutMode.QA)

    batch = workflow.discover_questions(ScoutMode.QA)  # must not propagate RuntimeError

    assert [entry["question"] for entry in batch] == [angle]
    # Flagged so the chat can tell "here is a batch" from "we got nothing" and
    # keep the previous batch on screen instead of blanking it.
    assert batch[0]["fallback"] is True


def test_an_all_burned_result_falls_back_to_the_angle(tmp_path):
    burned_text = "Times Superman's invulnerability failed against kryptonite radiation"
    client = _ScriptedYouCom(candidates=[{"question": burned_text}])
    workflow = _workflow(tmp_path, client, digest=f"- {burned_text}")
    angle = workflow.next_angle(ScoutMode.QA)

    batch = workflow.discover_questions(ScoutMode.QA)

    assert [entry["question"] for entry in batch] == [angle]
    assert batch[0]["fallback"] is True


def test_a_real_batch_is_never_flagged_as_a_fallback(tmp_path):
    client = _ScriptedYouCom(candidates=[{"question": "A perfectly good question?"}])
    workflow = _workflow(tmp_path, client)

    batch = workflow.discover_questions(ScoutMode.QA)

    assert not batch[0].get("fallback")


# ─── the prompt and the schema actually sent ────────────────────────────────


def test_the_prompt_carries_every_angle_the_digest_and_the_exclusions(tmp_path):
    """One rotated angle bought one lane per call. Feeding all five angles is
    what makes five DIFFERENT questions out of the same single research call."""
    digest = "- Already produced: some unrelated moment"
    client = _ScriptedYouCom(candidates=[{"question": "A fresh question nobody asked yet"}])
    workflow = _workflow(tmp_path, client, digest=digest)

    workflow.discover_questions(ScoutMode.QA, exclude=["An already-rejected question?"])

    for angle in _qa_angles():
        assert angle in client.seen_prompt
    assert digest in client.seen_prompt
    assert "An already-rejected question?" in client.seen_prompt


def test_the_response_schema_asks_the_model_to_label_each_candidate_with_its_angle(tmp_path):
    client = _ScriptedYouCom(candidates=[{"question": "Anything?"}])
    workflow = _workflow(tmp_path, client)

    workflow.discover_questions(ScoutMode.QA)

    item = client.seen_schema["properties"]["candidates"]["items"]
    assert "angle" in item["properties"]
    # You.com validates OpenAI-strict style: every property must also be required.
    assert "angle" in item["required"]


def test_micro_mode_uses_the_micro_discover_prompt_qa_uses_the_qa_one(tmp_path):
    qa_client = _ScriptedYouCom()
    micro_client = _ScriptedYouCom()
    qa_workflow = _workflow(tmp_path, qa_client)
    micro_workflow = _workflow(tmp_path, micro_client)

    qa_workflow.discover_questions(ScoutMode.QA)
    micro_workflow.discover_questions(ScoutMode.MICRO)

    # Wording lifted from run_discover / run_micro (stages/youcom_scout.py) —
    # each mode's distinguishing phrase must land in the prompt actually sent.
    assert "LIST of 3 or more separate moments" in qa_client.seen_prompt
    assert "LIST of 3 or more separate moments" not in micro_client.seen_prompt
    assert "MICRO MOMENT" in micro_client.seen_prompt
    assert "MICRO MOMENT" not in qa_client.seen_prompt


def test_micro_returns_moments_and_gets_a_batch_too(tmp_path):
    """Micro shares the code path on purpose: five moments, not one."""
    moments = [f"Moment number {n}" for n in range(1, 6)]
    client = _ScriptedYouCom(candidates=[{"moment": m} for m in moments])
    workflow = _workflow(tmp_path, client)

    batch = workflow.discover_questions(ScoutMode.MICRO)

    assert [entry["moment"] for entry in batch] == moments


# ─── the bridge above it ────────────────────────────────────────────────────


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


def test_the_bridge_hands_the_whole_batch_through(tmp_path, monkeypatch):
    """ui/bridge.py::discover_questions is what the Stage 1 chat calls; it must
    pass `exclude` down and return the list untouched."""
    root = tmp_path / "research_sessions"
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", root)
    client = _ScriptedYouCom(candidates=[
        {"question": "First fresh question?"},
        {"question": "Second fresh question?"},
    ])
    monkeypatch.setattr(
        "stages.research_scout.workflow.YouComClient", lambda *a, **k: client
    )

    batch = bridge.discover_questions("qa", count=2, exclude=["Old question?"])

    assert [entry["question"] for entry in batch] == [
        "First fresh question?", "Second fresh question?",
    ]
    assert "Old question?" in client.seen_prompt
