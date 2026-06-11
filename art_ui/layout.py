# art_ui/layout.py
"""Art-tab layout: 6-stage stepper + 3-column shell. Mirrors ui/layout.py but
parameterized for the art stage list; reuses comic theme constants and the
generic button/log primitives read-only."""
from typing import Callable

import flet as ft

from ui.layout import log_list, primary_button, secondary_button  # noqa: F401  (re-exported for screens)
from ui.theme import (
    BG, BG_ELEVATED, BG_PANEL, BORDER, STATUS_DIRTY, STATUS_DONE,
    STATUS_PENDING, STATUS_REVIEW, TEXT_MUTED, TEXT_PRIMARY,
)

from .state import ArtAppState, N_STAGES, STAGE_NAMES


def status_for(state: ArtAppState, stage: int) -> tuple[str, str]:
    if state.is_approved(stage):
        if state.is_dirty(stage):
            return "STALE", STATUS_DIRTY
        return "DONE", STATUS_DONE
    if stage == state.current_stage:
        return "ACTIVE", STATUS_REVIEW
    return "PENDING", STATUS_PENDING


def art_stepper(state: ArtAppState, on_go: Callable[[int], None]) -> ft.Control:
    items: list[ft.Control] = [
        ft.Container(
            content=ft.Text("ART  →  SHORT", size=11, weight=ft.FontWeight.BOLD,
                            color=TEXT_MUTED),
            padding=ft.padding.only(left=20, top=22, bottom=18),
        ),
    ]
    for stage in range(1, N_STAGES + 1):
        label, color = status_for(state, stage)
        active = stage == state.current_stage

        def _click(_e, s=stage):
            on_go(s)

        row_items: list[ft.Control] = [
            ft.Text(f"{stage}", size=13, weight=ft.FontWeight.BOLD,
                    color=TEXT_PRIMARY if active else TEXT_MUTED),
            ft.Text(STAGE_NAMES[stage], size=13,
                    color=TEXT_PRIMARY if active else TEXT_MUTED,
                    weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_400),
            ft.Container(expand=True),
        ]
        if label != "DONE":
            row_items.insert(1, ft.Container(width=10, height=10, border_radius=5,
                                             bgcolor=color))
            row_items.append(ft.Container(
                content=ft.Text(label, size=9, color=color, weight=ft.FontWeight.BOLD),
                padding=ft.padding.symmetric(horizontal=6, vertical=2),
                border=ft.border.all(1, color), border_radius=3,
            ))
        items.append(ft.Container(
            content=ft.Row(row_items, spacing=10,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=18, vertical=14),
            bgcolor=BG_ELEVATED if active else None,
            border=ft.border.only(left=ft.BorderSide(3, color if active else BG_PANEL)),
            on_click=_click, ink=True,
        ))
    if state.project_name:
        items.append(ft.Container(
            content=ft.Column([
                ft.Text("PROJECT", size=9, color=TEXT_MUTED),
                ft.Text(state.project_name, size=12, color=TEXT_PRIMARY,
                        weight=ft.FontWeight.W_500, selectable=True),
            ], spacing=4),
            padding=ft.padding.symmetric(horizontal=20, vertical=14),
            margin=ft.margin.only(top=12),
            border=ft.border.only(top=ft.BorderSide(1, BORDER)),
        ))
    return ft.Container(
        content=ft.Column(items, spacing=0, expand=True),
        width=240, bgcolor=BG_PANEL,
        border=ft.border.only(right=ft.BorderSide(1, BORDER)),
    )


def art_shell(
    center: ft.Control,
    right: ft.Control,
    *,
    state: ArtAppState,
    on_go: Callable[[int], None],
    header_title: str = "",
    header_subtitle: str = "",
) -> ft.Control:
    header_parts: list[ft.Control] = [
        ft.Text(header_title, size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
    ]
    if header_subtitle:
        header_parts.append(ft.Text(header_subtitle, size=12, color=TEXT_MUTED))
    return ft.Row(
        [
            art_stepper(state, on_go),
            ft.Container(
                content=ft.Column([
                    ft.Container(
                        content=ft.Column(header_parts, spacing=3),
                        padding=ft.padding.only(left=28, right=28, top=22, bottom=16),
                        border=ft.border.only(bottom=ft.BorderSide(1, BORDER)),
                    ),
                    center,
                ], spacing=0, expand=True),
                expand=True, bgcolor=BG,
            ),
            ft.Container(content=right, width=320, bgcolor=BG_PANEL,
                         border=ft.border.only(left=ft.BorderSide(1, BORDER)),
                         padding=20),
        ],
        spacing=0, expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )
