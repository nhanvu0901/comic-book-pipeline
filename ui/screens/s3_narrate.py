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
from pathlib import Path
from typing import Callable

import flet as ft

from config import PROJECTS_ROOT
from ..bridge import format_exception, load_narration, run_blocking
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BORDER, DANGER, SUCCESS, TEXT_MUTED,
    TEXT_PRIMARY, WARN,
)
from stages.stage_3.gemini_prompt import generate_gemini_writer_prompt, parse_and_save_script
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

    async def _copy_to_clipboard(text: str, label: str = ""):
        _ensure_clipboard()
        try:
            await clipboard.set(text)
            _show_snack(f"Copied {label} to clipboard!")
        except Exception as exc:
            _show_snack(f"Failed to copy {label}: {exc}")

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    # ── Load existing narration if present ─────────────────────────────────
    loaded = load_narration(state.project_name) if state.project_name else None
    initial_text = ""
    if loaded:
        scenes = loaded.get("scenes") or []
        initial_text = "\n\n".join(str(s.get("text", "")).strip() for s in scenes if s.get("text"))

    # Also check if master_narration.md exists
    if not initial_text and state.project_name:
        mn_path = PROJECTS_ROOT / state.project_name / "master_narration.md"
        if mn_path.exists():
            initial_text = mn_path.read_text(encoding="utf-8").strip()

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
    status_text = ft.Text("", color=TEXT_MUTED, size=12)
    lv, push_log = log_list(page)

    def _update_counter(_e=None):
        text = script_area.value or ""
        sentences = [t.strip() for t in text.replace("\n\n", "\n").split("\n") if t.strip()]
        wc = len(text.split())
        dur = wc / 2.9
        counter.value = (
            f"{wc} words · ~{dur:.1f}s estimated · {len(sentences)} sentence(s)"
            + ("  ⚠️ over 58s" if dur > 58 else "")
        )
        counter.color = WARN if dur > 58 else TEXT_MUTED
        try:
            counter.update()
        except Exception:
            pass

    script_area.on_change = _update_counter
    _update_counter()

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
            push_log(f"[prompt] Generated {out_path.name} ({len(prompt_text)} chars)")
            await _copy_to_clipboard(prompt_text, "Gemini Prompt")
            running.visible = False
            status_text.value = f"Prompt saved to {out_path.name} and copied to clipboard!"
            status_text.color = SUCCESS
            page.update()
        except Exception as exc:
            running.visible = False
            status_text.value = f"Failed to generate prompt: {exc}"
            status_text.color = DANGER
            push_log(format_exception(exc))
            page.update()

    def export_prompt_click(_e):
        if not state.project_name:
            _show_snack("No project selected.")
            return
        try:
            _, out_path = generate_gemini_writer_prompt(state.project_name)
            push_log(f"[prompt] Exported: {out_path}")
            _show_snack(f"Exported to {out_path.name}")
            status_text.value = f"Exported prompt to {out_path.name}"
            status_text.color = SUCCESS
            page.update()
        except Exception as exc:
            _show_snack(f"Error exporting prompt: {exc}")

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
            status_text.value = "Failed to parse/save narration — see log."
            status_text.color = DANGER
            push_log(format_exception(exc))
            page.update()

    # Top action bar in center column
    top_bar = ft.Row([
        primary_button("Copy Gemini Prompt", copy_prompt_click, icon=ft.Icons.COPY),
        secondary_button("Download Prompt (.md)", export_prompt_click, icon=ft.Icons.DOWNLOAD_ROUNDED),
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
                ft.Row([running, status_text], spacing=10),
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
            for it in items[:6]:
                rank = it.get("rank") or "?"
                ent = it.get("entity") or ""
                comic = it.get("source_comic") or ""
                items_controls.append(
                    ft.Text(f"#{rank} {ent} ({comic})", size=11, color=TEXT_MUTED)
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
