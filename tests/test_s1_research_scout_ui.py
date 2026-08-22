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

    def run_task(self, *args, **kwargs):
        self.tasks.append((args, kwargs))

    def update(self):
        pass


def _walk(control):
    if control is None:
        return
    yield control
    for child in getattr(control, "controls", None) or []:
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


def test_second_empty_send_after_bank_shown_skips_tier_a_and_uses_tier_b(tmp_path, monkeypatch):
    """The first empty Send must only SHOW the bank suggestions, spending nothing. A
    second empty Send is the user explicitly declining every suggestion shown — it
    must not silently re-seed suggestions[0] (the exact "always angles[0]" bug this
    whole fallback exists to avoid, just relocated to Tier A) and must instead reach
    Tier B (angle rotation) via start_scout_session(..., skip_bank=True)."""
    root = tmp_path / "research_sessions"
    s1_research_scout.RESEARCH_SESSIONS_ROOT = root
    bridge.RESEARCH_SESSIONS_ROOT = root
    monkeypatch.setattr("stages.research_scout.bank_fallback._REPO_ROOT", tmp_path)
    (tmp_path / "qa_question_bank.md").write_text(
        "| Status | Question | Answer items (comic, year) | Notes |\n"
        "|--------|----------|----------------------------|-------|\n"
        "| SAVE-FOR-LATER | The one open bank question? | item | note |\n",
        encoding="utf-8",
    )
    (tmp_path / "qa_question_banlist.md").write_text(
        "| Date | Question | Reason |\n|------|----------|--------|\n", encoding="utf-8",
    )

    seeded_intents = []
    real_start = bridge.start_scout_session

    def _spy_start(mode, user_intent, **kwargs):
        session = real_start(mode, user_intent, **kwargs)
        seeded_intents.append(session.user_intent)
        return session

    monkeypatch.setattr(s1_research_scout, "start_scout_session", _spy_start)
    monkeypatch.setattr(
        s1_research_scout, "run_scout_general",
        lambda session_id: bridge.load_scout_session(session_id, root=root),
    )

    page, controls = _build(tmp_path)
    send = next(node for node in _walk(controls) if getattr(node, "key", None) == "chat-send")

    send.on_click(object())  # first empty Send -> Tier A suggestions shown, nothing seeded
    assert seeded_intents == []
    assert "The one open bank question?" in _text_content(controls)

    send.on_click(object())  # second empty Send -> explicit ask, must skip Tier A
    _run_recorded_task(page)

    angles = PolicyBundle.load(ScoutMode.QA).general_angles["qa"]
    assert seeded_intents == [angles[0]]
    assert seeded_intents[0] != "The one open bank question?"
