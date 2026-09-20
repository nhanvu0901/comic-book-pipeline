"""The evidence gate must be handed evidence it can actually check.

Two defects, one symptom (session f56a9bc0: every candidate `inconclusive`):
the verification search was `json.dumps(candidate)` truncated mid-brace at 360
characters, and the URLs a candidate cited were never fetched at all — so the
gate was told to verify sources nobody had ever supplied it.
"""

import json

import pytest

import config
from stages.research_scout import cited_sources
from stages.research_scout.models import EvidenceGate, ScoutMode
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow


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
    "claim_citation": {
        "url": "https://www.cbr.com/black-panther-deadpool-solve-death/",
        "quote": "Shuri's scan shows necrotic cells multiplying.",
    },
}


class _FakeYouCom:
    """Both rounds are research calls now, so they are told apart by schema:
    only the verification round asks for a `verdict` per item."""

    def __init__(self, candidates=None):
        self.candidates = candidates if candidates is not None else [dict(_CANDIDATE)]
        self.verify_prompts: list[str] = []
        self.verify_efforts: list[str] = []
        self.verify_response = {
            "candidates": [{
                "verdict": "CONFIRMED",
                "verbatim_sentence": "Shuri's scan shows necrotic cells multiplying.",
                "source_url": "https://example.test/a",
                "second_source_url": "",
                "volume_and_year": "Black Panther vs. Deadpool (2018)",
                "comic_or_adaptation": "comic",
                "single_issue_or_multi": "single",
                "subject_main_in_issue": "main",
                "reprint_check": "not a reprint",
                "issue_year_matches_sources": "yes",
            }],
            "notes": "",
        }

    @staticmethod
    def _is_verification(schema):
        props = schema["properties"]["candidates"]["items"]["properties"]
        return "verdict" in props

    def research(self, prompt, schema, profile, *, effort="standard"):
        if self._is_verification(schema):
            self.verify_prompts.append(prompt)
            self.verify_efforts.append(effort)
            self.seen_profile = profile
            return type("RawCall", (), {
                "api": "research", "payload": self.verify_response, "error": None,
            })()
        payload = {
            "output": {
                "content": {"candidates": self.candidates},
                "sources": [
                    {"url": url}
                    for candidate in self.candidates
                    for url in candidate.get("evidence_urls", [])
                ],
            }
        }
        return type("RawCall", (), {"api": "research", "payload": payload, "error": None})()


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


# ─── 1. The verification round asks about one candidate ─────────────────────

def test_the_verification_round_sends_the_candidate_not_a_keyword_query(monkeypatch, workflow):
    """This used to be a keyword search truncated at 45 words, so the fields
    that identify the comic had to be crammed in first or the API got a bag of
    punctuation. A research round takes the whole candidate in a prompt."""
    _gate(monkeypatch, workflow)
    prompt = workflow.client.verify_prompts[0]

    assert "Black Panther vs. Deadpool" in prompt
    assert "#2" in prompt
    assert "2018" in prompt
    # One candidate, one verdict — never a second enumeration round. Both modes
    # open the same way; the fixture happens to run Micro.
    assert "Verify ONE proposed" in prompt
    assert "NOT CONFIRMED" in prompt


def test_the_verification_round_runs_deep(monkeypatch, workflow):
    """Confirming one item is the phase that earns the expensive pass — a
    standard sweep is what returned forum chatter for a claim about an issue."""
    _gate(monkeypatch, workflow)

    assert workflow.client.verify_efforts == [config.YOUCOM_VERIFY_EFFORT]
    assert config.YOUCOM_VERIFY_EFFORT == "deep"


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


def test_the_verification_payload_is_there_under_its_own_heading(monkeypatch, workflow):
    _stub_fetcher(monkeypatch, {
        u: "Shuri's scan shows necrotic cells multiplying. page text"
        for u in _CANDIDATE["evidence_urls"]
    })
    seen = _gate(monkeypatch, workflow)

    prompt = seen["prompt"]
    payload = json.dumps(workflow.client.verify_response, ensure_ascii=False)
    assert payload in prompt
    # It sits under VERIFICATION RESEARCH, after the fetched citations — not in
    # one undifferentiated blob, and not mislabelled as a plain web search.
    assert prompt.index("[1] ") < prompt.index("VERIFICATION RESEARCH\n  " + payload)
    # openrouter_gate still gets the payload itself, untouched.
    assert seen["raw_search_payload"] == workflow.client.verify_response


def test_a_source_we_could_not_open_is_recorded_not_raised(monkeypatch, workflow):
    """The gate must be able to tell "we read it and it does not say that" from
    "we never managed to look" — collapsing them back into one `inconclusive`
    is exactly the defect."""
    _stub_fetcher(
        monkeypatch,
        {_CANDIDATE["evidence_urls"][0]: "Shuri's scan shows necrotic cells multiplying."},
    )
    seen = _gate(monkeypatch, workflow)

    prompt = seen["prompt"]
    assert f"[1] {_CANDIDATE['evidence_urls'][0]} — fetched," in prompt
    assert f"[2] {_CANDIDATE['evidence_urls'][1]} — COULD NOT FETCH (timeout)" in prompt
    assert "COULD NOT FETCH" not in prompt.split("[1] ")[1].split("[2] ")[0]


def test_new_bound_citation_that_cannot_be_fetched_is_inconclusive_without_model_call(
    monkeypatch, workflow
):
    _stub_fetcher(monkeypatch, {})
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: pytest.fail("an unfetched bound source must not reach the model"),
    )
    session = workflow.start(ScoutMode.MICRO, "Deadpool healing factor")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-1"])

    written = json.loads(
        workflow.store.artifact_path(
            session.id, "specific/evidence_gate.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert [gate["verdict"] for gate in written["gates"]] == ["inconclusive"]
    assert "could not be retrieved" in written["gates"][0]["reason"]


def test_at_most_three_cited_urls_are_fetched_for_one_candidate(monkeypatch, tmp_path):
    greedy = dict(
        _CANDIDATE,
        evidence_urls=[f"https://cbr.com/{i}" for i in range(9)],
        claim_citation={"url": "https://cbr.com/0", "quote": "Bound quote."},
    )
    flow = ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom([greedy]),
        planner=lambda *a, **k: None,
    )
    asked = _stub_fetcher(monkeypatch, {
        f"https://cbr.com/{i}": "Bound quote." for i in range(9)
    })
    _gate(monkeypatch, flow)

    assert asked == ["https://cbr.com/0", "https://cbr.com/1", "https://cbr.com/2"]


def test_legacy_candidate_without_citations_keeps_the_research_only_gate_path(
    monkeypatch, tmp_path
):
    flow = ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom(),
        planner=lambda *a, **k: None,
    )
    monkeypatch.setattr(
        cited_sources,
        "fetch_source",
        lambda url, **kw: pytest.fail("nothing was cited, so nothing may be fetched"),
    )
    seen = {}
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: seen.update(kwargs) or EvidenceGate(verdict="confirmed"),
    )
    flow._gate_one(flow._bundle(ScoutMode.MICRO), "", "", {"title": "legacy"})

    assert "cited no URLs" in seen["prompt"]
    assert json.dumps(flow.client.verify_response, ensure_ascii=False) in seen["prompt"]


def test_new_bound_citation_with_unmatched_quote_is_inconclusive_without_model_call(
    monkeypatch, tmp_path
):
    candidate = dict(_CANDIDATE, claim_citation={
        "url": _CANDIDATE["evidence_urls"][0],
        "quote": "This sentence is fabricated.",
    })
    flow = ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom([candidate]),
        planner=lambda *a, **k: None,
    )
    _stub_fetcher(monkeypatch, {candidate["claim_citation"]["url"]: "A real fetched page."})
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: pytest.fail("an unmatched quote must not reach the model"),
    )

    session = flow.start(ScoutMode.MICRO, "Deadpool healing factor")
    flow.run_general(session.id)
    flow.verify_selected(session.id, ["candidate-1"])

    gates = json.loads(
        flow.store.artifact_path(session.id, "specific/evidence_gate.v1.json").read_text()
    )["gates"]
    assert gates[0]["verdict"] == "inconclusive"
    assert "does not occur" in gates[0]["reason"]


def test_matched_bound_quote_still_reaches_the_model_and_preserves_rejection(
    monkeypatch, tmp_path
):
    flow = ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom(),
        planner=lambda *a, **k: None,
    )
    _stub_fetcher(monkeypatch, {
        _CANDIDATE["claim_citation"]["url"]: (
            "Shuri's scan shows necrotic cells multiplying."
        ),
    })
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="rejected", reason="The issue is wrong."),
    )

    session = flow.start(ScoutMode.MICRO, "Deadpool healing factor")
    flow.run_general(session.id)
    flow.verify_selected(session.id, ["candidate-1"])

    gate = json.loads(
        flow.store.artifact_path(session.id, "specific/evidence_gate.v1.json").read_text()
    )["gates"][0]
    assert gate["verdict"] == "rejected"
    assert gate["reason"] == "The issue is wrong."
