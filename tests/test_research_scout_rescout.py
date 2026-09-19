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
