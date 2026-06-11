# art_ui/screens/a4_narrate.py
"""A4: two-step — gather grounded facts (Met+Wikipedia+SDK fallback), then
write the narration. Scenes are editable; saving recomputes word counts."""
from typing import Callable

import flet as ft

from ui.theme import BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN
from ui.bridge import format_exception, run_blocking

from .. import bridge
from ..layout import art_shell, log_list, primary_button, secondary_button
from ..state import ArtAppState, save_state


def build(page: ft.Page, state: ArtAppState, *,
          on_go: Callable[[int], None], on_state_change: Callable[[], None]) -> ft.Control:
    lv, push_log = log_list(page)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    ground_status = ft.Text("Facts not gathered yet.", color=TEXT_MUTED, size=12)
    narrate_status = ft.Text("", color=TEXT_MUTED, size=12)
    visuals_status = ft.Text("", color=TEXT_MUTED, size=12)
    scenes_col = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
    scene_fields: list[tuple[dict, ft.TextField]] = []

    def _refresh_ground_status():
        ctx = bridge.load_art_context(state.project_name)
        if ctx:
            ground_status.value = (f"Grounded: {len(ctx.get('plot_summary') or '')} chars "
                                   f"({ctx.get('plot_source','')}) · {ctx.get('wiki_url','')}")
            ground_status.color = SUCCESS

    def _mount_scenes():
        scenes_col.controls.clear()
        scene_fields.clear()
        narration = bridge.load_art_narration(state.project_name)
        if not narration:
            scenes_col.controls.append(ft.Text("No narration yet.", color=TEXT_MUTED, size=12))
            return
        narrate_status.value = (f"{narration.get('mode','')} · "
                                f"{narration.get('total_word_count', 0)} words · "
                                f"~{narration.get('estimated_duration_seconds', 0)}s · "
                                f"{narration.get('llm_model','')}")
        for s in narration.get("scenes") or []:
            tf = ft.TextField(value=s.get("text", ""), multiline=True, min_lines=1,
                              max_lines=4, dense=True, text_size=13)
            scene_fields.append((s, tf))
            tag = "HOOK" if s.get("is_intro") else ("OUTRO" if s.get("is_outro") else "")
            scenes_col.controls.append(ft.Column([
                ft.Text(f"scene {s.get('scene_id')} · p{s.get('page_ref')}/r{s.get('panel_ref')} {tag}",
                        size=10, color=TEXT_MUTED),
                tf,
            ], spacing=2))

    _refresh_ground_status()
    _mount_scenes()

    async def _ground():
        running.visible = True
        ground_status.value = "Gathering facts (Met + Wikipedia + SDK fallback)…"
        ground_status.color = WARN
        page.update()
        try:
            await run_blocking(bridge.run_ground, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            ground_status.value = "Grounding failed/too thin — see log."
            ground_status.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        running.visible = False
        _refresh_ground_status()
        page.update()

    async def _narrate():
        if not bridge.load_art_context(state.project_name):
            narrate_status.value = "Gather facts first."
            narrate_status.color = DANGER
            page.update()
            return
        running.visible = True
        narrate_status.value = "Writing narration… (first run loads the embedding model)"
        narrate_status.color = WARN
        page.update()
        try:
            await run_blocking(bridge.run_narrate, state.project_name, state.mode, push_log)
        except Exception as e:
            running.visible = False
            narrate_status.value = "Failed — see log."
            narrate_status.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        running.visible = False
        narrate_status.color = SUCCESS
        _mount_scenes()
        state.mark_approved(4)
        save_state(state)
        page.update()
        on_state_change()

    async def _hunt():
        if not bridge.load_art_narration(state.project_name):
            visuals_status.value = "Write narration first."
            visuals_status.color = DANGER
            page.update()
            return
        running.visible = True
        visuals_status.value = "Hunting related images on the web (Claude SDK)…"
        visuals_status.color = WARN
        page.update()
        try:
            out = await run_blocking(bridge.run_hunt, state.project_name, True, push_log)
        except Exception as e:
            running.visible = False
            visuals_status.value = "Failed — see log."
            visuals_status.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        running.visible = False
        if out.get("skipped"):
            visuals_status.value = "Already hunted — use force to redo."
        else:
            visuals_status.value = (f"{out.get('resolved', 0)}/{out.get('requested', 0)} "
                                    f"related scene(s) got web images.")
        visuals_status.color = SUCCESS
        _mount_scenes()
        state.mark_dirty(5)
        save_state(state)
        page.update()
        on_state_change()

    def save_edits(_e):
        narration = bridge.load_art_narration(state.project_name)
        if not narration:
            return
        by_id = {s.get("scene_id"): tf for s, tf in scene_fields}
        for s in narration.get("scenes") or []:
            tf = by_id.get(s.get("scene_id"))
            if tf is not None:
                s["text"] = tf.value or ""
        bridge.save_narration_edits(state.project_name, narration)
        narrate_status.value = "Edits saved (word counts recomputed)."
        narrate_status.color = SUCCESS
        state.mark_dirty(5)
        save_state(state)
        page.update()

    center = ft.Column([
        ft.Container(content=scenes_col,
                     padding=ft.padding.symmetric(horizontal=28, vertical=16), expand=True),
        ft.Container(
            content=ft.Column([
                ft.Row([running, ground_status], spacing=10),
                narrate_status,
                visuals_status,
                ft.Container(content=lv, height=140, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=6),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 4 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("Narration", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(f"mode: {state.mode}", size=12, color=TEXT_MUTED),
        ft.Container(height=12),
        primary_button("1 · Gather Facts", lambda _e: page.run_task(_ground),
                       icon=ft.Icons.FACT_CHECK),
        ft.Container(height=8),
        primary_button("2 · Write Narration", lambda _e: page.run_task(_narrate),
                       icon=ft.Icons.EDIT_NOTE),
        ft.Container(height=8),
        primary_button("3 · Hunt Visuals (SDK web)", lambda _e: page.run_task(_hunt),
                       icon=ft.Icons.IMAGE_SEARCH),
        ft.Container(height=8),
        secondary_button("Save scene edits", save_edits, icon=ft.Icons.SAVE),
        ft.Container(height=8),
        secondary_button("Next: TTS →", lambda _e: on_go(5), icon=ft.Icons.ARROW_FORWARD),
    ], spacing=8, expand=True)

    return art_shell(center, right, state=state, on_go=on_go,
                     header_title="Grounded Narration",
                     header_subtitle="Every claim must trace to the gathered facts — no fabrication.")
