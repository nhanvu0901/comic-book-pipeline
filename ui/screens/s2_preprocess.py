"""
Screen 2: Preprocess pages.

Runs the full Stage 2 pipeline (scraper + YOLO + VLM) and shows a thumbnail
grid of processed pages with a story/skip tag + panel count. Click a
thumbnail to see the page summary + extracted text in a side panel.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import flet as ft

from ..bridge import format_exception, load_preprocessed, run_blocking, run_stage_2
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, STATUS_DONE, STATUS_PENDING,
    SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    grid_ctl = ft.Container(expand=True)
    detail_ctl = ft.Container(expand=True)

    lv, push_log = log_list(page)
    status_text = ft.Text("", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)

    summary_text = ft.Text("", size=12, color=TEXT_MUTED)

    def render_grid(pages: list[dict]):
        if not pages:
            grid_ctl.content = ft.Container(
                content=ft.Text("No pages yet — click Run to start.",
                                color=TEXT_MUTED, size=13),
                alignment=ft.Alignment.CENTER, expand=True,
            )
            return
        tiles: list[ft.Control] = []
        story_count = 0
        for pg in pages:
            if pg.get("is_story_page"):
                story_count += 1
            tiles.append(_thumbnail(pg, on_click=lambda _e, p=pg: _show_detail(p)))
        summary_text.value = f"{story_count}/{len(pages)} story pages · {len(pages)} total"

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
                    runs_count=4,
                    max_extent=190,
                    child_aspect_ratio=0.65,
                    spacing=10,
                    run_spacing=10,
                ),
            ],
            spacing=4, expand=True,
        )

    def _show_detail(pg: dict):
        panels = pg.get("panels") or []
        texts = pg.get("text_blocks") or []
        blocks: list[ft.Control] = [
            ft.Text(f"Page {pg.get('page_number')} — {pg.get('issue_label','')}",
                    size=14, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ft.Text(f"Story: {pg.get('is_story_page')}  ·  "
                    f"{len(panels)} panels  ·  {len(texts)} text blocks",
                    size=11, color=TEXT_MUTED),
            ft.Container(height=6),
        ]
        if pg.get("page_summary"):
            blocks.append(ft.Text("Summary", size=10, color=TEXT_MUTED,
                                   weight=ft.FontWeight.BOLD))
            blocks.append(ft.Text(pg["page_summary"], size=12, color=TEXT_PRIMARY,
                                   selectable=True))
            blocks.append(ft.Container(height=10))
        if texts:
            blocks.append(ft.Text("Extracted text", size=10, color=TEXT_MUTED,
                                   weight=ft.FontWeight.BOLD))
            for tb in texts[:30]:
                spk = tb.get("speaker") or "—"
                ttype = tb.get("type", "speech")
                blocks.append(
                    ft.Text(f"  [{ttype}] {spk}: \"{tb.get('text','')}\"",
                            size=11, color=TEXT_PRIMARY, selectable=True)
                )
        detail_ctl.content = ft.Container(
            content=ft.Column(blocks, spacing=3, scroll=ft.ScrollMode.AUTO, expand=True),
            padding=14,
            border=ft.border.all(1, BORDER),
            border_radius=6,
            bgcolor=BG_ELEVATED,
        )
        page.update()

    # Load cached preprocessed if any
    if state.project_name:
        existing = load_preprocessed(state.project_name)
        if existing:
            render_grid(existing)

    async def _execute():
        if not state.project_name:
            status_text.value = "No project loaded — go back to Stage 1."
            status_text.color = DANGER
            page.update()
            return
        running.visible = True
        status_text.value = "Running Stage 2 (scraping + YOLO + VLM)… This takes several minutes."
        status_text.color = WARN
        page.update()

        try:
            pages = await run_blocking(run_stage_2, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            status_text.value = "Failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return

        render_grid(pages)
        state.mark_approved(2)
        state.current_stage = max(state.current_stage, 3)
        save_state(state)

        running.visible = False
        status_text.value = f"Stage 2 complete — {len(pages)} pages."
        status_text.color = SUCCESS
        page.update()
        on_state_change()

    def run_click(_e):
        page.run_task(_execute)

    def approve_and_go(_e):
        state.mark_approved(2)
        state.current_stage = 3
        save_state(state)
        on_go(3)

    # Center + right columns
    center = ft.Column([
        ft.Container(content=grid_ctl, padding=ft.padding.symmetric(horizontal=28, vertical=16),
                     expand=True),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status_text], spacing=10),
                ft.Container(content=lv, height=140, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=16),
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 2 OF 5", size=10, color=TEXT_MUTED),
        ft.Text("Preprocess Pages", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Downloads the comic pages from batcave.biz, detects panels with YOLO, "
            "and asks the vision LLM to extract text, speakers, and panel descriptions. "
            "Cached per page — re-runs are fast.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=16),
        primary_button("Run Preprocessing", run_click, icon=ft.Icons.PLAY_ARROW),
        ft.Container(height=12),
        primary_button("Approve & Continue →", approve_and_go,
                       disabled=not state.is_approved(2)),
        ft.Container(height=20),
        ft.Text("Selected page", size=10, color=TEXT_MUTED),
        ft.Container(content=detail_ctl, expand=True),
    ], spacing=8, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Preprocess Pages",
        header_subtitle="Scrape the comic, detect panels, and extract text.",
    )


def _thumbnail(pg: dict, *, on_click: Callable) -> ft.Control:
    img_path = pg.get("source_image") or ""
    page_type = (pg.get("page_type") or ("story" if pg.get("is_story_page") else "skip")).lower()
    panels = pg.get("panels") or []
    if page_type == "story":
        tag, tag_color = "STORY", STATUS_DONE
    elif page_type == "cover":
        tag, tag_color = "COVER", ACCENT
    else:
        tag = (pg.get("skip_reason", "SKIP")[:10] or "SKIP").upper()
        tag_color = STATUS_PENDING

    img_ctl = (
        ft.Image(src=img_path, width=160, height=200, fit=ft.BoxFit.COVER,
                 border_radius=4)
        if img_path and Path(img_path).exists()
        else ft.Container(width=160, height=200, bgcolor=BG_PANEL, border_radius=4)
    )
    return ft.Container(
        content=ft.Column([
            img_ctl,
            ft.Row([
                ft.Text(f"p{pg.get('page_number'):03d}" if pg.get('page_number') else "p?",
                        size=10, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Text(tag, size=8, color=tag_color, weight=ft.FontWeight.BOLD),
                    padding=ft.padding.symmetric(horizontal=4, vertical=2),
                    border=ft.border.all(1, tag_color), border_radius=3,
                ),
            ], spacing=4),
            ft.Text(f"{len(panels)} panels", size=9, color=TEXT_MUTED),
        ], spacing=4),
        padding=6,
        on_click=on_click, ink=True,
        border=ft.border.all(1, BORDER), border_radius=6, bgcolor=BG_ELEVATED,
    )
