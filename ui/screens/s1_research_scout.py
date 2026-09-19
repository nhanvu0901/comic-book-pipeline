"""Stage 1 Research Scout UI.

Redesigned as a chat: every research round, approval, feedback and verdict is a
chat bubble rebuilt from durable disk state (session.json + audit.jsonl +
artifacts). The only in-memory state kept across renders is UI-only state —
which candidates are ticked, which are mid-verify, whether the user has
consented to override the verdicts — and the busy flag; never a parallel log of
chat messages. Typing feedback re-runs general research with that feedback
threaded into the prompt (see workflow._intent_with_feedback).

There is ONE candidate list, reviewed once. It used to be asked twice over the
identical candidates: a radio round deciding who got evidence-gated, then a
checkbox round deciding who became the project, with the radio pick discarded.
"""
from __future__ import annotations

from typing import Callable

import flet as ft

from config import RESEARCH_SESSIONS_ROOT
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.stage_1.storage import slugify

from ..bridge import (
    archive_scout_session,
    approve_scout_selection,
    back_scout_candidates,
    create_scout_project,
    delete_scout_session,
    discover_questions,
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
    start_scout_session,
    verify_scout_selection,
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

# How many questions one Tier B research call is asked for — one per angle in
# research_policies/general_angles.v1.json.
_DISCOVER_BATCH = 5


def _session_for_ui(session_id: str) -> ResearchSession | None:
    if not session_id:
        return None
    try:
        return load_scout_session(session_id, root=RESEARCH_SESSIONS_ROOT)
    except Exception:
        return None


def _session_created_at(session_id: str) -> str:
    """YYYY-MM-DD from the session_created audit event, or "" if unavailable — the
    compact "created date" the delete-confirm dialog shows alongside mode + intent."""
    for event in load_scout_audit(session_id, root=RESEARCH_SESSIONS_ROOT):
        if event.get("event") == "session_created":
            return str(event.get("timestamp") or "")[:10]
    return ""


def _candidate_id(candidate: dict, index: int) -> str:
    value = candidate.get("id")
    return str(value).strip() if value is not None and str(value).strip() else f"candidate-{index}"


def _candidate_gate(candidate_id: str, gates: list[dict]) -> dict:
    """The gate for THIS candidate, or nothing.

    This used to end in `return gates[0] if len(gates) == 1 else {}`, and the
    only artifact production code ever wrote held exactly one gate — so all ten
    cards proudly displayed one candidate's verdict as if it were their own.
    An unverified candidate has no verdict, and saying so is the honest answer.
    """
    for gate in gates:
        if str(gate.get("candidate_id") or gate.get("id") or "") == candidate_id:
            return gate
    return {}


def _verdict_badge(gate: dict) -> ft.Control:
    """The card's own verdict line: NOT VERIFIED until this candidate was gated."""
    verdict = str(gate.get("verdict") or "").strip()
    if not verdict:
        return ft.Text("NOT VERIFIED", size=11, color=TEXT_MUTED, weight=ft.FontWeight.BOLD)
    color = SUCCESS if verdict == "confirmed" else (WARN if verdict == "inconclusive" else DANGER)
    return ft.Text(f"{verdict.upper()}", size=11, color=color, weight=ft.FontWeight.BOLD)


def _needs_override(selected: set[str], gates: list[dict]) -> bool:
    """True when approving would need an explicit override: any ticked candidate
    whose gate is missing or came back as anything but confirmed."""
    return any(
        str(_candidate_gate(candidate_id, gates).get("verdict") or "").strip().lower()
        != "confirmed"
        for candidate_id in selected
    )


def _candidate_card(
    candidate: dict,
    index: int,
    *,
    gate: dict,
    selection: ft.Control,
    trailing: list[ft.Control] | None = None,
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
        ft.Row([
            ft.Text(title, size=14, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD, expand=True),
            _verdict_badge(gate),
        ], spacing=8),
        ft.Text(summary, size=12, color=TEXT_MUTED, selectable=True),
    ]
    if gate.get("reason"):
        details.append(ft.Text(str(gate["reason"]), size=11, color=TEXT_MUTED, selectable=True))
    if candidate.get("series_issue_year"):
        details.append(ft.Text(str(candidate["series_issue_year"]), size=11, color=TEXT_PRIMARY))
    for url in urls:
        details.append(ft.Text(f"Source: {url}", size=10, color=ACCENT, selectable=True))
    if flags:
        details.append(ft.Text(f"Flags: {', '.join(flags)}", size=10, color=WARN, selectable=True))
    details.extend(trailing or [])
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


def _general_collapsed_lines(session: ResearchSession, candidates: list[dict]) -> ft.Control:
    selected = set(session.selected_specific_candidate_ids)
    lines: list[ft.Control] = []
    for index, candidate in enumerate(candidates):
        candidate_id = _candidate_id(candidate, index)
        title = str(candidate.get("title") or candidate.get("entity") or candidate_id)
        approved = candidate_id in selected
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
    if session.state is SessionState.CANDIDATE_REVIEW:
        return "Type feedback and press Send to re-run research…", False
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


def _selection_approved_bubble(detail: dict, candidates: list[dict]) -> ft.Control:
    ids = [str(candidate_id) for candidate_id in (detail.get("candidate_ids") or [])]
    titles = [_resolve_title(candidate_id, candidates) for candidate_id in ids]
    return _user_bubble(ft.Text(
        f"Approved: {', '.join(titles) if titles else '(none)'}", size=13, color=TEXT_PRIMARY,
    ))


def _verified_bubble(detail: dict, candidates: list[dict]) -> ft.Control:
    """One line per gated candidate — the verdict, or why the branch never
    produced one. A failure marks only its own candidate."""
    verdicts = detail.get("verdicts") or {}
    failed = detail.get("failed") or {}
    lines: list[ft.Control] = []
    for candidate_id in (detail.get("candidate_ids") or []):
        title = _resolve_title(str(candidate_id), candidates)
        if candidate_id in failed:
            lines.append(ft.Text(f"{title}: could not verify — {failed[candidate_id]}",
                                 size=12, color=DANGER, selectable=True))
        elif candidate_id in verdicts:
            verdict = str(verdicts[candidate_id])
            color = SUCCESS if verdict == "confirmed" else WARN
            lines.append(ft.Text(f"{title}: {verdict.upper()}", size=12, color=color))
    if not lines:
        lines.append(ft.Text("Nothing was re-verified.", size=12, color=TEXT_MUTED))
    return _scout_bubble(ft.Column(lines, spacing=4))


def _archived_bubble(detail: dict) -> ft.Control:
    reason = str(detail.get("reason") or "")
    return _system_bubble(f"Session archived — {reason}" if reason else "Session archived.")


def _discovered_text(entry: dict) -> str:
    """The one human-readable line of a Tier B entry, whichever mode produced
    it — QA discovers a `question`, Micro discovers a `moment`."""
    return str(entry.get("question") or entry.get("moment") or "").strip()


def _bank_suggestions_bubble(suggestions: list[dict]) -> ft.Control:
    """Tier A of the empty-intent fallback, shown for free before any research
    round runs. Master 2026-08-22: Send-with-empty-box must not silently spend
    API budget, so this is a dead-end by design — nothing here starts a
    session. Typing one of these into the box (or anything else) and pressing
    Send runs the normal flow; pressing Send empty AGAIN spends one research
    call on a batch of fresh questions (Tier B) and offers them as a choice —
    it does NOT research any of them (Master 2026-08-28: that keeps the
    human-review step is_burned's docstring in stages/youcom_scout.py says
    catches synonym re-skins of already-rejected bank questions)."""
    lines: list[ft.Control] = [
        ft.Text(
            "Still-open questions from qa_question_bank.md — type one into the box "
            "and press Send, or press Send again on an empty box and we'll find a "
            "batch of new questions for you to choose from before anything is "
            "researched.",
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
    selected_specific: set[str] = set(
        session_holder[0].selected_specific_candidate_ids if session_holder[0] else []
    )
    busy = [False]
    slug_holder: list[ft.TextField | None] = [None]
    # Override is DECIDED here but APPLIED at creation time (project_factory), so
    # the tick has to survive the hop from candidate review to production gates.
    override_holder = [False]
    # Per-card progress from verify_selected's worker threads. Display only —
    # on_result must never write to the store.
    verifying: set[str] = set()
    # Tier A suggestions currently on screen (empty-intent fallback) and whether
    # they've already been shown once for the CURRENT no-session state — a second
    # empty Send is read as an explicit ask to research a fresh angle instead
    # (Tier B), rather than silently spending API budget on the first empty Send.
    bank_suggestions_holder: list[list[dict]] = [[]]
    bank_shown = [False]
    # Tier B: the batch of discovered questions currently offered in the chat,
    # which one is ticked, every question offered so far (the re-roll's
    # `exclude`, so a new batch can't repeat one the user already turned down),
    # and the one-line note a re-roll that found nothing new leaves behind.
    discovered_holder: list[list[dict]] = [[]]
    discovered_pick = [""]
    discovered_offered: list[str] = []
    discovered_note = [""]

    def _forget_suggestions() -> None:
        bank_shown[0] = False
        bank_suggestions_holder[0] = []
        discovered_holder[0] = []
        discovered_pick[0] = ""
        discovered_offered.clear()
        discovered_note[0] = ""

    # ─── Chat transcript: rebuilt fresh from disk on every render ─────────

    def _candidate_review_content(
        session: ResearchSession, candidates: list[dict], gates: list[dict],
    ) -> ft.Control:
        """The one candidate list. Tick, verify, approve.

        There used to be two of these over the identical list: a radio round
        that chose who got evidence-gated, then a checkbox round that chose who
        became the project — and the radio pick was thrown away.
        """
        cards: list[ft.Control] = []
        for index, candidate in enumerate(candidates):
            candidate_id = _candidate_id(candidate, index)
            selection = ft.Checkbox(
                key=f"select-{candidate_id}",
                label="Select",
                value=candidate_id in selected_specific,
                on_change=lambda e, cid=candidate_id: _selection_changed(
                    cid, bool(e.control.value)
                ),
            )
            trailing: list[ft.Control] = []
            if candidate_id in verifying:
                trailing.append(ft.Row([
                    ft.ProgressRing(width=12, height=12, stroke_width=2),
                    ft.Text("Verifying…", size=11, color=TEXT_MUTED),
                ], spacing=6))
            else:
                reverify = secondary_button(
                    "Re-verify", lambda _e, cid=candidate_id: _reverify_click(cid),
                    disabled=candidate_id not in selected_specific,
                )
                reverify.key = f"reverify-{candidate_id}"
                trailing.append(reverify)
            cards.append(_candidate_card(
                candidate, index,
                gate=_candidate_gate(candidate_id, gates),
                selection=selection,
                trailing=trailing,
            ))

        verify_button = primary_button(
            f"Verify selected ({len(selected_specific)})", _verify_click,
            disabled=not selected_specific,
            icon=ft.Icons.FACT_CHECK,
        )
        verify_button.key = "verify-selected"
        approve_button = primary_button(
            "Approve & name project", _approve_selection_click,
            disabled=not _selection_count_valid(session.mode, selected_specific),
            icon=ft.Icons.CHECK,
        )
        approve_button.key = "approve-selected"

        controls: list[ft.Control] = [*cards, verify_button]
        if _needs_override(selected_specific, gates):
            controls.append(ft.Checkbox(
                key="override-gates",
                label="I have read the verdicts above and want to continue anyway",
                value=override_holder[0],
                on_change=_override_changed,
            ))
        controls.append(approve_button)
        return ft.Column(controls, spacing=8)

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
            _candidate_review_content(session, candidates, gates)
            if session.state is SessionState.CANDIDATE_REVIEW
            else _general_collapsed_lines(session, candidates)
        )
        plan_summary = detail.get("plan_summary")
        if not plan_summary:
            return _scout_bubble(content)
        return _scout_bubble(ft.Column([
            ft.Text(f"Plan: {plan_summary}", size=10, color=TEXT_MUTED),
            content,
        ], spacing=6))

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
        back_button = secondary_button("← Back to candidates", _back_to_candidates_click)
        headline = (
            "Selection locked in — overriding the verdicts above."
            if override_holder[0]
            else "Selection locked in."
        )
        return _scout_bubble(ft.Column([
            ft.Text(headline, size=13, color=TEXT_PRIMARY),
            ft.Row([slug_field, create_button, back_button], spacing=10),
        ], spacing=8))

    def _run_general_bubble() -> ft.Control:
        button = primary_button("Run general research", _run_general_click, icon=ft.Icons.SEARCH)
        return _scout_bubble(ft.Column([
            ft.Text("Ready to run the first research round for this session.",
                    size=12, color=TEXT_MUTED),
            button,
        ], spacing=8))

    def _discovered_questions_bubble() -> ft.Control:
        """Tier B's batch, single-select, with a re-roll.

        Ticking one only FILLS THE INPUT BOX — it must never start research.
        Master 2026-08-28: discover -> start_scout_session -> run_scout_general
        in one go spent a second research call enumerating answers to a dud
        lane before any human read the question, and is_burned()'s own
        docstring (stages/youcom_scout.py) says the review step after discover
        is what catches synonym re-skins of already-rejected bank questions.
        The bubble stays on screen after a pick so the user can change it.
        """
        choices: list[ft.Control] = []
        for index, entry in enumerate(discovered_holder[0]):
            angle = str(entry.get("angle") or "").strip()
            choices.append(ft.Row([
                ft.Radio(value=str(index)),
                ft.Text(f"[{angle}]", size=11, color=TEXT_MUTED) if angle else ft.Container(),
                ft.Text(_discovered_text(entry), size=12, color=TEXT_PRIMARY,
                        selectable=True, expand=True),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        lines: list[ft.Control] = [
            ft.Text("Pick one to research, or look for a different batch:",
                    size=12, color=TEXT_MUTED),
            ft.RadioGroup(
                key="discovered-questions",
                value=discovered_pick[0] or None,
                content=ft.Column(choices, spacing=2),
                on_change=_discovered_pick_changed,
            ),
        ]
        if discovered_note[0]:
            lines.append(ft.Text(discovered_note[0], size=11, color=WARN))
        reroll = secondary_button(
            f"None of these — find {_DISCOVER_BATCH} more", _reroll_click
        )
        reroll.key = "discovered-reroll"
        lines.append(ft.Row([
            reroll,
            ft.Text("costs one research call", size=10, color=TEXT_MUTED),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        lines.append(ft.Text(
            "The one you pick lands in the box — edit it if you like, then press Send to research it.",
            size=11, color=TEXT_MUTED,
        ))
        return _scout_bubble(ft.Column(lines, spacing=8))

    def _render_chat() -> list[ft.Control]:
        session = session_holder[0]
        if session is None:
            if discovered_holder[0]:
                return [_discovered_questions_bubble()]
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
            elif name == "candidates_verified":
                bubbles.append(_verified_bubble(detail, candidates))
            elif name == "selection_approved":
                bubbles.append(_selection_approved_bubble(detail, candidates))
            elif name == "session_archived":
                bubbles.append(_archived_bubble(detail))
            # unknown events (returned_to_candidate_review, project_created, ...): skip silently

        # State-driven appends happen regardless of audit content — this keeps the
        # review controls reachable even for a session seeded directly onto disk with
        # no matching audit trail (a hand-built CANDIDATE_REVIEW fixture, say),
        # matching the state-based contract those keys carry.
        if session.state is SessionState.CANDIDATE_REVIEW and not any(
            event.get("event") == "general_research_completed" for event in audit
        ):
            bubbles.append(_scout_bubble(
                _candidate_review_content(session, candidates, gates)
            ))
        if session.state is SessionState.PRODUCTION_GATES:
            bubbles.append(_production_gates_bubble(session))
        if session.state is SessionState.GENERAL_DRAFT:
            bubbles.append(_run_general_bubble())

        return bubbles

    # ─── Actions: busy-guarded, disk-driven re-render on completion ────────

    def _apply_session_and_render(result) -> None:
        session_holder[0] = result
        intent_field.value = ""
        _forget_suggestions()
        _render_full()

    def _clear_to_new(_result=None) -> None:
        selected_specific.clear()
        verifying.clear()
        override_holder[0] = False
        session_holder[0] = None
        intent_field.value = ""
        _forget_suggestions()
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

    def _discovered_pick_changed(event) -> None:
        """Tick a discovered question -> it lands in the input box, full stop.

        Deliberately NOT a research trigger. See _discovered_questions_bubble
        and the long comment in _send_click: the human reading (and optionally
        editing) the question before Send IS the review step that catches the
        synonym re-skins is_burned() lets through on purpose.
        """
        if busy[0]:
            return
        raw = str(getattr(event.control, "value", "") or "")
        try:
            entry = discovered_holder[0][int(raw)]
        except (TypeError, ValueError, IndexError):
            return
        discovered_pick[0] = raw
        intent_field.value = _discovered_text(entry)
        _render_full()

    def _show_discovered(batch) -> None:
        entries = [entry for entry in (batch or []) if _discovered_text(entry)]
        already = {text.casefold() for text in discovered_offered}
        fresh = [
            entry for entry in entries
            if _discovered_text(entry).casefold() not in already
        ]
        if discovered_holder[0]:
            # A re-roll. Never blank the list: nothing new — or nothing at all
            # but Tier B's angle fallback, which is what a dead You.com yields —
            # leaves the batch already on screen exactly where it is.
            if not fresh or all(entry.get("fallback") for entry in fresh):
                discovered_note[0] = (
                    "Nothing new came back — keeping the batch above."
                )
                _render_full()
                return
        elif not fresh:
            fresh = entries
        if not fresh:
            _render_full(error="No question came back — type one into the box yourself.")
            return
        bank_shown[0] = False
        bank_suggestions_holder[0] = []
        discovered_note[0] = ""
        discovered_pick[0] = ""
        discovered_holder[0] = fresh
        discovered_offered.extend(_discovered_text(entry) for entry in fresh)
        _render_full()

    def _discover_batch(mode: str) -> None:
        """One research call, whether it is the first batch or a re-roll. The
        questions already offered go along as `exclude` so the model is not paid
        to hand back a batch the user has just turned down."""
        excluded = list(discovered_offered)
        _run_busy(
            f"Finding {_DISCOVER_BATCH} questions…",
            lambda: discover_questions(mode, count=_DISCOVER_BATCH, exclude=excluded),
            on_success=_show_discovered,
        )

    def _reroll_click(_e) -> None:
        if busy[0]:
            return
        _discover_batch(mode_group.value or ScoutMode.QA.value)

    def _send_click(_e) -> None:
        if busy[0]:
            return
        session = session_holder[0]
        text = (intent_field.value or "").strip()

        if session is None or session.state in {SessionState.ARCHIVED, SessionState.COMPLETE}:
            mode = mode_group.value or ScoutMode.QA.value
            if not text:
                # A Tier B batch is already on screen: Send-on-empty is neither a
                # re-roll (that button says what it costs) nor a reason to put the
                # bank bubble back behind it — say what to do and spend nothing.
                if discovered_holder[0]:
                    _render_full(
                        error="Pick one of the questions above, or ask for a different batch."
                    )
                    return
                # First empty Send: show Tier A (bank) suggestions for free and stop —
                # do NOT spend API budget without the user asking for it.
                if not bank_shown[0]:
                    suggestions = list_bank_suggestions(mode)
                    if suggestions:
                        bank_shown[0] = True
                        bank_suggestions_holder[0] = suggestions
                        _render_full()
                        return
                # Second empty Send (bank_shown[0] already True), or the bank had
                # nothing to show at all (empty for this mode, or nothing left
                # after banlist filtering): spend ONE research call on a Tier B
                # batch and OFFER IT AS A CHOICE — do NOT research any of it
                # yet. Master 2026-08-28: a straight-through discover ->
                # start_scout_session -> run_scout_general used to spend a
                # SECOND research call enumerating answers to a dud lane (a
                # synonym re-skin of an already-rejected bank question) before
                # any human saw it — is_burned()'s own docstring in
                # stages/youcom_scout.py says the Master-review step after
                # discover is what is supposed to catch those. Picking one only
                # lands it in the input box; the human reads it, edits it if
                # they want, and presses Send again to research it.
                _discover_batch(mode)
                return

            _forget_suggestions()
            old = session

            def _work():
                if old is not None and old.state not in {SessionState.ARCHIVED, SessionState.COMPLETE}:
                    archive_scout_session(old.id, "Started a new research session")
                new_session = start_scout_session(mode, text)
                return run_scout_general(new_session.id)

            state.scout_mode = mode
            state.last_prompt = text
            _run_busy("Researching… ~30s", _work)
            return

        if session.state is SessionState.CANDIDATE_REVIEW:
            if not text:
                _render_full(
                    error="Type feedback before sending, or verify the candidates above."
                )
                return
            _run_busy("Researching… ~30s", lambda: rerun_scout_general(session.id, text))
            return

        # GENERAL_DRAFT / PRODUCTION_GATES: Send stays disabled; defensive no-op.

    def _mode_changed(_e) -> None:
        state.scout_mode = mode_group.value or ScoutMode.QA.value
        # Tier A suggestions are QA-only content and a Tier B batch is either
        # questions or moments, never both — stale ones from the other mode must
        # not linger, and switching modes counts as a fresh attempt.
        _forget_suggestions()

    def _selection_changed(candidate_id: str, checked: bool) -> None:
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
        # A tick that no longer needs overriding must not keep a stale consent.
        gates = load_scout_gates(session.id, root=RESEARCH_SESSIONS_ROOT) if session else []
        if not _needs_override(selected_specific, gates):
            override_holder[0] = False
        _render_full()

    def _override_changed(event) -> None:
        override_holder[0] = bool(event.control.value)
        _render_full()

    def _verify(candidate_ids: list[str], only: list[str] | None, label: str) -> None:
        session = session_holder[0]
        if not session or not candidate_ids:
            return
        verifying.clear()
        verifying.update(candidate_ids if only is None else only)

        async def _redraw() -> None:
            _apply_render()
            page.update()

        def _landed(candidate_id, _outcome) -> None:
            """Fires from one of verify_selected's worker threads as that branch
            finishes. Display only — it drops the card's spinner and asks the page
            to redraw, and deliberately writes nothing: the gate artifact is the
            workflow's to write, once, after every branch has settled."""
            verifying.discard(candidate_id)
            try:
                page.run_task(_redraw)
            except Exception:
                pass

        def _work():
            try:
                return verify_scout_selection(
                    session.id, candidate_ids, only=only, on_result=_landed,
                )
            finally:
                # Whichever way it ends — including an exception _run_busy will
                # turn into an error bubble — no card may be left spinning.
                verifying.clear()

        _run_busy(label, _work, on_success=_apply_session_and_render)

    def _verify_click(_e) -> None:
        session = session_holder[0]
        if not session or not selected_specific:
            return
        ids = sorted(selected_specific)
        _verify(ids, None, f"Checking evidence for {len(ids)}…")

    def _reverify_click(candidate_id: str) -> None:
        """Re-gate one card without paying for the others again. The whole
        selection still goes in, so the artifact stays one-entry-per-selection."""
        if candidate_id not in selected_specific:
            return
        _verify(sorted(selected_specific), [candidate_id], "Re-checking evidence…")

    def _approve_selection_click(_e) -> None:
        session = session_holder[0]
        if not session or not _selection_count_valid(session.mode, selected_specific):
            return
        _run_busy("Locking the selection in…", lambda: approve_scout_selection(session.id))

    def _back_to_candidates_click(_e) -> None:
        session = session_holder[0]
        if not session:
            return
        _run_busy("Returning to the candidates…", lambda: back_scout_candidates(session.id))

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
            lambda: create_scout_project(
                session.id, project_slug, override=override_holder[0]
            ),
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
            selected_specific.clear()
            selected_specific.update(loaded.selected_specific_candidate_ids)
            verifying.clear()
            override_holder[0] = False
            _apply_session_and_render(loaded)

        _run_busy("Loading session…", _work, on_success=_on_resumed)

    def _delete_session_row(session: ResearchSession) -> None:
        """Compact confirm — mode + intent + created date is enough, sessions are small
        (~272K for six) — then hard-delete. If this is the session currently loaded,
        clear it back to the empty state instead of leaving the screen pointing at a
        session directory that no longer exists."""
        if busy[0]:
            return

        def _do_delete(_e):
            page.pop_dialog()
            delete_scout_session(session.id, root=RESEARCH_SESSIONS_ROOT)
            if session_holder[0] is not None and session_holder[0].id == session.id:
                selected_specific.clear()
                verifying.clear()
                override_holder[0] = False
                session_holder[0] = None
                state.scout_session_id = ""
                intent_field.value = ""
                _forget_suggestions()
            _render_full()

        created = _session_created_at(session.id)
        detail: list[ft.Control] = [
            ft.Text(f"{session.mode.value.upper()} · {session.user_intent}",
                    size=12, color=TEXT_PRIMARY),
        ]
        if created:
            detail.append(ft.Text(f"Created {created}", size=11, color=TEXT_MUTED))
        detail.append(ft.Text("This cannot be undone.", size=12, color=DANGER,
                              weight=ft.FontWeight.BOLD))
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete this research session?"),
            content=ft.Column(detail, spacing=6, tight=True),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _e: page.pop_dialog()),
                primary_button("Delete", _do_delete, icon=ft.Icons.DELETE_OUTLINE),
            ],
        ))

    # ─── Right rail ─────────────────────────────────────────────────────

    def _render_resume_list() -> ft.Control:
        sessions = list_scout_sessions(root=RESEARCH_SESSIONS_ROOT)
        if not sessions:
            return ft.Text("No unfinished research sessions.", size=11, color=TEXT_MUTED)
        return ft.Column([
            ft.Container(
                content=ft.Row([
                    # Sibling ink region + delete button, not nested — an IconButton
                    # placed INSIDE an ink=True on_click container risks the tap being
                    # swallowed by the outer InkWell instead of reaching the button.
                    ft.Container(
                        key=f"resume-session-{session.id}",
                        content=ft.Column([
                            ft.Text(f"{session.mode.value.upper()} · {session.user_intent}",
                                    size=12, color=TEXT_PRIMARY),
                            ft.Text(f"{session.id} · {session.state.value}",
                                    size=10, color=TEXT_MUTED),
                        ], spacing=2),
                        expand=True,
                        ink=True,
                        on_click=lambda _e, s=session: _resume_click(s),
                    ),
                    ft.IconButton(
                        key=f"delete-session-{session.id}",
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_size=16,
                        icon_color=DANGER,
                        tooltip="Delete this research session",
                        on_click=lambda _e, s=session: _delete_session_row(s),
                        style=ft.ButtonStyle(padding=ft.padding.all(0)),
                    ),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.START),
                padding=10,
                border=ft.border.all(1, BORDER),
                border_radius=6,
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
