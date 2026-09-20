"""Integration: the real workflow's own artifacts must satisfy project_factory.

Every other scout test hands project_factory a gate collection it builds by
hand, so nothing ever checked that production code writes a shape the factory
accepts.  That gap is why QA mode shipped unable to create a project at all.
"""
import json

import pytest

import stages.research_scout.project_factory as factory
from stages.research_scout import cited_sources
from stages.research_scout.models import EvidenceGate, ScoutMode
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow


class _FakeYouCom:
    """Enough You.com for a full QA round: five gateable candidates, one search."""

    def __init__(self):
        self.general_response = {
            "output": {
                "content": {
                    "candidates": [
                        {
                            "id": f"candidate-{i}",
                            "title": f"Hero {i}",
                            "summary": f"Hero {i} does the thing.",
                            "series_issue_year": f"Thor #{i} (2024)",
                            "what_visibly_happens": f"Hero {i} visibly does the thing.",
                            "evidence_urls": [f"https://source.test/{i}"],
                            "claim_citation": {
                                "url": f"https://source.test/{i}",
                                "quote": "Evidence sentence.",
                            },
                        }
                        for i in range(1, 6)
                    ]
                },
                "sources": [{"url": f"https://source.test/{i}"} for i in range(1, 6)],
            }
        }
        self.searches = []

    def research(self, prompt, schema, profile, *, effort="standard"):
        return type("RawCall", (), {
            "api": "research", "payload": self.general_response, "error": None,
        })()

    def search(self, query, profile):
        self.searches.append(query)
        return type("RawCall", (), {
            "api": "search",
            "payload": {"results": {"web": [{"url": "https://source.test/1"}]}},
            "error": None,
        })()

@pytest.fixture(autouse=True)
def _no_reader_network(monkeypatch):
    """Gating now fetches the URLs a candidate cited. Nothing here is about that
    retrieval, and no test may open a socket — so the reader call is closed off
    and every citation comes back COULD NOT FETCH."""
    monkeypatch.setattr(
        cited_sources,
        "fetch_source",
        lambda url, **kwargs: cited_sources.FetchedSource(url=url, text="Evidence sentence."),
    )
    yield

@pytest.fixture
def wired(tmp_path, monkeypatch):
    sessions_root = tmp_path / "research-sessions"
    projects_root = tmp_path / "projects"

    def get_project_dirs(slug):
        root = projects_root / slug
        root.mkdir(parents=True, exist_ok=True)
        return {"root": root}

    monkeypatch.setattr(factory.config, "RESEARCH_SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(factory, "get_project_dirs", get_project_dirs)
    monkeypatch.setattr(factory.answer_research, "get_project_dirs", get_project_dirs)

    built = []

    def fake_build_contexts(question, research, project_name, **kwargs):
        built.append((question, research, project_name))
        return (tmp_path / "answer_context.json", tmp_path / "comic_context.json")

    monkeypatch.setattr(factory.answer_research, "build_contexts", fake_build_contexts)

    workflow = ScoutWorkflow(
        store=SessionStore(sessions_root),
        client=_FakeYouCom(),
        planner=lambda *a, **k: None,
    )
    return workflow, built


def _confirmed_gate(**kwargs):
    return EvidenceGate(
        verdict="confirmed",
        reason="The source confirms the moment.",
        evidence_urls=["https://source.test/1"],
        reader_url="https://batcave.biz/reader/1/2",
        flags=[],
    )


def test_qa_session_the_workflow_itself_produced_creates_a_project(monkeypatch, wired):
    """workflow -> project_factory, end to end, with no hand-built artifacts."""
    workflow, built = wired
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review", lambda **kwargs: _confirmed_gate()
    )

    session = workflow.start(ScoutMode.QA, "Which heroes did this?")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-1", "candidate-2", "candidate-3"])
    workflow.approve_selected(session.id)

    assert factory.create_project_from_session(session.id, "hero-question") == "hero-question"
    assert len(built) == 1
    assert len(built[0][1]["items"]) == 3


def test_the_gate_artifact_the_workflow_wrote_is_the_shape_the_factory_reads(monkeypatch, wired):
    """The regression in one line: the writer's output must satisfy the reader.
    Production code used to write a single bare gate object, which
    _gate_assignments rejects for QA outright, so every candidate came back
    unassigned and create_project_from_session raised malformed_output."""
    workflow, _built = wired
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review", lambda **kwargs: _confirmed_gate()
    )
    session = workflow.start(ScoutMode.QA, "Which heroes did this?")
    workflow.run_general(session.id)
    selected = ["candidate-1", "candidate-2", "candidate-3"]
    workflow.verify_selected(session.id, selected)
    approved = workflow.approve_selected(session.id)

    assert factory.evaluate_production_gates(approved) == {}


def test_a_deselection_after_verifying_does_not_strand_the_factory(monkeypatch, wired):
    """Un-ticking a candidate between verify and approve used to leave its gate in
    the artifact, so len(gates) != len(selected_ids) and every assignment came
    back None — the shipped bug, through a different door."""
    workflow, _built = wired
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review", lambda **kwargs: _confirmed_gate()
    )
    session = workflow.start(ScoutMode.QA, "Which heroes did this?")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-%d" % i for i in range(1, 6)])
    workflow.verify_selected(session.id, ["candidate-1", "candidate-2", "candidate-3"], only=[])
    approved = workflow.approve_selected(session.id)

    assert factory.evaluate_production_gates(approved) == {}


def test_approve_uses_the_current_three_qa_selections_without_regating(monkeypatch, wired):
    """The UI can un-tick two already-gated cards before approving.

    Approval must persist those three current ids and drop the two keyed gates
    that no longer belong to the project.  It must not pay to gate the three
    retained cards a second time.
    """
    workflow, _built = wired
    review_calls = []

    def confirmed(**kwargs):
        review_calls.append(kwargs["candidate"]["id"])
        return _confirmed_gate()

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", confirmed)
    session = workflow.start(ScoutMode.QA, "Which heroes did this?")
    workflow.run_general(session.id)
    all_ids = [f"candidate-{i}" for i in range(1, 6)]
    selected = all_ids[:3]
    workflow.verify_selected(session.id, all_ids)

    approved = workflow.approve_selected(session.id, selected)

    assert approved.selected_specific_candidate_ids == selected
    assert approved.state.value == "production_gates"
    gates = json.loads(
        workflow.store.artifact_path(session.id, "specific/evidence_gate.v1.json").read_text()
    )
    assert [gate["candidate_id"] for gate in gates["gates"]] == selected
    # verify_selected gates in parallel, so completion order is not the caller's
    # order. What matters is that each candidate was bought exactly once.
    assert sorted(review_calls) == all_ids
    assert factory.evaluate_production_gates(approved) == {}


def test_a_micro_session_the_workflow_produced_creates_a_project(monkeypatch, wired, tmp_path):
    workflow, _built = wired
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review", lambda **kwargs: _confirmed_gate()
    )
    session = workflow.start(ScoutMode.MICRO, "One Hulk moment")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-2"])
    workflow.approve_selected(session.id)

    slug = factory.create_project_from_session(session.id, "hulk-moment")
    context = json.loads(
        (tmp_path / "projects" / slug / "comic_context.json").read_text(encoding="utf-8")
    )
    assert context["reader_url"] == "https://batcave.biz/reader/1/2"
