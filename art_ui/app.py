"""Two-tab host: COMIC (embeds existing ui/screens builders unmodified) and
ART (the 6-step art flow). Custom tab bar — not ft.Tabs — for zero API risk
and theme control. The comic app entry (python3 -m ui) keeps working untouched."""
from __future__ import annotations

import flet as ft

from ui.app import STAGE_BUILDERS as COMIC_BUILDERS  # read-only reuse
from ui.state import AppState as ComicAppState
from ui.state import list_projects as list_comic_projects
from ui.state import load_state as load_comic_state
from ui.state import save_state as save_comic_state
from ui.theme import (
    ACCENT, BG, BG_PANEL, BORDER, TEXT_MUTED, TEXT_PRIMARY, apply_theme,
)

from .screens import a1_select, a2_fetch, a3_regions, a4_narrate, a5_tts, a6_video
from .state import ArtAppState, N_STAGES, save_state as save_art_state

ART_BUILDERS = {
    1: a1_select.build,
    2: a2_fetch.build,
    3: a3_regions.build,
    4: a4_narrate.build,
    5: a5_tts.build,
    6: a6_video.build,
}


async def main(page: ft.Page):
    page.title = "Comic + Art → Short"
    apply_theme(page)
    page.window.width = 1400
    page.window.height = 900
    page.window.min_width = 1200
    page.window.min_height = 760

    content = ft.Container(expand=True)
    current = {"tab": "art"}

    # ── Art tab host ────────────────────────────────────────────────────────
    art_state = ArtAppState()

    def goto_art(stage: int):
        if stage < 1 or stage > N_STAGES:
            return
        art_state.current_stage = stage
        save_art_state(art_state)
        render_art()

    tabbar = ft.Container(bgcolor=BG_PANEL,
                          border=ft.border.only(bottom=ft.BorderSide(1, BORDER)))
    root = ft.Column([tabbar, content], spacing=0, expand=True)

    def _ensure_mounted():
        # Comic S1's "Start over" calls page.views.clear() then on_state_change()
        # — re-mounting here heals the host (same views model as ui/app.py).
        if not page.views or root not in page.views[0].controls:
            page.views.clear()
            page.views.append(ft.View(route="/", bgcolor=BG, padding=0, controls=[root]))

    def render_art():
        content.content = ART_BUILDERS[art_state.current_stage](
            page, art_state, on_go=goto_art, on_state_change=render_art,
        )
        _ensure_mounted()
        page.update()

    # ── Comic tab host (embeds existing builders; ui/ files untouched) ─────
    comic_state = ComicAppState()
    comic_started = {"flag": False}

    def goto_comic(stage: int):
        if stage < 1 or stage > 6:
            return
        comic_state.current_stage = stage
        save_comic_state(comic_state)
        render_comic()

    def render_comic():
        if not comic_started["flag"]:
            content.content = _comic_picker()
            _ensure_mounted()
            page.update()
            return
        content.content = COMIC_BUILDERS[comic_state.current_stage](
            page, comic_state, on_go=goto_comic, on_state_change=render_comic,
        )
        _ensure_mounted()
        page.update()

    def _comic_picker() -> ft.Control:
        rows: list[ft.Control] = [
            ft.Text("COMIC  →  SHORT", size=11, color=TEXT_MUTED,
                    weight=ft.FontWeight.BOLD),
            ft.Text("Open a project or start a new one.", size=14, color=TEXT_PRIMARY),
            ft.Container(height=16),
        ]

        def select(name: str):
            loaded = load_comic_state(name)
            comic_state.__dict__.update(loaded.__dict__)
            comic_started["flag"] = True
            render_comic()

        def new_project(_e):
            comic_state.project_name = ""
            comic_state.current_stage = 1
            comic_state.approved = {}
            comic_state.dirty = {}
            comic_started["flag"] = True
            render_comic()

        for name in list_comic_projects():
            rows.append(ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.FOLDER_OPEN, color=TEXT_MUTED, size=18),
                    ft.Text(name, size=14, color=TEXT_PRIMARY),
                ], spacing=10),
                padding=ft.padding.symmetric(horizontal=14, vertical=10),
                border=ft.border.all(1, BORDER), border_radius=6,
                ink=True, on_click=lambda _e, n=name: select(n),
            ))
        rows.append(ft.Container(height=8))
        rows.append(ft.ElevatedButton(
            "+ New project", on_click=new_project, bgcolor=ACCENT, color="#ffffff",
        ))
        return ft.Container(
            content=ft.Column(rows, spacing=8, scroll=ft.ScrollMode.AUTO),
            padding=40, width=520, bgcolor=BG_PANEL,
            border=ft.border.all(1, BORDER), border_radius=12,
            margin=ft.margin.only(top=80, left=80),
        )

    # ── Tab bar ─────────────────────────────────────────────────────────────
    def _tab_button(label: str, key: str) -> ft.Container:
        active = current["tab"] == key
        return ft.Container(
            content=ft.Text(label, size=13,
                            weight=ft.FontWeight.BOLD if active else ft.FontWeight.W_400,
                            color=TEXT_PRIMARY if active else TEXT_MUTED),
            padding=ft.padding.symmetric(horizontal=22, vertical=12),
            border=ft.border.only(bottom=ft.BorderSide(2, ACCENT if active else BG_PANEL)),
            ink=True, on_click=lambda _e, k=key: switch(k),
        )

    def _render_tabbar():
        tabbar.content = ft.Row(
            [_tab_button("COMIC", "comic"), _tab_button("ART", "art")], spacing=0)

    def switch(tab: str):
        current["tab"] = tab
        _render_tabbar()
        if tab == "art":
            render_art()
        else:
            render_comic()

    _render_tabbar()
    switch("art")
