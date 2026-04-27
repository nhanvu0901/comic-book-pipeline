"""
Screen 5: Final video assembly + review.

Runs Stage 5 (ffmpeg Ken Burns + captions + audio), shows final.mp4 in a
player, lets the user open the output folder or re-run.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import flet as ft
from flet_video import Video, VideoMedia

from config import GDRIVE_BASE
from ..bridge import format_exception, run_blocking, run_stage_5
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    final_path = GDRIVE_BASE / state.project_name / "final.mp4" if state.project_name else None
    existing = final_path and final_path.exists()

    video_slot = ft.Container(expand=True, alignment=ft.Alignment.CENTER)
    status_text = ft.Text(
        "final.mp4 already exists — press Play below" if existing else "Click Assemble to build the video.",
        color=TEXT_MUTED, size=12,
    )
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    lv, push_log = log_list(page)

    def _mount_video(path: Path):
        v = Video(
            playlist=[VideoMedia(resource=str(path))],
            autoplay=False,
            show_controls=True,
            width=405,   # 9:16 at reasonable screen size
            height=720,
        )
        video_slot.content = v

    if existing:
        _mount_video(final_path)
    else:
        video_slot.content = ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.MOVIE_OUTLINED, size=64, color=TEXT_MUTED),
                ft.Text("Not assembled yet.", color=TEXT_MUTED, size=13),
            ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    async def _execute():
        running.visible = True
        status_text.value = "Rendering with ffmpeg…"
        status_text.color = WARN
        page.update()
        try:
            result_path = await run_blocking(run_stage_5, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            status_text.value = "Failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return

        running.visible = False
        p = Path(result_path)
        size_mb = p.stat().st_size / (1024 * 1024)
        status_text.value = f"Rendered {p.name} ({size_mb:.1f} MB)."
        status_text.color = SUCCESS
        _mount_video(p)

        state.mark_approved(5)
        save_state(state)
        page.update()
        on_state_change()

    def assemble_click(_e):
        page.run_task(_execute)

    def open_folder(_e):
        if not state.project_name:
            return
        folder = GDRIVE_BASE / state.project_name
        try:
            subprocess.run(["open", str(folder)], check=False)
        except Exception as e:
            push_log(f"open failed: {e}")

    def start_over(_e):
        state.reset()
        save_state(state)
        on_state_change()
        on_go(1)

    center = ft.Column([
        ft.Container(content=video_slot,
                     padding=ft.padding.symmetric(horizontal=28, vertical=16),
                     expand=True),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status_text], spacing=10),
                ft.Container(content=lv, height=120, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 5 OF 5", size=10, color=TEXT_MUTED),
        ft.Text("Final Video", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "1080×1920 9:16 H.264 MP4 with Ken Burns on panels, MrBeast-style "
            "captions, and Cartesia audio.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=16),
        primary_button("Assemble Video", assemble_click, icon=ft.Icons.MOVIE_FILTER),
        ft.Container(height=8),
        secondary_button("Open Project Folder", open_folder, icon=ft.Icons.FOLDER_OPEN),
        ft.Container(height=20),
        ft.Text("WHEN YOU'RE DONE", size=10, color=TEXT_MUTED),
        secondary_button("Start a new project", start_over, icon=ft.Icons.ADD),
    ], spacing=8, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Review & Export",
        header_subtitle="Play the rendered Short. If something's off, go back to any earlier stage.",
    )
