"""
Shared layout primitives: the left stepper nav, the 3-column view shell,
and status chips.
"""
import asyncio
from pathlib import Path
from typing import Callable

import flet as ft

from .clipboard import BLOCKED_HINT, copy_text
from .project_log import append_log
from .state import AppState, PICKER_STAGE, STAGE_NAMES
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
    # Every stage the app routes to (STAGE_NAMES mirrors app.STAGE_BUILDERS). A literal
    # range here was bumped by hand as stages were added and stopped at 7 when the review
    # gate went in, so Final Video (8) had no row and could only be reached by a button.
    for stage in sorted(STAGE_NAMES):
        label, color = status_for(state, stage)
        active = stage == state.current_stage
        items.append(_step_row(stage, active, label, color, on_go, state))
    footer_children: list[ft.Control] = []
    if state.project_name:
        footer_children.append(ft.Text("PROJECT", size=9, color=TEXT_MUTED))
        footer_children.append(
            ft.Text(state.project_name, size=12, color=TEXT_PRIMARY,
                     weight=ft.FontWeight.W_500, selectable=True)
        )
    # The only way back to the project list. This must render UNCONDITIONALLY: Stage 1's
    # Research Scout runs before any project exists (state.project_name == ""), so gating
    # this button on project_name made an unfinished research session a dead end — there
    # was no route back to the picker at all until a project got created.
    footer_children.append(
        ft.TextButton(
            "All projects",
            icon=ft.Icons.ARROW_BACK,
            on_click=lambda _e: on_go(PICKER_STAGE),
            style=ft.ButtonStyle(padding=ft.padding.all(0)),
        )
    )
    items.append(
        ft.Container(
            content=ft.Column(footer_children, spacing=4),
            width=240,   # span the rail, so the divider above it does not shrink to the text
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
    def _click(_e, s=stage):
        on_go(s)

    number = ft.Text(
        f"{stage}", size=13, weight=ft.FontWeight.BOLD,
        color=TEXT_PRIMARY if active else TEXT_MUTED,
    )
    title = ft.Text(
        STAGE_NAMES[stage], size=13,
        color=TEXT_PRIMARY if active else TEXT_MUTED,
        weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_400,
    )
    row_items: list[ft.Control] = [number, title, ft.Container(expand=True)]
    if label != "DONE":
        row_items.insert(1, ft.Container(
            width=10, height=10, border_radius=5, bgcolor=color,
        ))
        row_items.append(ft.Container(
            content=ft.Text(label, size=9, color=color, weight=ft.FontWeight.BOLD),
            padding=ft.padding.symmetric(horizontal=6, vertical=2),
            border=ft.border.all(1, color),
            border_radius=3,
        ))
    return ft.Container(
        content=ft.Row(row_items, spacing=10,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.padding.symmetric(horizontal=18, vertical=14),
        bgcolor=BG_ELEVATED if active else None,
        border=ft.border.only(left=ft.BorderSide(3, color if active else BG_PANEL)),
        on_click=_click,
        ink=True,
    )


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
        ft.Text(title, size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, selectable=True),
    ]
    if subtitle:
        parts.append(ft.Text(subtitle, size=12, color=TEXT_MUTED, selectable=True))
    return ft.Container(
        content=ft.Column(parts, spacing=3),
        padding=ft.padding.only(left=28, right=28, top=22, bottom=16),
        border=ft.border.only(bottom=ft.BorderSide(1, BORDER)),
    )


# ─── Small reusable bits ──────────────────────────────────────────────────

def primary_button(label: str, on_click=None, *, icon=None, disabled: bool = False, url: str | None = None) -> ft.ElevatedButton:
    # Colours per control state, not one fixed bgcolor: a fixed ACCENT painted a disabled
    # button exactly like an enabled one, so a gated action (Download while reader URLs
    # are missing, Approve before audio exists) looked clickable and silently did nothing.
    # Per-state colours also follow a `disabled` flipped after the button was built.
    return ft.ElevatedButton(
        label,
        on_click=on_click,
        icon=icon,
        disabled=disabled,
        url=url,
        height=42,
        style=ft.ButtonStyle(
            bgcolor={ft.ControlState.DISABLED: BG_ELEVATED, ft.ControlState.DEFAULT: ACCENT},
            color={ft.ControlState.DISABLED: TEXT_MUTED, ft.ControlState.DEFAULT: "#ffffff"},
            icon_color={ft.ControlState.DISABLED: TEXT_MUTED, ft.ControlState.DEFAULT: "#ffffff"},
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.padding.symmetric(horizontal=20),
        ),
    )


def secondary_button(label: str, on_click=None, *, icon=None, disabled: bool = False, url: str | None = None) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        label,
        on_click=on_click,
        icon=icon,
        disabled=disabled,
        url=url,
        height=42,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.padding.symmetric(horizontal=16),
            side=ft.BorderSide(1, BORDER),
            color=TEXT_PRIMARY,
        ),
    )


def log_list(
    page: ft.Page, max_lines: int = 300, *, log_path: Callable[[], Path | None] | None = None,
) -> tuple[ft.Control, Callable[[str], None]]:
    """
    Return (container, push_line). The container is a Stack with a scrollable
    ListView of log lines plus a small copy-all IconButton pinned to the top
    right corner. One click copies every visible line to the clipboard via
    Flet 0.84's `ft.Clipboard` service (the old `page.set_clipboard` was
    removed before 0.80).

    log_path: returns the file to append every line to as well (see ui.project_log),
    asked per line because the open project can change while the screen is up.
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
        if log_path is not None:
            append_log(log_path(), line)
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

    async def _copy_and_mark(txt: str):
        # Mark the button from the copy's REAL result. It used to turn green as soon as
        # the copy was *scheduled*, which on a plain-http LAN address (clipboard blocked)
        # announced a copy that never happened.
        copied = await copy_text(clipboard, txt)
        if copied:
            copy_btn.icon = ft.Icons.CHECK
            copy_btn.icon_color = "#3ecf8e"
            copy_btn.tooltip = f"Copied {len(txt)} chars"
        else:
            copy_btn.icon = ft.Icons.ERROR_OUTLINE
            copy_btn.icon_color = "#e06060"
            copy_btn.tooltip = f"Not copied — {BLOCKED_HINT}"
        try:
            copy_btn.update()
        except Exception:
            pass
        await asyncio.sleep(1.2 if copied else 4)
        copy_btn.icon = ft.Icons.CONTENT_COPY
        copy_btn.icon_color = TEXT_MUTED
        copy_btn.tooltip = "Copy entire log"
        try:
            copy_btn.update()
        except Exception:
            pass

    def _copy_all(_e):
        _ensure_clipboard_attached()
        text = "\n".join(lines_cache) or "(log empty)"
        try:
            page.run_task(_copy_and_mark, text)
        except Exception:
            pass

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
