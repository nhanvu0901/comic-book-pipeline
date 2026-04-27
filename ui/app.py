"""
Main Flet app entry: window setup, routing, stage screen dispatch.

Navigation model: manual routing via `page.views` stack. Each stage is
rendered by its own builder in ui/screens/*. We swap the current view when
the user clicks the stepper, presses Approve, or lands via resume-on-launch.
"""
from __future__ import annotations

import flet as ft

from .screens import s1_identify, s2_preprocess, s3_narrate, s4_tts, s5_video
from .state import AppState, list_projects, load_state, save_state
from .theme import BG, BG_PANEL, BORDER, ACCENT, TEXT_MUTED, TEXT_PRIMARY, apply_theme


STAGE_BUILDERS = {
    1: s1_identify.build,
    2: s2_preprocess.build,
    3: s3_narrate.build,
    4: s4_tts.build,
    5: s5_video.build,
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
        if stage < 1 or stage > 5:
            return
        state.current_stage = stage
        save_state(state)
        render_current()

    def _refresh():
        # re-render current screen when approvals/state change so stepper updates
        render_current()

    if not state.project_name:
        _show_project_picker(page, state, render_current)
    else:
        render_current()


def _bootstrap_state() -> AppState:
    # If exactly one project exists, resume it. Else show a picker.
    projects = list_projects()
    if len(projects) == 1:
        return load_state(projects[0])
    return AppState()


def _show_project_picker(page: ft.Page, state: AppState, on_selected):
    projects = list_projects()

    def select(name: str):
        s = load_state(name)
        state.__dict__.update(s.__dict__)
        page.views.clear()
        on_selected()

    def new_project(_e):
        # leave project empty — screen 1 will slug-create one from the prompt
        state.project_name = ""
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
    if projects:
        for name in projects:
            rows.append(
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.FOLDER_OPEN, color=TEXT_MUTED, size=18),
                        ft.Text(name, size=14, color=TEXT_PRIMARY),
                    ], spacing=10),
                    padding=ft.padding.symmetric(horizontal=14, vertical=10),
                    border=ft.border.all(1, BORDER),
                    border_radius=6,
                    ink=True,
                    on_click=lambda _e, n=name: select(n),
                )
            )
        rows.append(ft.Container(height=8))
    else:
        rows.append(ft.Text("No existing projects found.", color=TEXT_MUTED, size=12))
        rows.append(ft.Container(height=8))

    rows.append(
        ft.ElevatedButton(
            "+ New project",
            on_click=new_project,
            bgcolor=ACCENT, color="#ffffff",
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.padding.symmetric(horizontal=20, vertical=14),
            ),
        )
    )

    page.views.clear()
    page.views.append(
        ft.View(
            route="/",
            bgcolor=BG,
            padding=0,
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
