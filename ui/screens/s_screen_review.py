"""Select-beat review screen for screen_qa projects (no comic panels)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import flet as ft

import config
from ..layout import primary_button, secondary_button
from ..state import AppState, save_state
from ..theme import ACCENT, BG_PANEL, BORDER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN


def build_screen_review(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    """Build the screen_qa review UI showing narration beats and assigned video clips."""
    project = state.project_name
    proj_dir = Path(config.PROJECTS_ROOT) / (project or "")

    nar_file = proj_dir / "narration.json"
    if not project or not nar_file.exists():
        return ft.Container(
            content=ft.Column([
                ft.Text("Chưa có kịch bản narration cho dự án Screen QA.", size=16, color=WARN),
                ft.Text("Hãy chạy bước nghiên cứu và sinh kịch bản trước.", size=12, color=TEXT_MUTED),
            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            expand=True,
            alignment=ft.alignment.center,
        )

    nar_data = json.loads(nar_file.read_text("utf-8"))
    scenes = nar_data.get("scenes") or []

    clips_file = proj_dir / "review" / "clips" / "clips.json"
    clips_by_beat: dict[str, dict] = {}
    if clips_file.exists():
        try:
            m = json.loads(clips_file.read_text("utf-8"))
            for c in m.get("clips") or []:
                if c.get("beat"):
                    clips_by_beat[str(c["beat"])] = c
        except Exception:
            pass

    beat_cards: list[ft.Control] = []

    for s_idx, scene in enumerate(scenes):
        sid = int(scene.get("scene_id") or (s_idx + 1))
        vbeats = scene.get("visual_beats") or [scene.get("text", "")]
        for f_idx, vb_text in enumerate(vbeats):
            bk = f"{sid}:{f_idx}" if len(vbeats) > 1 else str(sid)
            has_clip = bk in clips_by_beat
            clip_info = clips_by_beat.get(bk, {})

            def _open_pick(_e, b=bk):
                async def _go():
                    await page.launch_url(f"/moments_review?project={project}&beat={b}")
                page.run_task(_go)

            actions = [
                ft.IconButton(
                    ft.Icons.VIDEO_FILE_OUTLINED,
                    icon_color=SUCCESS if has_clip else None,
                    tooltip="Chọn clip YouTube cho beat này",
                    on_click=_open_pick,
                )
            ]

            status_badge = ft.Container(
                content=ft.Text("CLIP ĐÃ CHỌN" if has_clip else "TEXT CARD FALLBACK", size=10, weight=ft.FontWeight.BOLD),
                bgcolor="#1E3A8A" if has_clip else "#374151",
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=4,
            )

            card = ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text(f"Beat {bk}", size=13, weight=ft.FontWeight.BOLD, color=ACCENT),
                        status_badge,
                        ft.Container(expand=True),
                        *actions,
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Text(vb_text, size=13, color=TEXT_PRIMARY),
                    ft.Text(
                        f"Video ID: {clip_info.get('id', 'N/A')} (Start: {clip_info.get('start', 0.0):.1f}s)"
                        if has_clip else "Chưa gán clip — sẽ tự động dùng text card không bao giờ crash",
                        size=11,
                        color=TEXT_MUTED,
                    ),
                ], spacing=6),
                padding=12,
                border=ft.border.all(1, BORDER),
                border_radius=6,
                bgcolor=BG_PANEL,
            )
            beat_cards.append(card)

    def _approve_and_continue(_e):
        state.mark_approved(5)
        save_state(state)
        on_go(5)

    approve_btn = primary_button(
        "Approve & Render Video",
        _approve_and_continue,
        icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
    )

    return ft.Column([
        ft.Row([
            ft.Text(f"Screen QA Review: {nar_data.get('title', project)}", size=18, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            approve_btn,
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.ListView(beat_cards, spacing=10, expand=True),
    ], expand=True, spacing=14)
