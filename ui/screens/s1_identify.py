"""
Screen 1: Identify Comic.

User enters a prompt. We run the Stage 1 agent in the background, stream
its logs, display the resulting comic_context as a card, user approves
or types a different prompt.
"""
from __future__ import annotations

from typing import Callable

import flet as ft

from config import GDRIVE_BASE
from ..bridge import InputBridge, format_exception, run_blocking, run_stage_1
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    # Center widgets
    preview = ft.Container(expand=True)
    prompt_field = ft.TextField(
        value=state.last_prompt,
        hint_text="e.g. The death of Gwen Stacy  ·  Invincible vs Omni-Man  ·  One Piece Marineford",
        multiline=False,
        autofocus=True,
        expand=True,
        border_color=BORDER,
        focused_border_color=ACCENT,
        on_submit=lambda e: run_btn_click(e),
    )

    lv, push_log = log_list(page)
    status_text = ft.Text("Ready.", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)

    # Q&A row — shown whenever the agent pauses for input.  Highly visible
    # (thick accent border, icon, bright label) so the user can't miss it.
    question_text = ft.Text("", color=TEXT_PRIMARY, size=14,
                            weight=ft.FontWeight.W_500, selectable=True)
    answer_field = ft.TextField(
        hint_text="Type your answer and press Enter  ·  type 'skip' to let the agent decide",
        border_color=ACCENT, focused_border_color=ACCENT, expand=True,
        text_size=13,
    )
    qa_row = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.QUESTION_ANSWER_ROUNDED, color=ACCENT, size=18),
                ft.Text("AGENT NEEDS YOUR INPUT", size=10, color=ACCENT,
                        weight=ft.FontWeight.BOLD),
            ], spacing=6),
            question_text,
            ft.Row([answer_field,
                    ft.ElevatedButton("Send", bgcolor=ACCENT, color="#ffffff",
                                      on_click=lambda e: _submit_answer(e))],
                   spacing=8),
        ], spacing=8),
        padding=14,
        bgcolor=BG_ELEVATED,
        border=ft.border.all(2, ACCENT),
        border_radius=8,
        visible=False,
    )

    bridge_holder: dict[str, InputBridge | None] = {"b": None}

    async def _apply_question_ui(prompt_str: str):
        # Runs on Flet's event loop → safe to mutate controls + focus.
        question_text.value = prompt_str
        answer_field.value = ""
        qa_row.visible = True
        page.update()
        try:
            answer_field.focus()
        except Exception:
            pass

    def _show_question(prompt_str: str):
        """
        Called from the Stage-1 worker thread (via bridge.on_question).
        Marshal the UI update onto Flet's event loop so cross-thread
        control mutations are safe.
        """
        try:
            page.run_task(_apply_question_ui, prompt_str)
        except Exception:
            # Fallback: direct update — may warn in dev tools but still paints.
            question_text.value = prompt_str
            answer_field.value = ""
            qa_row.visible = True
            try:
                page.update()
            except Exception:
                pass
        push_log(f"↪ Waiting on your answer: {prompt_str}")

    def _submit_answer(_e):
        b = bridge_holder["b"]
        if not b or not b.pending():
            return
        ans = (answer_field.value or "").strip()
        qa_row.visible = False
        question_text.value = ""
        answer_field.value = ""
        page.update()
        push_log(f"→ answered: {ans or '(empty → skip)'}")
        b.answer(ans)

    answer_field.on_submit = _submit_answer

    def render_context(ctx: dict) -> ft.Control:
        rows = [
            _row("Title", ctx.get("title", "?")),
            _row("Series", f"{ctx.get('series','?')} {ctx.get('issues','')}".strip()),
            _row("Year", ctx.get("year", "?")),
            _row("Publisher", ctx.get("publisher", "?")),
            _row("Writer / Artist", f"{ctx.get('writer','?')} / {ctx.get('artist','?')}"),
            _row("Era", ctx.get("era", "?")),
            _row("Characters", ", ".join(ctx.get("characters", []) or ["?"])),
        ]
        bat = ctx.get("batcave_url") or ""
        rows.append(
            _row("batcave_url", bat or "(missing — required for Stage 2)",
                 color=TEXT_PRIMARY if bat else WARN)
        )
        plot = ctx.get("plot_summary", "")
        plot_preview = plot[:500] + ("…" if len(plot) > 500 else "") if plot else ""
        if plot_preview:
            rows.append(ft.Container(height=12))
            rows.append(ft.Text("Plot summary (from wiki):", size=11,
                                color=TEXT_MUTED, weight=ft.FontWeight.BOLD,
                                spans=[]))
            rows.append(ft.Text(plot_preview, size=13, color=TEXT_PRIMARY, selectable=True))

        return ft.Container(
            content=ft.Column(rows, spacing=8),
            padding=20,
            border=ft.border.all(1, BORDER),
            border_radius=8,
            bgcolor=BG_ELEVATED,
        )

    async def _execute(prompt: str):
        running.visible = True
        status_text.value = "Running Stage 1…"
        status_text.color = WARN
        page.update()

        bridge = InputBridge(on_question=_show_question)
        bridge_holder["b"] = bridge

        try:
            ctx, project_name = await run_blocking(
                run_stage_1, prompt, push_log, bridge.ask,
            )
        except Exception as e:
            bridge.cancel()
            qa_row.visible = False
            running.visible = False
            status_text.value = "Failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        finally:
            bridge_holder["b"] = None

        state.project_name = project_name
        state.last_prompt = prompt
        state.mark_approved(1)
        state.current_stage = max(state.current_stage, 2)
        save_state(state)

        preview.content = render_context(ctx)
        running.visible = False
        status_text.value = "Stage 1 complete."
        status_text.color = SUCCESS
        page.update()
        on_state_change()

    def run_btn_click(_e):
        p = (prompt_field.value or "").strip()
        if not p:
            status_text.value = "Enter a prompt first."
            status_text.color = DANGER
            page.update()
            return
        page.run_task(_execute, p)

    # If we already have a comic_context on disk, show it
    if state.project_name:
        ctx_path = GDRIVE_BASE / state.project_name / "comic_context.json"
        if ctx_path.exists():
            import json
            try:
                preview.content = render_context(json.loads(ctx_path.read_text()))
            except json.JSONDecodeError:
                pass

    # Center column
    center = ft.Column(
        [
            ft.Container(
                content=ft.Row(
                    [prompt_field,
                     primary_button("Identify", run_btn_click, icon=ft.Icons.TRAVEL_EXPLORE)],
                    spacing=12,
                ),
                padding=ft.padding.symmetric(horizontal=28, vertical=20),
            ),
            ft.Container(
                content=preview,
                padding=ft.padding.symmetric(horizontal=28),
                expand=True,
            ),
            ft.Container(
                content=ft.Column([
                    qa_row,
                    ft.Row([running, status_text], spacing=10),
                    ft.Container(content=lv, height=140, border=ft.border.all(1, BORDER),
                                 border_radius=6),
                ], spacing=8),
                padding=ft.padding.symmetric(horizontal=28, vertical=16),
            ),
        ],
        spacing=0, expand=True,
    )

    # Right column
    def approve_and_go(_e):
        state.mark_approved(1)
        state.current_stage = 2
        save_state(state)
        on_go(2)

    right = ft.Column([
        ft.Text("STEP 1 OF 5", size=10, color=TEXT_MUTED),
        ft.Text("Identify Comic", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "The agent web-searches to verify the comic and find its batcave.biz URL, "
            "then pulls plot context from fandom wikis. Takes ~30-90s.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=20),
        primary_button("Approve & Continue →", approve_and_go,
                       disabled=not state.is_approved(1)),
        ft.Container(height=8),
        secondary_button("Restart with new prompt",
                         lambda _e: (prompt_field.focus(), page.update())),
    ], spacing=8, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Identify the Comic",
        header_subtitle="Describe the event or arc you want to make a Short from.",
    )


def _row(label: str, value, *, color: str = TEXT_PRIMARY) -> ft.Control:
    return ft.Row(
        [
            ft.Container(
                content=ft.Text(label, size=11, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
                width=140,
            ),
            ft.Text(str(value), size=13, color=color, selectable=True, expand=True),
        ],
        alignment=ft.MainAxisAlignment.START,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )
