"""
Screen 3: Narration synthesis.

Two phases in one screen:
  A. LLM proposes 3 narration modes as cards. User picks one.
  B. Once picked, LLM writes the script; it appears in an editable textarea.

The right column shows the mode catalog, editable params, and the
Approve-and-continue action.
"""
from __future__ import annotations

import json
from typing import Callable

import flet as ft

from ..bridge import (
    format_exception, load_narration, run_blocking, run_stage_3_propose,
    run_stage_3_write, save_narration_edits,
)
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED,
    TEXT_PRIMARY, WARN,
)
from stages.stage_3.modes import MODES_BY_KEY


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    proposals_ctl = ft.Container(expand=True)
    script_ctl = ft.Container(expand=True, visible=False)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    status_text = ft.Text("", color=TEXT_MUTED, size=12)
    lv, push_log = log_list(page)

    script_area = ft.TextField(
        value="", multiline=True, min_lines=12, max_lines=28,
        border_color=BORDER, focused_border_color=ACCENT,
        text_size=14, expand=True,
    )
    counter = ft.Text("", size=11, color=TEXT_MUTED)

    def _render_proposals(props: list[dict]):
        cards: list[ft.Control] = []
        for i, p in enumerate(props, start=1):
            mode = p.get("mode", "")
            label = MODES_BY_KEY.get(mode).label if mode in MODES_BY_KEY else mode
            hook = p.get("hook", "")
            why = p.get("rationale", "")

            def _pick(_e, chosen_mode=mode, chosen_hook=hook):
                state.chosen_mode = chosen_mode
                state.chosen_hook = chosen_hook
                save_state(state)
                page.run_task(_write_script)

            cards.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text(f"[{i}]", size=10, color=TEXT_MUTED,
                                    weight=ft.FontWeight.BOLD),
                            ft.Text(label, size=16, weight=ft.FontWeight.BOLD,
                                    color=TEXT_PRIMARY),
                        ], spacing=8),
                        ft.Container(height=6),
                        ft.Text(hook, size=13, color=TEXT_PRIMARY, italic=True,
                                selectable=True),
                        ft.Container(height=6),
                        ft.Text(why, size=11, color=TEXT_MUTED, selectable=True),
                    ], spacing=2),
                    padding=16,
                    border=ft.border.all(1, BORDER),
                    border_radius=8,
                    bgcolor=BG_ELEVATED,
                    on_click=_pick, ink=True,
                    expand=True,
                )
            )
        proposals_ctl.content = ft.Column(cards, spacing=12, expand=True,
                                          scroll=ft.ScrollMode.AUTO)
        proposals_ctl.visible = True
        script_ctl.visible = False

    def _render_script(narration: dict):
        scenes = narration.get("scenes") or []
        full = "\n\n".join(str(s.get("text", "")).strip() for s in scenes)
        script_area.value = full
        wc = narration.get("total_word_count") or sum(len(s.get("text","").split()) for s in scenes)
        dur = narration.get("estimated_duration_seconds") or round(wc / 2.9, 2)
        counter.value = (f"{wc} words · ~{dur:.1f}s estimated · {len(scenes)} scenes"
                         + ("  ⚠️ over 58s" if dur > 58 else ""))
        counter.color = WARN if dur > 58 else TEXT_MUTED
        proposals_ctl.visible = False
        script_ctl.visible = True

    async def _propose():
        running.visible = True
        status_text.value = "Proposing narration modes…"
        status_text.color = WARN
        page.update()
        try:
            props = await run_blocking(run_stage_3_propose, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            status_text.value = "Failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return

        running.visible = False
        status_text.value = f"LLM proposed {len(props)} modes. Pick one."
        status_text.color = SUCCESS
        _render_proposals(props)
        page.update()

    async def _write_script():
        running.visible = True
        status_text.value = f"Writing {state.chosen_mode} narration…"
        status_text.color = WARN
        page.update()
        try:
            narration = await run_blocking(
                run_stage_3_write,
                state.project_name,
                state.chosen_mode,
                state.chosen_hook,
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
        status_text.value = (
            f"Script drafted: {narration.get('total_word_count','?')} words, "
            f"{narration.get('estimated_duration_seconds','?')}s."
        )
        status_text.color = SUCCESS
        _render_script(narration)
        page.update()

    # Resume: if narration already exists, show script directly
    loaded = load_narration(state.project_name) if state.project_name else None
    if loaded:
        _render_script(loaded)

    def propose_click(_e):
        page.run_task(_propose)

    def _update_counter(_e):
        text = script_area.value or ""
        sentences = [t.strip() for t in text.replace("\n\n", "\n").split("\n") if t.strip()]
        wc = len(text.split())
        dur = wc / 2.9
        counter.value = (f"{wc} words · ~{dur:.1f}s estimated · {len(sentences)} scenes"
                         + ("  ⚠️ over 58s" if dur > 58 else ""))
        counter.color = WARN if dur > 58 else TEXT_MUTED
        counter.update()

    script_area.on_change = _update_counter

    def approve_and_go(_e):
        # save edited narration text back to narration.json (splitting into scenes)
        if state.project_name:
            current = load_narration(state.project_name)
            if current:
                sentences = [s.strip() for s in (script_area.value or "").split("\n\n")
                             if s.strip()]
                if sentences:
                    original_scenes = current.get("scenes") or []
                    new_scenes: list[dict] = []
                    for i, text in enumerate(sentences):
                        wc = len(text.split())
                        ref = original_scenes[i] if i < len(original_scenes) else {}
                        new_scenes.append({
                            "scene_id": i + 1,
                            "text": text,
                            "page_ref": int(ref.get("page_ref", 0) or 0),
                            "panel_ref": int(ref.get("panel_ref", -1) if ref.get("panel_ref") is not None else -1),
                            "word_count": wc,
                            "target_seconds": round(wc / 2.9, 2),
                        })
                    current["scenes"] = new_scenes
                    current["total_word_count"] = sum(s["word_count"] for s in new_scenes)
                    current["estimated_duration_seconds"] = round(
                        current["total_word_count"] / 2.9, 2
                    )
                    save_narration_edits(state.project_name, current)
        state.mark_approved(3)
        state.current_stage = 4
        save_state(state)
        on_go(4)

    def rewrite_click(_e):
        page.run_task(_write_script)

    # Center column
    center = ft.Column([
        ft.Container(content=proposals_ctl, padding=ft.padding.symmetric(horizontal=28, vertical=16),
                     expand=True, visible=True),
        ft.Container(
            content=ft.Column([
                script_area,
                counter,
            ], spacing=6, expand=True),
            padding=ft.padding.symmetric(horizontal=28, vertical=16),
            expand=True, visible=False,
        ),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status_text], spacing=10),
                ft.Container(content=lv, height=100, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)
    # bind proposals/script containers for visibility toggling
    center.controls[0] = ft.Container(content=proposals_ctl,
        padding=ft.padding.symmetric(horizontal=28, vertical=16), expand=True)
    center.controls[1] = ft.Container(content=ft.Column([script_area, counter], spacing=6,
                                                         expand=True),
        padding=ft.padding.symmetric(horizontal=28, vertical=16), expand=True)
    proposals_ctl.visible = not bool(loaded)
    script_ctl.visible = bool(loaded)
    center.controls[1].visible = script_ctl.visible
    center.controls[0].visible = proposals_ctl.visible

    # Right column
    right = ft.Column([
        ft.Text("STEP 3 OF 5", size=10, color=TEXT_MUTED),
        ft.Text("Narration Script", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Pick one of three LLM-proposed angles, then edit the generated script. "
            "Target ≤58s at ~2.9 words/sec.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=16),
        primary_button("Propose 3 modes", propose_click, icon=ft.Icons.LIGHTBULB_OUTLINE),
        ft.Container(height=8),
        secondary_button("Rewrite in current mode", rewrite_click,
                         icon=ft.Icons.REFRESH,
                         disabled=not state.chosen_mode),
        ft.Container(height=14),
        primary_button("Approve & Continue →", approve_and_go,
                       disabled=not (state.project_name and script_ctl.visible)),
        ft.Container(height=20),
        ft.Text("MODE CATALOG", size=10, color=TEXT_MUTED),
        ft.Container(
            content=ft.Column(
                [ft.Text(f"• {m.key}", size=11, color=TEXT_MUTED,
                         tooltip=m.description, selectable=False)
                 for m in MODES_BY_KEY.values()],
                spacing=2, scroll=ft.ScrollMode.AUTO,
            ),
            expand=True,
        ),
    ], spacing=8, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Write the Narration",
        header_subtitle=("Pick an angle, then edit the script."
                         if not loaded else "Edit the generated script. Autosaves on Continue."),
    )
