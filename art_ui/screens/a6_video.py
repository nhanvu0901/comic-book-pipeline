# art_ui/screens/a6_video.py
"""A6: final 9:16 render (comic Stage 5 with no-mirror/no-inpaint overrides) +
the compliance youtube_description.txt for copy-paste."""
import subprocess
from pathlib import Path
from typing import Callable

import flet as ft
from flet_video import Video, VideoMedia

from ui.theme import BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN
from ui.bridge import format_exception, run_blocking

from .. import bridge
from ..layout import art_shell, log_list, primary_button, secondary_button
from ..state import ArtAppState, save_state


def build(page: ft.Page, state: ArtAppState, *,
          on_go: Callable[[int], None], on_state_change: Callable[[], None]) -> ft.Control:
    lv, push_log = log_list(page)
    status = ft.Text("Click Assemble to render the video.", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    video_slot = ft.Container(expand=True, alignment=ft.Alignment.CENTER)
    desc_text = ft.Text(bridge.load_youtube_description(state.project_name),
                        size=10, color=TEXT_MUTED, selectable=True)

    def _mount_video(path: Path):
        video_slot.content = Video(
            playlist=[VideoMedia(resource=str(path))],
            autoplay=False, show_controls=True, width=405, height=720,
        )

    final_path = bridge.ART_ROOT / state.project_name / "final.mp4" if state.project_name else None
    if final_path and final_path.exists():
        _mount_video(final_path)
        status.value = "final.mp4 exists — press Play below."
    else:
        video_slot.content = ft.Column([
            ft.Icon(ft.Icons.MOVIE_OUTLINED, size=64, color=TEXT_MUTED),
            ft.Text("Not assembled yet.", color=TEXT_MUTED, size=13),
        ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    async def _execute():
        running.visible = True
        status.value = "Rendering with ffmpeg (no mirror, no inpaint)…"
        status.color = WARN
        page.update()
        try:
            result_path = await run_blocking(bridge.run_video, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            status.value = "Failed — see log."
            status.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        running.visible = False
        p = Path(result_path)
        size_mb = p.stat().st_size / (1024 * 1024)
        status.value = f"Rendered {p.name} ({size_mb:.1f} MB)."
        status.color = SUCCESS
        _mount_video(p)
        desc_text.value = bridge.load_youtube_description(state.project_name)
        state.mark_approved(6)
        save_state(state)
        page.update()
        on_state_change()

    def open_folder(_e):
        if state.project_name:
            subprocess.run(["open", str(bridge.ART_ROOT / state.project_name)], check=False)

    def start_over(_e):
        state.reset()
        state.current_stage = 1
        save_state(state)
        on_go(1)

    center = ft.Column([
        ft.Container(content=video_slot,
                     padding=ft.padding.symmetric(horizontal=28, vertical=16), expand=True),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status], spacing=10),
                ft.Container(content=lv, height=120, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 6 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("Final Video", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Container(height=8),
        primary_button("Assemble Video", lambda _e: page.run_task(_execute),
                       icon=ft.Icons.MOVIE_FILTER),
        ft.Container(height=8),
        secondary_button("Open Project Folder", open_folder, icon=ft.Icons.FOLDER_OPEN),
        ft.Container(height=16),
        ft.Text("YOUTUBE DESCRIPTION (copy-paste)", size=10, color=TEXT_MUTED),
        ft.Container(content=ft.Column([desc_text], scroll=ft.ScrollMode.AUTO),
                     height=220, border=ft.border.all(1, BORDER), border_radius=6,
                     padding=8),
        ft.Container(height=8),
        secondary_button("Start a new artwork", start_over, icon=ft.Icons.ADD),
    ], spacing=8, expand=True, scroll=ft.ScrollMode.AUTO)

    return art_shell(center, right, state=state, on_go=on_go,
                     header_title="Final Video",
                     header_subtitle="Artwork is never mirrored or inpainted; description includes museum credit + CC0.")
