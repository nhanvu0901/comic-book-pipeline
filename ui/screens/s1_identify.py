"""
Screen 1: Identify Comic — Interactive chat-style agent UI.

The agent runs through phases (identify → search → wiki → confirm).
After each phase, the user sees the result in a chat bubble and can
approve or reject with feedback. Streaming LLM output shown in real-time.
"""
from __future__ import annotations

import shutil
from typing import Callable

import flet as ft

from config import PipelineMode, PROJECTS_ROOT, MAX_PHASE_RETRIES
from ..bridge import PhaseApprovalBridge, format_exception, run_blocking, run_stage_1
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS,
    TEXT_MUTED, TEXT_PRIMARY, WARN,
)

MODE_LABELS = {
    PipelineMode.NARRATE_1_COMIC: "Narrate 1 Comic",
    PipelineMode.STORY_ARC: "Story Arc (coming soon)",
    PipelineMode.CHARACTER_FEAT: "Character Feature (coming soon)",
    PipelineMode.VERSUS: "Versus / Battle (coming soon)",
    PipelineMode.WHAT_IF: "What If…? (coming soon)",
    PipelineMode.ORIGIN_STORY: "Origin Story (coming soon)",
    PipelineMode.TOP_MOMENTS: "Top Moments (coming soon)",
}

IMPLEMENTED_MODES = {PipelineMode.NARRATE_1_COMIC}

PHASE_LABELS = {
    "plan": "Phase 1: Query Planning",
    "search": "Phase 2: Search & Identify",
    "wiki": "Phase 3: Wiki Plot Fetch",
    "confirm": "Phase 4: Final Confirmation",
}

PHASE_ICONS = {
    "plan": ft.Icons.ROUTE,
    "search": ft.Icons.TRAVEL_EXPLORE,
    "wiki": ft.Icons.MENU_BOOK,
    "confirm": ft.Icons.CHECK_CIRCLE_OUTLINE,
}


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:

    chat_list = ft.ListView(
        expand=True, spacing=12, padding=16, auto_scroll=True,
    )

    mode_dropdown = ft.Dropdown(
        value=state.pipeline_mode,
        options=[
            ft.dropdown.Option(
                key=mode.value,
                text=MODE_LABELS[mode],
                disabled=mode not in IMPLEMENTED_MODES,
            )
            for mode in PipelineMode
        ],
        width=260,
        border_color=BORDER,
        focused_border_color=ACCENT,
        text_size=13,
        label="Pipeline Mode",
        label_style=ft.TextStyle(size=10, color=TEXT_MUTED),
    )

    def _on_mode_change(_e):
        state.pipeline_mode = mode_dropdown.value or "narrate_1_comic"

    mode_dropdown.on_change = _on_mode_change

    prompt_field = ft.TextField(
        value=state.last_prompt,
        hint_text="e.g. The death of Gwen Stacy  ·  Invincible vs Omni-Man  ·  One Piece Marineford",
        multiline=False,
        autofocus=True,
        expand=True,
        border_color=BORDER,
        focused_border_color=ACCENT,
        on_submit=lambda e: _start_pipeline(e),
    )

    status_text = ft.Text("Ready.", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)

    bridge_holder: dict[str, PhaseApprovalBridge | None] = {"b": None}
    streaming_text_ref: dict[str, ft.Text | None] = {"t": None}

    # ─── Chat bubble builders ─────────────────────────────────────────

    def _add_user_bubble(text: str):
        bubble = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.PERSON, color=ACCENT, size=16),
                ft.Text(text, color=TEXT_PRIMARY, size=13, selectable=True, expand=True),
            ], spacing=8),
            padding=12,
            bgcolor=BG_ELEVATED,
            border=ft.border.all(1, BORDER),
            border_radius=ft.border_radius.only(
                top_left=12, top_right=4, bottom_left=12, bottom_right=12,
            ),
        )
        chat_list.controls.append(bubble)
        try:
            page.update()
            chat_list.scroll_to(offset=-1, duration=150)
        except Exception:
            pass

    def _add_agent_phase_bubble(phase: str, attempt: int, max_attempts: int):
        """Add a new agent bubble with streaming text. Returns the Text control."""
        label = PHASE_LABELS.get(phase, phase)
        icon = PHASE_ICONS.get(phase, ft.Icons.SMART_TOY)

        header = ft.Row([
            ft.Icon(icon, color=ACCENT, size=16),
            ft.Text(label, size=12, color=ACCENT, weight=ft.FontWeight.BOLD),
            ft.Text(
                f"(attempt {attempt}/{max_attempts})" if attempt > 1 else "",
                size=10, color=TEXT_MUTED,
            ),
            ft.ProgressRing(width=14, height=14, stroke_width=2),
        ], spacing=6)

        streaming_body = ft.Text("", color=TEXT_PRIMARY, size=13, selectable=True)

        bubble_col = ft.Column([header, streaming_body], spacing=8)

        bubble = ft.Container(
            content=bubble_col,
            padding=14,
            bgcolor=BG_PANEL,
            border=ft.border.all(1, BORDER),
            border_radius=ft.border_radius.only(
                top_left=4, top_right=12, bottom_left=12, bottom_right=12,
            ),
            data={"col": bubble_col, "header": header},
        )
        chat_list.controls.append(bubble)
        try:
            page.update()
            chat_list.scroll_to(offset=-1, duration=150)
        except Exception:
            pass
        return streaming_body, bubble

    def _finalize_bubble(bubble: ft.Container, streaming_body: ft.Text, phase_result):
        """Replace spinner with result card and add approve/reject buttons."""
        col: ft.Column = bubble.data["col"]
        header: ft.Row = bubble.data["header"]

        # Remove spinner from header
        header.controls = [c for c in header.controls if not isinstance(c, ft.ProgressRing)]

        # Build result display
        result_card = _build_result_card(phase_result)
        if result_card:
            col.controls.append(result_card)

        # Action buttons
        approve_btn = ft.ElevatedButton(
            "Approve",
            icon=ft.Icons.CHECK,
            bgcolor=SUCCESS,
            color="#ffffff",
            height=36,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.padding.symmetric(horizontal=16),
            ),
        )
        reject_btn = ft.OutlinedButton(
            "Revise",
            icon=ft.Icons.EDIT,
            height=36,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.padding.symmetric(horizontal=16),
                side=ft.BorderSide(1, WARN),
                color=WARN,
            ),
        )

        feedback_field = ft.TextField(
            hint_text="Describe what to change...",
            border_color=WARN,
            focused_border_color=WARN,
            expand=True,
            text_size=13,
            visible=False,
        )
        send_feedback_btn = ft.ElevatedButton(
            "Submit",
            bgcolor=WARN,
            color="#ffffff",
            height=34,
            visible=False,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.padding.symmetric(horizontal=14),
            ),
        )

        feedback_row = ft.Row(
            [feedback_field, send_feedback_btn],
            spacing=8, visible=False,
        )
        action_row = ft.Row([approve_btn, reject_btn], spacing=8)

        def _on_approve(_e):
            action_row.visible = False
            feedback_row.visible = False
            col.controls.append(
                ft.Text("Approved", color=SUCCESS, size=11, weight=ft.FontWeight.BOLD, italic=True)
            )
            page.update()
            b = bridge_holder.get("b")
            if b:
                b.approve()

        def _on_reject(_e):
            feedback_field.visible = True
            send_feedback_btn.visible = True
            feedback_row.visible = True
            reject_btn.visible = False
            page.update()
            try:
                feedback_field.focus()
            except Exception:
                pass

        def _on_send_feedback(_e):
            fb = (feedback_field.value or "").strip()
            if not fb:
                return
            action_row.visible = False
            feedback_row.visible = False
            col.controls.append(
                ft.Text(f"Rejected: {fb}", color=WARN, size=11, italic=True)
            )
            page.update()
            _add_user_bubble(fb)
            b = bridge_holder.get("b")
            if b:
                b.reject(fb)

        approve_btn.on_click = _on_approve
        reject_btn.on_click = _on_reject
        send_feedback_btn.on_click = _on_send_feedback
        feedback_field.on_submit = _on_send_feedback

        col.controls.append(action_row)
        col.controls.append(feedback_row)
        try:
            page.update()
            chat_list.scroll_to(offset=-1, duration=200)
        except Exception:
            pass

    def _build_result_card(phase_result) -> ft.Control | None:
        """Build a styled card showing the phase result data."""
        data = phase_result.data
        if not data:
            return ft.Text("(no data returned)", color=DANGER, size=12, italic=True)

        if phase_result.phase == "plan":
            return _plan_card(data)
        elif phase_result.phase == "search":
            return _search_card(data)
        elif phase_result.phase == "wiki":
            return _wiki_card(data)
        elif phase_result.phase == "confirm":
            return _context_card(data)
        return None

    def _plan_card(data: dict) -> ft.Control:
        entities = data.get("entities", {})
        rows = [
            _kv("Characters", ", ".join(entities.get("characters", ["?"]))),
            _kv("Publisher hint", entities.get("publisher_hint", "?")),
            _kv("Era hint", entities.get("era_hint") or "?"),
            _kv("Story type", entities.get("story_type", "?")),
        ]
        queries = data.get("search_queries", [])
        if queries:
            rows.append(ft.Divider(height=1, color=BORDER))
            rows.append(ft.Text("Search queries:", size=11, color=TEXT_MUTED, weight=ft.FontWeight.BOLD))
            for i, q in enumerate(queries, 1):
                rows.append(ft.Text(f"  {i}. {q}", size=12, color=TEXT_PRIMARY, selectable=True))

        ambiguities = data.get("ambiguities", [])
        if ambiguities:
            rows.append(ft.Divider(height=1, color=BORDER))
            for a in ambiguities:
                rows.append(ft.Text(f"  ? {a}", color=WARN, size=11))

        return ft.Container(
            content=ft.Column(rows, spacing=4),
            padding=12,
            border=ft.border.all(1, BORDER),
            border_radius=6,
            bgcolor=BG_ELEVATED,
        )

    def _search_card(data: dict) -> ft.Control:
        rows = [
            _kv("Title", data.get("title", "?")),
            _kv("Series", f"{data.get('series', '?')} {data.get('issues', '')}".strip()),
            _kv("Year", str(data.get("year", "?"))),
            _kv("Writer", data.get("writer", "?")),
            _kv("Artist", data.get("artist", "?")),
            _kv("Publisher", data.get("publisher", "?")),
            _kv("Characters", ", ".join(data.get("characters", ["?"]))),
            _kv("Confidence", data.get("confidence", "?")),
        ]
        batcave = data.get("batcave_url", "")
        if batcave:
            rows.append(_kv("batcave_url", batcave))

        ambiguities = data.get("ambiguities", [])
        if ambiguities:
            rows.append(ft.Divider(height=1, color=BORDER))
            for a in ambiguities:
                rows.append(ft.Text(f"  ? {a}", color=WARN, size=11))

        return ft.Container(
            content=ft.Column(rows, spacing=4),
            padding=12,
            border=ft.border.all(1, BORDER),
            border_radius=6,
            bgcolor=BG_ELEVATED,
        )

    def _wiki_card(data: dict) -> ft.Control:
        plot = data.get("wiki_plot", "")
        url = data.get("wiki_url", "")
        length = data.get("plot_length", 0)

        rows = []
        if url:
            rows.append(_kv("Source", url))
        rows.append(_kv("Plot length", f"{length} chars"))
        if plot:
            rows.append(ft.Text(plot, color=TEXT_PRIMARY, size=12, selectable=True))
        else:
            rows.append(ft.Text("(no plot text found)", color=WARN, size=12, italic=True))

        return ft.Container(
            content=ft.Column(rows, spacing=4),
            padding=12,
            border=ft.border.all(1, BORDER),
            border_radius=6,
            bgcolor=BG_ELEVATED,
        )

    def _context_card(ctx: dict) -> ft.Control:
        rows = [
            _kv("Title", ctx.get("title", "?")),
            _kv("Series", f"{ctx.get('series', '?')} {ctx.get('issues', '')}".strip()),
            _kv("Year", str(ctx.get("year", "?"))),
            _kv("Writer", ctx.get("writer", "?")),
            _kv("Publisher", ctx.get("publisher", "?")),
            _kv("Characters", ", ".join(ctx.get("characters", []) or ["?"])),
        ]
        bat = ctx.get("batcave_url") or ""
        rows.append(
            _kv("batcave_url", bat or "(missing)", color=TEXT_PRIMARY if bat else WARN)
        )
        plot = ctx.get("plot_summary", "")
        if plot:
            preview = plot[:500] + ("..." if len(plot) > 500 else "")
            rows.append(ft.Divider(height=1, color=BORDER))
            rows.append(ft.Text("Plot summary:", size=11, color=TEXT_MUTED, weight=ft.FontWeight.BOLD))
            rows.append(ft.Text(preview, size=12, color=TEXT_PRIMARY, selectable=True))

        return ft.Container(
            content=ft.Column(rows, spacing=4),
            padding=12,
            border=ft.border.all(1, BORDER),
            border_radius=6,
            bgcolor=BG_ELEVATED,
        )

    def _kv(label: str, value: str, *, color: str = TEXT_PRIMARY) -> ft.Control:
        return ft.Row([
            ft.Container(
                content=ft.Text(label, size=11, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
                width=110,
            ),
            ft.Text(str(value), size=12, color=color, selectable=True, expand=True),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)

    # ─── Streaming + phase result handlers (called from worker thread) ──

    _token_batch: list[str] = []
    _token_count = [0]

    def _on_token(token: str):
        """Called from worker thread per token. Batch updates to UI."""
        ref = streaming_text_ref.get("t")
        if not ref:
            return
        _token_batch.append(token)
        _token_count[0] += 1
        if _token_count[0] >= 5:
            _flush_tokens()

    def _flush_tokens():
        ref = streaming_text_ref.get("t")
        if not ref or not _token_batch:
            return
        ref.value += "".join(_token_batch)
        _token_batch.clear()
        _token_count[0] = 0
        try:
            page.run_task(_async_update_chat)
        except Exception:
            pass

    async def _async_update_chat():
        try:
            page.update()
            chat_list.scroll_to(offset=-1, duration=100)
        except Exception:
            pass

    def _on_phase_result(phase_result):
        """Called from worker thread when a phase completes."""
        _flush_tokens()

        def _apply():
            streaming_body = streaming_text_ref.get("t")
            bubble = streaming_text_ref.get("bubble")
            if streaming_body and bubble:
                _finalize_bubble(bubble, streaming_body, phase_result)

        try:
            page.run_task(_async_apply_result, phase_result)
        except Exception:
            _apply()

    async def _async_apply_result(phase_result):
        _flush_tokens()
        streaming_body = streaming_text_ref.get("t")
        bubble = streaming_text_ref.get("bubble")
        if streaming_body and bubble:
            _finalize_bubble(bubble, streaming_body, phase_result)

    def _on_log(msg: str):
        """Called from worker thread with status messages."""
        def _apply():
            # Create a new bubble for the new phase attempt
            if msg.startswith("Phase:"):
                parts = msg.split("(attempt ")
                phase_name = parts[0].replace("Phase:", "").strip()
                attempt = 1
                if len(parts) > 1:
                    try:
                        attempt = int(parts[1].split("/")[0])
                    except (ValueError, IndexError):
                        pass
                streaming_body, bubble = _add_agent_phase_bubble(
                    phase_name, attempt, MAX_PHASE_RETRIES,
                )
                streaming_text_ref["t"] = streaming_body
                streaming_text_ref["bubble"] = bubble
                _token_batch.clear()
                _token_count[0] = 0

        try:
            page.run_task(_async_log, msg)
        except Exception:
            _apply()

    async def _async_log(msg: str):
        if msg.startswith("Phase:"):
            parts = msg.split("(attempt ")
            phase_name = parts[0].replace("Phase:", "").strip()
            attempt = 1
            if len(parts) > 1:
                try:
                    attempt = int(parts[1].split("/")[0])
                except (ValueError, IndexError):
                    pass
            streaming_body, bubble = _add_agent_phase_bubble(
                phase_name, attempt, MAX_PHASE_RETRIES,
            )
            streaming_text_ref["t"] = streaming_body
            streaming_text_ref["bubble"] = bubble
            _token_batch.clear()
            _token_count[0] = 0

    # ─── Pipeline execution ───────────────────────────────────────────

    async def _execute(prompt: str):
        running.visible = True
        status_text.value = "Running Stage 1..."
        status_text.color = WARN
        page.update()

        _add_user_bubble(prompt)

        bridge = PhaseApprovalBridge(
            on_phase_result=_on_phase_result,
            on_token=_on_token,
            on_log=_on_log,
        )
        bridge_holder["b"] = bridge

        try:
            selected_mode = mode_dropdown.value or "narrate_1_comic"
            ctx, project_name = await run_blocking(run_stage_1, prompt, bridge, selected_mode)
        except Exception as e:
            bridge.cancel()
            running.visible = False
            status_text.value = "Failed — see chat."
            status_text.color = DANGER
            chat_list.controls.append(
                ft.Container(
                    content=ft.Text(
                        format_exception(e),
                        color=DANGER, size=11, font_family="Menlo", selectable=True,
                    ),
                    padding=12, bgcolor=BG_ELEVATED, border_radius=6,
                    border=ft.border.all(1, DANGER),
                )
            )
            page.update()
            return
        finally:
            bridge_holder["b"] = None

        state.project_name = project_name
        state.last_prompt = prompt
        state.pipeline_mode = mode_dropdown.value or "narrate_1_comic"
        state.mark_approved(1)
        state.current_stage = max(state.current_stage, 2)
        save_state(state)

        running.visible = False
        status_text.value = "Stage 1 complete."
        status_text.color = SUCCESS
        page.update()
        on_state_change()

    def _start_pipeline(_e):
        p = (prompt_field.value or "").strip()
        if not p:
            status_text.value = "Enter a prompt first."
            status_text.color = DANGER
            page.update()
            return
        page.run_task(_execute, p)

    # ─── Restore existing context if present ──────────────────────────

    if state.project_name:
        ctx_path = PROJECTS_ROOT / state.project_name / "comic_context.json"
        if ctx_path.exists():
            import json
            try:
                ctx = json.loads(ctx_path.read_text())
                chat_list.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Text("Previously saved context:", size=11,
                                    color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
                            _context_card(ctx),
                        ], spacing=6),
                        padding=12,
                    )
                )
            except Exception:
                pass

    # ─── Layout ───────────────────────────────────────────────────────

    center = ft.Column([
        ft.Container(
            content=ft.Column([
                ft.Row([mode_dropdown], spacing=12),
                ft.Row([
                    prompt_field,
                    primary_button("Identify", _start_pipeline, icon=ft.Icons.TRAVEL_EXPLORE),
                ], spacing=12),
            ], spacing=12),
            padding=ft.padding.symmetric(horizontal=28, vertical=20),
        ),
        ft.Container(
            content=chat_list,
            padding=ft.padding.symmetric(horizontal=12),
            expand=True,
        ),
        ft.Container(
            content=ft.Row([running, status_text], spacing=10),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)

    # Right column
    def approve_and_go(_e):
        state.mark_approved(1)
        state.current_stage = 2
        save_state(state)
        on_go(2)

    def restart_with_new_prompt(_e):
        if state.project_name:
            shutil.rmtree(PROJECTS_ROOT / state.project_name, ignore_errors=True)
        state.project_name = ""
        state.last_prompt = ""
        state.current_stage = 1
        state.approved = {}
        state.dirty = {}
        page.views.clear()
        on_state_change()

    right = ft.Column([
        ft.Text("STEP 1 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("Identify Comic", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Interactive agent — approve or revise each phase. "
            "The agent identifies the comic, verifies via web search, "
            "and pulls plot context from wikis.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=12),
        ft.Text("PHASES", size=10, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
        ft.Text("1. Plan search queries from your description", size=11, color=TEXT_MUTED),
        ft.Text("2. Web search to identify & find batcave URL", size=11, color=TEXT_MUTED),
        ft.Text("3. Fetch verified plot from wiki", size=11, color=TEXT_MUTED),
        ft.Text("4. Review final context", size=11, color=TEXT_MUTED),
        ft.Container(height=12),
        ft.Text(f"Max retries per phase: {MAX_PHASE_RETRIES}", size=10, color=TEXT_MUTED),
        ft.Container(height=20),
        primary_button("Continue to Stage 2 →", approve_and_go,
                       disabled=not state.is_approved(1)),
        ft.Container(height=8),
        secondary_button("Start over", restart_with_new_prompt),
    ], spacing=6, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Identify the Comic",
        header_subtitle="Describe the event or arc you want to make a Short from.",
    )
