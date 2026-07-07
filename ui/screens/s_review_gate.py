"""
Screen: Review Beats (pre-TTS panel-lock gate).

Sits between narration (stage 4) and TTS (stage 6). stages/review_gate.py
(built separately) writes review/candidates.json — one entry per narration
beat with its research source and ranked panel candidates. Here the user can
tweak the narration text, select 2-5 candidate panels per beat (rendered as
sub-shots), and Approve, which stamps review/locks.json with a sha1 of
narration.json so a later text edit can be detected as stale.

This screen never imports stages/review_gate.py — it reads/writes the JSON
files directly per the shared contract.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

import flet as ft

from ..bridge import (
    load_narration, load_review_candidates, load_review_locks,
    list_review_projects, narration_sha1, review_thumb_b64,
    save_narration_edits, save_review_locks,
)
from ..layout import primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)


THUMB_H = 200  # candidate tile thumb height — big enough to actually judge a panel
MIN_PANELS = 2
MAX_PANELS = 5


def _anchor_key(page_ref, panel_ref) -> tuple[int, int]:
    p = int(page_ref) if page_ref is not None else -1
    pan = int(panel_ref) if panel_ref is not None else -1
    return (p, pan)


def _anchor_label(page_ref, panel_ref) -> str:
    if page_ref is None:
        return ""
    p, pan = _anchor_key(page_ref, panel_ref)
    return f"p{p:02d} · full" if pan < 0 else f"p{p:02d} · pan{pan:02d}"


def _normalize_lock_panels(raw: dict | None) -> list[dict]:
    """A scene's locks.json entry as a list of {"page","panel"} dicts. Handles
    both the current v2 shape ({"panels": [...]}) and the old v1 single-panel
    shape ({"page","panel"}) so a project locked before the multi-select
    upgrade still loads as a 1-item selection."""
    if not raw:
        return []
    if "panels" in raw:
        return [{"page": int(p["page"]), "panel": int(p["panel"])}
                for p in raw.get("panels") or []]
    if "page" in raw:
        return [{"page": int(raw["page"]), "panel": int(raw["panel"])}]
    return []


def _chip(label: str, color: str, *, visible: bool = True) -> ft.Container:
    return ft.Container(
        content=ft.Text(label, size=9, color=color, weight=ft.FontWeight.BOLD),
        padding=ft.padding.symmetric(horizontal=8, vertical=3),
        border=ft.border.all(1, color), border_radius=3,
        visible=visible,
    )


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    project = state.project_name
    review = load_review_candidates(project) if project else None

    if not review or not (review.get("beats") or []):
        return _empty_state(page, state, on_go, on_state_change)

    narration = load_narration(project) or {}
    orig_text = {int(s.get("scene_id") or 0): str(s.get("text", ""))
                 for s in narration.get("scenes") or []}
    edited_text: dict[int, str] = dict(orig_text)

    locks_doc = load_review_locks(project)
    locks: dict = dict(locks_doc.get("locks") or {})
    locks_doc["locks"] = locks

    status_text = ft.Text("", size=12, color=TEXT_MUTED)
    # per-scene control refs so a lock/dup change repaints just that card
    card_refs: dict[int, dict] = {}

    def _selected_keys(scene_id: int) -> set[tuple[int, int]]:
        return {(p["page"], p["panel"])
                for p in _normalize_lock_panels(locks.get(str(scene_id)))}

    def _dup_scene_ids() -> set[int]:
        by_panel: dict[tuple[int, int], list[int]] = {}
        for sid_str, lock in locks.items():
            sid = int(sid_str)
            for p in _normalize_lock_panels(lock):
                key = (p["page"], p["panel"])
                by_panel.setdefault(key, []).append(sid)
        return {sid for ids in by_panel.values() if len(ids) > 1 for sid in ids}

    def _refresh_dup_badges():
        dups = _dup_scene_ids()
        for sid, refs in card_refs.items():
            refs["dup_badge"].visible = sid in dups
            try:
                refs["dup_badge"].update()
            except Exception:
                pass

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def _refresh_beat_card(scene_id: int):
        refs = card_refs.get(scene_id)
        if not refs:
            return
        selected = _selected_keys(scene_id)
        for key, tile in refs["tiles"].items():
            is_sel = key in selected
            tile.border = ft.border.all(2 if is_sel else 1, ACCENT if is_sel else BORDER)
            tile.bgcolor = BG_ELEVATED if is_sel else None
            icon = refs["icons"][key]
            icon.icon = ft.Icons.CHECK_CIRCLE if is_sel else ft.Icons.RADIO_BUTTON_UNCHECKED
            icon.icon_color = ACCENT if is_sel else TEXT_MUTED
        refs["auto_badge"].visible = not selected
        refs["count_chip"].value = f"{len(selected)}/{MAX_PANELS} selected"
        refs["warn_chip"].visible = len(selected) < MIN_PANELS
        try:
            refs["cand_row"].update()
            refs["auto_badge"].update()
            refs["count_chip"].update()
            refs["warn_chip"].update()
        except Exception:
            pass
        _refresh_dup_badges()

    def _toggle_candidate(scene_id: int, cand_page: int, cand_panel: int):
        sid = str(scene_id)
        panels = _normalize_lock_panels(locks.get(sid))
        key = (cand_page, cand_panel)
        if key in {(p["page"], p["panel"]) for p in panels}:
            panels = [p for p in panels if (p["page"], p["panel"]) != key]
        elif len(panels) >= MAX_PANELS:
            _show_snack(f"Max {MAX_PANELS} panels per beat.")
            return
        else:
            panels = panels + [{"page": cand_page, "panel": cand_panel}]
        if panels:
            locks[sid] = {"panels": panels, "source": "batcave"}
        else:
            locks.pop(sid, None)
        save_review_locks(project, locks_doc)
        _refresh_beat_card(scene_id)

    def _save_narration_text(_e=None):
        current = load_narration(project)
        if not current:
            return
        for s in current.get("scenes") or []:
            sid = int(s.get("scene_id") or 0)
            if sid in edited_text:
                s["text"] = edited_text[sid]
        save_narration_edits(project, current)
        status_text.value = "Saved narration text edits."
        status_text.color = SUCCESS
        try:
            status_text.update()
        except Exception:
            pass

    def _thumb(rel_path: str, *, height: int = THUMB_H) -> ft.Control:
        b64 = review_thumb_b64(project, rel_path)
        if b64:
            # this Flet version unifies path/URL/base64 into one `src` field —
            # a base64 string here is embedded directly, no filesystem fetch
            return ft.Image(src=b64, height=height, fit=ft.BoxFit.CONTAIN, border_radius=2)
        return ft.Container(
            width=int(height * 0.75), height=height, bgcolor=BG_ELEVATED,
            border=ft.border.all(1, BORDER), border_radius=2, alignment=ft.Alignment.CENTER,
            content=ft.Text("no thumb", size=9, color=TEXT_MUTED, font_family="Menlo"),
        )

    def _open_preview(scene_id: int, key: tuple[int, int], c: dict):
        """Lightbox: same crop, shown large, with an Add/Remove toggle so
        selecting stays reachable without leaving the dialog."""
        def _handler(_e):
            desc = str(c.get("desc") or "")
            dialog_line = c.get("dialog")

            def _toggle(_e2, sid=scene_id, k=key):
                _toggle_candidate(sid, k[0], k[1])
                page.pop_dialog()

            body: list[ft.Control] = [
                ft.Container(
                    content=_thumb(c.get("thumb", ""), height=520),
                    alignment=ft.Alignment.CENTER, expand=True,
                ),
            ]
            if desc:
                body.append(ft.Text(desc, size=12, color=TEXT_MUTED))
            if dialog_line:
                body.append(ft.Text(f"“{dialog_line}”", size=12,
                                     color=TEXT_PRIMARY, italic=True))

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(f"Scene {scene_id:02d} — p{key[0]:02d}·{key[1]}"),
                content=ft.Container(width=640, height=620,
                                      content=ft.Column(body, spacing=10, expand=True)),
                actions=[
                    ft.TextButton("Close", on_click=lambda _e: page.pop_dialog()),
                    primary_button("Add / Remove this panel", _toggle,
                                   icon=ft.Icons.CHECK_CIRCLE_OUTLINE),
                ],
            )
            page.show_dialog(dialog)
        return _handler

    def _beat_card(beat: dict) -> ft.Control:
        scene_id = int(beat.get("scene_id") or 0)
        anchor_key = _anchor_key(beat.get("page_ref"), beat.get("panel_ref"))
        selected = _selected_keys(scene_id)

        text_field = ft.TextField(
            value=edited_text.get(scene_id, str(beat.get("narration_text", ""))),
            multiline=True, min_lines=2, max_lines=5,
            border_color=BORDER, focused_border_color=ACCENT, text_size=13,
        )

        def _on_text(e, sid=scene_id):
            edited_text[sid] = e.control.value or ""
        text_field.on_change = _on_text

        source = beat.get("source") or {}
        src_label = " · ".join(x for x in (source.get("title"), source.get("issue")) if x)
        src_url = source.get("url") or ""

        def _open(url: str):
            def _click(_e, u=url):
                page.launch_url(u)
            return _click

        source_items: list[ft.Control] = []
        # Comic Vine cross-check WARN chip — a flagged item means the cited issue may be
        # wrong (couldn't find it / character not in credits); Master should re-check the
        # source BEFORE locking panels from a possibly-wrong download.
        if source.get("verified") is False:
            source_items.append(ft.Container(
                content=ft.Text(
                    f"⚠ UNVERIFIED — {source.get('verify_note') or 'Comic Vine could not confirm this issue'}",
                    size=11, color="#FFB000", weight=ft.FontWeight.BOLD),
                bgcolor="#3A2A00", border_radius=6,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                tooltip="Comic Vine cross-check flagged this item — the downloaded issue may not match the research.",
            ))
        # Moment-present WARN (stronger, red) — the vision judge found NO panel in the
        # cited issue that depicts this beat's moment, so the issue number is likely wrong
        # (the moment lives in a neighbouring issue). Master should change the source.
        if source.get("moment_warn"):
            source_items.append(ft.Container(
                content=ft.Text(f"⛔ {source['moment_warn']}", size=11,
                                color="#FF5555", weight=ft.FontWeight.BOLD),
                bgcolor="#3A0000", border_radius=6,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                tooltip="No panel in the cited issue depicts this moment — likely the wrong issue number.",
            ))
        if src_label:
            if src_url:
                source_items.append(ft.TextButton(
                    src_label, on_click=_open(src_url),
                    style=ft.ButtonStyle(padding=ft.padding.all(0)),
                ))
            else:
                source_items.append(ft.Text(src_label, size=11, color=TEXT_MUTED))
        for i, u in enumerate(source.get("research_urls") or [], start=1):
            source_items.append(ft.TextButton(
                f"research {i}", on_click=_open(u),
                style=ft.ButtonStyle(padding=ft.padding.all(0)),
            ))

        drawable_moment = source.get("drawable_moment")

        tiles: dict[tuple[int, int], ft.Container] = {}
        icons: dict[tuple[int, int], ft.IconButton] = {}
        cand_tiles: list[ft.Control] = []
        for c in beat.get("candidates") or []:
            key = (int(c.get("page", -1)), int(c.get("panel", -1)))
            is_selected = key in selected
            is_anchor = key == anchor_key
            tooltip = str(c.get("desc") or "")
            if c.get("dialog"):
                tooltip = f"{tooltip}\n\n“{c['dialog']}”"

            def _pick(_e, sid=scene_id, k=key):
                _toggle_candidate(sid, k[0], k[1])

            lock_icon = ft.IconButton(
                icon=ft.Icons.CHECK_CIRCLE if is_selected else ft.Icons.RADIO_BUTTON_UNCHECKED,
                icon_color=ACCENT if is_selected else TEXT_MUTED, icon_size=16,
                tooltip="Select this panel", on_click=_pick,
                style=ft.ButtonStyle(padding=ft.padding.all(0)),
            )
            icons[key] = lock_icon

            tile = ft.Container(
                content=ft.Column([
                    ft.Container(content=_thumb(c.get("thumb", "")), ink=True,
                                 on_click=_open_preview(scene_id, key, c),
                                 tooltip=tooltip or None),
                    ft.Row([
                        ft.Text(f"p{key[0]:02d}·{key[1]}", size=9, color=TEXT_MUTED,
                                font_family="Menlo"),
                        ft.Text(f"score {float(c.get('score', 0)):.2f}", size=9, color=ACCENT),
                    ], spacing=6),
                    ft.Row([_chip("anchor", TEXT_MUTED, visible=is_anchor), lock_icon],
                           spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=6, border=ft.border.all(2 if is_selected else 1,
                                                 ACCENT if is_selected else BORDER),
                border_radius=4, bgcolor=BG_ELEVATED if is_selected else None,
            )
            tiles[key] = tile
            cand_tiles.append(tile)

        # ListView lazy-builds children on demand (build_controls_on_demand
        # defaults True) — a Row would eagerly decode every candidate's
        # base64 thumb, which chokes once a beat has hundreds of candidates.
        cand_row = ft.ListView(cand_tiles, horizontal=True, spacing=8, height=270)
        auto_badge = _chip("auto", TEXT_MUTED, visible=not selected)
        dup_badge = _chip("duplicate panel", WARN, visible=scene_id in _dup_scene_ids())
        count_chip = ft.Text(f"{len(selected)}/{MAX_PANELS} selected", size=9,
                              color=TEXT_MUTED, font_family="Menlo")
        warn_chip = _chip("pick at least 2", WARN, visible=len(selected) < MIN_PANELS)
        card_refs[scene_id] = {"tiles": tiles, "icons": icons, "cand_row": cand_row,
                                "auto_badge": auto_badge, "dup_badge": dup_badge,
                                "count_chip": count_chip, "warn_chip": warn_chip}

        header = ft.Row([
            ft.Text(f"{scene_id:02d}", size=12, color=TEXT_MUTED, font_family="Menlo"),
            ft.Text(_anchor_label(beat.get("page_ref"), beat.get("panel_ref")),
                    size=10, color=TEXT_MUTED, font_family="Menlo"),
            ft.Container(expand=True),
            count_chip, auto_badge, dup_badge, warn_chip,
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        children: list[ft.Control] = [header, text_field]
        if source_items:
            children.append(ft.Row(source_items, spacing=14, wrap=True))
        if drawable_moment:
            children.append(ft.Text(f"Looking for: {drawable_moment}", size=11,
                                     color=TEXT_MUTED, italic=True))
        children.append(cand_row)

        return ft.Container(
            content=ft.Column(children, spacing=8),
            padding=14, border=ft.border.all(1, BORDER), border_radius=4, bgcolor=BG_PANEL,
        )

    cards = ft.ListView(
        [_beat_card(b) for b in review.get("beats") or []],
        spacing=12, expand=True, padding=ft.padding.symmetric(horizontal=28, vertical=16),
    )

    # ─── Approve / Un-approve ───────────────────────────────────────────────
    continue_btn = primary_button(
        "Continue → TTS", lambda _e: _continue(),
        icon=ft.Icons.ARROW_FORWARD, disabled=not locks_doc.get("approved"),
    )
    approve_btn = primary_button("Approve", lambda _e: _toggle_approve(),
                                  icon=ft.Icons.CHECK_CIRCLE_OUTLINE)

    def _refresh_approve_ui():
        approved = bool(locks_doc.get("approved"))
        approve_btn.text = "Un-approve" if approved else "Approve"
        approve_btn.icon = ft.Icons.UNPUBLISHED_OUTLINED if approved else ft.Icons.CHECK_CIRCLE_OUTLINE
        continue_btn.disabled = not approved
        status_text.value = (f"Approved at {locks_doc.get('approved_at')}."
                              if approved else "Not approved yet.")
        status_text.color = SUCCESS if approved else TEXT_MUTED
        if approved:
            state.mark_approved(5)
        else:
            state.approved[str(5)] = False
        save_state(state)
        page.update()

    def _toggle_approve():
        if locks_doc.get("approved"):
            locks_doc["approved"] = False
            locks_doc["approved_at"] = None
        else:
            _save_narration_text()
            locks_doc["approved"] = True
            locks_doc["approved_at"] = dt.datetime.now().isoformat(timespec="seconds")
            locks_doc["narration_sha1"] = narration_sha1(project)
        save_review_locks(project, locks_doc)
        _refresh_approve_ui()

    def _continue():
        state.current_stage = 6
        save_state(state)
        on_go(6)

    center = ft.Column([
        ft.Container(content=cards, expand=True),
        ft.Container(content=status_text,
                     padding=ft.padding.symmetric(horizontal=28, vertical=12)),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 5 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Review Beats", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Check each line's research source and select 2-5 of the best panels "
            "(rendered as sub-shots). Beats left on auto fall back to their "
            "grounded anchor panel.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=14),
        secondary_button("Save narration edits", _save_narration_text,
                         icon=ft.Icons.SAVE_OUTLINED),
        ft.Container(height=8),
        approve_btn,
        ft.Container(height=14),
        continue_btn,
    ], spacing=8, expand=True)

    _refresh_approve_ui()

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Review Beats",
        header_subtitle="Edit text, verify sources, lock panels, then approve before TTS.",
    )


def _empty_state(page: ft.Page, state: AppState, on_go, on_state_change) -> ft.Control:
    def _switch(name: str):
        state.project_name = name
        state.current_stage = 5
        save_state(state)
        on_state_change()

    other_projects = [p for p in list_review_projects() if p != state.project_name]
    rows: list[ft.Control] = [
        ft.Icon(ft.Icons.FACT_CHECK_OUTLINED, size=64, color=TEXT_MUTED),
        ft.Text("No beat candidates yet — run the review-gate build first.",
                size=13, color=TEXT_MUTED),
    ]
    if other_projects:
        rows.append(ft.Text("Other projects ready to review:", size=11, color=TEXT_MUTED))
        for name in other_projects:
            rows.append(ft.TextButton(name, on_click=lambda _e, n=name: _switch(n)))

    center = ft.Container(
        content=ft.Column(rows, spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.Alignment.CENTER, expand=True,
    )
    right = ft.Column([
        ft.Text("STEP 5 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Review Beats", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text("Nothing to review — generate review/candidates.json first.",
                size=12, color=TEXT_MUTED),
    ], spacing=8, expand=True)
    return three_col(center, right, state=state, on_go=on_go,
                      header_title="Review Beats",
                      header_subtitle="Run the review-gate build to get here.")
