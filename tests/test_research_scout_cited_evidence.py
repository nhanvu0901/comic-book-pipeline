"""The evidence gate must be handed evidence it can actually check.

Two defects, one symptom (session f56a9bc0: every candidate `inconclusive`):
the verification search was `json.dumps(candidate)` truncated mid-brace at 360
characters, and the URLs a candidate cited were never fetched at all — so the
gate was told to verify sources nobody had ever supplied it.
"""

import json

import pytest

from stages.research_scout import cited_sources
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


@pytest.fixture(autouse=True)
def _no_reader_network(monkeypatch):
    """No test here opens r.jina.ai. Tests that care about the fetch replace
    this with their own stub; the rest get COULD NOT FETCH, which is a real
    outcome rather than a hang."""
    monkeypatch.setattr(
        cited_sources.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network in tests")),
    )
    yield


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


# ─── 2. The cited sources reach the gate ────────────────────────────────────

def _stub_fetcher(monkeypatch, pages):
    """Replace the one network call with a dict of url -> text (or an error)."""
    asked: list[str] = []

    def _fetch(url, **kwargs):
        asked.append(url)
        value = pages.get(url)
        if isinstance(value, str):
            return cited_sources.FetchedSource(url=url, text=value)
        return cited_sources.FetchedSource(url=url, error=value or "timeout")

    monkeypatch.setattr(cited_sources, "fetch_source", _fetch)
    return asked


def test_the_text_of_a_cited_page_reaches_the_gate_labelled_with_its_url(
    monkeypatch, workflow
):
    asked = _stub_fetcher(
        monkeypatch,
        {
            _CANDIDATE["evidence_urls"][0]: "Shuri's scan shows necrotic cells multiplying.",
            _CANDIDATE["evidence_urls"][1]: "The dying factor is named in issue 2.",
        },
    )
    seen = _gate(monkeypatch, workflow)

    assert asked == _CANDIDATE["evidence_urls"]
    prompt = seen["prompt"]
    assert "CITED SOURCES (fetched from the candidate's own citations)" in prompt
    assert f"[1] {_CANDIDATE['evidence_urls'][0]}" in prompt
    assert "Shuri's scan shows necrotic cells multiplying." in prompt
    assert f"[2] {_CANDIDATE['evidence_urls'][1]}" in prompt
    assert "The dying factor is named in issue 2." in prompt


def test_the_search_payload_is_still_there_under_its_own_heading(monkeypatch, workflow):
    _stub_fetcher(monkeypatch, {u: "page text" for u in _CANDIDATE["evidence_urls"]})
    seen = _gate(monkeypatch, workflow)

    prompt = seen["prompt"]
    payload = json.dumps(workflow.client.search_response, ensure_ascii=False)
    assert payload in prompt
    # The payload sits under SEARCH RESULTS, after the fetched citations — not
    # in one undifferentiated blob the way v1 handed it over.
    assert prompt.index("[1] ") < prompt.index("SEARCH RESULTS\n  " + payload)
    # openrouter_gate still gets the payload itself, untouched.
    assert seen["raw_search_payload"] == workflow.client.search_response


def test_a_source_we_could_not_open_is_recorded_not_raised(monkeypatch, workflow):
    """The gate must be able to tell "we read it and it does not say that" from
    "we never managed to look" — collapsing them back into one `inconclusive`
    is exactly the defect."""
    _stub_fetcher(
        monkeypatch,
        {_CANDIDATE["evidence_urls"][0]: "Shuri's scan shows necrotic cells."},
    )
    seen = _gate(monkeypatch, workflow)

    prompt = seen["prompt"]
    assert f"[1] {_CANDIDATE['evidence_urls'][0]} — fetched," in prompt
    assert f"[2] {_CANDIDATE['evidence_urls'][1]} — COULD NOT FETCH (timeout)" in prompt
    assert "COULD NOT FETCH" not in prompt.split("[1] ")[1].split("[2] ")[0]


def test_every_fetch_failing_still_leaves_the_candidate_with_a_verdict(
    monkeypatch, workflow
):
    """The reader proxy being down must cost a candidate its citations, not its
    verdict: verify_selected reads a raised exception as "this branch failed"."""
    monkeypatch.setattr(
        cited_sources.urllib.request,
        "urlopen",
        lambda request, timeout=None: (_ for _ in ()).throw(
            RuntimeError("the reader proxy fell over")
        ),
    )
    seen = {}

    def _review(**kwargs):
        seen.update(kwargs)
        return EvidenceGate(verdict="confirmed", reason="ok")

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _review)
    session = workflow.start(ScoutMode.MICRO, "Deadpool healing factor")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-1"])

    assert seen["prompt"].count("— COULD NOT FETCH (") == 2
    written = json.loads(
        workflow.store.artifact_path(
            session.id, "specific/evidence_gate.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert [gate["verdict"] for gate in written["gates"]] == ["confirmed"]


def test_at_most_three_cited_urls_are_fetched_for_one_candidate(monkeypatch, tmp_path):
    greedy = dict(_CANDIDATE, evidence_urls=[f"https://cbr.com/{i}" for i in range(9)])
    flow = ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom([greedy]),
        planner=lambda *a, **k: None,
    )
    asked = _stub_fetcher(monkeypatch, {f"https://cbr.com/{i}": "text" for i in range(9)})
    _gate(monkeypatch, flow)

    assert asked == ["https://cbr.com/0", "https://cbr.com/1", "https://cbr.com/2"]


def test_a_candidate_with_no_citations_still_gates_on_the_search_alone(
    monkeypatch, tmp_path
):
    uncited = {k: v for k, v in _CANDIDATE.items() if k != "evidence_urls"}
    flow = ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom([uncited]),
        planner=lambda *a, **k: None,
    )
    monkeypatch.setattr(
        cited_sources,
        "fetch_source",
        lambda url, **kw: pytest.fail("nothing was cited, so nothing may be fetched"),
    )
    seen = _gate(monkeypatch, flow)

    assert "cited no URLs" in seen["prompt"]
    assert json.dumps(flow.client.search_response, ensure_ascii=False) in seen["prompt"]
