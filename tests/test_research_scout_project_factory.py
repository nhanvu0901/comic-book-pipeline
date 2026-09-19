import json

import pytest

import stages.research_scout.project_factory as factory
from stages.research_scout.evidence import GateFlag
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.research_scout.storage import SessionStore


def _wire_roots(tmp_path, monkeypatch):
    sessions_root = tmp_path / "research-sessions"
    projects_root = tmp_path / "projects"

    def get_project_dirs(slug):
        root = projects_root / slug
        root.mkdir(parents=True, exist_ok=True)
        return {"root": root}

    monkeypatch.setattr(factory.config, "RESEARCH_SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(factory, "get_project_dirs", get_project_dirs)
    monkeypatch.setattr(factory.answer_research, "get_project_dirs", get_project_dirs)
    return sessions_root, projects_root


def _candidate(candidate_id, *, mode=ScoutMode.QA, index=1):
    return {
        "id": candidate_id,
        "entity": f"Hero {index}",
        "title": f"Hero {index}",
        "character": f"Hero {index}",
        "series_issue_year": f"Thor #{index} (2024)",
        "source_comic": f"Thor #{index}",
        "source_year": "2024",
        "how_or_why": f"Hero {index} performs the confirmed action.",
        "visible_event": f"Hero {index} visibly performs the action.",
        "drawable_moment": f"Hero {index} raises a hammer.",
        "verification_note": f"Verified from source {index}.",
        "surprise_level": "low" if index == 1 else "medium",
        "reader_url": f"https://batcave.biz/reader/{index}/20{index}",
        "evidence_urls": [f"https://source.test/{index}"],
    }


def _gate(candidate_id, *, reader_url=None, verdict="confirmed", flags=None):
    return {
        "candidate_id": candidate_id,
        "verdict": verdict,
        "reason": "The source confirms the selected moment.",
        "evidence_urls": ["https://source.test/primary"],
        "reader_url": reader_url,
        "flags": flags or [],
    }


def _session(tmp_path, mode, candidates, gates):
    store = SessionStore(tmp_path / "research-sessions")
    session = ResearchSession(
        id=f"{mode.value}-session",
        mode=mode,
        user_intent="Which heroes did this?",
        state=SessionState.PRODUCTION_GATES,
        selected_specific_candidate_ids=[candidate["id"] for candidate in candidates],
    )
    store.save(session)
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": candidates})
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", {"gates": gates})
    return session


def test_micro_factory_writes_target_moment_without_changing_stage_contract(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro", mode=ScoutMode.MICRO)
    session = _session(tmp_path, ScoutMode.MICRO, [candidate], [_gate("micro")])

    slug = factory.create_project_from_session(session.id, "thor-hammer")

    context = json.loads((tmp_path / "projects" / slug / "comic_context.json").read_text())
    assert context["target_moment"] == candidate["visible_event"]
    assert context["series_issue_year"] == candidate["series_issue_year"]
    assert context["issue"] == candidate["series_issue_year"]
    assert context["reader_url"] == candidate["reader_url"]


def test_qa_factory_requires_three_confirmed_reader_urls(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate("a", index=1), _candidate("b", index=2)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate("a"), _gate("b")])

    with pytest.raises(ValueError, match="three"):
        factory.create_project_from_session(session.id, "hulk-question")


def test_qa_factory_builds_contexts_once_from_selected_confirmed_gates(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    gates = [_gate(candidate["id"]) for candidate in candidates]
    session = _session(tmp_path, ScoutMode.QA, candidates, gates)
    calls = []

    def fake_build_contexts(question, research, project_name, **kwargs):
        calls.append((question, research, project_name, kwargs))
        return (tmp_path / "answer_context.json", tmp_path / "comic_context.json")

    monkeypatch.setattr(factory.answer_research, "build_contexts", fake_build_contexts)

    assert factory.create_project_from_session(session.id, "hulk-question") == "hulk-question"
    assert len(calls) == 1
    question, research, project_name, _ = calls[0]
    assert question == session.user_intent
    assert project_name == "hulk-question"
    assert len(research["items"]) == 3
    required = {
        "entity", "how_or_why", "source_comic", "source_year", "reader_url",
        "drawable_moment", "verification_note", "surprise_level",
    }
    assert all(required <= set(item) for item in research["items"])


def test_qa_factory_rejects_legacy_single_gate_reused_for_all_candidates(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate("a")])
    store = SessionStore(tmp_path / "research-sessions")
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", _gate("a"))

    with pytest.raises(ValueError, match="malformed"):
        factory.create_project_from_session(session.id, "hulk-question")


def test_qa_factory_rejects_keyed_gates_for_unselected_candidates(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(
        tmp_path,
        ScoutMode.QA,
        candidates,
        [_gate("x"), _gate("y"), _gate("z")],
    )

    with pytest.raises(ValueError, match="malformed"):
        factory.create_project_from_session(session.id, "hulk-question")


def test_factory_preserves_build_contexts_reader_url_failure_and_does_not_mark_created(
    tmp_path, monkeypatch
):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate(c["id"]) for c in candidates])

    def fail_loudly(*args, **kwargs):
        raise ValueError("empty reader_url for item(s): Hero 2")

    monkeypatch.setattr(factory.answer_research, "build_contexts", fail_loudly)

    with pytest.raises(ValueError, match="empty reader_url"):
        factory.create_project_from_session(session.id, "hulk-question")
    assert SessionStore(tmp_path / "research-sessions").load(session.id).created_project is None


def test_factory_rejects_second_create_for_a_session_with_created_project(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro")
    session = _session(tmp_path, ScoutMode.MICRO, [candidate], [_gate("micro")])

    factory.create_project_from_session(session.id, "thor-hammer")

    with pytest.raises(ValueError, match="already created"):
        factory.create_project_from_session(session.id, "thor-hammer")


def test_evaluate_production_gates_names_the_candidate_each_flag_belongs_to(
    tmp_path, monkeypatch
):
    """A flat list of flags could not say WHICH of five candidates failed, so the
    error it produced was unactionable. The result is keyed by candidate now."""
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro")
    session = _session(
        tmp_path,
        ScoutMode.MICRO,
        [candidate],
        [_gate("micro", flags=[GateFlag.NO_VISUAL_EVENT.value])],
    )

    assert factory.evaluate_production_gates(session) == {
        "micro": [GateFlag.NO_VISUAL_EVENT],
    }


def test_a_clean_session_reports_no_flags_at_all(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro")
    session = _session(tmp_path, ScoutMode.MICRO, [candidate], [_gate("micro")])

    assert factory.evaluate_production_gates(session) == {}


# ─── Blocking rules: what a human may wave through, and what they may not ───

def test_an_inconclusive_verdict_blocks_but_can_be_overridden(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    gates = [_gate("a", verdict="inconclusive"), _gate("b"), _gate("c")]
    session = _session(tmp_path, ScoutMode.QA, candidates, gates)
    monkeypatch.setattr(
        factory.answer_research, "build_contexts", lambda *a, **k: (tmp_path, tmp_path)
    )

    with pytest.raises(ValueError, match="verdict inconclusive"):
        factory.create_project_from_session(session.id, "hulk-question")

    assert factory.create_project_from_session(
        session.id, "hulk-question", override=True
    ) == "hulk-question"


def test_a_model_emitted_flag_blocks_but_can_be_overridden(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    gates = [_gate("a", flags=["panel_is_a_flashback"]), _gate("b"), _gate("c")]
    session = _session(tmp_path, ScoutMode.QA, candidates, gates)
    monkeypatch.setattr(
        factory.answer_research, "build_contexts", lambda *a, **k: (tmp_path, tmp_path)
    )

    with pytest.raises(ValueError, match="panel_is_a_flashback"):
        factory.create_project_from_session(session.id, "hulk-question")

    assert factory.create_project_from_session(
        session.id, "hulk-question", override=True
    ) == "hulk-question"


def test_a_missing_gate_is_a_defect_and_override_cannot_wave_it_through(tmp_path, monkeypatch):
    """A gate that cannot be assigned is a broken artifact, not a judgement call —
    there is nothing for a human to have an opinion about."""
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate("a"), _gate("b")])

    with pytest.raises(ValueError, match="malformed"):
        factory.create_project_from_session(session.id, "hulk-question", override=True)


def test_a_duplicate_selection_is_a_defect_and_override_cannot_wave_it_through(
    tmp_path, monkeypatch
):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate(c["id"]) for c in candidates])
    store = SessionStore(tmp_path / "research-sessions")
    session.selected_specific_candidate_ids = ["a", "a", "b"]
    store.save(session)

    with pytest.raises(ValueError, match="duplicate"):
        factory.create_project_from_session(session.id, "hulk-question", override=True)


def test_an_override_is_recorded_in_the_audit_with_each_candidates_reason(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    gates = [_gate("a", verdict="inconclusive"), _gate("b"), _gate("c")]
    session = _session(tmp_path, ScoutMode.QA, candidates, gates)
    monkeypatch.setattr(
        factory.answer_research, "build_contexts", lambda *a, **k: (tmp_path, tmp_path)
    )

    factory.create_project_from_session(session.id, "hulk-question", override=True)

    store = SessionStore(tmp_path / "research-sessions")
    audit = [
        json.loads(line)
        for line in (store.session_dir(session.id) / "audit.jsonl").read_text().splitlines()
    ]
    overridden = [event for event in audit if event["event"] == "gates_overridden"]
    assert len(overridden) == 1
    assert "a" in overridden[0]["detail"]["candidates"]
    assert "inconclusive" in json.dumps(overridden[0]["detail"]["candidates"]["a"])


def test_the_failure_message_names_the_candidate_its_issue_and_the_gates_reason(
    tmp_path, monkeypatch
):
    """`production gates failed: malformed_output` told a user nothing about which
    of five candidates to look at. Spec section 7's shape does."""
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    bad = _gate("b", verdict="inconclusive")
    bad["reason"] = "cited URLs not present in raw evidence"
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate("a"), bad, _gate("c")])

    with pytest.raises(ValueError) as caught:
        factory.create_project_from_session(session.id, "hulk-question")

    message = str(caught.value)
    assert "b (Thor #2 (2024))" in message
    assert "verdict inconclusive" in message
    assert "cited URLs not present in raw evidence" in message
    # The two healthy candidates have nothing to say and must not be listed.
    assert "\na (" not in message and "\nc (" not in message
