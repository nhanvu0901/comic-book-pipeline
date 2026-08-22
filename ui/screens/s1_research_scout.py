"""Stage 1 Research Scout UI.

Redesigned as a chat: every research round, approval, feedback and verdict is a
chat bubble rebuilt from durable disk state (session.json + audit.jsonl +
artifacts). The only in-memory state kept across renders is UI-only selection
(which candidate is radio'd / checked) and the busy flag — never a parallel
log of chat messages. Typing feedback re-runs the current phase with that
feedback threaded into the research prompt (see workflow._intent_with_feedback).
"""
from __future__ import annotations

from typing import Callable

import flet as ft

from config import RESEARCH_SESSIONS_ROOT
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.stage_1.storage import slugify

from ..bridge import (
    archive_scout_session,
    approve_scout_general,
    approve_scout_specific,
    back_scout_general,
    create_scout_project,
    format_exception,
    list_scout_sessions,
    load_scout_audit,
    load_scout_candidates,
    load_scout_candidates_rev,
    load_scout_gates,
    load_scout_session,
    list_bank_suggestions,
    rerun_scout_general,
    run_blocking,
    run_scout_general,
    run_scout_specific,
    start_scout_session,
)
from ..layout import primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)


# Kept as a module value so headless tests and LAN callers can point the screen
# at an isolated session store without creating a project.
RESEARCH_SESSIONS_ROOT = RESEARCH_SESSIONS_ROOT

_BUBBLE_WIDTH = 640


def _session_for_ui(session_id: str) -> ResearchSession | None:
    if not session_id:
        return None
    try:
        return load_scout_session(session_id, root=RESEARCH_SESSIONS_ROOT)
    except Exception:
        return None


def _candidate_id(candidate: dict, index: int) -> str:
    value = candidate.get("id")
    return str(value).strip() if value is not None and str(value).strip() else f"candidate-{index}"


def _candidate_gate(candidate_id: str, gates: list[dict]) -> dict:
    for gate in gates:
        if str(gate.get("candidate_id") or gate.get("id") or "") == candidate_id:
            return gate
    return gates[0] if len(gates) == 1 else {}


def _candidate_card(
    candidate: dict,
    index: int,
    *,
    gate: dict,
    selection: ft.Control,
) -> ft.Control:
    candidate_id = _candidate_id(candidate, index)
    title = str(candidate.get("title") or candidate.get("entity") or candidate_id)
    summary = str(
        candidate.get("summary")
        or candidate.get("how_or_why")
        or candidate.get("visible_event")
        or "No summary provided."
    )
    urls = candidate.get("evidence_urls") or []
    if isinstance(urls, str):
        urls = [urls]
    reader_url = candidate.get("reader_url") or gate.get("reader_url")
    if reader_url and reader_url not in urls:
        urls = [*urls, reader_url]
    flags = [str(flag) for flag in (candidate.get("flags") or [])]
    flags.extend(str(flag) for flag in (gate.get("flags") or []))
    details: list[ft.Control] = [
        ft.Text(title, size=14, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
        ft.Text(summary, size=12, color=TEXT_MUTED, selectable=True),
    ]
    if candidate.get("series_issue_year"):
        details.append(ft.Text(str(candidate["series_issue_year"]), size=11, color=TEXT_PRIMARY))
    for url in urls:
        details.append(ft.Text(f"Source: {url}", size=10, color=ACCENT, selectable=True))
    if flags:
        details.append(ft.Text(f"Flags: {', '.join(flags)}", size=10, color=WARN, selectable=True))
    return ft.Container(
        key=f"candidate-card-{candidate_id}",
        content=ft.Row([selection, ft.Column(details, spacing=4, expand=True)], spacing=10),
        padding=12,
        bgcolor=BG_ELEVATED,
        border=ft.border.all(1, BORDER),
        border_radius=8,
    )


def _selection_count_valid(mode: ScoutMode, selected: set[str]) -> bool:
    return (3 <= len(selected) <= 5) if mode is ScoutMode.QA else len(selected) == 1


def _resolve_title(candidate_id: str, candidates: list[dict]) -> str:
    for index, candidate in enumerate(candidates):
        if _candidate_id(candidate, index) == candidate_id:
            return str(candidate.get("title") or candidate.get("entity") or candidate_id)
    return candidate_id


def _current_general_round_index(completed_events: list[dict], session: ResearchSession) -> int | None:
    """Pick which general_research_completed event is the CURRENT (non-superseded) round.

    Legacy audits written before the revision field existed carry no
    detail.revision at all — treat the LAST such event as current, earlier
    ones as superseded, per spec.
    """
    if not completed_events:
        return None
    revisions = [(event.get("detail") or {}).get("revision") for event in completed_events]
    if all(revision is None for revision in revisions):
        return len(completed_events) - 1
    matches = [index for index, revision in enumerate(revisions) if revision == session.revision]
    return matches[-1] if matches else len(completed_events) - 1


def _gate_content(gates: list[dict]) -> list[ft.Control]:
    gate = gates[0] if gates else {}
    verdict = str(gate.get("verdict") or "inconclusive")
    reason = str(gate.get("reason") or "")
    flags = [str(flag) for flag in (gate.get("flags") or [])]
    color = SUCCESS if verdict == "confirmed" else (WARN if verdict == "inconclusive" else DANGER)
    content: list[ft.Control] = [
        ft.Text(f"Verdict: {verdict.upper()}", size=13, color=color, weight=ft.FontWeight.BOLD),
    ]
    if reason:
        content.append(ft.Text(reason, size=12, color=TEXT_MUTED, selectable=True))
    if flags:
        content.append(ft.Text(f"Flags: {', '.join(flags)}", size=11, color=WARN))
    return content


def _general_collapsed_lines(session: ResearchSession, candidates: list[dict]) -> ft.Control:
    approved_id = session.selected_general_candidate_id
    lines: list[ft.Control] = []
    for index, candidate in enumerate(candidates):
        candidate_id = _candidate_id(candidate, index)
        title = str(candidate.get("title") or candidate.get("entity") or candidate_id)
        approved = candidate_id == approved_id
        prefix = "✓ " if approved else "• "
        lines.append(ft.Text(f"{prefix}{title}", size=12, color=SUCCESS if approved else TEXT_MUTED))
    if not lines:
        lines.append(ft.Text("No candidates recorded for this round.", size=12, color=TEXT_MUTED))
    return ft.Column(lines, spacing=4)


def _input_spec(session: ResearchSession | None) -> tuple[str, bool]:
    """Return (hint_text, disabled) shared by the intent field and Send."""
    if session is None or session.state in {SessionState.ARCHIVED, SessionState.COMPLETE}:
        return (
            "Ask a comic question, describe one visual moment, or press Send "
            "empty for open-bank suggestions.",
            False,
        )
    if session.state is SessionState.GENERAL_DRAFT:
        return "Press Run general research above.", True
    if session.state is SessionState.GENERAL_REVIEW:
        return "Type feedback and press Send to re-run research…", False
    if session.state is SessionState.SPECIFIC_REVIEW:
        return "Type feedback to redo the evidence search…", False
    if session.state is SessionState.PRODUCTION_GATES:
        return "Create the project or archive.", True
    return "", True


def _user_bubble(content: ft.Control) -> ft.Control:
    return ft.Row([
        ft.Container(
            content=content,
            bgcolor=BG_ELEVATED,
            border=ft.border.all(1, ACCENT),
            border_radius=10,
            padding=12,
            width=_BUBBLE_WIDTH,
        ),
    ], alignment=ft.MainAxisAlignment.END)


def _scout_bubble(content: ft.Control) -> ft.Control:
    return ft.Row([
        ft.Container(
            content=ft.Column([
                ft.Text("SCOUT", size=9, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
                content,
            ], spacing=6),
            bgcolor=BG_PANEL,
            border=ft.border.all(1, BORDER),
            border_radius=10,
            padding=12,
            width=_BUBBLE_WIDTH,
        ),
    ], alignment=ft.MainAxisAlignment.START)


def _system_bubble(text: str, *, danger: bool = False) -> ft.Control:
    return ft.Row([
        ft.Text(text, size=11, color=DANGER if danger else TEXT_MUTED,
                text_align=ft.TextAlign.CENTER),
    ], alignment=ft.MainAxisAlignment.CENTER)


def _progress_bubble(label: str) -> ft.Control:
    return _scout_bubble(ft.Row([
        ft.ProgressRing(width=14, height=14, stroke_width=2),
        ft.Text(label, size=12, color=TEXT_MUTED),
    ], spacing=10))


def _session_created_bubble(session: ResearchSession) -> ft.Control:
    chip = ft.Container(
        content=ft.Text(session.mode.value.upper(), size=9, color=TEXT_MUTED,
                        weight=ft.FontWeight.BOLD),
        padding=ft.padding.symmetric(horizontal=6, vertical=2),
        border=ft.border.all(1, BORDER),
        border_radius=4,
    )
    return _user_bubble(ft.Column([
        ft.Text(session.user_intent, size=13, color=TEXT_PRIMARY, selectable=True),
        chip,
    ], spacing=6))


def _rerun_bubble(detail: dict) -> ft.Control:
    feedback = str(detail.get("feedback") or "").strip()
    text = feedback if feedback else "Re-run requested."
    return _user_bubble(ft.Text(text, size=13, color=TEXT_PRIMARY, selectable=True))


def _general_approved_bubble(detail: dict, candidates: list[dict]) -> ft.Control:
    title = _resolve_title(str(detail.get("candidate_id") or ""), candidates)
    return _user_bubble(ft.Text(f"Approved: {title}", size=13, color=TEXT_PRIMARY))


def _specific_decided_bubble(detail: dict, candidates: list[dict]) -> ft.Control:
    ids = [str(candidate_id) for candidate_id in (detail.get("candidate_ids") or [])]
    titles = [_resolve_title(candidate_id, candidates) for candidate_id in ids]
    lines: list[ft.Control] = [
        ft.Text(f"Selected: {', '.join(titles) if titles else '(none)'}",
                size=13, color=TEXT_PRIMARY),
    ]
    feedback = str(detail.get("feedback") or "").strip()
    if feedback:
        lines.append(ft.Text(f"Feedback: {feedback}", size=12, color=TEXT_MUTED))
    return _user_bubble(ft.Column(lines, spacing=4))


def _archived_bubble(detail: dict) -> ft.Control:
    reason = str(detail.get("reason") or "")
    return _system_bubble(f"Session archived — {reason}" if reason else "Session archived.")


def _bank_suggestions_bubble(suggestions: list[dict]) -> ft.Control:
    """Tier A of the empty-intent fallback, shown for free before any research
    round runs. Master 2026-08-22: Send-with-empty-box must not silently spend
    API budget, so this is a dead-end by design — nothing here starts a
    session. Typing one of these into the box (or anything else) and pressing
    Send runs the normal flow; pressing Send empty AGAIN explicitly asks for a
    fresh-angle research round instead (Tier B)."""
    lines: list[ft.Control] = [
        ft.Text(
            "Still-open questions from qa_question_bank.md — type one into the box "
            "and press Send, or press Send empty again to research a fresh angle.",
            size=12, color=TEXT_MUTED,
        ),
    ]
    for row in suggestions:
        lines.append(ft.Text(
            f"[{row.get('status', '')}] {row.get('question', '')}",
            size=12, color=TEXT_PRIMARY, selectable=True,
        ))
    return _scout_bubble(ft.Column(lines, spacing=6))


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    session_holder: list[ResearchSession | None] = [_session_for_ui(state.scout_session_id)]
    if session_holder[0]:
        state.scout_mode = session_holder[0].mode.value
        if not state.last_prompt:
            state.last_prompt = session_holder[0].user_intent
    selected_general: list[str] = [
        session_holder[0].selected_general_candidate_id or "" if session_holder[0] else ""
    ]
    selected_specific: set[str] = set(
        session_holder[0].selected_specific_candidate_ids if session_holder[0] else []
    )
    busy = [False]
    slug_holder: list[ft.TextField | None] = [None]
    # Tier A suggestions currently on screen (empty-intent fallback) and whether
    # they've already been shown once for the CURRENT no-session state — a second
    # empty Send is read as an explicit ask to research a fresh angle instead
    # (Tier B), rather than silently spending API budget on the first empty Send.
    bank_suggestions_holder: list[list[dict]] = [[]]
    bank_shown = [False]

    # ─── Chat transcript: rebuilt fresh from disk on every render ─────────

    def _general_review_content(candidates: list[dict], gates: list[dict]) -> ft.Control:
        cards: list[ft.Control] = []
        for index, candidate in enumerate(candidates):
            candidate_id = _candidate_id(candidate, index)
            radio = ft.Radio(value=candidate_id, label="Approve this candidate")
            cards.append(_candidate_card(
                candidate, index, gate=_candidate_gate(candidate_id, gates), selection=radio,
            ))

        def _radio_changed(event):
            if busy[0]:
                return
            selected_general[0] = event.control.value
            _render_full()

        group = ft.RadioGroup(
            value=selected_general[0] or None,
            content=ft.Column(cards, spacing=8),
            on_change=_radio_changed,
        )
        approve_button = primary_button(
            "Approve & find evidence →", _approve_general_click,
            disabled=not selected_general[0],
        )
        return ft.Column([group, approve_button], spacing=10)

    def _general_completed_bubble(
        event: dict, idx: int, is_current: bool, session: ResearchSession,
        candidates: list[dict], gates: list[dict],
    ) -> ft.Control:
        detail = event.get("detail") or {}
        revision = detail.get("revision")
        label_rev = revision if revision is not None else idx + 1
        if not is_current:
            rev_candidates = load_scout_candidates_rev(
                session.id, revision if revision is not None else label_rev,
                root=RESEARCH_SESSIONS_ROOT,
            )
            n = len(rev_candidates)
            count_text = "no candidates" if n == 0 else f"{n} candidates"
            summary = f"Round {label_rev} — {count_text} (superseded)"
            return _scout_bubble(ft.Text(summary, size=12, color=TEXT_MUTED))
        content = (
            _general_review_content(candidates, gates)
            if session.state is SessionState.GENERAL_REVIEW
            else _general_collapsed_lines(session, candidates)
        )
        plan_summary = detail.get("plan_summary")
        if not plan_summary:
            return _scout_bubble(content)
        return _scout_bubble(ft.Column([
            ft.Text(f"Plan: {plan_summary}", size=10, color=TEXT_MUTED),
            content,
        ], spacing=6))

    def _specific_review_content(
        session: ResearchSession, candidates: list[dict], gates: list[dict],
    ) -> ft.Control:
        cards: list[ft.Control] = []
        for index, candidate in enumerate(candidates):
            candidate_id = _candidate_id(candidate, index)
            selection = ft.Checkbox(
                key=f"select-{candidate_id}",
                label="Select",
                value=candidate_id in selected_specific,
                on_change=lambda e, cid=candidate_id: _specific_selection_changed(
                    cid, bool(e.control.value)
                ),
            )
            cards.append(_candidate_card(
                candidate, index, gate=_candidate_gate(candidate_id, gates), selection=selection,
            ))
        approve_button = primary_button(
            "Approve selected →", _approve_specific_click,
            disabled=not _selection_count_valid(session.mode, selected_specific),
            icon=ft.Icons.CHECK,
        )
        approve_button.key = "approve-specific"
        back_button = secondary_button("← Back to general", _back_general_click)
        return ft.Column([
            *cards,
            ft.Row([approve_button, back_button], spacing=10),
        ], spacing=8)

    def _production_gates_bubble(session: ResearchSession) -> ft.Control:
        slug_field = ft.TextField(
            key="project-slug",
            value=slugify(session.user_intent or "untitled_research"),
            label="Project name",
            width=230,
            border_color=BORDER,
            focused_border_color=ACCENT,
        )
        slug_holder[0] = slug_field
        create_button = primary_button(
            "Create project", _create_project_click, icon=ft.Icons.CREATE_NEW_FOLDER,
        )
        return _scout_bubble(ft.Column([
            ft.Text("Production gates passed.", size=13, color=TEXT_PRIMARY),
            ft.Row([slug_field, create_button], spacing=10),
        ], spacing=8))

    def _run_general_bubble() -> ft.Control:
        button = primary_button("Run general research", _run_general_click, icon=ft.Icons.SEARCH)
        return _scout_bubble(ft.Column([
            ft.Text("Ready to run the first research round for this session.",
                    size=12, color=TEXT_MUTED),
            button,
        ], spacing=8))

    def _render_chat() -> list[ft.Control]:
        session = session_holder[0]
        if session is None:
            if bank_suggestions_holder[0]:
                return [_bank_suggestions_bubble(bank_suggestions_holder[0])]
            return [_system_bubble(
                "Pick a mode, type a comic question or describe one visual moment, "
                "then press Send."
            )]

        candidates = load_scout_candidates(session.id, root=RESEARCH_SESSIONS_ROOT)
        gates = load_scout_gates(session.id, root=RESEARCH_SESSIONS_ROOT)
        audit = load_scout_audit(session.id, root=RESEARCH_SESSIONS_ROOT)

        completed_events = [e for e in audit if e.get("event") == "general_research_completed"]
        current_idx = _current_general_round_index(completed_events, session)

        bubbles: list[ft.Control] = []
        general_seen = 0
        for event in audit:
            name = event.get("event")
            detail = event.get("detail") or {}
            if name == "session_created":
                bubbles.append(_session_created_bubble(session))
            elif name == "general_research_completed":
                idx = general_seen
                general_seen += 1
                bubbles.append(_general_completed_bubble(
                    event, idx, idx == current_idx, session, candidates, gates,
                ))
            elif name == "general_research_rerun":
                bubbles.append(_rerun_bubble(detail))
            elif name == "general_candidate_approved":
                bubbles.append(_general_approved_bubble(detail, candidates))
            elif name == "specific_research_completed":
                bubbles.append(_scout_bubble(ft.Column(_gate_content(gates), spacing=6)))
            elif name == "specific_candidates_decided":
                bubbles.append(_specific_decided_bubble(detail, candidates))
            elif name == "session_archived":
                bubbles.append(_archived_bubble(detail))
            # unknown events (returned_to_general_review, project_created, ...): skip silently

        # State-driven appends happen regardless of audit content — this keeps the
        # approve-specific control reachable even for a session seeded directly onto
        # disk without a matching audit trail (a hand-built SPECIFIC_REVIEW fixture,
        # for example), matching the non-negotiable state-based contract for that key.
        if session.state is SessionState.SPECIFIC_REVIEW:
            bubbles.append(_scout_bubble(_specific_review_content(session, candidates, gates)))
        if session.state is SessionState.PRODUCTION_GATES:
            bubbles.append(_production_gates_bubble(session))
        if session.state is SessionState.GENERAL_DRAFT:
            bubbles.append(_run_general_bubble())

        return bubbles

    # ─── Actions: busy-guarded, disk-driven re-render on completion ────────

    def _apply_session_and_render(result) -> None:
        session_holder[0] = result
        intent_field.value = ""
        bank_suggestions_holder[0] = []
        bank_shown[0] = False
        _render_full()

    def _clear_to_new(_result=None) -> None:
        selected_general[0] = ""
        selected_specific.clear()
        session_holder[0] = None
        intent_field.value = ""
        bank_suggestions_holder[0] = []
        bank_shown[0] = False
        _render_full()

    def _finish_create_project(project_name) -> None:
        session = session_holder[0]
        state.project_name = project_name
        state.scout_session_id = ""
        if session:
            state.last_prompt = session.user_intent
            state.pipeline_mode = "explore_answer" if session.mode is ScoutMode.QA else "micro_moment"
        state.mark_approved(1)
        state.current_stage = 2
        save_state(state)
        on_state_change()

    def _run_busy(label: str, work, on_success=None) -> None:
        if busy[0]:
            return
        busy[0] = True
        intent_field.disabled = True
        send_button.disabled = True
        transcript.controls = _render_chat() + [_progress_bubble(label)]
        page.update()

        async def _execute() -> None:
            try:
                result = await run_blocking(work)
            except Exception as exc:
                busy[0] = False
                _render_full(error=format_exception(exc))
                return
            busy[0] = False
            (on_success or _apply_session_and_render)(result)

        page.run_task(_execute)

    def _send_click(_e) -> None:
        if busy[0]:
            return
        session = session_holder[0]
        text = (intent_field.value or "").strip()

        if session is None or session.state in {SessionState.ARCHIVED, SessionState.COMPLETE}:
            mode = mode_group.value or ScoutMode.QA.value
            already_offered = False
            if not text:
                # First empty Send: show Tier A (bank) suggestions for free and stop —
                # do NOT spend API budget without the user asking for it.
                if not bank_shown[0]:
                    suggestions = list_bank_suggestions(mode)
                    if suggestions:
                        bank_shown[0] = True
                        bank_suggestions_holder[0] = suggestions
                        _render_full()
                        return
                # Either the bank had nothing to show (empty for this mode, or
                # nothing left after banlist filtering), or the user has ALREADY
                # seen Tier A once and pressed Send empty again anyway — that is
                # an explicit ask for something OTHER than the listed questions
                # (see _bank_suggestions_bubble's own on-screen copy). Either way
                # this must not consult Tier A again: re-reading the bank here
                # would always hand back suggestions[0], the exact "always
                # angles[0]" bug this fallback exists to avoid, just moved from
                # Tier B to Tier A. skip_bank forces bridge.start_scout_session
                # straight to Tier B (angle rotation) instead.
                already_offered = bank_shown[0]
                bank_shown[0] = False
                bank_suggestions_holder[0] = []
            else:
                bank_shown[0] = False
                bank_suggestions_holder[0] = []
            old = session

            def _work():
                if old is not None and old.state not in {SessionState.ARCHIVED, SessionState.COMPLETE}:
                    archive_scout_session(old.id, "Started a new research session")
                new_session = start_scout_session(mode, text, skip_bank=already_offered)
                return run_scout_general(new_session.id)

            state.scout_mode = mode
            state.last_prompt = text
            _run_busy("Researching… ~30s", _work)
            return

        if session.state is SessionState.GENERAL_REVIEW:
            if not text:
                _render_full(error="Type feedback before sending, or approve a candidate above.")
                return
            _run_busy("Researching… ~30s", lambda: rerun_scout_general(session.id, text))
            return

        if session.state is SessionState.SPECIFIC_REVIEW:
            if not text:
                _render_full(error="Type feedback before sending.")
                return
            _run_busy("Checking evidence…", lambda: run_scout_specific(session.id, text))
            return

        # GENERAL_DRAFT / PRODUCTION_GATES: Send stays disabled; defensive no-op.

    def _mode_changed(_e) -> None:
        state.scout_mode = mode_group.value or ScoutMode.QA.value
        # Tier A suggestions are QA-only content — stale ones from the other
        # mode must not linger, and switching modes counts as a fresh attempt.
        bank_shown[0] = False
        bank_suggestions_holder[0] = []

    def _specific_selection_changed(candidate_id: str, checked: bool) -> None:
        if busy[0]:
            return
        session = session_holder[0]
        if session and session.mode is ScoutMode.MICRO:
            selected_specific.clear()
            if checked:
                selected_specific.add(candidate_id)
        elif checked:
            selected_specific.add(candidate_id)
        else:
            selected_specific.discard(candidate_id)
        _render_full()

    def _approve_general_click(_e) -> None:
        session = session_holder[0]
        candidate_id = selected_general[0]
        if not session or not candidate_id:
            return

        def _work():
            approved = approve_scout_general(session.id, candidate_id)
            return run_scout_specific(approved.id)

        _run_busy("Checking evidence…", _work)

    def _approve_specific_click(_e) -> None:
        session = session_holder[0]
        if not session or not _selection_count_valid(session.mode, selected_specific):
            return
        ids = sorted(selected_specific)
        _run_busy("Saving selection…", lambda: approve_scout_specific(session.id, ids, ""))

    def _back_general_click(_e) -> None:
        session = session_holder[0]
        if not session:
            return
        _run_busy("Returning to general review…", lambda: back_scout_general(session.id))

    def _create_project_click(_e) -> None:
        session = session_holder[0]
        if not session:
            return
        slug_field = slug_holder[0]
        project_slug = (slug_field.value if slug_field else "").strip() or slugify(
            session.user_intent or ""
        )
        if not project_slug:
            _render_full(error="Enter a project name before creating the project.")
            return
        _run_busy(
            "Creating project…",
            lambda: create_scout_project(session.id, project_slug),
            on_success=_finish_create_project,
        )

    def _run_general_click(_e) -> None:
        session = session_holder[0]
        if not session:
            return
        _run_busy("Researching… ~30s", lambda: run_scout_general(session.id))

    def _archive_click(_e) -> None:
        session = session_holder[0]
        if not session:
            return
        _run_busy(
            "Archiving…",
            lambda: archive_scout_session(session.id, "Archived from Stage 1 UI"),
            on_success=_clear_to_new,
        )

    def _new_research_click(_e) -> None:
        session = session_holder[0]

        def _work():
            if session and session.state not in {SessionState.ARCHIVED, SessionState.COMPLETE}:
                archive_scout_session(session.id, "Started a new research session")
            return None

        _run_busy("Archiving…", _work, on_success=_clear_to_new)

    def _resume_click(session: ResearchSession) -> None:
        if busy[0]:
            return

        def _work():
            return load_scout_session(session.id, root=RESEARCH_SESSIONS_ROOT)

        def _on_resumed(loaded):
            state.scout_session_id = loaded.id
            state.scout_mode = loaded.mode.value
            state.last_prompt = loaded.user_intent
            selected_general[0] = loaded.selected_general_candidate_id or ""
            selected_specific.clear()
            selected_specific.update(loaded.selected_specific_candidate_ids)
            _apply_session_and_render(loaded)

        _run_busy("Loading session…", _work, on_success=_on_resumed)

    # ─── Right rail ─────────────────────────────────────────────────────

    def _render_resume_list() -> ft.Control:
        sessions = list_scout_sessions(root=RESEARCH_SESSIONS_ROOT)
        if not sessions:
            return ft.Text("No unfinished research sessions.", size=11, color=TEXT_MUTED)
        return ft.Column([
            ft.Container(
                key=f"resume-session-{session.id}",
                content=ft.Column([
                    ft.Text(f"{session.mode.value.upper()} · {session.user_intent}",
                            size=12, color=TEXT_PRIMARY),
                    ft.Text(f"{session.id} · {session.state.value}",
                            size=10, color=TEXT_MUTED),
                ], spacing=2),
                padding=10,
                border=ft.border.all(1, BORDER),
                border_radius=6,
                ink=True,
                on_click=lambda _e, s=session: _resume_click(s),
            )
            for session in sessions
        ], spacing=6, scroll=ft.ScrollMode.AUTO)

    def _build_right_rail_controls() -> list[ft.Control]:
        session = session_holder[0]
        controls: list[ft.Control] = [
            ft.Text("STEP 1 OF 8", size=10, color=TEXT_MUTED),
            ft.Text("Research Scout", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ft.Text(
                "Collect source-backed comic candidates, review evidence, "
                "then create the project.",
                size=12, color=TEXT_MUTED,
            ),
            ft.Container(height=12),
        ]
        if session is not None:
            controls.append(ft.Row([
                ft.Text("CURRENT STATE", size=10, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
                ft.Container(
                    key="current-state",
                    content=ft.Text(session.state.value.replace("_", " ").upper(),
                                    size=10, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
                    bgcolor=BG_PANEL,
                    border=ft.border.all(1, BORDER),
                    border_radius=4,
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                ),
            ], spacing=8))
            controls.append(ft.Container(height=12))
        controls.extend([
            ft.Text("UNFINISHED SESSIONS", size=10, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
            _render_resume_list(),
            ft.Container(height=12),
            secondary_button("New research (archive current)", _new_research_click),
            secondary_button("Archive research session", _archive_click, disabled=session is None),
        ])
        return controls

    # ─── Render plumbing ────────────────────────────────────────────────

    def _sync_input_row() -> None:
        session = session_holder[0]
        hint, disabled = _input_spec(session)
        show_mode = session is None or session.state in {SessionState.ARCHIVED, SessionState.COMPLETE}
        intent_field.hint_text = hint
        intent_field.disabled = disabled
        send_button.disabled = disabled
        input_row.controls = ([mode_group] if show_mode else []) + [intent_field, send_button]

    def _apply_render(error: str | None = None) -> None:
        bubbles = _render_chat()
        if error:
            bubbles = bubbles + [_system_bubble(error, danger=True)]
        transcript.controls = bubbles
        _sync_input_row()
        right_column.controls = _build_right_rail_controls()

    def _render_full(error: str | None = None) -> None:
        _apply_render(error=error)
        page.update()

    # ─── Widgets ────────────────────────────────────────────────────────

    transcript = ft.ListView(
        expand=True, spacing=10, auto_scroll=True,
        padding=ft.padding.symmetric(horizontal=28, vertical=16),
    )
    mode_group = ft.RadioGroup(
        key="scout-mode",
        value=state.scout_mode if state.scout_mode in {"qa", "micro"} else "qa",
        content=ft.Row([
            ft.Radio(value="qa", label="Q&A (3–5 items)"),
            ft.Radio(value="micro", label="Micro (1 moment)"),
        ], spacing=18),
        on_change=_mode_changed,
    )
    intent_field = ft.TextField(
        key="scout-intent",
        value=(state.last_prompt or "") if session_holder[0] is None else "",
        expand=True,
        border_color=BORDER,
        focused_border_color=ACCENT,
        multiline=True,
        min_lines=1,
        max_lines=3,
    )
    send_button = primary_button("Send", _send_click)
    send_button.key = "chat-send"
    input_row = ft.Row([intent_field, send_button], spacing=12)
    input_container = ft.Container(
        content=input_row,
        padding=ft.padding.symmetric(horizontal=28, vertical=12),
        border=ft.border.only(top=ft.BorderSide(1, BORDER)),
    )

    right_column = ft.Column([], spacing=7, expand=True)

    _apply_render()

    center = ft.Column([transcript, input_container], spacing=0, expand=True)

    return three_col(
        center,
        right_column,
        state=state,
        on_go=on_go,
        header_title="Research Scout",
        header_subtitle=(
            "Choose Q&A or one visual moment, then verify the source before "
            "creating a project."
        ),
    )
