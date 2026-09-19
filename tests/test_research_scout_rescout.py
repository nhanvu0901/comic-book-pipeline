"""Re-scouting a round while keeping the candidates already confirmed.

Two things are tested here, and they are the same defect seen from two sides.
Candidate ids used to be pure list positions (`candidate-{index}`), so a gate
written for `candidate-1` in round 1 was displayed against whatever landed at
position 1 in round 2 — a green verdict on the wrong comic, with no error. That
made a "keep the confirmed one, replace the rest" button unbuildable AND
corrupted the plain feedback re-run that already shipped.
"""

import json

import pytest

from stages.research_scout.models import EvidenceGate, ScoutMode, SessionState
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow


class _FakeYouCom:
    """A You.com stand-in whose candidates carry NO id of their own.

    That is the real shape: `_GENERAL_ITEM_PROPS` has no `id` property, so the
    model is never asked for one and every id on disk is one the workflow
    assigned. Tests that hand the parser pre-identified candidates cannot see
    the positional-id defect at all.
    """

    def __init__(self, rounds=None):
        self.rounds = [list(titles) for titles in rounds] if rounds else None
        self.research_calls = 0
        self.search_response = {"results": {"web": [{"url": "https://example.test/a"}]}}

    def _titles(self):
        if self.rounds:
            return self.rounds.pop(0)
        return ["Alpha", "Beta", "Gamma"]

    def research(self, prompt, schema, profile, *, effort="standard"):
        self.seen_prompt = prompt
        self.research_calls += 1
        candidates = [
            {
                "title": title,
                "summary": f"{title} visibly happens.",
                "series_issue_year": f"{title} #1 (2001)",
                "what_visibly_happens": f"{title} lands a punch.",
                "evidence_urls": [f"https://example.test/{title.lower()}"],
                # Tier B reads this field; the general round ignores it. One fake
                # serves both callers of _extract_candidates.
                "question": f"What did {title} do?",
            }
            for title in self._titles()
        ]
        payload = {"output": {"content": {"candidates": candidates}}}
        return type("RawCall", (), {"api": "research", "payload": payload, "error": None})()

    def search(self, query, profile):
        return type(
            "RawCall", (), {"api": "search", "payload": self.search_response, "error": None}
        )()


@pytest.fixture
def workflow(tmp_path):
    # config.py load_dotenv()s real API keys, so an uninjected planner would hit
    # OpenRouter for real — the stub keeps every test on the fallback path.
    return ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom(),
        planner=lambda *a, **k: None,
    )


def _candidate_ids(workflow, session_id, artifact="general/candidates.v1.json"):
    path = workflow.store.artifact_path(session_id, artifact)
    data = json.loads(path.read_text(encoding="utf-8"))
    return [candidate["id"] for candidate in data["candidates"]]


# ─── 1. Candidate identity is namespaced by revision ────────────────────────

def test_round_two_ids_never_collide_with_round_one(workflow):
    """`candidate-1` used to mean nothing more than "first in this round's
    list", so round 2 re-used every id round 1 had issued."""
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    round_one = _candidate_ids(workflow, session.id)

    workflow.rerun_general(session.id)
    workflow.run_general(session.id)
    round_two = _candidate_ids(workflow, session.id)

    assert round_one == ["candidate-1", "candidate-2", "candidate-3"]
    assert round_two == ["r2-candidate-1", "r2-candidate-2", "r2-candidate-3"]
    assert not set(round_one) & set(round_two)


def test_the_tier_b_discover_batch_stays_out_of_the_revision_namespace(workflow):
    """`_extract_candidates` parses the Tier B discover batch as well.

    The revision namespace belongs to the general-research round: discovery
    has no session and no revision at all, and its entries are keyed by their
    question/moment text, so threading a prefix through every caller would
    rename ids for a path that gains nothing from it.
    """
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"]])
    batch = workflow.discover_questions(ScoutMode.QA, count=2)

    assert [entry["id"] for entry in batch] == ["candidate-1", "candidate-2"]


# ─── 5. The live defect: a plain re-run must let go of the old round ────────

def _gates_on_disk(workflow, session_id):
    path = workflow.store.artifact_path(session_id, "specific/evidence_gate.v1.json")
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["gates"]


def test_a_plain_rerun_drops_the_selection_and_the_gates(monkeypatch, workflow):
    """`rerun_general` bumped the revision and returned to GENERAL_DRAFT while
    leaving `selected_specific_candidate_ids` and `specific/evidence_gate.v1.json`
    untouched, so a confirmed gate from round 1 reattached itself to round 2.

    Namespaced ids alone would only downgrade that from a wrong reference to a
    dangling one. Letting go is what makes it correct: a plain re-run keeps
    nothing, which is precisely what distinguishes it from the new action.
    """
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="confirmed", reason="Backed."),
    )
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-1", "candidate-2", "candidate-3"])
    assert _gates_on_disk(workflow, session.id)

    reran = workflow.rerun_general(session.id, "different villains please")

    assert reran.selected_specific_candidate_ids == []
    assert _gates_on_disk(workflow, session.id) == []


# ─── 3. run_general honours the carry-over ──────────────────────────────────

def _hand_off_to_a_new_round(workflow, session_id, kept_ids):
    """Put a session where `rescout_keeping_confirmed` would leave it, without
    depending on that action — this is run_general's half of the contract."""
    session = workflow.store.load(session_id)
    session.kept_candidate_ids = list(kept_ids)
    session.selected_specific_candidate_ids = list(kept_ids)
    session.revision += 1
    session.state = SessionState.GENERAL_DRAFT
    return workflow.store.save(session)


def test_a_carried_over_candidate_keeps_its_id_and_leads_the_new_round(workflow):
    """Rewriting the kept candidate's id into the new namespace would orphan the
    gate that was already paid for, so it keeps the id its gate is keyed by and
    is simply prepended to the round that replaces the rest."""
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"], ["Delta", "Echo"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    _hand_off_to_a_new_round(workflow, session.id, ["candidate-1"])

    workflow.run_general(session.id)

    path = workflow.store.artifact_path(session.id, "general/candidates.v1.json")
    candidates = json.loads(path.read_text(encoding="utf-8"))["candidates"]
    assert [c["id"] for c in candidates] == [
        "candidate-1", "r2-candidate-1", "r2-candidate-2",
    ]
    # The kept entry is the round-1 candidate itself, not a same-id stand-in.
    assert candidates[0]["title"] == "Alpha"
    assert candidates[1]["title"] == "Delta"


def test_the_carry_over_is_spent_once_and_not_again_next_round(workflow):
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"], ["Delta"], ["Golf"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    _hand_off_to_a_new_round(workflow, session.id, ["candidate-1"])
    after = workflow.run_general(session.id)

    assert after.kept_candidate_ids == []

    workflow.rerun_general(session.id)
    workflow.run_general(session.id)

    assert _candidate_ids(workflow, session.id) == ["r3-candidate-1"]


def test_the_revision_archive_records_the_merged_list_the_user_saw(workflow):
    """`candidates.rev{N}.v1.json` is what the chat replays for a superseded
    round, so it has to hold the list that was actually on screen — the kept
    candidate included, not just the fresh half."""
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"], ["Delta"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    _hand_off_to_a_new_round(workflow, session.id, ["candidate-2"])
    workflow.run_general(session.id)

    assert _candidate_ids(
        workflow, session.id, "general/candidates.rev2.v1.json"
    ) == ["candidate-2", "r2-candidate-1"]


def test_a_carry_over_id_that_no_longer_exists_is_simply_dropped(workflow):
    """A hand-edited or truncated artifact must not take the round down with it."""
    workflow.client = _FakeYouCom(rounds=[["Alpha"], ["Delta"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    _hand_off_to_a_new_round(workflow, session.id, ["candidate-1", "ghost-99"])

    workflow.run_general(session.id)

    assert _candidate_ids(workflow, session.id) == ["candidate-1", "r2-candidate-1"]
