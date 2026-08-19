"""Stage 1 Research Scout UI.

The scout is deliberately session-first: research is durable before a project
exists, and project creation is the final hand-off after evidence gates pass.
"""
from __future__ import annotations

from typing import Callable

import flet as ft

from config import PROJECTS_ROOT, RESEARCH_SESSIONS_ROOT
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.stage_1.storage import slugify

from ..bridge import (
    archive_scout_session,
    approve_scout_general,
    approve_scout_specific,
    create_scout_project,
    format_exception,
    list_scout_sessions,
    load_scout_candidates,
    load_scout_gates,
    load_scout_session,
    run_blocking,
    run_scout_general,
    run_scout_specific,
    start_scout_session,
)
from ..layout import primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN


# Kept as a module value so headless tests and LAN callers can point the screen
# at an isolated session store without creating a project.
RESEARCH_SESSIONS_ROOT = RESEARCH_SESSIONS_ROOT


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
    selected_general = [
        session_holder[0].selected_general_candidate_id if session_holder[0] else ""
    ]
    selected_specific: set[str] = set(
        session_holder[0].selected_specific_candidate_ids if session_holder[0] else []
    )
    error_text = ft.Text("", size=11, color=DANGER, selectable=True)
    status_text = ft.Text("", size=11, color=TEXT_MUTED, selectable=True)
    intent_field = ft.TextField(
        key="scout-intent",
        value=state.last_prompt,
        label="Research intent",
        hint_text="Ask a comic question or describe one visual moment.",
        expand=True,
        border_color=BORDER,
        focused_border_color=ACCENT,
        multiline=True,
        min_lines=1,
        max_lines=3,
    )
    slug_field = ft.TextField(
        key="project-slug",
        value=slugify(state.last_prompt or "untitled_research"),
        label="Project name",
        width=230,
        border_color=BORDER,
        focused_border_color=ACCENT,
    )
    feedback_field = ft.TextField(
        key="revision-feedback",
        label="Revision feedback",
        hint_text="Optional notes for the research decision.",
        multiline=True,
        min_lines=1,
        max_lines=3,
        border_color=BORDER,
        focused_border_color=ACCENT,
    )
    mode_group = ft.RadioGroup(
        key="scout-mode",
        value=state.scout_mode if state.scout_mode in {"qa", "micro"} else "qa",
        content=ft.Row([
            ft.Radio(value="qa", label="Q&A (3–5 items)"),
            ft.Radio(value="micro", label="Micro (1 moment)"),
        ], spacing=18),
    )
    workspace = ft.Container(expand=True)

    def _set_error(message: str = "") -> None:
        error_text.value = message
        error_text.color = DANGER if message else TEXT_MUTED

    def _current_candidates() -> tuple[list[dict], list[dict]]:
        session = session_holder[0]
        if not session:
            return [], []
        return (
            load_scout_candidates(session.id, root=RESEARCH_SESSIONS_ROOT),
            load_scout_gates(session.id, root=RESEARCH_SESSIONS_ROOT),
        )

    def _render_resume_list() -> ft.Control:
        sessions = list_scout_sessions(root=RESEARCH_SESSIONS_ROOT)
        if not sessions:
            return ft.Text("No unfinished research sessions.", size=11, color=TEXT_MUTED)

        def resume(session: ResearchSession):
            async def _resume():
                try:
                    loaded = await run_blocking(
                        load_scout_session, session.id, root=RESEARCH_SESSIONS_ROOT
                    )
                    session_holder[0] = loaded
                    state.scout_session_id = loaded.id
                    state.scout_mode = loaded.mode.value
                    state.last_prompt = loaded.user_intent
                    selected_general[0] = loaded.selected_general_candidate_id or ""
                    selected_specific.clear()
                    selected_specific.update(loaded.selected_specific_candidate_ids)
                    intent_field.value = loaded.user_intent
                    mode_group.value = loaded.mode.value
                    _set_error()
                    workspace.content = _render_workspace()
                    page.update()
                except Exception as exc:
                    _set_error(format_exception(exc))
                    page.update()

            page.run_task(_resume)

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
                on_click=lambda _e, s=session: resume(s),
            )
            for session in sessions
        ], spacing=6, scroll=ft.ScrollMode.AUTO)

    def _selection_changed(candidate_id: str, checked: bool) -> None:
        if mode_group.value == ScoutMode.MICRO.value:
            selected_specific.clear()
            if checked:
                selected_specific.add(candidate_id)
        elif checked:
            selected_specific.add(candidate_id)
        else:
            selected_specific.discard(candidate_id)
        workspace.content = _render_workspace()
        page.update()

    def _run_start(_e):
        intent = (intent_field.value or "").strip()
        mode = mode_group.value or ScoutMode.QA.value
        if not intent:
            _set_error("Enter a research intent first.")
            page.update()
            return

        async def _execute():
            try:
                old = session_holder[0]
                if old and old.state not in {SessionState.ARCHIVED, SessionState.COMPLETE}:
                    await run_blocking(
                        archive_scout_session, old.id, "Started a new research session"
                    )
                session = await run_blocking(start_scout_session, mode, intent)
                session = await run_blocking(run_scout_general, session.id)
                session_holder[0] = session
                state.scout_session_id = session.id
                state.scout_mode = mode
                state.last_prompt = intent
                slug_field.value = slugify(intent)
                selected_general[0] = ""
                selected_specific.clear()
                _set_error()
                status_text.value = "General research complete — review the candidates."
                status_text.color = SUCCESS
                workspace.content = _render_workspace()
                page.update()
            except Exception as exc:
                _set_error(format_exception(exc))
                page.update()

        page.run_task(_execute)

    def _approve_general(_e):
        session = session_holder[0]
        if not session or not selected_general[0]:
            return

        async def _execute():
            try:
                updated = await run_blocking(
                    approve_scout_general, session.id, selected_general[0]
                )
                updated = await run_blocking(run_scout_specific, updated.id)
                session_holder[0] = updated
                _set_error()
                status_text.value = "Specific evidence is ready — choose production items."
                status_text.color = SUCCESS
                workspace.content = _render_workspace()
                page.update()
            except Exception as exc:
                _set_error(format_exception(exc))
                page.update()

        page.run_task(_execute)

    def _approve_specific(_e):
        session = session_holder[0]
        if not session or not _selection_count_valid(session.mode, selected_specific):
            return

        async def _execute():
            try:
                updated = await run_blocking(
                    approve_scout_specific,
                    session.id,
                    sorted(selected_specific),
                    feedback_field.value or "",
                )
                session_holder[0] = updated
                _set_error()
                status_text.value = "Production gates passed — create the project when ready."
                status_text.color = SUCCESS
                workspace.content = _render_workspace()
                page.update()
            except Exception as exc:
                _set_error(format_exception(exc))
                page.update()

        page.run_task(_execute)

    def _create_project(_e):
        session = session_holder[0]
        project_slug = (slug_field.value or "").strip() or slugify(intent_field.value or "")
        if not session or not project_slug:
            _set_error("Enter a project name before creating the project.")
            page.update()
            return

        async def _execute():
            try:
                project_name = await run_blocking(
                    create_scout_project, session.id, project_slug
                )
                state.project_name = project_name
                state.scout_session_id = ""
                state.last_prompt = session.user_intent
                state.pipeline_mode = (
                    "explore_answer" if session.mode is ScoutMode.QA else "micro_moment"
                )
                state.mark_approved(1)
                state.current_stage = 2
                save_state(state)
                on_state_change()
            except Exception as exc:
                _set_error(format_exception(exc))
                page.update()

        page.run_task(_execute)

    def _archive(_e):
        session = session_holder[0]
        if not session:
            return

        async def _execute():
            try:
                await run_blocking(archive_scout_session, session.id, "Archived from Stage 1 UI")
                session_holder[0] = None
                state.scout_session_id = ""
                selected_general[0] = ""
                selected_specific.clear()
                _set_error()
                status_text.value = "Research session archived."
                status_text.color = SUCCESS
                workspace.content = _render_workspace()
                page.update()
            except Exception as exc:
                _set_error(format_exception(exc))
                page.update()

        page.run_task(_execute)

    def _render_workspace() -> ft.Control:
        session = session_holder[0]
        if not session:
            return ft.Column([
                ft.Text("Start a Research Scout session to collect source-backed candidates.",
                        size=13, color=TEXT_MUTED),
                ft.Text("Unfinished sessions can be resumed before a project exists.",
                        size=12, color=TEXT_MUTED),
            ], spacing=8)

        candidates, gates = _current_candidates()
        rows: list[ft.Control] = [
            ft.Row([
                ft.Text("CURRENT STATE", size=10, color=TEXT_MUTED,
                        weight=ft.FontWeight.BOLD),
                ft.Container(
                    key="current-state",
                    content=ft.Text(session.state.value.replace("_", " ").upper(),
                                    size=10, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
                    bgcolor=BG_PANEL,
                    border=ft.border.all(1, BORDER),
                    border_radius=4,
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                ),
            ], spacing=8),
        ]

        if candidates:
            rows.append(ft.Text("CANDIDATES", size=10, color=TEXT_MUTED,
                                weight=ft.FontWeight.BOLD))

        if session.state is SessionState.GENERAL_REVIEW:
            radios = []
            cards = []
            for index, candidate in enumerate(candidates):
                candidate_id = _candidate_id(candidate, index)
                radio = ft.Radio(value=candidate_id, label="Approve this general candidate")
                radios.append(radio)
                cards.append(_candidate_card(
                    candidate, index, gate=_candidate_gate(candidate_id, gates),
                    selection=radio,
                ))
            def _general_changed(event):
                selected_general[0] = event.control.value
                general_button.disabled = not bool(selected_general[0])
                page.update()

            general_group = ft.RadioGroup(
                value=selected_general[0] or None,
                content=ft.Column(cards, spacing=8),
                on_change=_general_changed,
            )
            rows.append(general_group)
            general_button = primary_button(
                "Approve general candidate →",
                _approve_general,
                disabled=not selected_general[0],
            )
            rows.append(general_button)
        elif session.state in {SessionState.SPECIFIC_REVIEW, SessionState.PRODUCTION_GATES}:
            controls: list[ft.Control] = []
            for index, candidate in enumerate(candidates):
                candidate_id = _candidate_id(candidate, index)
                if session.mode is ScoutMode.MICRO:
                    selection = ft.Checkbox(
                        key=f"select-{candidate_id}",
                        label="Select",
                        value=candidate_id in selected_specific,
                        on_change=lambda e, cid=candidate_id: _selection_changed(
                            cid, bool(e.control.value)
                        ),
                    )
                else:
                    selection = ft.Checkbox(
                        key=f"select-{candidate_id}",
                        label="Select",
                        value=candidate_id in selected_specific,
                        on_change=lambda e, cid=candidate_id: _selection_changed(
                            cid, bool(e.control.value)
                        ),
                    )
                controls.append(_candidate_card(
                    candidate, index, gate=_candidate_gate(candidate_id, gates),
                    selection=selection,
                ))
            rows.extend(controls)
            if session.state is SessionState.SPECIFIC_REVIEW:
                valid = _selection_count_valid(session.mode, selected_specific)
                approve_button = primary_button(
                    "Approve selected items →",
                    _approve_specific,
                    disabled=not valid,
                    icon=ft.Icons.CHECK,
                )
                approve_button.key = "approve-specific"
                rows.extend([
                    feedback_field,
                    approve_button,
                ])
            else:
                rows.extend([
                    ft.Text("Production selections are locked for project creation.",
                            size=11, color=TEXT_MUTED),
                    ft.Row([slug_field, primary_button(
                        "Create project",
                        _create_project,
                        icon=ft.Icons.CREATE_NEW_FOLDER,
                    )], spacing=10),
                ])
        else:
            rows.append(ft.Text(
                "Run general research to populate source-backed candidates."
                if session.state is SessionState.GENERAL_DRAFT
                else f"Session is {session.state.value}.",
                size=12, color=TEXT_MUTED,
            ))
            if session.state is SessionState.GENERAL_DRAFT:
                rows.append(primary_button("Run general research", _run_start, icon=ft.Icons.SEARCH))

        return ft.Column(rows, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    def _selection_count_valid(mode: ScoutMode, selected: set[str]) -> bool:
        return (3 <= len(selected) <= 5) if mode is ScoutMode.QA else len(selected) == 1

    def _mode_changed(_e):
        state.scout_mode = mode_group.value or ScoutMode.QA.value
        selected_specific.clear()
        workspace.content = _render_workspace()
        page.update()

    mode_group.on_change = _mode_changed
    workspace.content = _render_workspace()

    start_label = "Start research" if not session_holder[0] else "Start new research (archive current)"
    center = ft.Column([
        ft.Container(
            content=ft.Column([
                ft.Row([mode_group, intent_field, primary_button(
                    start_label, _run_start, icon=ft.Icons.SEARCH,
                )], spacing=12),
                ft.Row([slug_field], spacing=10),
            ], spacing=10),
            padding=ft.padding.symmetric(horizontal=28, vertical=16),
        ),
        ft.Container(content=workspace, padding=ft.padding.symmetric(horizontal=28), expand=True),
        ft.Container(content=ft.Column([status_text, error_text], spacing=4),
                     padding=ft.padding.symmetric(horizontal=28, vertical=10)),
    ], spacing=0, expand=True)

    session = session_holder[0]
    right_controls: list[ft.Control] = [
        ft.Text("STEP 1 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Research Scout", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Collect source-backed comic candidates, review evidence, then create the project.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=12),
        ft.Text("UNFINISHED SESSIONS", size=10, color=TEXT_MUTED,
                weight=ft.FontWeight.BOLD),
        _render_resume_list(),
        ft.Container(height=12),
        secondary_button("Archive research session", _archive, disabled=session is None),
    ]
    right = ft.Column(right_controls, spacing=7, expand=True)

    return three_col(
        center,
        right,
        state=state,
        on_go=on_go,
        header_title="Research Scout",
        header_subtitle="Choose Q&A or one visual moment, then verify the source before creating a project.",
    )
