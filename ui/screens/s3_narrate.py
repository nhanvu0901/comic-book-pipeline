"""
Screen 3 (Step 4 of 8): Narration Script (Gemini-First Workflow).

Workflow:
1. User clicks "Copy Gemini Prompt" (or "Export Prompt (.md)").
2. User pastes the prompt into their Gemini account (Gemini 3.1 Pro, Search Grounding ON).
3. User copies Gemini's generated script and pastes it into the text box.
4. User clicks "Approve & Continue →" to parse, anchor, and proceed to Stage 5.
No Claude API call is used.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import flet as ft

from config import PROJECTS_ROOT
from ..bridge import format_exception, run_blocking
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BORDER, DANGER, SUCCESS, TEXT_MUTED,
    TEXT_PRIMARY, WARN,
)
from stages.stage_3.gemini_prompt import (
    generate_gemini_writer_prompt, parse_and_save_script, saved_script_for_editor,
)
from stages.user_errors import UserFacingError
from ..clipboard import BLOCKED_HINT, copy_text
from utils.clear_stage import clear_stage_3


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    # ── Clipboard service ──────────────────────────────────────────────────
    clipboard = ft.Clipboard()
    clipboard_attached = {"done": False}

    def _ensure_clipboard():
        if clipboard_attached["done"]:
            return
        services = getattr(page, "services", None)
        if services is not None and clipboard not in services:
            services.append(clipboard)
        clipboard_attached["done"] = True

    async def _copy_to_clipboard(text: str, label: str = "") -> bool:
        _ensure_clipboard()
        copied = await copy_text(clipboard, text)
        _show_snack(f"Copied {label} to clipboard." if copied
                    else f"Could not copy {label}: {BLOCKED_HINT}.")
        return copied

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    # ── Load existing narration if present ─────────────────────────────────
    # One paragraph per item (not per sentence), and never a script written for a
    # different item list — see saved_script_for_editor.
    initial_text, initial_note = (
        saved_script_for_editor(state.project_name) if state.project_name else ("", "")
    )

    # ── Center Column: Script Input & Controls ─────────────────────────────
    script_area = ft.TextField(
        value=initial_text,
        multiline=True,
        min_lines=16,
        max_lines=30,
        hint_text=(
            "Paste your narration script from Gemini here...\n\n"
            "Format example:\n"
            "Nobody breaks Captain America's shield. Three people did.\n\n"
            "During a war with the Serpent, Cap throws his shield at the god.\n"
            "The deity catches it mid-air and shatters it with his bare hands.\n\n"
            "So the most famous weapon on Earth is actually highly breakable."
        ),
        border_color=BORDER,
        focused_border_color=ACCENT,
        text_size=13,
        expand=True,
    )

    counter = ft.Text("", size=11, color=TEXT_MUTED)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    status_text = ft.Text(initial_note, color=WARN if initial_note else TEXT_MUTED, size=12)
    lv, push_log = log_list(page)

    n_items = _item_count(state.project_name)

    def _update_counter(_e=None):
        text = script_area.value or ""
        # Blank-line paragraphs: the unit the parser maps (paragraph N = item N). This
        # counted non-empty LINES and called them sentences.
        paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        wc = len(text.split())
        dur = wc / 2.9
        expected = (f" (need {n_items}, plus an optional hook and closing line)"
                    if n_items else "")
        counter.value = (
            f"{wc} words · ~{dur:.1f}s estimated · {len(paragraphs)} paragraph(s){expected}"
            + ("  ⚠️ over 58s" if dur > 58 else "")
        )
        counter.color = WARN if dur > 58 else TEXT_MUTED
        try:
            counter.update()
        except Exception:
            pass

    script_area.on_change = _update_counter
    _update_counter()

    def _open_prompt_dialog(prompt_text: str, file_url: str, filename: str):
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.DESCRIPTION, color=ACCENT),
                ft.Text(f"Gemini Prompt: {filename}", size=16, weight=ft.FontWeight.BOLD),
            ], spacing=10),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(
                        "Your prompt is ready! You can download the .md file to your Mac or copy the text directly:",
                        size=12, color=TEXT_MUTED,
                    ),
                    ft.TextField(
                        value=prompt_text,
                        multiline=True,
                        read_only=True,
                        min_lines=12,
                        max_lines=18,
                        text_size=12,
                        border_color=BORDER,
                        focused_border_color=ACCENT,
                    ),
                ], spacing=10, scroll=ft.ScrollMode.AUTO),
                width=650,
                height=380,
            ),
            actions=[
                secondary_button(
                    "Download to Mac (.md)",
                    lambda _e: page.run_task(page.launch_url, file_url),
                    icon=ft.Icons.DOWNLOAD,
                ),
                primary_button(
                    "Copy to Clipboard",
                    lambda _e: page.run_task(_copy_to_clipboard, prompt_text, "Prompt"),
                    icon=ft.Icons.COPY,
                ),
                ft.TextButton("Close", on_click=lambda _e: page.pop_dialog()),
            ],
        )
        page.show_dialog(dialog)

    # ── Prompt Generation actions ──────────────────────────────────────────
    async def copy_prompt_click(_e):
        if not state.project_name:
            _show_snack("No project selected.")
            return
        running.visible = True
        status_text.value = "Generating Gemini prompt (.md)…"
        status_text.color = WARN
        page.update()
        try:
            prompt_text, out_path = generate_gemini_writer_prompt(state.project_name)
            file_url = f"/{state.project_name}/{out_path.name}"
            push_log(f"[prompt] Generated {out_path.name} ({len(prompt_text)} chars)")
            copied = await _copy_to_clipboard(prompt_text, "Gemini Prompt")
            running.visible = False
            status_text.value = (
                f"Prompt saved to {out_path.name} and copied to clipboard." if copied
                else f"Prompt saved to {out_path.name}. Not copied: {BLOCKED_HINT} "
                     "(in the box below), or download the .md.")
            status_text.color = SUCCESS if copied else WARN
            _open_prompt_dialog(prompt_text, file_url, out_path.name)
            page.update()
        except Exception as exc:
            running.visible = False
            status_text.value = f"Failed to generate prompt: {exc}"
            status_text.color = DANGER
            push_log(format_exception(exc))
            page.update()

    async def export_prompt_click(_e):
        if not state.project_name:
            _show_snack("No project selected.")
            return
        running.visible = True
        status_text.value = "Preparing prompt download for your browser…"
        status_text.color = WARN
        page.update()
        try:
            prompt_text, out_path = generate_gemini_writer_prompt(state.project_name)
            file_url = f"/{state.project_name}/{out_path.name}"
            push_log(f"[prompt] Triggering browser download to Mac: {file_url}")
            # Launch file URL in browser — client browser on Mac fetches the .md file directly
            await page.launch_url(file_url)
            copied = await _copy_to_clipboard(prompt_text, "Gemini Prompt")
            running.visible = False
            status_text.value = (f"Downloading {out_path.name}"
                                 + (" — also copied to clipboard." if copied else "."))
            status_text.color = SUCCESS
            _show_snack(f"Opened {out_path.name} in your browser to download to Mac!")
            _open_prompt_dialog(prompt_text, file_url, out_path.name)
            page.update()
        except Exception as exc:
            running.visible = False
            status_text.value = f"Failed to export prompt: {exc}"
            status_text.color = DANGER
            push_log(format_exception(exc))
            page.update()

    def clear_script_click(_e):
        script_area.value = ""
        _update_counter()
        page.update()

    # ── Approve and Continue (Stage 4 -> Stage 5) ──────────────────────────
    async def approve_and_go(_e):
        raw_text = (script_area.value or "").strip()
        if not raw_text:
            _show_snack("Please paste or write narration script before continuing.")
            return

        if not state.project_name:
            _show_snack("No project selected.")
            return

        running.visible = True
        status_text.value = "Parsing script & anchoring scenes…"
        status_text.color = WARN
        page.update()

        try:
            narration = await run_blocking(
                parse_and_save_script,
                state.project_name,
                raw_text,
                log=push_log,
            )
            running.visible = False
            status_text.value = (
                f"Narration approved: {narration.get('total_word_count', 0)} words, "
                f"{len(narration.get('scenes', []))} scenes."
            )
            status_text.color = SUCCESS
            page.update()

            state.mark_approved(4)
            state.current_stage = 5
            save_state(state)
            on_go(5)
        except Exception as exc:
            running.visible = False
            # A mapping refusal leads with its own first line (what is wrong); the log
            # below is too short to show that line above the paragraph listing.
            status_text.value = (
                str(exc).splitlines()[0] if isinstance(exc, UserFacingError)
                else "Failed to parse/save narration — see log."
            )
            status_text.color = DANGER
            for line in format_exception(exc).splitlines():
                push_log(line)
            page.update()

    # Top action bar in center column
    top_bar = ft.Row([
        primary_button("Copy Gemini Prompt", copy_prompt_click, icon=ft.Icons.COPY),
        secondary_button("Download Prompt to Mac (.md)", export_prompt_click, icon=ft.Icons.DOWNLOAD_ROUNDED),
        secondary_button("Clear", clear_script_click, icon=ft.Icons.CLEAR),
    ], spacing=10, wrap=True)

    center = ft.Column([
        ft.Container(
            content=ft.Column([
                top_bar,
                ft.Container(height=4),
                script_area,
                counter,
            ], spacing=6, expand=True),
            padding=ft.padding.symmetric(horizontal=24, vertical=16),
            expand=True,
        ),
        ft.Container(
            content=ft.Column([
                ft.Row([running, ft.Container(status_text, expand=True)], spacing=10),
                ft.Container(content=lv, height=90, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=24, vertical=8),
        ),
    ], spacing=0, expand=True)

    # ── Right Column: Instructions & Project Context Preview ────────────────
    answer_path = PROJECTS_ROOT / state.project_name / "answer_context.json" if state.project_name else None
    q_text = ""
    items_controls = []
    if answer_path and answer_path.exists():
        try:
            actx = json.loads(answer_path.read_text(encoding="utf-8"))
            q_text = actx.get("question", "")
            items = actx.get("items") or []
            # Numbered in download order — the order the script's paragraphs must follow.
            for n, it in enumerate(items, start=1):
                ent = it.get("entity") or ""
                comic = it.get("source_comic") or ""
                items_controls.append(
                    ft.Text(f"#{n} {ent} ({comic})", size=11, color=TEXT_MUTED, selectable=True)
                )
        except Exception:
            pass

    right = ft.Column([
        ft.Text("STEP 4 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Narration Script", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Gemini-First Narration (0% Claude):\n"
            "1. Click 'Copy Gemini Prompt'.\n"
            "2. Paste into Gemini Web (3.1 Pro, Search Grounding ON).\n"
            "3. Copy Gemini's script and paste on the left.\n"
            "4. Click 'Approve & Continue →'.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=12),
        primary_button("Approve & Continue →", approve_and_go,
                       icon=ft.Icons.ARROW_FORWARD),
        ft.Container(height=16),
        ft.Text("PROJECT CONTEXT", size=10, color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
        ft.Text(f"Question: {q_text}" if q_text else f"Project: {state.project_name}",
                size=12, color=TEXT_PRIMARY, weight=ft.FontWeight.W_500),
        ft.Container(height=6),
        ft.Column(items_controls, spacing=4, scroll=ft.ScrollMode.AUTO),
    ], spacing=8, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Step 4: Narration Script",
        header_subtitle="Export context to your Gemini account, then paste the written script below.",
    )


def _item_count(project_name: str) -> int:
    """Number of answer items (0 for a project without them)."""
    if not project_name:
        return 0
    try:
        ctx = json.loads((PROJECTS_ROOT / project_name / "answer_context.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return len(ctx.get("items") or []) if isinstance(ctx, dict) else 0
