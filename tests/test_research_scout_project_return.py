"""Returning a downloaded Scout project to its existing Stage 1 session."""

import json
import os
from pathlib import Path

import pytest

import stages.research_scout.project_factory as factory
import stages.research_scout.project_return as project_return
from stages.research_scout.models import FeedbackNote, ResearchSession, ScoutMode, SessionState
from stages.research_scout.project_return import return_project_to_research
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow


def _candidate():
    return {
        "id": "moment",
        "title": "Thor",
        "character": "Thor",
        "series_issue_year": "Thor #1 (2024)",
        "source_year": "2024",
        "visible_event": "Thor raises the hammer.",
        "summary": "Thor raises the hammer in battle.",
        "reader_url": "https://batcave.biz/reader/1/2",
        "evidence_urls": ["https://source.test/1"],
    }


def _gate():
    return {
        "candidate_id": "moment",
        "verdict": "confirmed",
        "reason": "The source confirms the moment.",
        "evidence_urls": ["https://source.test/1"],
        "reader_url": "https://batcave.biz/reader/1/2",
        "flags": [],
    }


def _linked_session(tmp_path, *, slug="thor-hammer"):
    store = SessionStore(tmp_path / "research-sessions")
    session = ResearchSession(
        id="return-session",
        mode=ScoutMode.MICRO,
        user_intent="Thor raises the hammer",
        state=SessionState.PRODUCTION_GATES,
        revision=7,
        selected_specific_candidate_ids=["moment"],
        created_project=slug,
    )
    store.save(session, event="selection_approved")
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": [_candidate()]})
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", {"gates": [_gate()]})
    store.write_artifact(session.id, "general/raw_search.v1.json", {"source": "kept"})
    return store, session


def _project(tmp_path, slug="thor-hammer"):
    project = tmp_path / "projects" / slug
    (project / "raw_comic").mkdir(parents=True)
    (project / "raw_comic" / "manifest.json").write_text('{"pages": [1]}')
    (project / "raw_comic" / "page.jpg").write_bytes(b"page-bytes")
    (project / "preprocessed").mkdir()
    (project / "preprocessed" / "page_001.json").write_text('{"page_number": 1}')
    (project / "comic_context.json").write_text('{"stage": 1}')
    (project / "answer_context.json").write_text('{"question": "kept"}')
    (project / "narration.json").write_text('{"script": "kept"}')
    (project / "audio.wav").write_bytes(b"later-stage-audio")
    return project


def test_return_removes_only_stage2_caches_and_restores_the_same_research_session(tmp_path):
    """Removing any non-Stage-2 file or clearing gates/selection would lose work."""
    store, session = _linked_session(tmp_path)
    project = _project(tmp_path)
    before_candidates = (store.session_dir(session.id) / "general/candidates.v1.json").read_bytes()
    before_gates = (store.session_dir(session.id) / "specific/evidence_gate.v1.json").read_bytes()
    before_raw_search = (store.session_dir(session.id) / "general/raw_search.v1.json").read_bytes()

    restored = return_project_to_research(
        "thor-hammer",
        projects_root=tmp_path / "projects",
        sessions_root=tmp_path / "research-sessions",
    )

    assert restored.id == session.id
    assert restored.state is SessionState.CANDIDATE_REVIEW
    assert restored.created_project is None
    assert restored.revision == 7
    assert restored.selected_specific_candidate_ids == ["moment"]
    assert not (project / "raw_comic").exists()
    assert not (project / "preprocessed").exists()
    assert (project / "comic_context.json").read_text() == '{"stage": 1}'
    assert (project / "answer_context.json").read_text() == '{"question": "kept"}'
    assert (project / "narration.json").read_text() == '{"script": "kept"}'
    assert (project / "audio.wav").read_bytes() == b"later-stage-audio"
    assert (store.session_dir(session.id) / "general/candidates.v1.json").read_bytes() == before_candidates
    assert (store.session_dir(session.id) / "specific/evidence_gate.v1.json").read_bytes() == before_gates
    assert (store.session_dir(session.id) / "general/raw_search.v1.json").read_bytes() == before_raw_search
    audit = [json.loads(line) for line in (store.session_dir(session.id) / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["event"] == "project_returned_to_research"
    assert audit[-1]["detail"]["project"] == "thor-hammer"


def test_return_rejects_ambiguous_link_before_deleting_any_project_files(tmp_path):
    """Choosing an arbitrary linked session could restore the wrong research."""
    _linked_session(tmp_path)
    store = SessionStore(tmp_path / "research-sessions")
    duplicate = ResearchSession(
        id="second-session", mode=ScoutMode.MICRO, user_intent="Other",
        state=SessionState.PRODUCTION_GATES, selected_specific_candidate_ids=["moment"],
        created_project="thor-hammer",
    )
    store.save(duplicate)
    project = _project(tmp_path)

    with pytest.raises(ValueError, match="exactly one"):
        return_project_to_research(
            "thor-hammer", projects_root=tmp_path / "projects",
            sessions_root=tmp_path / "research-sessions",
        )

    assert (project / "raw_comic" / "manifest.json").exists()
    assert (project / "preprocessed" / "page_001.json").exists()
    assert store.load("return-session").state is SessionState.PRODUCTION_GATES
    assert store.load("second-session").created_project == "thor-hammer"


def test_return_then_reapprove_allows_the_factory_to_recreate_the_same_slug(tmp_path, monkeypatch):
    """Leaving created_project set would make the existing factory reject a re-create."""
    sessions_root, projects_root = tmp_path / "research-sessions", tmp_path / "projects"
    store, session = _linked_session(tmp_path)
    session.created_project = None
    store.save(session)

    def get_project_dirs(slug):
        root = projects_root / slug
        root.mkdir(parents=True, exist_ok=True)
        return {"root": root}

    monkeypatch.setattr(factory.config, "RESEARCH_SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(factory, "get_project_dirs", get_project_dirs)

    # First creation is the real factory path; Stage 2 then leaves only its caches.
    assert factory.create_project_from_session(session.id, "thor-hammer") == "thor-hammer"
    project = projects_root / "thor-hammer"
    (project / "raw_comic").mkdir()
    (project / "raw_comic" / "manifest.json").write_text("{}")
    (project / "preprocessed").mkdir()
    (project / "preprocessed" / "page_001.json").write_text("{}")

    return_project_to_research(
        "thor-hammer", projects_root=projects_root, sessions_root=sessions_root,
    )
    ScoutWorkflow(store=store).approve_selected(session.id)

    assert factory.create_project_from_session(session.id, "thor-hammer") == "thor-hammer"
    assert store.load(session.id).created_project == "thor-hammer"


def test_return_restores_session_and_caches_when_audit_fails_after_session_write(tmp_path, monkeypatch):
    """A save can fail after session.json changed, so rollback must cover both resources."""
    store, session = _linked_session(tmp_path)
    project = _project(tmp_path)
    session_json = (store.session_dir(session.id) / "session.json").read_bytes()

    original_append = SessionStore.append_audit

    def append_then_fail(self, session_id, event, detail=None):
        original_append(self, session_id, event, detail)
        if event == "project_returned_to_research":
            raise OSError("audit disk error after append")

    monkeypatch.setattr(SessionStore, "append_audit", append_then_fail)

    with pytest.raises(OSError, match="audit disk error"):
        return_project_to_research(
            "thor-hammer", projects_root=tmp_path / "projects",
            sessions_root=tmp_path / "research-sessions",
        )

    assert (store.session_dir(session.id) / "session.json").read_bytes() == session_json
    assert (project / "raw_comic" / "manifest.json").exists()
    assert (project / "preprocessed" / "page_001.json").exists()


def test_return_preserves_qa_selection_gates_feedback_and_revision(tmp_path):
    """A mode-specific reset that dropped carried QA research would force new API calls."""
    store = SessionStore(tmp_path / "research-sessions")
    selected = ["r2", "r4", "r5"]
    session = ResearchSession(
        id="qa-return", mode=ScoutMode.QA, user_intent="Which heroes did this?",
        state=SessionState.PRODUCTION_GATES, revision=4,
        selected_specific_candidate_ids=selected, created_project="hero-question",
        feedback_log=[FeedbackNote(state="candidate_review", text="keep the evidence")],
        kept_candidate_ids=["r2", "r4"],
    )
    store.save(session)
    candidates = [{**_candidate(), "id": candidate_id} for candidate_id in selected]
    gates = [{**_gate(), "candidate_id": candidate_id} for candidate_id in selected]
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": candidates})
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", {"gates": gates})
    project = _project(tmp_path, "hero-question")

    restored = return_project_to_research(
        "hero-question", projects_root=tmp_path / "projects",
        sessions_root=tmp_path / "research-sessions",
    )

    assert restored.mode is ScoutMode.QA
    assert restored.state is SessionState.CANDIDATE_REVIEW
    assert restored.revision == 4
    assert restored.selected_specific_candidate_ids == selected
    assert restored.kept_candidate_ids == ["r2", "r4"]
    assert restored.feedback_log[0].text == "keep the evidence"
    assert (project / "comic_context.json").exists()


def test_return_accepts_the_factory_supported_legacy_bare_micro_gate(tmp_path):
    """Old Micro sessions have one bare gate, which still belongs to their selection."""
    store, session = _linked_session(tmp_path)
    legacy_gate = _gate()
    legacy_gate.pop("candidate_id")
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", legacy_gate)
    _project(tmp_path)

    restored = return_project_to_research(
        "thor-hammer", projects_root=tmp_path / "projects",
        sessions_root=tmp_path / "research-sessions",
    )

    assert restored.state is SessionState.CANDIDATE_REVIEW
    assert restored.selected_specific_candidate_ids == ["moment"]


def test_return_retry_after_backend_success_is_idempotent_for_ui_state_save_recovery(tmp_path):
    """If UI persistence fails after backend success, retry must resume the same session."""
    store, session = _linked_session(tmp_path)
    _project(tmp_path)

    first = return_project_to_research(
        "thor-hammer", projects_root=tmp_path / "projects",
        sessions_root=tmp_path / "research-sessions",
    )
    retried = return_project_to_research(
        "thor-hammer", projects_root=tmp_path / "projects",
        sessions_root=tmp_path / "research-sessions",
    )

    assert first.id == retried.id == session.id
    audit = [json.loads(line) for line in (store.session_dir(session.id) / "audit.jsonl").read_text().splitlines()]
    assert [event["event"] for event in audit].count("project_returned_to_research") == 1


def test_return_without_a_link_or_with_missing_research_leaves_stage2_files_untouched(tmp_path):
    """Validation must precede cleanup; an incomplete link is not a safe restore."""
    project = _project(tmp_path)

    with pytest.raises(ValueError, match="exactly one"):
        return_project_to_research(
            "thor-hammer", projects_root=tmp_path / "projects",
            sessions_root=tmp_path / "research-sessions",
        )
    assert (project / "raw_comic" / "manifest.json").exists()

    store, session = _linked_session(tmp_path)
    (store.session_dir(session.id) / "specific/evidence_gate.v1.json").unlink()
    with pytest.raises(ValueError, match="missing restorable"):
        return_project_to_research(
            "thor-hammer", projects_root=tmp_path / "projects",
            sessions_root=tmp_path / "research-sessions",
        )
    assert (project / "preprocessed" / "page_001.json").exists()


def test_return_retries_a_partly_deleted_staged_cache_without_a_second_audit(tmp_path, monkeypatch):
    """A partial rmtree is irreversible, so retry resumes its recorded staging cleanup."""
    store, session = _linked_session(tmp_path)
    project = _project(tmp_path)
    original_rmtree = project_return.shutil.rmtree
    calls = []

    def partly_delete(path):
        calls.append(path.name)
        original_rmtree(path)
        raise OSError("disk full")

    monkeypatch.setattr(project_return.shutil, "rmtree", partly_delete)

    with pytest.raises(RuntimeError, match="cache cleanup failed"):
        return_project_to_research(
            "thor-hammer", projects_root=tmp_path / "projects",
            sessions_root=tmp_path / "research-sessions",
        )

    pending = store.load(session.id)
    assert pending.state is SessionState.CANDIDATE_REVIEW
    assert pending.created_project is None
    monkeypatch.setattr(project_return.shutil, "rmtree", original_rmtree)

    restored = return_project_to_research(
        "thor-hammer", projects_root=tmp_path / "projects",
        sessions_root=tmp_path / "research-sessions",
    )

    assert restored.id == session.id
    assert not (project / "raw_comic").exists()
    assert not (project / "preprocessed").exists()
    audit = [json.loads(line) for line in (store.session_dir(session.id) / "audit.jsonl").read_text().splitlines()]
    assert [event["event"] for event in audit].count("project_returned_to_research") == 1
    assert calls


def test_pending_return_marker_cannot_claim_a_recreated_project_with_the_same_slug(tmp_path, monkeypatch):
    """A slug reused after deletion is a different project, even if its name matches."""
    _linked_session(tmp_path)
    project = _project(tmp_path)
    original_rmtree = project_return.shutil.rmtree
    monkeypatch.setattr(project_return.shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(RuntimeError):
        return_project_to_research(
            "thor-hammer", projects_root=tmp_path / "projects",
            sessions_root=tmp_path / "research-sessions",
        )

    monkeypatch.setattr(project_return.shutil, "rmtree", original_rmtree)
    original_rmtree(project)
    replacement = tmp_path / "projects" / "thor-hammer"
    (replacement / "raw_comic").mkdir(parents=True)
    (replacement / "raw_comic" / "manifest.json").write_text("new project")

    with pytest.raises(ValueError, match="exactly one"):
        return_project_to_research(
            "thor-hammer", projects_root=tmp_path / "projects",
            sessions_root=tmp_path / "research-sessions",
        )
    assert (replacement / "raw_comic" / "manifest.json").read_text() == "new project"


def test_a_page_held_open_during_return_is_named_and_nothing_changes(tmp_path, monkeypatch):
    """Windows refuses to move a folder while a file in it is open — for example a page
    thumbnail the browser is still loading. The return used to fail with a raw
    WinError naming two internal paths."""
    import utils.fs_remove as fs_remove
    from stages.user_errors import UserFacingError

    monkeypatch.setattr(fs_remove.time, "sleep", lambda _s: None)
    store, session = _linked_session(tmp_path)
    project = _project(tmp_path)
    held = project / "raw_comic" / "page.jpg"
    real_replace = os.replace

    def _windows_like(src, dst):
        if Path(src) in (project / "raw_comic", held):
            raise PermissionError(32, "The process cannot access the file", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(project_return.os, "replace", _windows_like)

    with pytest.raises(PermissionError) as caught:
        return_project_to_research(
            "thor-hammer", projects_root=tmp_path / "projects",
            sessions_root=tmp_path / "research-sessions",
        )

    assert isinstance(caught.value, UserFacingError)
    assert "'page.jpg' is still open" in str(caught.value)
    assert (project / "raw_comic" / "page.jpg").exists()
    assert (project / "preprocessed" / "page_001.json").exists()
    assert store.load(session.id).state is SessionState.PRODUCTION_GATES


def test_a_briefly_held_folder_is_retried_during_return(tmp_path, monkeypatch):
    import utils.fs_remove as fs_remove

    monkeypatch.setattr(fs_remove.time, "sleep", lambda _s: None)
    _linked_session(tmp_path)
    project = _project(tmp_path)
    real_replace = os.replace
    blocked = []

    def _held_once(src, dst):
        if Path(src) == project / "raw_comic" and not blocked:
            blocked.append(src)
            raise PermissionError(32, "The process cannot access the file", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(project_return.os, "replace", _held_once)

    return_project_to_research(
        "thor-hammer", projects_root=tmp_path / "projects",
        sessions_root=tmp_path / "research-sessions",
    )

    assert blocked and not (project / "raw_comic").exists()
