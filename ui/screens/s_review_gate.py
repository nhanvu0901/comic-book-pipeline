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

from pathlib import Path

from config import PROJECTS_ROOT
from ..bridge import (
    image_b64, load_narration, load_preprocessed, load_review_candidates,
    load_review_locks, list_review_projects, narration_sha1, review_thumb_b64,
    run_blocking, save_narration_edits, save_review_locks,
)
from ..intro_import import import_intro_image, remove_intro_image
from ..layout import primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)
from stages.subject_panels import load_subject_panels


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


def _beat_key(beat: dict) -> str:
    """The beat's lock key. `beat_key` is the phase-2 contract field; a beat from a
    project reviewed before that upgrade lacks it, so fall back to str(scene_id) —
    identical to the key every existing locks.json already uses."""
    bk = beat.get("beat_key")
    return str(bk) if bk else str(int(beat.get("scene_id") or 0))


def _beat_unit(beat: dict) -> str:
    """"scene" (old default, MULTI-select) | "fragment" | "intro" (both SINGLE-select)."""
    return str(beat.get("unit") or "scene")


def _frag_idx_from_key(beat_key: str) -> int:
    """0-based fragment index from a "<scene_id>:<frag_idx>" beat_key."""
    if ":" not in beat_key:
        return 0
    try:
        return int(beat_key.rsplit(":", 1)[1])
    except ValueError:
        return 0


def _row_label(beat: dict, beat_key: str, unit: str) -> str:
    """Static label for a fragment/intro row — these have no free-text edit field
    (a fragment shares its scene_id's narration.json text with sibling fragments, so
    editing one in isolation has no safe write-back path)."""
    text = str(beat.get("narration_text", ""))
    if unit == "intro":
        return f"INTRO (cold-open): {text}"
    if unit == "fragment":
        scene_id = int(beat.get("scene_id") or 0)
        return f"s{scene_id} · mảnh {_frag_idx_from_key(beat_key) + 1}: {text}"
    return text


def _init_pre_selected(beats: list[dict], locks: dict) -> None:
    """Seed `locks` (in place) with each beat's pre_selected panels when locks.json has
    no entry for that beat_key yet — an existing lock always wins, and a beat with no
    (or empty) pre_selected is a no-op. Old-format beats (no beat_key/pre_selected
    fields) are untouched, matching prior behaviour exactly."""
    for beat in beats:
        bk = _beat_key(beat)
        if bk in locks:
            continue
        panels = [{"page": int(p["page"]), "panel": int(p["panel"])}
                  for p in (beat.get("pre_selected") or []) if p.get("page") is not None]
        if panels:
            locks[bk] = {"panels": panels, "source": "pre_selected"}


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
    _init_pre_selected(review.get("beats") or [], locks)

    status_text = ft.Text("", size=12, color=TEXT_MUTED)
    # per-beat control refs (keyed by beat_key) so a lock/dup change repaints just that card
    card_refs: dict[str, dict] = {}

    def _selected_keys(beat_key: str) -> set[tuple[int, int]]:
        return {(p["page"], p["panel"])
                for p in _normalize_lock_panels(locks.get(beat_key))}

    def _dup_beat_keys() -> set[str]:
        by_panel: dict[tuple[int, int], list[str]] = {}
        for bk, lock in locks.items():
            for p in _normalize_lock_panels(lock):
                key = (p["page"], p["panel"])
                by_panel.setdefault(key, []).append(bk)
        return {bk for ids in by_panel.values() if len(ids) > 1 for bk in ids}

    def _refresh_dup_badges():
        dups = _dup_beat_keys()
        for bk, refs in card_refs.items():
            refs["dup_badge"].visible = bk in dups
            try:
                refs["dup_badge"].update()
            except Exception:
                pass

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    # ─── Import external intro image ────────────────────────────────────────
    # Master can open a Q&A with an image from disk instead of a comic panel. The
    # inject (sips → jpg + preprocessed page + subject_panels force_intro entry)
    # lives in ui/intro_import.py (pure, tested); this screen only calls it and
    # renders the current imports as a strip above the beat cards.
    intro_list_col = ft.Column(spacing=8)
    file_picker = ft.FilePicker()
    try:
        page.services.append(file_picker)
    except Exception:
        pass

    def _intro_row(entry: dict, src_by_page: dict[int, str]) -> ft.Control:
        page_n = int(entry.get("page", -1))
        b64 = image_b64(src_by_page.get(page_n, ""))
        thumb = (ft.Image(src=b64, height=90, fit=ft.BoxFit.CONTAIN, border_radius=2)
                 if b64 else ft.Container(width=68, height=90, bgcolor=BG_ELEVATED,
                                          border=ft.border.all(1, BORDER), border_radius=2))

        def _remove(_e, pn=page_n):
            try:
                remove_intro_image(PROJECTS_ROOT / project, pn)
                _show_snack(f"Removed imported intro p{pn}.")
                _rebuild_intro_box()
            except Exception as e:
                _show_snack(f"Remove failed: {e}")

        return ft.Container(
            content=ft.Row([
                thumb,
                ft.Column([
                    _chip("IMPORTED — intro", ACCENT),
                    ft.Text(f"p{page_n}", size=10, color=TEXT_MUTED, font_family="Menlo"),
                ], spacing=4),
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=WARN,
                              tooltip="Remove imported intro", on_click=_remove),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            padding=8, border=ft.border.all(1, ACCENT), border_radius=4, bgcolor=BG_PANEL,
        )

    def _rebuild_intro_box():
        src_by_page = {int(pg.get("page_number", -1)): str(pg.get("source_image") or "")
                       for pg in load_preprocessed(project)}
        entries = [p for p in (load_subject_panels(project).get("panels") or [])
                   if p.get("force_intro")]
        intro_list_col.controls = [_intro_row(e, src_by_page) for e in entries]
        try:
            intro_list_col.update()
        except Exception:
            pass

    async def _do_import():
        try:
            files = await file_picker.pick_files(
                dialog_title="Pick an intro image (avif / jpg / png)",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["avif", "jpg", "jpeg", "png"],
                allow_multiple=False,
            )
        except Exception as e:
            _show_snack(f"File picker failed: {e}")
            return
        if not files or not files[0].path:
            return
        subject = str(load_subject_panels(project).get("subject") or "").strip() or "intro"
        try:
            entry = await run_blocking(
                import_intro_image, PROJECTS_ROOT / project, Path(files[0].path), subject)
            _show_snack(f"Imported intro image → p{entry['page']} (subject: {entry['subject']}).")
            _rebuild_intro_box()
        except Exception as e:
            _show_snack(f"Import failed: {e}")

    def _on_import_click(_e):
        page.run_task(_do_import)

    def _refresh_beat_card(beat_key: str):
        refs = card_refs.get(beat_key)
        if not refs:
            return
        unit = refs["unit"]
        selected = _selected_keys(beat_key)
        for key, tile in refs["tiles"].items():
            is_sel = key in selected
            tile.border = ft.border.all(2 if is_sel else 1, ACCENT if is_sel else BORDER)
            tile.bgcolor = BG_ELEVATED if is_sel else None
            icon = refs["icons"][key]
            icon.icon = ft.Icons.CHECK_CIRCLE if is_sel else ft.Icons.RADIO_BUTTON_UNCHECKED
            icon.icon_color = ACCENT if is_sel else TEXT_MUTED
        refs["auto_badge"].visible = not selected
        cap = MAX_PANELS if unit == "scene" else 1
        refs["count_chip"].value = f"{len(selected)}/{cap} selected"
        refs["warn_chip"].visible = unit == "scene" and len(selected) < MIN_PANELS
        try:
            refs["cand_row"].update()
            refs["auto_badge"].update()
            refs["count_chip"].update()
            refs["warn_chip"].update()
        except Exception:
            pass
        _refresh_dup_badges()

    def _toggle_candidate(beat_key: str, cand_page: int, cand_panel: int, unit: str):
        panels = _normalize_lock_panels(locks.get(beat_key))
        key = (cand_page, cand_panel)
        already = key in {(p["page"], p["panel"]) for p in panels}
        if unit == "scene":
            if already:
                panels = [p for p in panels if (p["page"], p["panel"]) != key]
            elif len(panels) >= MAX_PANELS:
                _show_snack(f"Max {MAX_PANELS} panels per beat.")
                return
            else:
                panels = panels + [{"page": cand_page, "panel": cand_panel}]
        else:  # fragment / intro — single-select: pick replaces, re-pick clears
            panels = [] if already else [{"page": cand_page, "panel": cand_panel}]
        if panels:
            locks[beat_key] = {"panels": panels, "source": "batcave"}
        else:
            locks.pop(beat_key, None)
        save_review_locks(project, locks_doc)
        _refresh_beat_card(beat_key)

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

    def _open_preview(beat_key: str, unit: str, scene_id: int, key: tuple[int, int], c: dict):
        """Lightbox: same crop, shown large, with an Add/Remove toggle so
        selecting stays reachable without leaving the dialog."""
        def _handler(_e):
            desc = str(c.get("desc") or "")
            dialog_line = c.get("dialog")

            def _toggle(_e2, bk=beat_key, u=unit, k=key):
                _toggle_candidate(bk, k[0], k[1], u)
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
        beat_key = _beat_key(beat)
        unit = _beat_unit(beat)
        anchor_key = _anchor_key(beat.get("page_ref"), beat.get("panel_ref"))
        selected = _selected_keys(beat_key)

        if unit == "scene":
            text_field = ft.TextField(
                value=edited_text.get(scene_id, str(beat.get("narration_text", ""))),
                multiline=True, min_lines=2, max_lines=5,
                border_color=BORDER, focused_border_color=ACCENT, text_size=13,
            )

            def _on_text(e, sid=scene_id):
                edited_text[sid] = e.control.value or ""
            text_field.on_change = _on_text
            text_control: ft.Control = text_field
        else:
            text_control = ft.Text(_row_label(beat, beat_key, unit), size=13,
                                    color=TEXT_PRIMARY)

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

            def _pick(_e, bk=beat_key, k=key, u=unit):
                _toggle_candidate(bk, k[0], k[1], u)

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
                                 on_click=_open_preview(beat_key, unit, scene_id, key, c),
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
        dup_badge = _chip("duplicate panel", WARN, visible=beat_key in _dup_beat_keys())
        cap = MAX_PANELS if unit == "scene" else 1
        count_chip = ft.Text(f"{len(selected)}/{cap} selected", size=9,
                              color=TEXT_MUTED, font_family="Menlo")
        warn_chip = _chip("pick at least 2", WARN,
                           visible=unit == "scene" and len(selected) < MIN_PANELS)
        card_refs[beat_key] = {"tiles": tiles, "icons": icons, "cand_row": cand_row,
                               "auto_badge": auto_badge, "dup_badge": dup_badge,
                               "count_chip": count_chip, "warn_chip": warn_chip,
                               "unit": unit}

        header = ft.Row([
            ft.Text(f"{scene_id:02d}", size=12, color=TEXT_MUTED, font_family="Menlo"),
            ft.Text(_anchor_label(beat.get("page_ref"), beat.get("panel_ref")),
                    size=10, color=TEXT_MUTED, font_family="Menlo"),
            ft.Container(expand=True),
            count_chip, auto_badge, dup_badge, warn_chip,
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        children: list[ft.Control] = [header, text_control]
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

    _rebuild_intro_box()  # populate the imported-intro strip on first paint
    intro_section = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Text("Intro image", size=12, weight=ft.FontWeight.BOLD,
                        color=TEXT_PRIMARY),
                ft.Container(expand=True),
                secondary_button("📷 Import intro image", _on_import_click,
                                 icon=ft.Icons.IMAGE_OUTLINED),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            intro_list_col,
        ], spacing=8),
        padding=ft.padding.symmetric(horizontal=28, vertical=10),
    )

    center = ft.Column([
        intro_section,
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
