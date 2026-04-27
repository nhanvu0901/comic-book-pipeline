"""
Shared layout primitives: the left stepper nav, the 3-column view shell,
and status chips.
"""
from typing import Callable

import flet as ft

from .state import AppState, STAGE_NAMES
from .theme import (
    ACCENT, BG, BG_ELEVATED, BG_PANEL, BORDER, STATUS_DIRTY, STATUS_DONE,
    STATUS_PENDING, STATUS_REVIEW, TEXT_MUTED, TEXT_PRIMARY,
)


def status_for(state: AppState, stage: int) -> tuple[str, str]:
    """Return (label, color) for a stage's current status."""
    if state.is_approved(stage):
        if state.is_dirty(stage):
            return "STALE", STATUS_DIRTY
        return "DONE", STATUS_DONE
    if stage == state.current_stage:
        return "ACTIVE", STATUS_REVIEW
    return "PENDING", STATUS_PENDING


def stepper_nav(state: AppState, on_go: Callable[[int], None]) -> ft.Control:
    items: list[ft.Control] = [
        ft.Container(
            content=ft.Text("COMIC  →  SHORT", size=11, weight=ft.FontWeight.BOLD,
                            color=TEXT_MUTED),
            padding=ft.padding.only(left=20, top=22, bottom=18),
        ),
    ]
    for stage in range(1, 6):
        label, color = status_for(state, stage)
        active = stage == state.current_stage
        items.append(_step_row(stage, active, label, color, on_go, state))
    if state.project_name:
        items.append(
            ft.Container(
                content=ft.Column([
                    ft.Text("PROJECT", size=9, color=TEXT_MUTED),
                    ft.Text(state.project_name, size=12, color=TEXT_PRIMARY,
                            weight=ft.FontWeight.W_500, selectable=True),
                ], spacing=4),
                padding=ft.padding.symmetric(horizontal=20, vertical=14),
                margin=ft.margin.only(top=12),
                border=ft.border.only(top=ft.BorderSide(1, BORDER)),
            )
        )
    return ft.Container(
        content=ft.Column(items, spacing=0, expand=True),
        width=240,
        bgcolor=BG_PANEL,
        border=ft.border.only(right=ft.BorderSide(1, BORDER)),
    )


def _step_row(
    stage: int, active: bool, label: str, color: str,
    on_go: Callable[[int], None], state: AppState,
) -> ft.Control:
    enabled = state.is_approved(stage) or stage == state.current_stage or stage == 1

    def _click(_e, s=stage):
        if enabled:
            on_go(s)

    dot = ft.Container(
        width=10, height=10, border_radius=5, bgcolor=color,
    )
    number = ft.Text(
        f"{stage}", size=13, weight=ft.FontWeight.BOLD,
        color=TEXT_PRIMARY if active else TEXT_MUTED,
    )
    title = ft.Text(
        STAGE_NAMES[stage], size=13,
        color=TEXT_PRIMARY if active else TEXT_MUTED,
        weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_400,
    )
    chip = ft.Container(
        content=ft.Text(label, size=9, color=color, weight=ft.FontWeight.BOLD,
                        ),
        padding=ft.padding.symmetric(horizontal=6, vertical=2),
        border=ft.border.all(1, color),
        border_radius=3,
    )
    row = ft.Container(
        content=ft.Row([number, dot, title, ft.Container(expand=True), chip],
                       spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.padding.symmetric(horizontal=18, vertical=14),
        bgcolor=BG_ELEVATED if active else None,
        border=ft.border.only(left=ft.BorderSide(3, color if active else BG_PANEL)),
        on_click=_click if enabled else None,
        ink=enabled,
    )
    return row


def three_col(
    center: ft.Control,
    right: ft.Control,
    *,
    state: AppState,
    on_go: Callable[[int], None],
    header_title: str = "",
    header_subtitle: str = "",
) -> ft.Control:
    return ft.Row(
        [
            stepper_nav(state, on_go),
            ft.Container(
                content=ft.Column(
                    [_header(header_title, header_subtitle), center],
                    spacing=0, expand=True,
                ),
                expand=True,
                bgcolor=BG,
            ),
            ft.Container(
                content=right,
                width=320,
                bgcolor=BG_PANEL,
                border=ft.border.only(left=ft.BorderSide(1, BORDER)),
                padding=20,
            ),
        ],
        spacing=0,
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )


def _header(title: str, subtitle: str) -> ft.Control:
    parts: list[ft.Control] = [
        ft.Text(title, size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
    ]
    if subtitle:
        parts.append(ft.Text(subtitle, size=12, color=TEXT_MUTED))
    return ft.Container(
        content=ft.Column(parts, spacing=3),
        padding=ft.padding.only(left=28, right=28, top=22, bottom=16),
        border=ft.border.only(bottom=ft.BorderSide(1, BORDER)),
    )


# ─── Small reusable bits ──────────────────────────────────────────────────

def primary_button(label: str, on_click, *, icon=None, disabled: bool = False) -> ft.ElevatedButton:
    return ft.ElevatedButton(
        label,
        on_click=on_click,
        icon=icon,
        disabled=disabled,
        bgcolor=ACCENT,
        color="#ffffff",
        height=42,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.padding.symmetric(horizontal=20),
        ),
    )


def secondary_button(label: str, on_click, *, icon=None, disabled: bool = False) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        label,
        on_click=on_click,
        icon=icon,
        disabled=disabled,
        height=42,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.padding.symmetric(horizontal=16),
            side=ft.BorderSide(1, BORDER),
            color=TEXT_PRIMARY,
        ),
    )


def log_list(page: ft.Page, max_lines: int = 300) -> tuple[ft.Control, Callable[[str], None]]:
    """
    Return (container, push_line). The container is a Stack with a scrollable
    ListView of log lines plus a small copy-all IconButton pinned to the top
    right corner. One click copies every visible line to the clipboard via
    Flet 0.84's `ft.Clipboard` service (the old `page.set_clipboard` was
    removed before 0.80).
    """
    lv = ft.ListView(expand=True, spacing=1, padding=8, auto_scroll=True)
    lines_cache: list[str] = []

    clipboard = ft.Clipboard()
    clipboard_attached = {"done": False}

    def _ensure_clipboard_attached():
        if clipboard_attached["done"]:
            return
        try:
            services = page.services
            if clipboard not in services:
                services.append(clipboard)
            clipboard_attached["done"] = True
        except Exception:
            pass

    copy_btn = ft.IconButton(
        icon=ft.Icons.CONTENT_COPY,
        icon_size=14,
        icon_color=TEXT_MUTED,
        tooltip="Copy entire log",
        style=ft.ButtonStyle(padding=ft.padding.all(4)),
    )

    def push(line: str) -> None:
        lines_cache.append(line)
        stripped = line.strip()
        if not stripped or set(stripped) <= {"═", "─", "-", "="}:
            color, size, weight = TEXT_MUTED, 9, ft.FontWeight.W_300
        elif "PHASE:" in stripped or stripped.startswith(("STAGE", "══")):
            color, size, weight = TEXT_PRIMARY, 13, ft.FontWeight.BOLD
        elif stripped.startswith(("🤖", "❓", "💬")):
            color, size, weight = ACCENT, 11, ft.FontWeight.W_600
        elif stripped.startswith(("⚠️", "❌")):
            color, size, weight = "#e0a060", 11, ft.FontWeight.W_500
        elif stripped.startswith(("✅", "✓")):
            color, size, weight = "#3ecf8e", 11, ft.FontWeight.W_500
        else:
            color, size, weight = TEXT_MUTED, 11, ft.FontWeight.W_400
        lv.controls.append(
            ft.Text(line, size=size, color=color, font_family="Menlo",
                    selectable=True, weight=weight)
        )
        if len(lv.controls) > max_lines:
            overflow = len(lv.controls) - max_lines
            del lv.controls[:overflow]
            del lines_cache[:overflow]
        try:
            lv.update()
        except Exception:
            pass

    async def _do_copy(txt: str):
        await clipboard.set(txt)

    def _copy_all(_e):
        _ensure_clipboard_attached()
        text = "\n".join(lines_cache) or "(log empty)"
        copied_ok = False
        try:
            page.run_task(_do_copy, text)
            copied_ok = True
        except Exception:
            copied_ok = False

        if copied_ok:
            copy_btn.icon = ft.Icons.CHECK
            copy_btn.icon_color = "#3ecf8e"
            copy_btn.tooltip = f"Copied {len(text)} chars"
        else:
            copy_btn.icon = ft.Icons.ERROR_OUTLINE
            copy_btn.icon_color = "#e06060"
            copy_btn.tooltip = "Clipboard failed"

        try:
            copy_btn.update()
        except Exception:
            pass

        def _reset():
            copy_btn.icon = ft.Icons.CONTENT_COPY
            copy_btn.icon_color = TEXT_MUTED
            copy_btn.tooltip = "Copy entire log"
            try:
                page.run_task(_async_update_btn)
            except Exception:
                pass

        async def _async_update_btn():
            copy_btn.update()

        import threading
        threading.Timer(1.2, _reset).start()

    copy_btn.on_click = _copy_all

    stack = ft.Stack(
        controls=[
            lv,
            ft.Container(
                content=copy_btn,
                top=2, right=2,
                bgcolor=BG_PANEL,
                border_radius=4,
            ),
        ],
        expand=True,
    )
    return stack, push
