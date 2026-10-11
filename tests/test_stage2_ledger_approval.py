"""Stage 2 approval must reserve a project only after a saved narration parses."""
import asyncio
import hashlib
import json

import flet as ft

import ui  # noqa: F401
import ui.screens.s3_narrate as s3_narrate
import ui.state as ui_state
from stages.research_scout.ledger import Ledger
from stages.research_scout.production_ledger import record_milestone
from tests.ui_test_doubles import StrictFakePage
from ui.state import AppState


def _walk(control):
    if control is None:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in (getattr(control, attr, None) or []):
            yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


def _screen(tmp_path, monkeypatch, parse, ledger, *, milestone=None):
    projects = tmp_path / "projects"
    project = projects / "p"
    project.mkdir(parents=True)
    (project / "comic_context.json").write_text(json.dumps({
        "series": "Batman", "issue": 77, "year": 2019, "series_start_year": 2016,
    }))
    monkeypatch.setattr(s3_narrate, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(s3_narrate, "saved_script_for_editor", lambda _p: ("", ""))
    monkeypatch.setattr(s3_narrate, "_item_count", lambda _p: 0)
    monkeypatch.setattr(s3_narrate, "parse_and_save_script", parse)
    if milestone is None:
        def milestone(project_name, kind, *, projects_root):
            return record_milestone(project_name, kind, projects_root=projects_root, ledger=ledger)
    monkeypatch.setattr(s3_narrate, "record_milestone", milestone, raising=False)
    state = AppState(project_name="p", current_stage=2)
    transitions = []
    page = StrictFakePage()
    root = s3_narrate.build(page, state, on_go=transitions.append, on_state_change=lambda: None)
    fields = [c for c in _walk(root) if isinstance(c, ft.TextField)]
    script = next(c for c in fields if c.multiline and not c.read_only)
    approve = next(c for c in _walk(root) if isinstance(c, ft.ElevatedButton)
                   and c.content == "Approve & Continue →")
    return state, script, approve, transitions, project, root


def _parse_saved(projects_root):
    def parse(project_name, text, *, log):
        payload = {"script": text, "scenes": [], "total_word_count": len(text.split())}
        (projects_root / project_name / "narration.json").write_text(json.dumps(payload))
        return payload
    return parse


def _ledger(tmp_path):
    return Ledger(tmp_path / "ledger.db", _writer_guard=lambda: True)


def test_refused_script_never_records(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    projects = tmp_path / "projects"

    def refuse(_project, _text, *, log):
        raise ValueError("script refused")

    state, script, approve, _transitions, _project, _root = _screen(
        tmp_path, monkeypatch, refuse, ledger,
    )
    script.value = "A draft that does not parse."
    asyncio.run(approve.on_click(None))

    assert ledger.count_events() == 0
    assert not state.is_approved(2)
    assert not state.approved_narration_sha256


def test_approved_script_records_before_stage_transition(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    projects = tmp_path / "projects"
    state, script, approve, transitions, project, _root = _screen(
        tmp_path, monkeypatch, _parse_saved(projects), ledger,
    )
    script.value = "The approved narration."
    asyncio.run(approve.on_click(None))

    assert state.is_approved(2)
    assert state.approved_narration_sha256 == hashlib.sha256((project / "narration.json").read_bytes()).hexdigest()
    assert ledger.count_events() == 1
    assert transitions == [3]


def test_ledger_failure_keeps_stage2_unapproved(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    projects = tmp_path / "projects"

    calls = []

    def fail_once(project_name, kind, *, projects_root):
        calls.append(kind)
        if len(calls) == 1:
            raise OSError("ledger temporarily unavailable")
        return record_milestone(project_name, kind, projects_root=projects_root, ledger=ledger)

    state, script, approve, transitions, project, root = _screen(
        tmp_path, monkeypatch, _parse_saved(projects), ledger, milestone=fail_once,
    )
    monkeypatch.setattr(s3_narrate.sys, "platform", "win32", raising=False)
    state.approved["2"] = True
    state.approved_narration_sha256 = "previously-approved-hash"
    ui_state.save_state(state)
    script.value = "Saved even though the ledger is offline."
    asyncio.run(approve.on_click(None))

    assert (project / "narration.json").exists()
    assert not state.is_approved(2)
    assert not state.approved_narration_sha256
    reloaded = ui_state.load_state("p")
    assert not reloaded.is_approved(2)
    assert reloaded.approved_narration_sha256 == ""
    assert transitions == []
    assert any(isinstance(c, ft.Text) and "retry Approve & Continue" in (c.value or "")
               for c in _walk(root))

    asyncio.run(approve.on_click(None))
    assert state.is_approved(2)
    assert ui_state.load_state("p").approved_narration_sha256 == state.approved_narration_sha256
    assert ledger.count_events() == 1
    assert transitions == [3]


def test_mac_approval_is_local_only(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    state, script, approve, transitions, project, root = _screen(
        tmp_path, monkeypatch, _parse_saved(projects), _ledger(tmp_path),
        milestone=lambda *_a, **_kw: "read_only",
    )
    monkeypatch.setattr(s3_narrate.sys, "platform", "darwin", raising=False)
    script.value = "Locally approved narration."
    asyncio.run(approve.on_click(None))

    assert state.is_approved(2)
    assert state.approved_narration_sha256 == hashlib.sha256((project / "narration.json").read_bytes()).hexdigest()
    assert transitions == [3]
    assert any(isinstance(c, ft.Text) and "Central ledger was not updated" in (c.value or "")
               for c in _walk(root))


def test_repeat_click_is_idempotent(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    projects = tmp_path / "projects"
    state, script, approve, _transitions, _project, _root = _screen(
        tmp_path, monkeypatch, _parse_saved(projects), ledger,
    )
    script.value = "Identical narration on both clicks."
    asyncio.run(approve.on_click(None))
    asyncio.run(approve.on_click(None))

    assert state.is_approved(2)
    assert ledger.count_events() == 1


def test_return_to_research_clears_narration_approval_hash():
    state = AppState(project_name="p", approved={"2": True}, approved_narration_sha256="abc")

    state.return_to_research("session", "qa", "intent")

    assert not state.is_approved(2)
    assert state.approved_narration_sha256 == ""
