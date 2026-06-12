# art_ui/screens/a2_fetch.py
"""A2: download CC0 image(s) + metadata from The Met into the art project."""
from typing import Callable

import flet as ft

from ui.theme import BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN
from ui.bridge import format_exception, run_blocking

from .. import bridge
from ..layout import art_shell, log_list, primary_button, secondary_button
from ..state import ArtAppState, save_state


def build(page: ft.Page, state: ArtAppState, *,
          on_go: Callable[[int], None], on_state_change: Callable[[], None]) -> ft.Control:
    lv, push_log = log_list(page)
    status = ft.Text("Click Fetch to download from The Met.", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    thumbs = ft.Row(spacing=10, wrap=True)

    def _mount_thumbs():
        thumbs.controls.clear()
        for ch in bridge.load_manifest(state.project_name):
            for img in ch.get("pages", []):
                thumbs.controls.append(ft.Column([
                    ft.Image(src=img, width=180, height=220, fit=ft.BoxFit.CONTAIN,
                             border_radius=6),
                    ft.Text(ch.get("label", ""), size=10, color=TEXT_MUTED, width=180),
                ], spacing=4))

    if bridge.load_manifest(state.project_name):
        _mount_thumbs()
        status.value = "Already fetched — re-fetch reuses cached images."

    async def _execute():
        running.visible = True
        status.value = "Fetching from The Met…"
        status.color = WARN
        page.update()
        try:
            await run_blocking(bridge.run_fetch, state.project_name, state.object_ids,
                               state.mode, state.theme, push_log,
                               length=getattr(state, "length", "short"))
        except Exception as e:
            running.visible = False
            status.value = "Failed — see log (non-CC0 artworks are refused)."
            status.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        running.visible = False
        status.value = "Fetched. Review the artwork below, then continue."
        status.color = SUCCESS
        _mount_thumbs()
        state.mark_approved(2)
        save_state(state)
        page.update()
        on_state_change()

    center = ft.Column([
        ft.Container(content=ft.Column([thumbs], scroll=ft.ScrollMode.AUTO, expand=True),
                     padding=ft.padding.symmetric(horizontal=28, vertical=16), expand=True),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status], spacing=10),
                ft.Container(content=lv, height=140, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 2 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("Fetch from Met", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(f"objectIDs: {', '.join(str(i) for i in state.object_ids)}\nmode: {state.mode}",
                size=12, color=TEXT_MUTED),
        ft.Container(height=12),
        primary_button("Fetch", lambda _e: page.run_task(_execute), icon=ft.Icons.DOWNLOAD),
        ft.Container(height=8),
        secondary_button("Next: Regions →", lambda _e: on_go(3), icon=ft.Icons.ARROW_FORWARD),
    ], spacing=8, expand=True)

    return art_shell(center, right, state=state, on_go=on_go,
                     header_title="Fetch from The Met",
                     header_subtitle="CC0 gate: non-public-domain objects are refused at this step.")
