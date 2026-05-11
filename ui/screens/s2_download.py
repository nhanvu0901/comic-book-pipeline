"""
Screen 2: Download Comic — scrape pages from batcave.biz.

Shows a thumbnail grid of downloaded pages and a progress log.
Clear button deletes raw_comic/ so the user can re-download.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import flet as ft

from ..bridge import format_exception, load_raw_pages, run_blocking, run_stage_download
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS,
    TEXT_MUTED, TEXT_PRIMARY, WARN,
)
from utils.clear_stage import clear_stage_2


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    grid_ctl = ft.Container(expand=True)

    lv, push_log = log_list(page)
    status_text = ft.Text("", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    summary_text = ft.Text("", size=12, color=TEXT_MUTED)

    def render_grid(manifest: list[dict]):
        if not manifest:
            grid_ctl.content = ft.Container(
                content=ft.Text("No pages downloaded yet — click Download to start.",
                                color=TEXT_MUTED, size=13),
                alignment=ft.Alignment.CENTER, expand=True,
            )
            return

        tiles: list[ft.Control] = []
        total_pages = 0
        for chapter in manifest:
            pages = chapter.get("pages", [])
            total_pages += len(pages)
            for img_path_str in pages:
                img_path = Path(img_path_str)
                tiles.append(_thumbnail(img_path, chapter["label"]))

        summary_text.value = (
            f"{total_pages} pages across {len(manifest)} chapter(s)"
        )

        grid_ctl.content = ft.Column(
            [
                ft.Row(
                    [summary_text, ft.Container(expand=True)],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(height=8),
                ft.GridView(
                    tiles,
                    expand=True,
                    runs_count=5,
                    max_extent=150,
                    child_aspect_ratio=0.6,
                    spacing=8,
                    run_spacing=8,
                ),
            ],
            spacing=4, expand=True,
        )

    # Load existing manifest if any
    if state.project_name:
        existing = load_raw_pages(state.project_name)
        if existing:
            render_grid(existing)

    async def _execute():
        if not state.project_name:
            status_text.value = "No project loaded — go back to Stage 1."
            status_text.color = DANGER
            page.update()
            return
        running.visible = True
        status_text.value = "Downloading comic pages…"
        status_text.color = WARN
        page.update()

        try:
            manifest = await run_blocking(run_stage_download, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            status_text.value = "Failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return

        render_grid(manifest)
        state.mark_approved(2)
        state.current_stage = max(state.current_stage, 3)
        save_state(state)

        running.visible = False
        total = sum(len(ch.get("pages", [])) for ch in manifest)
        status_text.value = f"Download complete — {total} pages."
        status_text.color = SUCCESS
        page.update()
        on_state_change()

    def run_click(_e):
        page.run_task(_execute)

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def _do_clear(_e):
        try:
            removed = clear_stage_2(
                state.project_name, raw=True, preprocessed=False,
            )
        except Exception as e:
            _show_snack(str(e))
            return
        if removed:
            _show_snack(f"Cleared raw_comic/ ({len(removed)} item(s))")
        else:
            _show_snack("Nothing to clear.")
        render_grid([])
        page.update()

    def approve_and_go(_e):
        state.mark_approved(2)
        state.current_stage = 3
        save_state(state)
        on_go(3)

    # Center column
    center = ft.Column([
        ft.Container(
            content=grid_ctl,
            padding=ft.padding.symmetric(horizontal=28, vertical=16),
            expand=True,
        ),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status_text], spacing=10),
                ft.Container(
                    content=lv, height=140,
                    border=ft.border.all(1, BORDER), border_radius=6,
                ),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=16),
        ),
    ], spacing=0, expand=True)

    # Right column
    right = ft.Column([
        ft.Text("STEP 2 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("Download Comic", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Downloads comic pages from batcave.biz using the URL found in Stage 1. "
            "Pages are saved to raw_comic/ and cached — re-runs skip existing files.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=16),
        primary_button("Download", run_click, icon=ft.Icons.DOWNLOAD),
        ft.Container(height=8),
        secondary_button("Clear downloads", _do_clear, icon=ft.Icons.DELETE_OUTLINE),
        ft.Container(height=12),
        primary_button("Continue to Stage 3 →", approve_and_go,
                       disabled=not state.is_approved(2)),
    ], spacing=8, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Download Comic",
        header_subtitle="Scrape comic pages from batcave.biz before preprocessing.",
    )


def _thumbnail(img_path: Path, label: str) -> ft.Control:
    name = img_path.name if img_path.exists() else "?"
    img_ctl = (
        ft.Image(src=str(img_path), width=130, height=180, fit=ft.BoxFit.COVER,
                 border_radius=4)
        if img_path.exists()
        else ft.Container(width=130, height=180, bgcolor=BG_PANEL, border_radius=4)
    )
    return ft.Container(
        content=ft.Column([
            img_ctl,
            ft.Row([
                ft.Text(name[:18], size=9, color=TEXT_MUTED,
                        weight=ft.FontWeight.BOLD, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Container(expand=True),
                ft.Text(label, size=8, color=ACCENT),
            ], spacing=4),
        ], spacing=4),
        padding=4,
        border=ft.border.all(1, BORDER), border_radius=6, bgcolor=BG_ELEVATED,
    )
