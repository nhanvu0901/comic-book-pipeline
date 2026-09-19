"""The evidence gate must be handed evidence it can actually check.

Two defects, one symptom (session f56a9bc0: every candidate `inconclusive`):
the verification search was `json.dumps(candidate)` truncated mid-brace at 360
characters, and the URLs a candidate cited were never fetched at all — so the
gate was told to verify sources nobody had ever supplied it.
"""

import pytest

from stages.research_scout.models import EvidenceGate, ScoutMode
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow
from stages.research_scout.youcom import compact_search_query


_CANDIDATE = {
    "title": "Wade's healing factor is regenerating dead tissue",
    "summary": "Wade's body is revealed to be regenerating dead tissue, not "
    "healthy tissue. The \"dying factor\" is named on panel.",
    "character_or_thing": "Deadpool",
    "series_issue_year": "Black Panther vs. Deadpool #2 (2018)",
    "what_visibly_happens": "Shuri's scan shows necrotic cells multiplying.",
    "evidence_urls": [
        "https://www.cbr.com/black-panther-deadpool-solve-death/",
        "https://www.cbr.com/deadpools-healing-factor-redefined/",
    ],
}


class _FakeYouCom:
    """Records every verification query; returns one fixed search payload."""

    def __init__(self, candidates=None):
        self.candidates = candidates if candidates is not None else [dict(_CANDIDATE)]
        self.queries: list[str] = []
        self.search_response = {"results": {"web": [{"url": "https://example.test/a"}]}}

    def research(self, prompt, schema, profile, *, effort="standard"):
        payload = {"output": {"content": {"candidates": self.candidates}}}
        return type("RawCall", (), {"api": "research", "payload": payload, "error": None})()

    def search(self, query, profile):
        self.queries.append(query)
        self.seen_profile = profile
        return type(
            "RawCall", (), {"api": "search", "payload": self.search_response, "error": None}
        )()


@pytest.fixture
def workflow(tmp_path):
    return ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom(),
        planner=lambda *a, **k: None,
    )


def _gate(monkeypatch, workflow, mode=ScoutMode.MICRO, intent="Deadpool healing factor"):
    """Run one candidate all the way through the gate and hand back the prompt."""
    seen = {}

    def _review(**kwargs):
        seen.update(kwargs)
        return EvidenceGate(verdict="confirmed")

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _review)
    session = workflow.start(mode, intent)
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-1"])
    return seen


# ─── 1. The query is words, not a JSON dump ─────────────────────────────────

def test_verification_query_is_plain_words_not_a_json_dump(monkeypatch, workflow):
    _gate(monkeypatch, workflow)
    query = workflow.client.queries[0]

    assert "{" not in query and "}" not in query
    assert '"' not in query
    assert "evidence_urls" not in query
    assert "https://" not in query


def test_verification_query_carries_the_series_issue_and_year(monkeypatch, workflow):
    _gate(monkeypatch, workflow)
    query = workflow.client.queries[0]

    assert "Black Panther vs. Deadpool" in query
    assert "#2" in query
    assert "2018" in query
    assert "Deadpool" in query


def test_the_identifying_detail_survives_the_45_word_truncation(monkeypatch, tmp_path):
    """compact_search_query keeps the first 45 words. Anything long in front of
    the issue number is the whole defect, so the issue number goes first."""
    long_candidate = dict(
        _CANDIDATE,
        summary="filler " * 200,
        what_visibly_happens="padding " * 200,
        title="a very long title " * 20,
    )
    flow = ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom([long_candidate]),
        planner=lambda *a, **k: None,
    )
    _gate(monkeypatch, flow)

    sent = compact_search_query(flow.client.queries[0])
    assert "Black Panther vs. Deadpool #2 (2018)" in sent
    assert "Deadpool" in sent
    assert len(sent.split()) <= 45
    assert len(sent) <= 360
