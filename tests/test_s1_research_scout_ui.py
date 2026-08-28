import asyncio

import flet as ft

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import ui.bridge as bridge
import ui.screens.s1_research_scout as s1_research_scout
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.research_scout.policies import PolicyBundle
from stages.research_scout.storage import SessionStore
from ui import app
from ui.state import AppState


class FakePage:
    def __init__(self):
        self.tasks = []
        self.views = []
        self.dialogs = []

    def run_task(self, *args, **kwargs):
        self.tasks.append((args, kwargs))

    def update(self):
        pass

    def show_dialog(self, d):
        self.dialogs.append(d)

    def pop_dialog(self, *a):
        pass


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


def _text_content(control):
    values = []
    for node in _walk(control):
        if isinstance(node, ft.Text):
            values.append(str(node.value or ""))
    return "\n".join(values)


def _label(b) -> str:
    """flet 0.85 keeps a button's caption in `content`, not `text`."""
    c = getattr(b, "content", None)
    return c if isinstance(c, str) else str(getattr(b, "text", "") or "")


def _buttons(control):
    return [c for c in _walk(control) if isinstance(c, (ft.TextButton, ft.ElevatedButton))]


class _FakeControl:
    def __init__(self, value):
        self.value = value


class _FakeEvent:
    def __init__(self, value):
        self.control = _FakeControl(value)


def _build(tmp_path, session=None):
    root = tmp_path / "research_sessions"
    s1_research_scout.RESEARCH_SESSIONS_ROOT = root
    bridge.RESEARCH_SESSIONS_ROOT = root
    page = FakePage()
    state = AppState()
    if session is not None:
        state.scout_session_id = session.id
    controls = s1_research_scout.build(
        page,
        state,
        on_go=lambda _stage: None,
        on_state_change=lambda: None,
    )
    return page, controls


def test_stage_one_routes_to_research_scout():
    assert app.STAGE_BUILDERS[1] is s1_research_scout.build


def test_specific_approve_is_disabled_for_qa_with_two_selected_items(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-session",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.SPECIFIC_REVIEW,
        selected_specific_candidate_ids=["a", "b"],
    )
    store.save(session)
    store.write_artifact(
        session.id,
        "general/candidates.v1.json",
        {"candidates": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]},
    )
    _page, controls = _build(tmp_path, session)
    approve = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == "approve-specific"
    )
    assert approve.disabled is True


def test_resume_lists_unfinished_session_without_creating_project(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = store.create(ScoutMode.MICRO, "Hulk")
    _page, controls = _build(tmp_path)
    assert session.id in _text_content(controls)
    assert session.created_project is None


def test_full_history_transcript_shows_feedback_and_superseded_round(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-session-history",
        mode=ScoutMode.QA,
        user_intent="Who has beaten Superman in a fight?",
        state=SessionState.GENERAL_REVIEW,
        revision=2,
    )
    store.save(session)
    store.append_audit(session.id, "session_created")
    store.append_audit(
        session.id, "general_research_completed",
        detail={"revision": 1, "prompt_hash": "x", "source_api": "research", "effort": "standard"},
    )
    store.append_audit(session.id, "general_research_rerun", detail={"feedback": "more villains"})
    store.append_audit(
        session.id, "general_research_completed",
        detail={"revision": 2, "prompt_hash": "y", "source_api": "research", "effort": "standard"},
    )
    store.write_artifact(
        session.id, "general/candidates.rev1.v1.json",
        {"revision": 1, "candidates": [{"id": "r1a", "title": "Round One Candidate"}]},
    )
    store.write_artifact(
        session.id, "general/candidates.rev2.v1.json",
        {"revision": 2, "candidates": [
            {"id": "r2a", "title": "Round Two Alpha"},
            {"id": "r2b", "title": "Round Two Beta"},
        ]},
    )
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [
            {"id": "r2a", "title": "Round Two Alpha"},
            {"id": "r2b", "title": "Round Two Beta"},
        ]},
    )

    _page, controls = _build(tmp_path, session)
    text = _text_content(controls)

    assert session.user_intent in text
    assert "more villains" in text
    assert "superseded" in text
    assert "Round 1" in text
    assert "Round Two Alpha" in text
    assert "Round Two Beta" in text
    assert any(isinstance(n, ft.RadioGroup) for n in _walk(controls))
    assert any(
        isinstance(n, ft.ElevatedButton)
        and getattr(n, "content", None) == "Approve & find evidence →"
        for n in _walk(controls)
    )


def test_specific_review_renders_verdict_checkboxes_and_back_button(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-session-specific",
        mode=ScoutMode.QA,
        user_intent="Who has beaten Superman in a fight?",
        state=SessionState.SPECIFIC_REVIEW,
        selected_general_candidate_id="a",
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)
    store.append_audit(session.id, "session_created")
    store.append_audit(
        session.id, "general_research_completed",
        detail={"revision": 1, "prompt_hash": "x", "source_api": "research", "effort": "standard"},
    )
    store.append_audit(session.id, "general_candidate_approved", detail={"candidate_id": "a"})
    store.append_audit(
        session.id, "specific_research_completed",
        detail={"model": "m", "prompt_hash": "z", "verdict": "confirmed"},
    )
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [
            {"id": "a", "title": "A"}, {"id": "b", "title": "B"}, {"id": "c", "title": "C"},
        ]},
    )
    store.write_artifact(
        session.id, "specific/evidence_gate.v1.json",
        {"verdict": "confirmed", "reason": "Backed by two sources.", "evidence_urls": [],
         "reader_url": None, "flags": []},
    )

    _page, controls = _build(tmp_path, session)
    text = _text_content(controls)

    assert "CONFIRMED" in text
    assert "Backed by two sources." in text
    checkboxes = [n for n in _walk(controls) if isinstance(n, ft.Checkbox)]
    assert len(checkboxes) == 3
    approve = next(
        node for node in _walk(controls) if getattr(node, "key", None) == "approve-specific"
    )
    assert approve.disabled is False
    assert any(
        isinstance(n, ft.OutlinedButton) and getattr(n, "content", None) == "← Back to general"
        for n in _walk(controls)
    )


def test_production_gates_renders_slug_field_and_disables_input(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-session-production",
        mode=ScoutMode.QA,
        user_intent="Who has beaten Superman in a fight?",
        state=SessionState.PRODUCTION_GATES,
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)

    _page, controls = _build(tmp_path, session)

    slug_field = next(
        node for node in _walk(controls) if getattr(node, "key", None) == "project-slug"
    )
    assert slug_field.value
    assert any(
        isinstance(n, ft.ElevatedButton) and getattr(n, "content", None) == "Create project"
        for n in _walk(controls)
    )
    intent_field = next(
        node for node in _walk(controls) if getattr(node, "key", None) == "scout-intent"
    )
    assert intent_field.disabled is True


def test_send_with_empty_input_and_no_session_creates_no_session(tmp_path):
    root = tmp_path / "research_sessions"
    _page, controls = _build(tmp_path)

    send = next(node for node in _walk(controls) if getattr(node, "key", None) == "chat-send")
    send.on_click(object())

    assert not root.exists() or not any(root.iterdir())


def test_micro_checkbox_exclusivity_keeps_one_selected(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="micro-session-exclusive",
        mode=ScoutMode.MICRO,
        user_intent="Hulk breaks a bridge",
        state=SessionState.SPECIFIC_REVIEW,
    )
    store.save(session)
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [{"id": "m1", "title": "Moment One"}, {"id": "m2", "title": "Moment Two"}]},
    )

    _page, controls = _build(tmp_path, session)

    def _checkboxes():
        return [n for n in _walk(controls) if isinstance(n, ft.Checkbox)]

    first = next(cb for cb in _checkboxes() if cb.key == "select-m1")
    first.on_change(_FakeEvent(True))
    checked_after_first = [cb for cb in _checkboxes() if cb.value]
    assert len(checked_after_first) == 1
    assert checked_after_first[0].key == "select-m1"

    second = next(cb for cb in _checkboxes() if cb.key == "select-m2")
    second.on_change(_FakeEvent(True))
    checked_after_second = [cb for cb in _checkboxes() if cb.value]
    assert len(checked_after_second) == 1
    assert checked_after_second[0].key == "select-m2"


def _run_recorded_task(page):
    """Actually execute the coroutine _run_busy handed to page.run_task — FakePage
    only records it, and this test needs the real work done to inspect the result."""
    (func,), _kwargs = page.tasks[-1]
    asyncio.run(func())


def test_second_empty_send_fills_the_intent_box_with_a_discovered_question_and_starts_no_session(
    tmp_path, monkeypatch,
):
    """The first empty Send must only SHOW the bank suggestions, spending nothing. A
    second empty Send is the user explicitly declining every suggestion shown — Master
    2026-08-28: it must discover a real Tier B question and drop it into the intent
    box for a human to read/edit/delete, NOT start a session and immediately spend a
    SECOND research call enumerating that question's answers before anyone looks at
    it. That silent double-spend is exactly what removed the human-review step
    is_burned()'s docstring (stages/youcom_scout.py) says catches synonym re-skins of
    already-rejected bank questions — landing the question in the box restores it."""
    root = tmp_path / "research_sessions"
    s1_research_scout.RESEARCH_SESSIONS_ROOT = root
    bridge.RESEARCH_SESSIONS_ROOT = root
    monkeypatch.setattr("stages.research_scout.bank_fallback._REPO_ROOT", tmp_path)

    class _NetworkTripwireYouCom:
        # discover_intent() -> ScoutWorkflow.discover_question spends one
        # client.research() call turning the angle into a real question before
        # falling back to it. bridge._scout_workflow() builds an uninjected, real
        # YouComClient() here, and config.load_dotenv() can put a LIVE key in this
        # process — so without this stub the assertions below would depend on a
        # real network call. Raising forces discover_question's mandatory
        # fallback, which returns the angle itself: exactly what this test's
        # angles[0] assertion expects.
        def research(self, *args, **kwargs):
            raise AssertionError("test tried to reach the real You.com client")

    monkeypatch.setattr(
        "stages.research_scout.workflow.YouComClient", lambda *a, **k: _NetworkTripwireYouCom()
    )
    (tmp_path / "qa_question_bank.md").write_text(
        "| Status | Question | Answer items (comic, year) | Notes |\n"
        "|--------|----------|----------------------------|-------|\n"
        "| SAVE-FOR-LATER | The one open bank question? | item | note |\n",
        encoding="utf-8",
    )
    (tmp_path / "qa_question_banlist.md").write_text(
        "| Date | Question | Reason |\n|------|----------|--------|\n", encoding="utf-8",
    )

    def _must_not_be_called(name):
        def _fail(*_a, **_k):
            raise AssertionError(f"{name} must not be called on a discover-only Send")
        return _fail

    monkeypatch.setattr(s1_research_scout, "start_scout_session", _must_not_be_called("start_scout_session"))
    monkeypatch.setattr(s1_research_scout, "run_scout_general", _must_not_be_called("run_scout_general"))

    page, controls = _build(tmp_path)
    send = next(node for node in _walk(controls) if getattr(node, "key", None) == "chat-send")

    send.on_click(object())  # first empty Send -> Tier A suggestions shown, nothing seeded
    assert "The one open bank question?" in _text_content(controls)

    send.on_click(object())  # second empty Send -> discover a question, do NOT research it
    _run_recorded_task(page)

    angles = PolicyBundle.load(ScoutMode.QA).general_angles["qa"]
    intent_field = next(
        node for node in _walk(controls) if getattr(node, "key", None) == "scout-intent"
    )
    assert intent_field.value == angles[0]
    # No session was ever created — the two monkeypatched functions above would have
    # raised if either had been called, and no session directory was written to disk.
    assert not root.exists() or not any(root.iterdir())


# ─── Delete a research session ──────────────────────────────────────────────
# Compact confirm (mode + intent + created date is enough — sessions are small, ~272K
# for six), then hard-delete. Master's chosen safety model: confirm then rmtree, no
# trash, no type-the-name. No test here may delete anything real — every session lives
# under tmp_path.

def test_deleting_the_currently_loaded_session_clears_scout_session_id(tmp_path):
    root = tmp_path / "research_sessions"
    store = SessionStore(root)
    session = store.create(ScoutMode.QA, "Who has beaten Superman in a fight?")

    page, controls = _build(tmp_path, session)

    delete_icon = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(object())
    assert page.dialogs, "clicking the delete affordance must open a confirm dialog"

    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")
    confirm.on_click(object())

    assert not store.session_dir(session.id).exists()
    # The screen must fall back to its empty state rather than keep pointing at a
    # session directory that no longer exists.
    assert "No unfinished research sessions." in _text_content(controls)


def test_deleting_the_currently_loaded_session_clears_state_scout_session_id(tmp_path):
    root = tmp_path / "research_sessions"
    s1_research_scout.RESEARCH_SESSIONS_ROOT = root
    bridge.RESEARCH_SESSIONS_ROOT = root
    store = SessionStore(root)
    session = store.create(ScoutMode.MICRO, "Hulk moment")

    page = FakePage()
    state = AppState(scout_session_id=session.id)
    controls = s1_research_scout.build(
        page, state, on_go=lambda _stage: None, on_state_change=lambda: None,
    )

    delete_icon = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(object())
    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")
    confirm.on_click(object())

    assert state.scout_session_id == ""


def test_delete_confirm_dialog_shows_mode_intent_and_is_irreversible_warning(tmp_path):
    root = tmp_path / "research_sessions"
    store = SessionStore(root)
    session = store.create(ScoutMode.QA, "Who has beaten Superman in a fight?")

    page, controls = _build(tmp_path, session)
    delete_icon = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(object())

    dialog_text = _text_content(page.dialogs[-1])
    assert "QA" in dialog_text
    assert "Who has beaten Superman in a fight?" in dialog_text
    assert "cannot be undone" in dialog_text.lower()


def test_cancelling_the_session_delete_dialog_leaves_the_session_on_disk(tmp_path):
    root = tmp_path / "research_sessions"
    store = SessionStore(root)
    session = store.create(ScoutMode.QA, "Who has beaten Superman in a fight?")

    page, controls = _build(tmp_path, session)
    delete_icon = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(object())
    cancel = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Cancel")
    cancel.on_click(object())

    assert store.session_dir(session.id).exists()
