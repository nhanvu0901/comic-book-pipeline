# art_ui/screens/a5_tts.py
"""A5: Cartesia TTS via the art wrapper (comic Stage 4 reused; logs captured
via print-redirect in art_ui.bridge)."""
import subprocess
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
    status = ft.Text("Click Synthesize to generate the voiceover.", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)

    audio_path = bridge.ART_ROOT / state.project_name / "audio.wav" if state.project_name else None
    if audio_path and audio_path.exists():
        status.value = "audio.wav exists — Synthesize re-generates it."

    async def _execute():
        running.visible = True
        status.value = "Synthesizing via Cartesia…"
        status.color = WARN
        page.update()
        try:
            result = await run_blocking(bridge.run_tts, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            status.value = "Failed — see log."
            status.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        running.visible = False
        status.value = (f"Done: {result.get('audio_duration_seconds', 0)}s, "
                        f"{len(result.get('word_timestamps') or [])} words.")
        status.color = SUCCESS
        state.mark_approved(5)
        save_state(state)
        page.update()
        on_state_change()

    def play(_e):
        p = bridge.ART_ROOT / state.project_name / "audio.wav"
        if p.exists():
            subprocess.run(["open", str(p)], check=False)

    center = ft.Column([
        ft.Container(
            content=ft.Column([
                ft.Row([running, status], spacing=10),
                ft.Container(content=lv, expand=True, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8, expand=True),
            padding=ft.padding.symmetric(horizontal=28, vertical=16), expand=True,
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 5 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("TTS Audio", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text("Cartesia voiceover + word timestamps (drives caption timing and cuts).",
                size=12, color=TEXT_MUTED),
        ft.Container(height=12),
        primary_button("Synthesize", lambda _e: page.run_task(_execute), icon=ft.Icons.MIC),
        ft.Container(height=8),
        secondary_button("Play audio.wav", play, icon=ft.Icons.PLAY_CIRCLE),
        ft.Container(height=8),
        secondary_button("Next: Video →", lambda _e: on_go(6), icon=ft.Icons.ARROW_FORWARD),
    ], spacing=8, expand=True)

    return art_shell(center, right, state=state, on_go=on_go,
                     header_title="TTS Audio",
                     header_subtitle="Reuses comic Stage 4 unmodified via runtime PROJECTS_ROOT override.")
