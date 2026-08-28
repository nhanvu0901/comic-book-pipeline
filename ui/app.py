"""
Main Flet app entry: window setup, routing, stage screen dispatch.

Navigation model: manual routing via `page.views` stack. Each stage is
rendered by its own builder in ui/screens/*. We swap the current view when
the user clicks the stepper, presses Approve, or lands via resume-on-launch.
"""
from __future__ import annotations

import flet as ft

from config import PROJECTS_ROOT

from .screens import (
    s1_identify, s1_research_scout, s2_download, s2_preprocess, s3_narrate, s4_tts, s5_video,
    s6_review, s_review_gate,
)
from .bridge import (
    delete_project,
    delete_scout_session,
    describe_project,
    format_exception,
    human_size,
    list_scout_sessions,
)
from .layout import primary_button
from .state import (AppState, PICKER_STAGE, list_projects, load_state,
                    save_state)
from .theme import BG, BG_PANEL, BORDER, ACCENT, DANGER, TEXT_MUTED, TEXT_PRIMARY, apply_theme


STAGE_BUILDERS = {
    1: s1_research_scout.build,
    2: s2_download.build,
    3: s2_preprocess.build,
    4: s3_narrate.build,
    5: s_review_gate.build,
    6: s4_tts.build,
    7: s6_review.build,
    8: s5_video.build,
}


async def main(page: ft.Page):
    page.title = "Comic → Short"
    apply_theme(page)
    page.window.width = 1400
    page.window.height = 900
    page.window.min_width = 1200
    page.window.min_height = 760

    state = _bootstrap_state()

    def render_current():
        page.views.clear()
        page.views.append(
            ft.View(
                route=f"/s{state.current_stage}",
                bgcolor=BG,
                padding=0,
                controls=[STAGE_BUILDERS[state.current_stage](
                    page, state,
                    on_go=goto_stage,
                    on_state_change=_refresh,
                )],
            )
        )
        page.update()

    def goto_stage(stage: int):
        # PICKER_STAGE is the "back to the project list" sentinel (see ui/state.py). Until
        # this existed the picker was reachable ONLY at launch, and only when no project was
        # resolved — so opening a comic was a one-way door for the whole session.
        if stage == PICKER_STAGE:
            save_state(state)               # never lose the current project's stage/approvals
            # Reached mid-flow (a stage screen), not at launch — so offer a way out.
            _show_project_picker(page, state, render_current, can_cancel=True)
            return
        if stage < 1 or stage > 8:
            return
        state.current_stage = stage
        save_state(state)
        render_current()

    def _refresh():
        # re-render current screen when approvals/state change so stepper updates
        render_current()

    if not state.project_name:
        # The picker IS the entry point here — cancelling would strand the user on a
        # blank screen, so no Cancel button at bootstrap.
        _show_project_picker(page, state, render_current, can_cancel=False)
    else:
        render_current()


def _bootstrap_state() -> AppState:
    # If exactly one project exists, resume it. Else show a picker.
    projects = list_projects()
    if len(projects) == 1:
        return load_state(projects[0])
    return AppState()


def _show_project_picker(
    page: ft.Page, state: AppState, on_selected, can_cancel: bool = False,
    error: str | None = None,
):
    """can_cancel=True when reached from a stage screen (goto_stage(PICKER_STAGE)) — the
    user came from somewhere and can back out. False at bootstrap, where the picker IS the
    entry point and a Cancel button would strand the user on a blank screen.

    error: a preformatted message (see format_exception) to render at the top of the
    panel — set by _safe() below when a click handler raises. Flet swallows exceptions
    raised inside event handlers, so without this a failing click looks IDENTICAL to a
    dead button (the exact "clicking delete does nothing" symptom this exists to catch).
    """
    projects = list_projects()

    def _safe(handler):
        """Wrap a click handler so a raised exception rebuilds the picker with the
        exception visible instead of vanishing silently. Every on_click assigned in
        this function goes through this."""
        def _wrapped(*args, **kwargs):
            try:
                return handler(*args, **kwargs)
            except Exception as exc:
                page.views.clear()
                _show_project_picker(
                    page, state, on_selected, can_cancel=can_cancel,
                    error=format_exception(exc),
                )
        return _wrapped

    def select(name: str):
        s = load_state(name)
        state.__dict__.update(s.__dict__)
        page.views.clear()
        on_selected()

    def _confirm_delete_project(name: str) -> None:
        """Show what would be destroyed, then hard-delete on confirm. Master's chosen
        safety model for this app: a confirmation dialog, no trash folder, no
        type-the-name — see CLAUDE.md job notes."""

        def _do_delete(_e):
            page.pop_dialog()
            delete_project(name)
            if state.project_name == name:
                # The directory is gone — clear the dangling stage/approval state that
                # pointed at it (same reset new_project() below uses for a blank start),
                # so the app never keeps addressing a project that no longer exists.
                state.project_name = ""
                state.scout_session_id = ""
                state.scout_mode = "qa"
                state.last_prompt = ""
                state.current_stage = 1
                state.approved = {}
                state.dirty = {}
            page.views.clear()
            _show_project_picker(page, state, on_selected, can_cancel=can_cancel)

        inv = describe_project(name)
        top_ext = sorted(inv.extension_counts.items(), key=lambda kv: -kv[1])[:4]
        inventory_text = (
            ", ".join(f"{count} {ext}" for ext, count in top_ext) if top_ext else "empty"
        )
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(f'Delete project "{name}"?'),
            content=ft.Column([
                ft.Text(str(PROJECTS_ROOT / name), size=11, color=TEXT_MUTED, selectable=True),
                ft.Text(f"{human_size(inv.total_bytes)} · {inv.file_count} files "
                        f"({inventory_text})", size=12, color=TEXT_PRIMARY),
                ft.Text("This cannot be undone.", size=12, color=DANGER,
                        weight=ft.FontWeight.BOLD),
            ], spacing=8, tight=True),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _e: page.pop_dialog()),
                primary_button("Delete", _safe(_do_delete), icon=ft.Icons.DELETE_OUTLINE),
            ],
        ))

    def _confirm_delete_session(session) -> None:
        """Same compact-confirm-then-hard-delete model as _confirm_delete_project and
        as the Stage 1 Research Scout right rail's _delete_session_row — mode + intent
        is enough context for a research session (no file inventory to show, sessions
        are small). If the deleted session is the one currently loaded in state, clear
        state.scout_session_id so the app never keeps addressing a session that no
        longer exists on disk."""

        def _do_delete(_e):
            page.pop_dialog()
            delete_scout_session(session.id)
            if state.scout_session_id == session.id:
                state.scout_session_id = ""
            page.views.clear()
            _show_project_picker(page, state, on_selected, can_cancel=can_cancel)

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete this research session?"),
            content=ft.Column([
                ft.Text(f"{session.mode.value.upper()} · {session.user_intent}",
                        size=12, color=TEXT_PRIMARY),
                ft.Text("This cannot be undone.", size=12, color=DANGER,
                        weight=ft.FontWeight.BOLD),
            ], spacing=8, tight=True),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _e: page.pop_dialog()),
                primary_button("Delete", _safe(_do_delete), icon=ft.Icons.DELETE_OUTLINE),
            ],
        ))

    def new_project(_e):
        # leave project empty — screen 1 will slug-create one from the prompt
        state.project_name = ""
        state.scout_session_id = ""
        state.scout_mode = "qa"
        state.last_prompt = ""
        state.current_stage = 1
        state.approved = {}
        state.dirty = {}
        page.views.clear()
        on_selected()

    rows: list[ft.Control] = [
        ft.Text("COMIC  →  SHORT", size=11, color=TEXT_MUTED,
                weight=ft.FontWeight.BOLD),
        ft.Text("Open a project or start a new one.",
                size=14, color=TEXT_PRIMARY),
        ft.Container(height=16),
    ]
    if error:
        rows.append(
            ft.Container(
                key="picker-error",
                content=ft.Text(error, size=11, color=DANGER, selectable=True,
                                 font_family="Menlo"),
                padding=ft.padding.symmetric(horizontal=10, vertical=8),
                border=ft.border.all(1, DANGER),
                border_radius=6,
            )
        )
        rows.append(ft.Container(height=8))
    if projects:
        for name in projects:
            rows.append(
                ft.Container(
                    content=ft.Row([
                        # A separate ink region for open-project, sibling to the delete
                        # button — an IconButton nested INSIDE an ink=True on_click
                        # container risks the tap being swallowed by the outer InkWell.
                        ft.Container(
                            key=f"open-project-{name}",
                            content=ft.Row([
                                ft.Icon(ft.Icons.FOLDER_OPEN, color=TEXT_MUTED, size=18),
                                ft.Text(name, size=14, color=TEXT_PRIMARY),
                            ], spacing=10),
                            expand=True,
                            ink=True,
                            on_click=_safe(lambda _e, n=name: select(n)),
                        ),
                        ft.IconButton(
                            key=f"delete-project-{name}",
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_size=18,
                            icon_color=DANGER,
                            tooltip=f"Delete {name}",
                            on_click=_safe(lambda _e, n=name: _confirm_delete_project(n)),
                            style=ft.ButtonStyle(padding=ft.padding.all(0)),
                        ),
                    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.padding.symmetric(horizontal=14, vertical=10),
                    border=ft.border.all(1, BORDER),
                    border_radius=6,
                )
            )
        rows.append(ft.Container(height=8))
    else:
        rows.append(ft.Text("No existing projects found.", color=TEXT_MUTED, size=12))
        rows.append(ft.Container(height=8))

    scout_sessions = list_scout_sessions()
    if scout_sessions:
        rows.extend([
            ft.Text("UNFINISHED RESEARCH", size=10, color=TEXT_MUTED,
                    weight=ft.FontWeight.BOLD),
            ft.Text("Resume a scout session without creating a project yet.",
                    size=12, color=TEXT_MUTED),
        ])

        def resume(session):
            state.project_name = ""
            state.scout_session_id = session.id
            state.scout_mode = session.mode.value
            state.last_prompt = session.user_intent
            state.current_stage = 1
            page.views.clear()
            on_selected()

        for session in scout_sessions:
            rows.append(
                ft.Container(
                    content=ft.Row([
                        # Same structure as the project rows above: a separate ink
                        # region for resume, SIBLING to the delete IconButton — never
                        # nested inside it, so the tap is not swallowed by the outer
                        # InkWell.
                        ft.Container(
                            key=f"resume-session-{session.id}",
                            content=ft.Column([
                                ft.Text(f"{session.mode.value.upper()} · {session.user_intent}",
                                        size=13, color=TEXT_PRIMARY),
                                ft.Text(f"{session.id} · {session.state.value}",
                                        size=10, color=TEXT_MUTED),
                            ], spacing=3),
                            expand=True,
                            ink=True,
                            on_click=_safe(lambda _e, s=session: resume(s)),
                        ),
                        ft.IconButton(
                            key=f"delete-session-{session.id}",
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_size=18,
                            icon_color=DANGER,
                            tooltip="Delete this research session",
                            on_click=_safe(lambda _e, s=session: _confirm_delete_session(s)),
                            style=ft.ButtonStyle(padding=ft.padding.all(0)),
                        ),
                    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                    padding=ft.padding.symmetric(horizontal=14, vertical=10),
                    border=ft.border.all(1, BORDER),
                    border_radius=6,
                )
            )
        rows.append(ft.Container(height=8))

    actions: list[ft.Control] = [
        ft.ElevatedButton(
            "+ New project",
            on_click=_safe(new_project),
            bgcolor=ACCENT, color="#ffffff",
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.padding.symmetric(horizontal=20, vertical=14),
            ),
        ),
    ]
    # Reached from a stage screen rather than at launch → let the user back out. Without
    # this, opening the picker by mistake forces a project choice, which is the same
    # one-way-door the picker button was added to remove. Gated on the explicit
    # can_cancel param (not state.project_name — a research session in progress has no
    # project yet, but still came from somewhere and still needs a way back).
    if can_cancel:
        def cancel(_e):
            page.views.clear()
            on_selected()
        label = (f"Cancel — back to {state.project_name}" if state.project_name
                  else "Cancel — back to research")
        actions.append(ft.TextButton(label, on_click=_safe(cancel)))
    rows.append(ft.Row(actions, spacing=12,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER))

    page.views.clear()
    page.views.append(
        ft.View(
            route="/",
            bgcolor=BG,
            padding=0,
            # A few projects plus several research sessions can exceed the window
            # height, and View.scroll defaults to None (no scrollbar, the overflow is
            # simply unreachable). View "represents a Column control" from a layout
            # perspective (flet's own docstring) and — unlike an arbitrary nested
            # Column — is already bounded by the real window/page height, so enabling
            # scroll HERE (rather than on some inner Column that has no outer bound)
            # is what actually makes it engage, at any window size.
            scroll=ft.ScrollMode.AUTO,
            controls=[
                ft.Container(
                    content=ft.Column(rows, spacing=8,
                                      horizontal_alignment=ft.CrossAxisAlignment.START),
                    padding=40,
                    width=520,
                    bgcolor=BG_PANEL,
                    border=ft.border.all(1, BORDER),
                    border_radius=12,
                    alignment=ft.Alignment.CENTER,
                    margin=ft.margin.only(top=120),
                )
            ],
            vertical_alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )
    page.update()
