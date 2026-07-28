"""
Screen 6: Review & Edit (storyboard).

A vertical list of scene cards — each card is the comic panel shown on screen for
that line + the spoken narration (editable) + a "pXX · panYY" meta tag + a HOOK/OUTRO
chip. The user can (a) edit each scene's narration, (b) change the panel a scene uses
via a picker dialog, then (c) re-render the video (Stage 4 + Stage 5) with the proven
recipe. Mirrors the delivered narration_view.html look on the app's dark theme.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import flet as ft

from ..bridge import (
    ensure_panel_thumbs, format_exception, is_answer_project, load_narration,
    page_numbers, panels_for_page, render_scene_panel_path, run_blocking,
    run_stage6_render, save_narration_edits,
)
from ..layout import log_list, primary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED,
    TEXT_PRIMARY, WARN,
)


WPS_DEFAULT = 2.88
# Ember accent for the reveal-style emphasis (matches narration_view.html).
EMBER = "#e5643c"
PANEL_W = 300


def _panel_label(page: int, panel_ref: int) -> str:
    if panel_ref is None or int(panel_ref) < 0:
        return f"p{int(page):02d} · full"
    return f"p{int(page):02d} · pan{int(panel_ref):02d}"


def _thumb_image(path: str, *, width: int) -> ft.Control:
    if path and Path(path).exists():
        return ft.Image(src=path, width=width, fit=ft.BoxFit.CONTAIN, border_radius=2)
    return ft.Container(
        width=width, height=int(width * 0.62), bgcolor=BG_ELEVATED,
        border=ft.border.all(1, BORDER), border_radius=2, alignment=ft.Alignment.CENTER,
        content=ft.Text("no panel", size=10, color=TEXT_MUTED, font_family="Menlo"),
    )


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    if state.project_name and is_answer_project(state.project_name):
        return _skipped_for_qa(state, on_go)

    narration = load_narration(state.project_name) if state.project_name else None

    if not narration or not (narration.get("scenes") or []):
        center = ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.EDIT_NOTE, size=64, color=TEXT_MUTED),
                ft.Text("No narration yet — run Stage 3-5 first.",
                        size=13, color=TEXT_MUTED),
            ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            alignment=ft.Alignment.CENTER, expand=True,
        )
        right = ft.Column([
            ft.Text("STEP 7 OF 8", size=10, color=TEXT_MUTED),
            ft.Text("Review & Edit", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ft.Text("Nothing to review — generate a narration first.",
                    size=12, color=TEXT_MUTED),
        ], spacing=8, expand=True)
        return three_col(center, right, state=state, on_go=on_go,
                         header_title="Review & Edit",
                         header_subtitle="Run the earlier stages to get here.")

    # Thumbnails — degrade gracefully to captions-only if PIL/pages are unavailable.
    try:
        ensure_panel_thumbs(state.project_name)
    except Exception:
        pass

    wps = float(narration.get("words_per_second") or WPS_DEFAULT) or WPS_DEFAULT
    # Working copy — edited in memory, written only on Save / Save & Re-render.
    scenes: list[dict] = [dict(s) for s in narration.get("scenes") or []]

    lv, push_log = log_list(page)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    status_text = ft.Text("", color=TEXT_MUTED, size=12)
    counter = ft.Text("", size=11, color=TEXT_MUTED)

    # Per-scene control refs so a panel change updates just that card.
    img_ctls: dict[int, ft.Container] = {}
    caption_ctls: dict[int, ft.Text] = {}

    def _recompute_counter():
        total = sum(len(str(s.get("text", "")).split()) for s in scenes)
        dur = total / wps if wps else 0.0
        counter.value = f"{total} words · ~{dur:.0f}s · {len(scenes)} scenes"
        try:
            counter.update()
        except Exception:
            pass

    def _set_card_panel(i: int, panel_page: int, panel_ref: int):
        scenes[i]["page_ref"] = int(panel_page)
        scenes[i]["panel_ref"] = int(panel_ref)
        # Re-point the card image + caption to the newly chosen panel.
        new_path = render_scene_panel_path(state.project_name, scenes[i])
        holder = img_ctls.get(i)
        if holder is not None:
            holder.content = _thumb_image(new_path, width=PANEL_W)
            try:
                holder.update()
            except Exception:
                pass
        cap = caption_ctls.get(i)
        if cap is not None:
            cap.value = _panel_label(panel_page, panel_ref)
            try:
                cap.update()
            except Exception:
                pass
        state.mark_dirty(6)
        save_state(state)

    # ─── Panel-picker dialog ────────────────────────────────────────────────
    def _open_picker(i: int):
        pages = page_numbers(state.project_name)
        if not pages:
            _show_snack("No preprocessed pages found.")
            return
        cur_page = int(scenes[i].get("page_ref") or 0)
        start_page = cur_page if cur_page in pages else pages[0]
        sel = {"page": start_page}

        grid = ft.GridView(expand=True, runs_count=3, max_extent=200,
                           child_aspect_ratio=0.72, spacing=10, run_spacing=10)

        def _tile(thumb_path: str, label: str, ref: int) -> ft.Control:
            def _pick(_e, r=ref):
                _set_card_panel(i, sel["page"], r)
                page.pop_dialog()
            return ft.Container(
                content=ft.Column([
                    _thumb_image(thumb_path, width=180),
                    ft.Text(label, size=9, color=TEXT_MUTED, font_family="Menlo"),
                ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=6, border=ft.border.all(1, BORDER), border_radius=4,
                bgcolor=BG_ELEVATED, ink=True, on_click=_pick,
            )

        def _rebuild_grid():
            pnls = panels_for_page(state.project_name, sel["page"])
            tiles: list[ft.Control] = [
                _tile(render_scene_panel_path(state.project_name,
                                              {"page_ref": sel["page"], "panel_ref": -1}),
                      "whole page", -1),
            ]
            for p in pnls:
                tiles.append(_tile(p["thumb_path"], f"pan{p['index']:02d}", p["index"]))
            grid.controls = tiles
            try:
                grid.update()
            except Exception:
                pass

        def _on_page_change(_e):
            try:
                sel["page"] = int(page_dd.value)
            except (TypeError, ValueError):
                return
            _rebuild_grid()

        page_dd = ft.Dropdown(
            label="Page", value=str(start_page),
            options=[ft.dropdown.Option(str(p)) for p in pages],
            border_color=BORDER, focused_border_color=ACCENT, text_size=12,
            on_change=_on_page_change, width=140,
        )
        _rebuild_grid()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Change panel — scene {scenes[i].get('scene_id', i + 1)}"),
            content=ft.Container(
                width=680, height=460,
                content=ft.Column([page_dd, grid], spacing=12, expand=True),
            ),
            actions=[ft.TextButton("Close", on_click=lambda _e: page.pop_dialog())],
        )
        page.show_dialog(dialog)

    # ─── Scene card ─────────────────────────────────────────────────────────
    def _scene_card(i: int, scene: dict) -> ft.Control:
        is_intro = bool(scene.get("is_intro"))
        is_outro = bool(scene.get("is_outro"))
        scene_id = int(scene.get("scene_id") or i + 1)

        img_holder = ft.Container(
            content=_thumb_image(render_scene_panel_path(state.project_name, scene),
                                  width=PANEL_W),
            width=PANEL_W,
        )
        caption = ft.Text(_panel_label(int(scene.get("page_ref") or 0),
                                       scene.get("panel_ref")),
                          size=10, color=TEXT_MUTED, font_family="Menlo")
        img_ctls[i] = img_holder
        caption_ctls[i] = caption

        head: list[ft.Control] = []
        if is_intro:
            head.append(_chip("HOOK", SUCCESS))
        elif is_outro:
            head.append(_chip("OUTRO", WARN))

        text_field = ft.TextField(
            value=str(scene.get("text", "")), multiline=True, min_lines=2, max_lines=6,
            border_color=BORDER, focused_border_color=ACCENT, text_size=14,
        )

        def _on_text(e, idx=i):
            scenes[idx]["text"] = e.control.value or ""
            _recompute_counter()

        text_field.on_change = _on_text

        left = ft.Column([
            img_holder,
            caption,
        ], spacing=4, width=PANEL_W)

        right_col = ft.Column([
            ft.Row(head + [ft.Container(expand=True),
                           ft.IconButton(icon=ft.Icons.SWAP_HORIZ, icon_size=18,
                                         icon_color=ACCENT, tooltip="Change panel",
                                         on_click=lambda _e, idx=i: _open_picker(idx))],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            text_field,
        ], spacing=6, expand=True)

        # intro/outro get a distinct dashed-feel (accent border + darker bg); the rest
        # keep the plain panel look.
        if is_intro or is_outro:
            border_color, bg = (SUCCESS if is_intro else WARN), "#101413"
        else:
            border_color, bg = BORDER, BG_PANEL

        return ft.Container(
            content=ft.Row([
                ft.Container(
                    content=ft.Text(f"{scene_id:02d}", size=12, color=TEXT_MUTED,
                                    font_family="Menlo"),
                    width=28,
                ),
                left,
                right_col,
            ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.START),
            padding=14, border=ft.border.all(1, border_color), border_radius=3,
            bgcolor=bg,
        )

    cards = ft.ListView(
        [_scene_card(i, s) for i, s in enumerate(scenes)],
        spacing=12, expand=True, padding=ft.padding.symmetric(horizontal=28, vertical=16),
    )
    _recompute_counter()

    # ─── Actions ─────────────────────────────────────────────────────────────
    def _save():
        for i, s in enumerate(scenes):
            wc = len(str(s.get("text", "")).split())
            s["word_count"] = wc
            s["target_seconds"] = round(wc / wps, 2) if wps else 0.0
        narration["scenes"] = scenes
        total = sum(s["word_count"] for s in scenes)
        narration["total_word_count"] = total
        narration["estimated_duration_seconds"] = round(total / wps, 2) if wps else 0.0
        save_narration_edits(state.project_name, narration)

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def save_click(_e):
        _save()
        status_text.value = "Saved edits to narration.json."
        status_text.color = SUCCESS
        _show_snack("Saved edits.")
        page.update()

    continue_btn = primary_button(
        "Continue → Final Video", lambda _e: _continue(),
        icon=ft.Icons.ARROW_FORWARD, disabled=not state.is_approved(7),
    )

    def _continue():
        state.mark_approved(7)
        state.current_stage = 8
        save_state(state)
        on_go(8)

    async def _render():
        _save()
        running.visible = True
        status_text.value = "Re-rendering (Stage 4 + Stage 5)…"
        status_text.color = WARN
        continue_btn.disabled = True
        page.update()
        try:
            final = await run_blocking(run_stage6_render, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            status_text.value = "Render failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        running.visible = False
        p = Path(final)
        size_mb = p.stat().st_size / (1024 * 1024) if p.exists() else 0.0
        status_text.value = f"Rendered {p.name} ({size_mb:.1f} MB)."
        status_text.color = SUCCESS
        state.mark_approved(7)
        save_state(state)
        continue_btn.disabled = False
        page.update()

    def render_click(_e):
        page.run_task(_render)

    center = ft.Column([
        ft.Container(content=cards, expand=True),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status_text], spacing=10),
                ft.Container(content=lv, height=110, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 7 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Review & Edit", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Each card is the panel shown while that line is spoken. Edit any line, or "
            "swap its panel, then re-render. The image previews what's currently rendered.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=10),
        counter,
        ft.Container(height=14),
        primary_button("Save edits", save_click, icon=ft.Icons.SAVE_OUTLINED),
        ft.Container(height=8),
        primary_button("Save & Re-render (Stage 4 + 5)", render_click,
                       icon=ft.Icons.MOVIE_FILTER),
        ft.Container(height=14),
        continue_btn,
    ], spacing=8, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Review & Edit the Storyboard",
        header_subtitle="Fix a line or swap a panel, then re-render the Short.",
    )


def _skipped_for_qa(state: AppState, on_go: Callable[[int], None]) -> ft.Control:
    """Q&A (answer_research) projects render multiple panels per scene — this
    single-panel-per-scene storyboard editor doesn't apply, so jump straight
    to Final Video."""
    def _continue(_e=None):
        state.mark_approved(7)
        state.current_stage = 8
        save_state(state)
        on_go(8)

    center = ft.Container(
        content=ft.Column([
            ft.Icon(ft.Icons.FACT_CHECK_OUTLINED, size=64, color=TEXT_MUTED),
            ft.Text("Skipped for Q&A — panel choices are made in Review Beats.",
                    size=13, color=TEXT_MUTED),
        ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.Alignment.CENTER, expand=True,
    )
    right = ft.Column([
        ft.Text("STEP 7 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Review & Edit", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Q&A projects render multiple panels per scene (sub-shots), so this "
            "single-panel storyboard editor doesn't apply. Panel choices were "
            "already made in Review Beats.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=14),
        primary_button("Continue → Final Video", _continue, icon=ft.Icons.ARROW_FORWARD),
    ], spacing=8, expand=True)
    return three_col(center, right, state=state, on_go=on_go,
                      header_title="Review & Edit",
                      header_subtitle="Skipped for Q&A — panel choices are made in Review Beats.")


def _chip(label: str, color: str) -> ft.Control:
    return ft.Container(
        content=ft.Text(label, size=9, color=color, weight=ft.FontWeight.BOLD),
        padding=ft.padding.symmetric(horizontal=8, vertical=3),
        border=ft.border.all(1, color), border_radius=3,
    )
