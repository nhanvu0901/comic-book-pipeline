"""
Screen 4: Cartesia TTS review.

Synthesizes narration with the chosen voice + model, then lets the user
preview the result with a real player (play/pause toggle, seek bar,
position/duration readout). No speed control — pipeline always runs at 1.0.
"""
from __future__ import annotations

import json
from typing import Callable

import flet as ft
from flet_audio import Audio, AudioState

from config import PROJECTS_ROOT, CARTESIA_MODEL, CARTESIA_VOICE_ID
from ..bridge import format_exception, run_blocking, run_stage_4
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)
from utils.clear_stage import clear_stage_4


# ─── Voice/model presets ────────────────────────────────────────────────────
# (label, voice_id) — UUIDs pasted via "Custom" override these.
VOICE_PRESETS: list[tuple[str, str]] = [
    ("Comic Vocal (cloned)", "f7248031-b419-4004-b447-2e9bf32f6b5e"),
    ("Barbershop Man",       "a0e99841-438c-4a64-b679-ae501e7d6091"),
]
CUSTOM_LABEL = "Custom UUID…"

MODEL_OPTIONS: list[str] = ["sonic-2", "sonic"]


def _fmt_ms(ms: int | float) -> str:
    s = max(0, int(ms)) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def _voice_label_for(voice_id: str) -> str:
    """Map a stored UUID back to the dropdown label, or fall back to Custom."""
    for label, uid in VOICE_PRESETS:
        if uid == voice_id:
            return label
    return CUSTOM_LABEL


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    audio_path = PROJECTS_ROOT / state.project_name / "audio.wav" if state.project_name else None
    existing_audio = bool(audio_path and audio_path.exists())

    # ─── Player widgets (defined first so audio handlers can close over them) ──
    play_btn = ft.IconButton(
        icon=ft.Icons.PLAY_CIRCLE_FILLED,
        icon_size=44,
        icon_color=ACCENT,
        tooltip="Play",
    )
    seek_slider = ft.Slider(
        min=0, max=1, value=0,
        active_color=ACCENT, inactive_color=BORDER,
        expand=True, disabled=not existing_audio,
    )
    pos_label = ft.Text("00:00", size=11, color=TEXT_MUTED, font_family="Menlo")
    dur_label = ft.Text("00:00", size=11, color=TEXT_MUTED, font_family="Menlo")

    # mutable state captured by handlers
    duration_ms = {"v": 0}
    user_seeking = {"v": False}
    is_playing = {"v": False}

    def _set_play_icon(playing: bool):
        play_btn.icon = ft.Icons.PAUSE_CIRCLE_FILLED if playing else ft.Icons.PLAY_CIRCLE_FILLED
        play_btn.tooltip = "Pause" if playing else "Play"

    # ─── Audio events ──────────────────────────────────────────────────────
    def on_duration_change(e):
        d = e.duration.in_milliseconds if e.duration else 0
        duration_ms["v"] = d
        seek_slider.max = max(d, 1)
        seek_slider.disabled = d == 0
        dur_label.value = _fmt_ms(d)
        try:
            page.update()
        except Exception:
            pass

    def on_position_change(e):
        if user_seeking["v"]:
            return
        seek_slider.value = min(e.position, seek_slider.max)
        pos_label.value = _fmt_ms(e.position)
        try:
            page.update()
        except Exception:
            pass

    def on_audio_state_change(e):
        playing = e.state == AudioState.PLAYING
        is_playing["v"] = playing
        _set_play_icon(playing)
        if e.state == AudioState.COMPLETED:
            seek_slider.value = 0
            pos_label.value = _fmt_ms(0)
        try:
            page.update()
        except Exception:
            pass

    audio_ctl = Audio(
        src=str(audio_path) if existing_audio else None,
        autoplay=False,
        on_duration_change=on_duration_change,
        on_position_change=on_position_change,
        on_state_change=on_audio_state_change,
    )

    # ─── Synthesis controls (right rail) ───────────────────────────────────
    initial_voice = state.tts_voice_id or CARTESIA_VOICE_ID
    initial_voice_label = _voice_label_for(initial_voice)
    initial_model = state.tts_model or CARTESIA_MODEL

    voice_dropdown = ft.Dropdown(
        label="Voice",
        value=initial_voice_label,
        options=[ft.dropdown.Option(label) for label, _ in VOICE_PRESETS]
                + [ft.dropdown.Option(CUSTOM_LABEL)],
        border_color=BORDER, focused_border_color=ACCENT, text_size=12,
    )
    custom_voice_field = ft.TextField(
        label="Custom voice UUID",
        value=initial_voice if initial_voice_label == CUSTOM_LABEL else "",
        hint_text="Cartesia voice UUID (from Voice Clone API)",
        border_color=BORDER, focused_border_color=ACCENT, text_size=12,
        visible=initial_voice_label == CUSTOM_LABEL,
    )
    model_dropdown = ft.Dropdown(
        label="Model",
        value=initial_model if initial_model in MODEL_OPTIONS else MODEL_OPTIONS[0],
        options=[ft.dropdown.Option(m) for m in MODEL_OPTIONS],
        border_color=BORDER, focused_border_color=ACCENT, text_size=12,
    )

    def _on_voice_change(_e):
        custom_voice_field.visible = voice_dropdown.value == CUSTOM_LABEL
        page.update()
    voice_dropdown.on_change = _on_voice_change

    # ─── Status / log ──────────────────────────────────────────────────────
    status_text = ft.Text(
        f"audio.wav: {'ready — press Play' if existing_audio else 'not yet synthesized'}",
        color=TEXT_MUTED, size=12,
    )
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    lv, push_log = log_list(page)
    info = ft.Column([], spacing=3)

    def _update_info():
        if not state.project_name:
            info.controls = []
            return
        root = PROJECTS_ROOT / state.project_name
        sc_path = root / "scene_timings.json"
        cap_path = root / "caption_chunks.json"
        ap = root / "audio.wav"
        rows: list[ft.Control] = []
        if ap.exists():
            size_mb = ap.stat().st_size / (1024 * 1024)
            rows.append(_kv("audio.wav", f"{size_mb:.1f} MB"))
        if sc_path.exists():
            try:
                scenes = json.loads(sc_path.read_text())
                if scenes:
                    last_end = max(float(s.get("end", 0)) for s in scenes)
                    rows.append(_kv(
                        "duration",
                        f"{last_end:.2f}s " + ("⚠️ over 58s" if last_end > 58 else ""),
                    ))
                    rows.append(_kv("scenes aligned", str(len(scenes))))
            except json.JSONDecodeError:
                pass
        if cap_path.exists():
            try:
                caps = json.loads(cap_path.read_text())
                rows.append(_kv("caption chunks", str(len(caps))))
            except json.JSONDecodeError:
                pass
        info.controls = rows
        try:
            info.update()
        except Exception:
            pass

    _update_info()

    # ─── Resolvers ─────────────────────────────────────────────────────────
    def _resolve_voice_id() -> str:
        if voice_dropdown.value == CUSTOM_LABEL:
            return (custom_voice_field.value or "").strip()
        for label, uid in VOICE_PRESETS:
            if label == voice_dropdown.value:
                return uid
        return CARTESIA_VOICE_ID

    # ─── Synthesize ────────────────────────────────────────────────────────
    async def _execute():
        voice_id = _resolve_voice_id()
        model = (model_dropdown.value or CARTESIA_MODEL).strip()

        state.tts_voice_id = voice_id
        state.tts_model = model
        save_state(state)

        running.visible = True
        status_text.value = f"Calling Cartesia ({model})…"
        status_text.color = WARN
        page.update()
        try:
            result = await run_blocking(
                run_stage_4,
                state.project_name,
                voice_id or None,
                model or None,
                push_log,
            )
        except Exception as e:
            running.visible = False
            status_text.value = "Failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return

        running.visible = False
        dur = result.get("audio_duration_seconds", 0.0)
        status_text.value = f"Synthesized {dur:.2f}s — press Play."
        status_text.color = SUCCESS

        # Reload the player with the fresh file
        new_path = PROJECTS_ROOT / state.project_name / "audio.wav"
        audio_ctl.src = str(new_path)
        seek_slider.value = 0
        seek_slider.disabled = False
        pos_label.value = _fmt_ms(0)
        _set_play_icon(False)
        is_playing["v"] = False
        _update_info()
        page.update()

    # ─── Player handlers ───────────────────────────────────────────────────
    async def play_pause_click(_e):
        if not audio_ctl.src:
            status_text.value = "No audio yet — click Synthesize first."
            status_text.color = DANGER
            page.update()
            return
        try:
            if is_playing["v"]:
                await audio_ctl.pause()
            else:
                await audio_ctl.play()
        except Exception as e:
            push_log(f"playback failed: {e}")

    play_btn.on_click = play_pause_click

    def slider_change(e):
        # Continuous drag — just preview the position label, don't seek yet.
        user_seeking["v"] = True
        pos_label.value = _fmt_ms(e.control.value)
        try:
            pos_label.update()
        except Exception:
            pass

    async def slider_change_end(e):
        target_ms = int(e.control.value)
        try:
            await audio_ctl.seek(ft.Duration(milliseconds=target_ms))
        except Exception as exc:
            push_log(f"seek failed: {exc}")
        user_seeking["v"] = False

    seek_slider.on_change = slider_change
    seek_slider.on_change_end = slider_change_end

    def generate_click(_e):
        page.run_task(_execute)

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    clear_radio = ft.RadioGroup(
        value="all",
        content=ft.Column([
            ft.Radio(value="all", label="Clear all (re-bills Cartesia)"),
            ft.Radio(value="alignment", label="Clear alignment only (free)"),
        ], tight=True, spacing=2),
    )

    def _do_clear_4(dialog):
        alignment_only = clear_radio.value == "alignment"
        try:
            removed = clear_stage_4(state.project_name, alignment_only=alignment_only)
        except Exception as e:
            page.pop_dialog()
            _show_snack(str(e))
            return
        page.pop_dialog()
        if removed:
            _show_snack(
                f"Removed {len(removed)} item(s): "
                + ", ".join(p.name for p in removed)
            )
        else:
            _show_snack("Nothing to clear.")
        new_path = (PROJECTS_ROOT / state.project_name / "audio.wav"
                    if state.project_name else None)
        still_exists = bool(new_path and new_path.exists())
        audio_ctl.src = str(new_path) if still_exists else None
        seek_slider.value = 0
        seek_slider.disabled = not still_exists
        pos_label.value = _fmt_ms(0)
        dur_label.value = _fmt_ms(0)
        duration_ms["v"] = 0
        is_playing["v"] = False
        _set_play_icon(False)
        status_text.value = (
            "audio.wav: ready — press Play" if still_exists
            else "audio.wav: not yet synthesized"
        )
        status_text.color = TEXT_MUTED
        _update_info()
        page.update()

    def open_clear_dialog(_e):
        clear_radio.value = "all"
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Clear Stage 4 data"),
            content=clear_radio,
            actions=[
                ft.TextButton("Cancel", on_click=lambda _e: page.pop_dialog()),
                secondary_button("Clear", lambda _e: _do_clear_4(dialog)),
            ],
        )
        page.show_dialog(dialog)

    def approve_and_go(_e):
        state.mark_approved(5)
        state.current_stage = 6
        save_state(state)
        on_go(6)

    # ─── Layout ────────────────────────────────────────────────────────────
    player_card = ft.Container(
        content=ft.Column([
            ft.Text("PREVIEW", size=10, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
            ft.Container(height=4),
            ft.Row(
                [
                    play_btn,
                    ft.Column(
                        [
                            seek_slider,
                            ft.Row(
                                [pos_label, ft.Container(expand=True), dur_label],
                                spacing=0,
                            ),
                        ],
                        spacing=0, expand=True,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ], spacing=4),
        padding=ft.padding.symmetric(horizontal=20, vertical=16),
        border=ft.border.all(1, BORDER),
        border_radius=8,
        bgcolor=BG_ELEVATED,
    )

    info_card = ft.Container(
        content=info,
        padding=16,
        border=ft.border.all(1, BORDER),
        border_radius=8,
        bgcolor=BG_ELEVATED,
    )

    center = ft.Column([
        ft.Container(
            content=ft.Column([
                player_card,
                ft.Container(height=14),
                info_card,
            ], spacing=0, expand=True),
            padding=ft.padding.symmetric(horizontal=28, vertical=18),
            expand=True,
        ),
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
        ft.Text("STEP 5 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("TTS Audio", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text("Cartesia TTS — voice + model selectable. Word-level timestamps.",
                size=12, color=TEXT_MUTED),
        ft.Container(height=18),
        voice_dropdown,
        custom_voice_field,
        model_dropdown,
        ft.Container(height=14),
        primary_button("Synthesize", generate_click, icon=ft.Icons.GRAPHIC_EQ),
        ft.Container(height=8),
        secondary_button("Clear…", open_clear_dialog, icon=ft.Icons.DELETE_OUTLINE),
        ft.Container(height=14),
        primary_button("Approve & Continue →", approve_and_go,
                       disabled=not state.is_approved(5)),
    ], spacing=10, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Synthesize Narration Audio",
        header_subtitle="Cartesia TTS + word timestamps. Play to preview.",
    )


def _kv(label: str, value: str) -> ft.Control:
    return ft.Row([
        ft.Container(content=ft.Text(label, size=10, color=TEXT_MUTED,
                                     weight=ft.FontWeight.BOLD),
                     width=110),
        ft.Text(value, size=13, color=TEXT_PRIMARY, selectable=True),
    ])
