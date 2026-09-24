import asyncio

import flet as ft

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import ui.bridge as bridge
import ui.screens.s1_research_scout as s1_research_scout
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.research_scout.policies import PolicyBundle
from stages.research_scout.storage import SessionStore
from tests.ui_test_doubles import StrictFakePage as FakePage
from ui import app
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


def test_returned_session_reloads_its_selected_cards_and_original_custom_slug(tmp_path):
    """The persisted return marker is narrow: it restores this session only."""
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="returned-session", mode=ScoutMode.QA, user_intent="Superman fight questions",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)
    store.write_artifact(session.id, "general/candidates.v1.json", {
        "candidates": [{"id": candidate_id, "title": candidate_id.upper()}
                       for candidate_id in ("a", "b", "c")],
    })
    s1_research_scout.RESEARCH_SESSIONS_ROOT = tmp_path / "research_sessions"
    bridge.RESEARCH_SESSIONS_ROOT = tmp_path / "research_sessions"
    state = AppState(
        project_name="my-custom-slug", scout_session_id=session.id,
        returned_scout_project="my-custom-slug", returned_scout_session_id=session.id,
    )
    page = FakePage()
    controls = s1_research_scout.build(
        page, state, on_go=lambda _stage: None, on_state_change=lambda: None,
    )

    selected = [node for node in _walk(controls)
                if isinstance(node, ft.Checkbox) and str(getattr(node, "key", "")).startswith("select-")]
    assert [node.value for node in selected] == [True, True, True]

    # Move the same restored session to the production form, as happens after
    # the user re-approves its saved candidate selection.
    session.state = SessionState.PRODUCTION_GATES
    store.save(session)
    controls = s1_research_scout.build(
        page, state, on_go=lambda _stage: None, on_state_change=lambda: None,
    )
    slug = next(node for node in _walk(controls) if getattr(node, "key", None) == "project-slug")
    assert slug.value == "my-custom-slug"


def test_approve_is_disabled_for_qa_with_two_selected_items(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-session",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW,
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
        if getattr(node, "key", None) == "approve-selected"
    )
    assert approve.disabled is True


def test_approve_passes_the_three_current_ui_selections_to_the_workflow(tmp_path, monkeypatch):
    """The current checkbox ticks, rather than stale disk state, own approval."""
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-current-selection", mode=ScoutMode.QA, user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW,
    )
    store.save(session)
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [{"id": candidate_id, "title": candidate_id.upper()}
                        for candidate_id in ("a", "b", "c")]},
    )
    captured = {}

    def approve(session_id, candidate_ids=None):
        captured["session_id"] = session_id
        captured["candidate_ids"] = candidate_ids
        updated = store.load(session_id)
        updated.selected_specific_candidate_ids = list(candidate_ids or [])
        updated.state = SessionState.PRODUCTION_GATES
        return store.save(updated)

    monkeypatch.setattr(s1_research_scout, "approve_scout_selection", approve)
    page, controls = _build(tmp_path, session)
    for candidate_id in ("a", "b", "c"):
        _by_key(controls, f"select-{candidate_id}").on_change(_FakeEvent(True))
    _by_key(controls, "approve-selected").on_click(object())
    _run_recorded_task(page)

    assert captured == {"session_id": session.id, "candidate_ids": ["a", "b", "c"]}


def test_tick_order_is_the_video_order_and_each_tick_shows_its_number(tmp_path, monkeypatch):
    """Item N is downloaded as chapter N and narrated as paragraph N, so the order the
    items are ticked in is the order the video tells them — not the ids' alphabet."""
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-tick-order", mode=ScoutMode.QA, user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW,
    )
    store.save(session)
    ids = ("candidate-2", "candidate-10", "candidate-1", "candidate-3")
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [{"id": candidate_id, "title": candidate_id.upper()} for candidate_id in ids]},
    )
    captured = {}

    def approve(session_id, candidate_ids=None):
        captured["candidate_ids"] = candidate_ids
        updated = store.load(session_id)
        updated.selected_specific_candidate_ids = list(candidate_ids or [])
        updated.state = SessionState.PRODUCTION_GATES
        return store.save(updated)

    monkeypatch.setattr(s1_research_scout, "approve_scout_selection", approve)
    page, controls = _build(tmp_path, session)
    for candidate_id in ("candidate-10", "candidate-3", "candidate-1", "candidate-2"):
        _by_key(controls, f"select-{candidate_id}").on_change(_FakeEvent(True))
    _by_key(controls, "select-candidate-3").on_change(_FakeEvent(False))   # later ticks move up

    labels = {node.key: node.label for node in _walk(controls)
              if isinstance(node, ft.Checkbox) and str(getattr(node, "key", "")).startswith("select-")}
    assert labels == {"select-candidate-10": "#1", "select-candidate-1": "#2",
                      "select-candidate-2": "#3", "select-candidate-3": "Select"}

    _by_key(controls, "approve-selected").on_click(object())
    _run_recorded_task(page)
    assert captured["candidate_ids"] == ["candidate-10", "candidate-1", "candidate-2"]


def test_bubbles_from_an_earlier_round_show_titles_not_raw_ids(tmp_path):
    """A verify/approve bubble names candidates of the round it happened in; once a
    re-run replaced that round, the current list no longer has them — the archive does."""
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-old-round-titles", mode=ScoutMode.QA, user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW, revision=2,
    )
    store.save(session)
    store.append_audit(session.id, "candidates_verified", detail={
        "candidate_ids": ["candidate-1"], "verdicts": {"candidate-1": "inconclusive"}})
    store.append_audit(session.id, "selection_approved", detail={"candidate_ids": ["candidate-1"]})
    store.write_artifact(session.id, "general/candidates.rev1.v1.json",
                         {"candidates": [{"id": "candidate-1", "title": "Round One Title"}]})
    store.write_artifact(session.id, "general/candidates.v1.json",
                         {"candidates": [{"id": "r2-candidate-1", "title": "Round Two Title"}]})
    _page, controls = _build(tmp_path, session)
    text = _text_content(controls)
    assert "Round One Title: INCONCLUSIVE" in text
    assert "#1 Round One Title" in text
    assert "candidate-1:" not in text


def test_right_rail_scrolls_so_every_session_stays_reachable(tmp_path):
    _page, controls = _build(tmp_path)
    rails = [n for n in _walk(controls) if isinstance(n, ft.Column)
             and any(isinstance(c, ft.Text) and c.value == "UNFINISHED SESSIONS" for c in n.controls)]
    assert rails and rails[0].scroll == ft.ScrollMode.AUTO


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
        state=SessionState.CANDIDATE_REVIEW,
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
    # One selection, so one list of checkboxes — not a radio round followed by an
    # identical checkbox round over the very same candidates.
    assert [cb.key for cb in _walk(controls) if isinstance(cb, ft.Checkbox)] == [
        "select-r2a", "select-r2b",
    ]
    assert not any(
        isinstance(n, ft.ElevatedButton)
        and getattr(n, "content", None) == "Approve & find evidence →"
        for n in _walk(controls)
    )


def test_candidate_review_renders_verdicts_checkboxes_and_the_two_buttons(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-session-specific",
        mode=ScoutMode.QA,
        user_intent="Who has beaten Superman in a fight?",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)
    store.append_audit(session.id, "session_created")
    store.append_audit(
        session.id, "general_research_completed",
        detail={"revision": 1, "prompt_hash": "x", "source_api": "research", "effort": "standard"},
    )
    store.append_audit(
        session.id, "candidates_verified",
        detail={"model": "m", "candidate_ids": ["a", "b", "c"]},
    )
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [
            {"id": "a", "title": "A"}, {"id": "b", "title": "B"}, {"id": "c", "title": "C"},
        ]},
    )
    store.write_artifact(
        session.id, "specific/evidence_gate.v1.json",
        {"gates": [
            {"candidate_id": "a", "verdict": "confirmed", "reason": "Backed by two sources.",
             "evidence_urls": [], "reader_url": "", "flags": []},
            {"candidate_id": "b", "verdict": "confirmed", "reason": "Also backed.",
             "evidence_urls": [], "reader_url": "", "flags": []},
            {"candidate_id": "c", "verdict": "confirmed", "reason": "Backed too.",
             "evidence_urls": [], "reader_url": "", "flags": []},
        ]},
    )

    _page, controls = _build(tmp_path, session)
    text = _text_content(controls)

    assert "CONFIRMED" in text
    assert "Backed by two sources." in text
    checkboxes = [n for n in _walk(controls) if isinstance(n, ft.Checkbox)]
    assert len(checkboxes) == 3
    approve = next(
        node for node in _walk(controls) if getattr(node, "key", None) == "approve-selected"
    )
    assert approve.disabled is False
    verify = next(
        node for node in _walk(controls) if getattr(node, "key", None) == "verify-selected"
    )
    # All three already hold a gate, and verify_selected buys a verdict once, so
    # there is nothing left for this button to fetch. Re-verify is per card.
    assert "0" in _label(verify)
    assert verify.disabled is True


def test_the_verify_button_counts_only_the_cards_it_would_actually_gate(tmp_path):
    """The button used to count the ticks. verify_selected skips a candidate
    that already holds a gate, so counting ticks promised verdicts it would not
    go and fetch — and after a re-scout that is every carried-over card."""
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-partial",
        mode=ScoutMode.QA,
        user_intent="Which heroes?",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": [
        {"id": "a", "title": "A"}, {"id": "b", "title": "B"}, {"id": "c", "title": "C"},
    ]})
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", {"gates": [
        {"candidate_id": "a", "verdict": "confirmed", "reason": "Held up.",
         "evidence_urls": [], "reader_url": "", "flags": []},
    ]})

    _page, controls = _build(tmp_path, session)
    verify = next(
        node for node in _walk(controls) if getattr(node, "key", None) == "verify-selected"
    )
    # Three ticked, one already gated -> two to buy.
    assert "2" in _label(verify)
    assert verify.disabled is False


def test_each_card_shows_its_own_verdict_and_never_a_neighbours(tmp_path):
    """_candidate_gate used to end in `return gates[0] if len(gates) == 1 else {}`.
    With one gate on disk — which is all the old writer ever wrote — every one of
    ten cards displayed that single candidate's verdict as if it were its own."""
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-verdicts",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=["a"],
    )
    store.save(session)
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]},
    )
    store.write_artifact(
        session.id, "specific/evidence_gate.v1.json",
        {"gates": [{"candidate_id": "a", "verdict": "confirmed",
                    "reason": "Only A was ever gated.", "evidence_urls": [],
                    "reader_url": "", "flags": []}]},
    )

    _page, controls = _build(tmp_path, session)

    cards = {
        node.key: _text_content(node)
        for node in _walk(controls)
        if str(getattr(node, "key", "")).startswith("candidate-card-")
    }
    assert "CONFIRMED" in cards["candidate-card-a"]
    assert "Only A was ever gated." in cards["candidate-card-a"]
    assert "CONFIRMED" not in cards["candidate-card-b"]
    assert "Only A was ever gated." not in cards["candidate-card-b"]


def test_an_unconfirmed_verdict_offers_an_override_before_approving(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-override",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [{"id": c, "title": c.upper()} for c in "abc"]},
    )
    store.write_artifact(
        session.id, "specific/evidence_gate.v1.json",
        {"gates": [
            {"candidate_id": "a", "verdict": "inconclusive", "reason": "Thin.",
             "evidence_urls": [], "reader_url": "", "flags": []},
            {"candidate_id": "b", "verdict": "confirmed", "reason": "Fine.",
             "evidence_urls": [], "reader_url": "", "flags": []},
            {"candidate_id": "c", "verdict": "confirmed", "reason": "Fine.",
             "evidence_urls": [], "reader_url": "", "flags": []},
        ]},
    )

    _page, controls = _build(tmp_path, session)

    assert any(
        getattr(node, "key", None) == "override-gates" for node in _walk(controls)
    ), "an unconfirmed verdict must offer an explicit override"


def test_all_confirmed_means_no_override_checkbox_is_shown(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-no-override",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [{"id": c, "title": c.upper()} for c in "abc"]},
    )
    store.write_artifact(
        session.id, "specific/evidence_gate.v1.json",
        {"gates": [
            {"candidate_id": c, "verdict": "confirmed", "reason": "Fine.",
             "evidence_urls": [], "reader_url": "", "flags": []} for c in "abc"
        ]},
    )

    _page, controls = _build(tmp_path, session)

    assert not any(getattr(node, "key", None) == "override-gates" for node in _walk(controls))


def test_a_failed_card_offers_a_re_verify_of_just_that_candidate(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-reverify",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [{"id": c, "title": c.upper()} for c in "abc"]},
    )
    _page, controls = _build(tmp_path, session)

    assert {
        node.key for node in _walk(controls)
        if str(getattr(node, "key", "")).startswith("reverify-")
    } == {"reverify-a", "reverify-b", "reverify-c"}


def test_production_gates_offers_a_way_back_to_the_candidates(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-back",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.PRODUCTION_GATES,
        selected_specific_candidate_ids=["a", "b", "c"],
    )
    store.save(session)

    _page, controls = _build(tmp_path, session)

    assert any(
        isinstance(n, ft.OutlinedButton) and getattr(n, "content", None) == "← Back to candidates"
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
        state=SessionState.CANDIDATE_REVIEW,
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


# ─── Tier B: a batch of discovered questions to choose from ─────────────────
# The first empty Send only SHOWS the free bank suggestions. A second empty Send is
# the user explicitly declining every one of them, and buys ONE research call — which
# now comes back with a whole batch of questions to pick from plus a re-roll, instead
# of a single take-it-or-leave-it question.
#
# Picking one must NEVER start research. Master 2026-08-28: a straight-through
# discover -> start_scout_session -> run_scout_general spent a SECOND research call
# enumerating answers to a dud lane (a synonym re-skin of an already-rejected bank
# question) before any human saw it — is_burned()'s own docstring in
# stages/youcom_scout.py says the Master-review step after discover is what is
# supposed to catch those. Landing the question in the box and stopping is that
# review step; the human presses Send again, normally, to research it.

_ANGLES = ["times a famous power or rule failed", "who broke a famously unbreakable rule"]


def _batch(*questions):
    return [
        {"question": q, "angle": _ANGLES[i % len(_ANGLES)]}
        for i, q in enumerate(questions)
    ]


def _must_not_be_called(name):
    def _fail(*_a, **_k):
        raise AssertionError(f"{name} must not be called on a discover-only Send")
    return _fail


def _discover_env(tmp_path, monkeypatch, *, batches):
    """A Stage 1 screen whose Tier A bank has one row and whose Tier B discovery
    is scripted. Returns (page, controls, calls) — `calls` records every
    discover_questions() call so a test can assert the re-roll's `exclude`."""
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

    calls = []
    queued = list(batches)

    def _fake_discover(mode, *, count=5, exclude=()):
        calls.append({"mode": mode, "count": count, "exclude": list(exclude)})
        return queued.pop(0) if queued else []

    monkeypatch.setattr(s1_research_scout, "discover_questions", _fake_discover)
    monkeypatch.setattr(
        s1_research_scout, "start_scout_session", _must_not_be_called("start_scout_session")
    )
    monkeypatch.setattr(
        s1_research_scout, "run_scout_general", _must_not_be_called("run_scout_general")
    )
    page, controls = _build(tmp_path)
    return page, controls, calls


def _intent_field(controls):
    return next(
        node for node in _walk(controls) if getattr(node, "key", None) == "scout-intent"
    )


def _send(controls):
    return next(node for node in _walk(controls) if getattr(node, "key", None) == "chat-send")


def _by_key(controls, key):
    return next(node for node in _walk(controls) if getattr(node, "key", None) == key)


def _discover_twice(page, controls):
    """First empty Send (free bank suggestions), then the second one that buys
    the batch, with the recorded coroutine actually executed."""
    send = _send(controls)
    send.on_click(object())
    send.on_click(object())
    _run_recorded_task(page)


def test_second_empty_send_offers_a_batch_of_questions_and_starts_no_session(
    tmp_path, monkeypatch,
):
    root = tmp_path / "research_sessions"
    page, controls, calls = _discover_env(
        tmp_path, monkeypatch,
        batches=[_batch("Who has lifted Mjolnir?", "Whose healing factor failed?",
                        "Who walked off a planet-buster?")],
    )

    send = _send(controls)
    send.on_click(object())  # first empty Send -> Tier A suggestions, spends nothing
    assert "The one open bank question?" in _text_content(controls)
    assert calls == []

    send.on_click(object())  # second empty Send -> ONE research call, a whole batch
    _run_recorded_task(page)

    shown = _text_content(controls)
    for question in ("Who has lifted Mjolnir?", "Whose healing factor failed?",
                     "Who walked off a planet-buster?"):
        assert question in shown
    assert _ANGLES[0] in shown  # every choice is labelled with the angle it came from
    assert len(calls) == 1
    # Nothing is chosen for the user, and nothing is researched.
    assert _intent_field(controls).value == ""
    assert not root.exists() or not any(root.iterdir())


def test_picking_a_question_fills_the_box_and_leaves_the_batch_on_screen(
    tmp_path, monkeypatch,
):
    """Selecting must only land the question in the input box — the human-review
    step. The bubble stays put so the user can change their mind."""
    page, controls, _calls = _discover_env(
        tmp_path, monkeypatch,
        batches=[_batch("Who has lifted Mjolnir?", "Whose healing factor failed?")],
    )
    _discover_twice(page, controls)

    _by_key(controls, "discovered-questions").on_change(_FakeEvent("1"))

    assert _intent_field(controls).value == "Whose healing factor failed?"
    still_shown = _text_content(controls)
    assert "Who has lifted Mjolnir?" in still_shown
    assert "Whose healing factor failed?" in still_shown
    # start_scout_session / run_scout_general are tripwires in _discover_env:
    # either one firing here is the regression this whole flow exists to prevent.


def test_rerolling_costs_one_call_and_excludes_every_question_already_offered(
    tmp_path, monkeypatch,
):
    page, controls, calls = _discover_env(
        tmp_path, monkeypatch,
        batches=[
            _batch("Who has lifted Mjolnir?", "Whose healing factor failed?"),
            _batch("Who survived a Phoenix hit?"),
        ],
    )
    _discover_twice(page, controls)

    _by_key(controls, "discovered-reroll").on_click(object())
    _run_recorded_task(page)

    assert len(calls) == 2  # exactly one more research call, same as before
    assert calls[1]["exclude"] == ["Who has lifted Mjolnir?", "Whose healing factor failed?"]
    shown = _text_content(controls)
    assert "Who survived a Phoenix hit?" in shown
    assert "Who has lifted Mjolnir?" not in shown


def test_a_reroll_that_finds_nothing_new_keeps_the_previous_batch(tmp_path, monkeypatch):
    """Never blank the list: a re-roll that comes back with nothing the user has
    not already turned down (or, You.com down, with only the raw angle) leaves
    the batch exactly where it was and says so in one line."""
    page, controls, _calls = _discover_env(
        tmp_path, monkeypatch,
        batches=[
            _batch("Who has lifted Mjolnir?", "Whose healing factor failed?"),
            [{"question": "times a famous power or rule failed",
              "angle": "times a famous power or rule failed", "fallback": True}],
        ],
    )
    _discover_twice(page, controls)

    _by_key(controls, "discovered-reroll").on_click(object())
    _run_recorded_task(page)

    shown = _text_content(controls)
    assert "Who has lifted Mjolnir?" in shown
    assert "Whose healing factor failed?" in shown
    assert "Nothing new came back" in shown


def test_an_empty_send_with_a_batch_on_screen_spends_nothing_and_says_what_to_do(
    tmp_path, monkeypatch,
):
    """With a batch already offered, Send-on-empty must not quietly re-show the
    bank bubble behind it (a dead click) and must not quietly buy another
    research call either — re-rolling is the button's job, and it says what it
    costs."""
    page, controls, calls = _discover_env(
        tmp_path, monkeypatch,
        batches=[_batch("Who has lifted Mjolnir?", "Whose healing factor failed?")],
    )
    _discover_twice(page, controls)

    _send(controls).on_click(object())

    assert len(calls) == 1  # no second research call
    shown = _text_content(controls)
    assert "Who has lifted Mjolnir?" in shown  # the batch is still the visible bubble
    assert "Pick one of the questions above" in shown


def test_a_dead_youcom_still_offers_the_angle_rather_than_an_empty_bubble(
    tmp_path, monkeypatch,
):
    """End-to-end through the real bridge with the network cut: Tier B's
    never-raises fallback (the rotated angle) still has to reach the chat, which
    is the pre-batch behaviour this must not lose."""
    root = tmp_path / "research_sessions"
    s1_research_scout.RESEARCH_SESSIONS_ROOT = root
    bridge.RESEARCH_SESSIONS_ROOT = root
    monkeypatch.setattr("stages.research_scout.bank_fallback._REPO_ROOT", tmp_path)

    class _NetworkTripwireYouCom:
        # bridge._scout_workflow() builds an uninjected, real YouComClient(), and
        # config.load_dotenv() can put a LIVE key in this process — so without this
        # stub the assertion below would depend on a real network call. Raising
        # forces discover_questions' mandatory fallback, which returns the angle.
        def research(self, *args, **kwargs):
            raise AssertionError("test tried to reach the real You.com client")

    monkeypatch.setattr(
        "stages.research_scout.workflow.YouComClient", lambda *a, **k: _NetworkTripwireYouCom()
    )
    monkeypatch.setattr(
        s1_research_scout, "start_scout_session", _must_not_be_called("start_scout_session")
    )
    monkeypatch.setattr(
        s1_research_scout, "run_scout_general", _must_not_be_called("run_scout_general")
    )

    page, controls = _build(tmp_path)
    _discover_twice(page, controls)

    angles = PolicyBundle.load(ScoutMode.QA).general_angles["qa"]
    assert angles[0] in _text_content(controls)
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


def test_deleting_a_session_that_is_already_gone_still_clears_the_screen(tmp_path):
    """Deleted from the project picker in another tab meanwhile. The rail must end up
    exactly where a normal delete leaves it, not keep a row that points at nothing."""
    root = tmp_path / "research_sessions"
    store = SessionStore(root)
    session = store.create(ScoutMode.QA, "Who has beaten Superman in a fight?")
    page, controls = _build(tmp_path, session)
    delete_icon = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(object())
    store.delete(session.id)

    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")
    confirm.on_click(object())

    assert "No unfinished research sessions." in _text_content(controls)


def test_a_session_delete_that_fails_says_why_on_screen(tmp_path, monkeypatch):
    from utils.fs_remove import FileInUseError

    root = tmp_path / "research_sessions"
    store = SessionStore(root)
    session = store.create(ScoutMode.QA, "Who has beaten Superman in a fight?")

    def _in_use(*_a, **_k):
        raise FileInUseError("Could not delete it: 'audit.jsonl' is still open in another program.")

    monkeypatch.setattr(s1_research_scout, "delete_scout_session", _in_use)
    page, controls = _build(tmp_path, session)
    delete_icon = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(object())
    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")

    confirm.on_click(object())

    assert "audit.jsonl" in _text_content(controls)
    assert store.session_dir(session.id).exists()


# ─── Per-card verification progress ─────────────────────────────────────────

def _review_session(tmp_path, ids=("a", "b", "c")):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-progress",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=list(ids),
    )
    store.save(session)
    store.write_artifact(
        session.id, "general/candidates.v1.json",
        {"candidates": [{"id": c, "title": c.upper()} for c in ids]},
    )
    return session


def _click(controls, key):
    next(n for n in _walk(controls) if getattr(n, "key", None) == key).on_click(object())


def test_verifying_shows_a_spinner_on_each_card_still_in_flight(tmp_path, monkeypatch):
    session = _review_session(tmp_path)
    captured = {}

    def fake_verify(session_id, candidate_ids, *, only=None, on_result=None):
        captured["on_result"] = on_result
        raise AssertionError("not run in this test")

    monkeypatch.setattr(s1_research_scout, "verify_scout_selection", fake_verify)
    _page, controls = _build(tmp_path, session)

    _click(controls, "verify-selected")

    rings = [n for n in _walk(controls) if isinstance(n, ft.ProgressRing)]
    assert rings, "a verify in flight must show progress on the cards"


def test_a_card_that_lands_stops_spinning_before_the_others_do(tmp_path, monkeypatch):
    """on_result fires per candidate from a worker thread. It is what makes the
    progress per-CARD rather than one spinner for the whole batch."""
    session = _review_session(tmp_path)
    holder = {}

    def fake_verify(session_id, candidate_ids, *, only=None, on_result=None):
        holder["on_result"] = on_result
        on_result("a", object())
        return SessionStore(tmp_path / "research_sessions").load(session_id)

    monkeypatch.setattr(s1_research_scout, "verify_scout_selection", fake_verify)
    page, controls = _build(tmp_path, session)

    _click(controls, "verify-selected")
    _run_recorded_task(page)

    assert holder["on_result"] is not None, "the UI must pass on_result through"
    assert not [n for n in _walk(controls) if isinstance(n, ft.ProgressRing)]


def test_a_verify_that_raises_does_not_leave_the_cards_spinning_forever(tmp_path, monkeypatch):
    session = _review_session(tmp_path)

    def exploding_verify(session_id, candidate_ids, *, only=None, on_result=None):
        raise RuntimeError("You.com is down")

    monkeypatch.setattr(s1_research_scout, "verify_scout_selection", exploding_verify)
    page, controls = _build(tmp_path, session)

    _click(controls, "verify-selected")
    _run_recorded_task(page)

    assert not [n for n in _walk(controls) if isinstance(n, ft.ProgressRing)], (
        "a failed verify must clear its progress, not strand the cards"
    )
    assert "You.com is down" in _text_content(controls)


def test_re_verifying_one_card_sends_the_whole_selection_but_regates_only_that_one(
    tmp_path, monkeypatch
):
    """The artifact must stay one-entry-per-selected-candidate, so the full tick
    set goes in; `only` is what keeps the other four from being paid for again."""
    session = _review_session(tmp_path)
    calls = []

    def fake_verify(session_id, candidate_ids, *, only=None, on_result=None):
        calls.append((list(candidate_ids), list(only) if only is not None else None))
        return SessionStore(tmp_path / "research_sessions").load(session_id)

    monkeypatch.setattr(s1_research_scout, "verify_scout_selection", fake_verify)
    page, controls = _build(tmp_path, session)

    _click(controls, "reverify-b")
    _run_recorded_task(page)

    assert calls == [(["a", "b", "c"], ["b"])]


# ─── Re-scout, keeping what is already confirmed ────────────────────────────
# A verification round usually lands one confirmed and two that are not. This
# button goes looking for replacements for the two while keeping the one — and
# the gating already paid for it. It is hidden when there is nothing to keep,
# because the plain feedback re-run already covers starting the round over.

def _reviewed_session(tmp_path, *, session_id, candidates, gates, selected):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id=session_id,
        mode=ScoutMode.QA,
        user_intent="Who has beaten Superman in a fight?",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=list(selected),
    )
    store.save(session)
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": candidates})
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", {"gates": gates})
    return session


def _gate(candidate_id, verdict):
    return {"candidate_id": candidate_id, "verdict": verdict, "reason": f"{verdict}.",
            "evidence_urls": [], "reader_url": "", "flags": []}


def test_a_mixed_verification_offers_to_rescout_and_says_what_it_costs(tmp_path):
    session = _reviewed_session(
        tmp_path,
        session_id="qa-rescout-offer",
        candidates=[{"id": c, "title": c.upper()} for c in "abc"],
        gates=[_gate("a", "confirmed"), _gate("b", "inconclusive"), _gate("c", "rejected")],
        selected=["a", "b", "c"],
    )
    _page, controls = _build(tmp_path, session)

    button = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == "rescout-keep-confirmed"
    )
    assert "Re-scout, keep confirmed (1)" == _label(button)
    assert "costs one research call" in _text_content(controls)


def test_nothing_confirmed_means_nothing_to_keep_and_no_button(tmp_path):
    session = _reviewed_session(
        tmp_path,
        session_id="qa-rescout-hidden",
        candidates=[{"id": c, "title": c.upper()} for c in "ab"],
        gates=[_gate("a", "inconclusive"), _gate("b", "rejected")],
        selected=["a", "b"],
    )
    _page, controls = _build(tmp_path, session)

    assert not any(
        getattr(node, "key", None) == "rescout-keep-confirmed" for node in _walk(controls)
    )


def test_an_unverified_round_offers_no_rescout(tmp_path):
    session = _reviewed_session(
        tmp_path,
        session_id="qa-rescout-unverified",
        candidates=[{"id": c, "title": c.upper()} for c in "ab"],
        gates=[],
        selected=[],
    )
    _page, controls = _build(tmp_path, session)

    assert not any(
        getattr(node, "key", None) == "rescout-keep-confirmed" for node in _walk(controls)
    )


def test_clicking_rescout_asks_the_bridge_for_exactly_that_session(tmp_path, monkeypatch):
    session = _reviewed_session(
        tmp_path,
        session_id="qa-rescout-click",
        candidates=[{"id": c, "title": c.upper()} for c in "abc"],
        gates=[_gate("a", "confirmed"), _gate("b", "inconclusive"), _gate("c", "rejected")],
        selected=["a", "b", "c"],
    )
    calls = []

    def _fake_rescout(session_id):
        calls.append(session_id)
        return ResearchSession(
            id=session_id,
            mode=ScoutMode.QA,
            user_intent=session.user_intent,
            state=SessionState.CANDIDATE_REVIEW,
            revision=2,
            selected_specific_candidate_ids=["a"],
        )

    monkeypatch.setattr(s1_research_scout, "rescout_keeping_confirmed", _fake_rescout)
    page, controls = _build(tmp_path, session)

    _by_key(controls, "rescout-keep-confirmed").on_click(object())
    _run_recorded_task(page)

    assert calls == [session.id]


def test_the_screen_stops_claiming_a_selection_the_session_no_longer_holds(
    tmp_path, monkeypatch,
):
    """After a re-scout the selection is just the kept candidate. Leaving the
    three ticks from the old round in the screen's own state would keep Approve
    enabled over candidates that no longer exist."""
    session = _reviewed_session(
        tmp_path,
        session_id="qa-rescout-resync",
        candidates=[{"id": c, "title": c.upper()} for c in "abc"],
        gates=[_gate("a", "confirmed"), _gate("b", "inconclusive"), _gate("c", "rejected")],
        selected=["a", "b", "c"],
    )
    store = SessionStore(tmp_path / "research_sessions")

    def _fake_rescout(session_id):
        store.write_artifact(
            session_id, "general/candidates.v1.json",
            {"candidates": [{"id": "a", "title": "A"},
                            {"id": "r2-candidate-1", "title": "Fresh One"},
                            {"id": "r2-candidate-2", "title": "Fresh Two"}]},
        )
        after = ResearchSession(
            id=session_id, mode=ScoutMode.QA, user_intent=session.user_intent,
            state=SessionState.CANDIDATE_REVIEW, revision=2,
            selected_specific_candidate_ids=["a"],
        )
        return store.save(after)

    monkeypatch.setattr(s1_research_scout, "rescout_keeping_confirmed", _fake_rescout)
    page, controls = _build(tmp_path, session)

    _by_key(controls, "rescout-keep-confirmed").on_click(object())
    _run_recorded_task(page)

    ticked = [cb.key for cb in _walk(controls) if isinstance(cb, ft.Checkbox) and cb.value]
    assert ticked == ["select-a"]
    assert _by_key(controls, "approve-selected").disabled is True


def test_a_candidate_with_no_id_cannot_borrow_another_rounds_verdict(tmp_path):
    """The screen's fallback id used to be `candidate-{index}` — the very shape
    the workflow issues — so an id-less candidate sitting next to one carried
    over from round 1 could be handed that round's confirmed gate."""
    session = _reviewed_session(
        tmp_path,
        session_id="qa-rescout-noid",
        candidates=[
            {"id": "candidate-1", "title": "Kept From Round One"},
            {"title": "No Id At All"},
        ],
        gates=[_gate("candidate-1", "confirmed")],
        selected=["candidate-1"],
    )
    _page, controls = _build(tmp_path, session)

    cards = {
        node.key: _text_content(node)
        for node in _walk(controls)
        if str(getattr(node, "key", "")).startswith("candidate-card-")
    }
    kept = cards.pop("candidate-card-candidate-1")
    assert "CONFIRMED" in kept
    (other,) = cards.values()
    assert "No Id At All" in other
    assert "CONFIRMED" not in other


def test_the_transcript_records_the_rescout_rather_than_skipping_it(tmp_path):
    """Every round, approval and verdict is a bubble rebuilt from disk. A
    re-scout spends a research call and changes the selection, so a transcript
    that stays silent about it is not the record it claims to be."""
    session = _reviewed_session(
        tmp_path,
        session_id="qa-rescout-transcript",
        candidates=[{"id": "a", "title": "A"}],
        gates=[_gate("a", "confirmed")],
        selected=["a"],
    )
    store = SessionStore(tmp_path / "research_sessions")
    store.append_audit(session.id, "session_created")
    store.append_audit(
        session.id, "rescout_keeping_confirmed",
        detail={"kept": ["a"], "dropped": ["b", "c"]},
    )

    _page, controls = _build(tmp_path, session)
    text = _text_content(controls)

    assert "keeping 1 confirmed" in text
    assert "replacing 2" in text


def test_unconfirmed_selection_offers_rescout_keeping_selected(tmp_path, monkeypatch):
    """When a user likes an inconclusive or unverified candidate, they can keep it
    and scout for more candidates into the next round."""
    session = _reviewed_session(
        tmp_path,
        session_id="qa-keep-selected-inconclusive",
        candidates=[{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
        gates=[_gate("a", "inconclusive"), _gate("b", "rejected")],
        selected=["a"],
    )
    calls = []

    def _fake_rescout_selected(session_id, ids):
        calls.append((session_id, ids))
        return session

    monkeypatch.setattr(s1_research_scout, "rescout_keeping_selected", _fake_rescout_selected)
    page, controls = _build(tmp_path, session)

    button = _by_key(controls, "rescout-keep-selected")
    assert "Keep selected & scout more (1)" in _label(button)

    button.on_click(object())
    _run_recorded_task(page)
    assert calls == [("qa-keep-selected-inconclusive", ["a"])]


def test_all_scout_text_is_selectable(tmp_path):
    """Every text element in Stage 1 scout UI must have selectable=True so users can copy text."""
    import ast
    from pathlib import Path

    # 1. AST check: ensure every ft.Text instantiation in s1_research_scout.py explicitly passes selectable=True
    source_path = Path(s1_research_scout.__file__)
    tree = ast.parse(source_path.read_text("utf-8"))

    class TextAstVisitor(ast.NodeVisitor):
        def __init__(self):
            self.failures = []

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "Text":
                selectable_kw = [kw for kw in node.keywords if kw.arg == "selectable"]
                if not selectable_kw:
                    self.failures.append(f"Line {node.lineno}: ft.Text is missing selectable=True")
                elif not getattr(selectable_kw[0].value, "value", None) is True:
                    self.failures.append(f"Line {node.lineno}: ft.Text has selectable != True")
            self.generic_visit(node)

    visitor = TextAstVisitor()
    visitor.visit(tree)
    assert not visitor.failures, "\n".join(visitor.failures)

    # 2. Runtime check on rendered scout controls (center transcript & right rail, excluding the global nav stepper)
    session = _reviewed_session(
        tmp_path,
        session_id="qa-selectable-test",
        candidates=[{"id": "a", "title": "Comic Alpha", "summary": "Visual summary"}],
        gates=[_gate("a", "confirmed")],
        selected=["a"],
    )
    _page, controls = _build(tmp_path, session)

    unselectable_texts = []
    for scout_container in controls.controls[1:]:
        for node in _walk(scout_container):
            if isinstance(node, ft.Text):
                if not getattr(node, "selectable", False):
                    unselectable_texts.append(f"Unselectable text found: {node.value!r}")

    assert not unselectable_texts, "\n".join(unselectable_texts)

