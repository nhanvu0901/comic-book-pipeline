"""ui/screens/s_screen_gate.py
Stage 5 for mode "screen_qa": pick a visual for each BEAT of a screen Q&A — an MP4 clip from
YouTube, a still image, or nothing (a text card). No comic panel exists in this mode, so the
comic review gate (s_review_gate) hands the screen over to this one.

A thin view over stages/stage_5/screen_selection.py (all the file IO) and
stages/stage_5/screen_beats.py (the beat rows). It writes the files the pipeline already reads:
review/clips/clips.json, review/locks.json + review/custom/ — so an MP4 picked in P1's
/moments_review tab (the SAME routes the comic gate's "Dùng MP4" button opens) and a still added
here resolve at render time through the unchanged comic resolvers.

Render order of preference per beat (stages/stage_5/screen_shots.py): clip → backup clip → still
→ text card, so a beat with nothing picked is never an error — it is just a card.

Flet 0.85+ note: ui/_flet_compat re-creates padding/margin/border helpers as KEYWORD-only, so
every call here passes keywords (ft.padding.symmetric(horizontal=..)); a positional call passes
on a 0.84 Mac and crashes the 0.86 server.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import flet as ft

import config
from stages.stage_5 import screen_selection as sel
from stages.stage_5.screen_beats import ScreenBeat, screen_beat_rows

from ..bridge import run_blocking
from ..custom_image import add_custom_image
from ..layout import primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)

log = logging.getLogger(__name__)

# One listener per Flet page: a rebuild of the screen replaces its own, instead of piling up a
# stale closure per visit (each one would repaint a screen nobody sees any more).
_LISTENERS: dict[int, Callable[[dict[str, Any]], None]] = {}


def _routes():
    """ui.web_routes (FastAPI) — only exists in the flag-ON build, so imported lazily."""
    from .. import web_routes
    return web_routes


def _replace_listener(page: ft.Page, cb: Callable[[dict[str, Any]], None]) -> None:
    try:
        wr = _routes()
    except Exception as exc:                                          # noqa: BLE001
        log.warning("moment-picked listener not registered (ui.web_routes unavailable): %s", exc)
        return
    old = _LISTENERS.pop(id(page), None)
    if old is not None:
        try:
            remover = getattr(wr, "remove_moment_picked_listener", None)
            if remover:
                remover(old)
            elif old in wr._listeners:
                wr._listeners.remove(old)
        except Exception as exc:                                      # noqa: BLE001
            log.warning("could not remove the previous moment-picked listener: %s", exc)
    wr.add_moment_picked_listener(cb)
    _LISTENERS[id(page)] = cb


def moments_url(project: str, beat: str, query: str) -> str:
    """The P1 pick tab for one beat (the query is URL-encoded: a clip search hint is free text)."""
    return f"/moments_review?project={quote(project)}&beat={quote(beat)}&q={quote(query)}"


def beat_label(b: ScreenBeat) -> str:
    if b.unit == "intro":
        return "Hook · intro"
    if b.unit == "outro":
        return "Chốt · outro"
    if b.unit == "scene":
        return f"Cảnh {b.scene_id}"
    return f"Cảnh {b.scene_id} · mảnh {int(b.frag_idx or 0) + 1}"


def _clip_chip_text(entry: dict) -> str:
    start = entry.get("source_start", entry.get("start", 0.0))
    try:
        start = float(start)
    except (TypeError, ValueError):
        start = 0.0
    return f"MP4 {entry.get('id', '?')} @ {start:.1f}s"


def _chip(text: str, color: str, icon: Any = None) -> ft.Control:
    row: list[ft.Control] = []
    if icon is not None:
        row.append(ft.Icon(icon, color=color, size=14))
    row.append(ft.Text(text, size=11, color=color, weight=ft.FontWeight.W_600))
    return ft.Container(
        content=ft.Row(row, spacing=5, tight=True),
        bgcolor=BG_ELEVATED, border_radius=6,
        padding=ft.padding.symmetric(horizontal=8, vertical=4),
    )


def _empty(message: str, hint: str) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [ft.Text(message, size=18, color=WARN), ft.Text(hint, size=14, color=TEXT_MUTED)],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=40, expand=True, alignment=ft.Alignment.CENTER,
    )


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    project = state.project_name
    root = Path(config.PROJECTS_ROOT) / project if project else None
    narration = sel.load_narration(root) if root is not None else {}
    beats: list[ScreenBeat] = []
    if root is not None and narration.get("scenes"):
        notice = "Kịch bản đã đổi — đã xóa toàn bộ lựa chọn cũ (clip, ảnh) và approval." \
            if sel.sync_with_narration(root) else ""
        ctx = sel.load_screen_context(root)
        beats = screen_beat_rows(narration, ctx)
    else:
        notice, ctx = "", {}

    if not beats:
        return three_col(
            _empty("Chưa có kịch bản Screen Q&A cho dự án này.",
                   "Chạy `python -m stages.screen_pipeline` (research → narrate) để tạo "
                   "narration.json với mode='screen_qa'."),
            ft.Text("Screen Q&A", size=14, color=TEXT_MUTED),
            state=state, on_go=on_go, header_title="Screen Q&A — chọn hình cho beat",
            header_subtitle="Chưa có narration.")

    clips_on = bool(config.ENABLE_VIDEO_CLIPS)
    clips: dict[str, dict] = sel.load_clips(root)
    stills: dict[str, str] = sel.load_stills(root)
    secs: dict[str, float] = sel.beat_seconds(root, narration, ctx)
    queries: dict[str, str] = {b.key: b.query for b in beats}
    backups_running: set[str] = set()

    if clips_on:
        # The comic gate does the same: durations for the clip download come from the background
        # TTS, which is started here (one runner per project; a failure is logged, not hidden).
        try:
            from stages.stage_4.background_tts import start_background_tts
            start_background_tts(project)
        except Exception as exc:                                      # noqa: BLE001
            log.warning("background TTS not started: %s", exc)

    status_text = ft.Text("", size=12, color=TEXT_MUTED)
    summary_row = ft.Row([], spacing=8, wrap=True)
    cards_col = ft.Column([], spacing=10)
    tts_text = ft.Text("", size=11, color=TEXT_MUTED)
    notice_box = ft.Container(
        content=ft.Text(notice, size=12, color=WARN), visible=bool(notice),
        bgcolor=BG_PANEL, border=ft.border.all(1, WARN), border_radius=8,
        padding=ft.padding.symmetric(horizontal=14, vertical=10),
    )

    file_picker = ft.FilePicker()
    try:
        page.services.append(file_picker)
    except Exception as exc:                                          # noqa: BLE001
        log.warning("file picker not attached: %s", exc)

    def _snack(msg: str) -> None:
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def _push() -> None:
        try:
            page.update()
        except Exception as exc:                                      # noqa: BLE001
            log.debug("page.update failed: %s", exc)

    # ─── approval ───────────────────────────────────────────────────────────
    approve_btn = primary_button("Approve", lambda _e: _toggle_approve(),
                                 icon=ft.Icons.CHECK_CIRCLE_OUTLINE)
    continue_btn = primary_button("Continue → TTS", lambda _e: _continue(),
                                  icon=ft.Icons.ARROW_FORWARD, disabled=True)

    def _refresh_approval(push: bool = True) -> None:
        approved = sel.is_approved(root)
        approve_btn.text = "Un-approve" if approved else "Approve"
        approve_btn.icon = (ft.Icons.UNPUBLISHED_OUTLINED if approved
                            else ft.Icons.CHECK_CIRCLE_OUTLINE)
        continue_btn.disabled = not approved
        status_text.value = (f"Approved at {sel.approved_at(root)}." if approved
                             else "Chưa approve — approve để chạy TTS và render.")
        status_text.color = SUCCESS if approved else TEXT_MUTED
        if approved:
            state.mark_approved(5)
        else:
            state.approved["5"] = False
        save_state(state)
        if push:
            _push()

    def _toggle_approve() -> None:
        sel.set_approved(root, not sel.is_approved(root))
        _refresh_approval()

    def _continue() -> None:
        state.current_stage = 6
        save_state(state)
        on_go(6)

    # ─── cards ──────────────────────────────────────────────────────────────
    def _open_picker(key: str) -> None:
        try:
            from stages.stage_4.background_tts import bump_priority_beat
            bump_priority_beat(project, key)
        except Exception as exc:                                      # noqa: BLE001
            log.debug("TTS priority bump skipped: %s", exc)
        url = moments_url(project, key, queries.get(key, ""))

        async def _go():
            await page.launch_url(url)          # flet 0.85 wraps launch_url: run_task needs a coroutine
        page.run_task(_go)

    async def _add_still(key: str) -> None:
        try:
            files = await file_picker.pick_files(
                dialog_title="Chọn ảnh cho beat này",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["jpg", "jpeg", "png", "webp"],
                allow_multiple=False, with_data=True)
        except Exception as exc:                                      # noqa: BLE001
            _snack(f"File picker lỗi: {exc}")
            return
        if not files:
            return
        f = files[0]
        if f.bytes is None and not f.path:
            _snack("File picker không trả dữ liệu — thử lại.")
            return
        try:
            entry = await run_blocking(add_custom_image, root, Path(f.path or f.name), key,
                                       data=f.bytes)
            sel.lock_still(root, key, entry["file"])
        except Exception as exc:                                      # noqa: BLE001
            _snack(f"Thêm ảnh lỗi: {exc}")
            return
        stills[key] = entry["file"]
        _refresh_cards(f"Đã thêm ảnh cho beat {key}.")

    def _use_card(key: str) -> None:
        sel.clear_beat(root, key)
        clips.pop(key, None)
        stills.pop(key, None)
        _refresh_cards(f"Beat {key}: dùng thẻ chữ.")

    def _reload() -> None:
        clips.clear()
        clips.update(sel.load_clips(root))
        stills.clear()
        stills.update(sel.load_stills(root))
        secs.clear()
        secs.update(sel.beat_seconds(root, narration, ctx))

    def _status_chips(key: str) -> list[ft.Control]:
        chips: list[ft.Control] = []
        clip = clips.get(key)
        if clip:
            chips.append(_chip(_clip_chip_text(clip), SUCCESS, ft.Icons.CHECK_CIRCLE))
            bk = clip.get("backup")
            if isinstance(bk, dict) and bk.get("id"):
                chips.append(_chip(f"dự phòng: {bk['id']}", ACCENT, ft.Icons.SHIELD_OUTLINED))
            elif key in backups_running:
                chips.append(_chip("đang tìm clip dự phòng…", TEXT_MUTED))
        if key in stills:
            chips.append(_chip(f"Ảnh: {Path(stills[key]).name}", ACCENT, ft.Icons.IMAGE_OUTLINED))
        if not clip and key not in stills:
            chips.append(_chip("Thẻ chữ (tự động)", TEXT_MUTED, ft.Icons.TEXT_SNIPPET_OUTLINED))
        return chips

    def _card(b: ScreenBeat) -> ft.Control:
        key = b.key
        has_clip, has_still = key in clips, key in stills

        def _on_query(e, k=key):
            queries[k] = e.control.value or ""

        query_field = ft.TextField(
            value=queries.get(key, ""), label="Từ khóa tìm clip", dense=True, text_size=12,
            expand=True, on_change=_on_query)
        pick_btn = secondary_button(
            "Đổi MP4" if has_clip else "Chọn MP4", lambda _e, k=key: _open_picker(k),
            icon=ft.Icons.VIDEO_FILE_OUTLINED, disabled=not clips_on)
        actions: list[ft.Control] = [
            query_field,
            pick_btn,
            secondary_button("Ảnh" if not has_still else "Đổi ảnh",
                             lambda _e, k=key: page.run_task(_add_still, k),
                             icon=ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED),
            secondary_button("Thẻ chữ", lambda _e, k=key: _use_card(k),
                             icon=ft.Icons.TEXT_FIELDS, disabled=not (has_clip or has_still)),
        ]
        sec = secs.get(key)
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text(f"{beat_label(b)}  [{key}]", size=14, weight=ft.FontWeight.BOLD,
                            color=ACCENT),
                    ft.Text(f"~{sec:.1f}s" if sec else "", size=12, color=TEXT_MUTED),
                    ft.Container(expand=True),
                    ft.Row(_status_chips(key), spacing=6, wrap=True),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text(b.text, size=14, color=TEXT_PRIMARY, selectable=True),
                ft.Row(actions, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=8),
            bgcolor=BG_PANEL, border=ft.border.all(1, SUCCESS if has_clip else BORDER),
            border_radius=8, padding=16,
        )

    def _refresh_summary() -> None:
        n_clip = sum(1 for b in beats if b.key in clips)
        n_still = sum(1 for b in beats if b.key in stills and b.key not in clips)
        n_card = len(beats) - n_clip - n_still
        summary_row.controls = [
            _chip(f"🎬 {n_clip} clip", SUCCESS), _chip(f"🖼 {n_still} ảnh", ACCENT),
            _chip(f"📄 {n_card} thẻ chữ", TEXT_MUTED),
        ]

    def _refresh_tts() -> None:
        st = sel.tts_status(root)
        done = len(st.get("scene_durations") or {})
        total = len(narration.get("scenes") or [])
        if not clips_on:
            tts_text.value = "Độ dài beat: ước lượng từ kịch bản (TTS nền chỉ chạy khi ENABLE_VIDEO_CLIPS=1)."
        elif st.get("completed"):
            tts_text.value = f"TTS nền: xong ({done}/{total} cảnh) — độ dài beat là thật."
        else:
            tts_text.value = f"TTS nền: đang chạy ({done}/{total} cảnh) — độ dài còn là ước lượng."

    def _refresh_cards(msg: str | None = None) -> None:
        cards_col.controls = [_card(b) for b in beats]
        _refresh_summary()
        _refresh_tts()
        _refresh_approval(push=False)        # a changed pick withdrew the approval
        if msg:
            status_text.value = msg
            status_text.color = TEXT_MUTED
        _push()

    def _kick_backup(key: str) -> None:
        """After a pick: look for a backup clip in the background (level 2 of the render chain)."""
        entry = clips.get(key)
        if not entry or isinstance(entry.get("backup"), dict) or key in backups_running:
            return
        backups_running.add(key)

        def _work():
            try:
                sel.suggest_backup(root, key, query=queries.get(key, ""),
                                   seconds=float(secs.get(key) or 3.0), log=lambda m: log.info(m))
            except Exception as exc:                                  # noqa: BLE001 — best effort
                log.warning("backup clip search failed for %s: %s", key, exc)
            finally:
                backups_running.discard(key)

                async def _done():
                    _reload()
                    _refresh_cards()
                try:
                    page.run_task(_done)
                except Exception as exc:                              # noqa: BLE001
                    log.debug("could not repaint after backup search: %s", exc)
        threading.Thread(target=_work, daemon=True, name=f"screen-backup-{key}").start()

    def _on_moment_picked(payload: dict[str, Any]) -> None:
        if payload.get("project") != project:
            return
        key = str(payload.get("beat"))

        async def _apply():
            sel.withdraw_approval(root)          # a pick made after Approve → approve again
            _reload()
            _refresh_cards(f"Đã chọn clip MP4 cho beat {key} — cần approve lại.")
            if clips_on and config_auto_backup():
                _kick_backup(key)
        try:
            page.run_task(_apply)
        except Exception as exc:                                      # noqa: BLE001
            log.warning("could not apply the picked moment to the screen: %s", exc)

    if clips_on:
        _replace_listener(page, _on_moment_picked)

    def _on_refresh(_e) -> None:
        _reload()
        _refresh_cards("Đã làm mới.")

    _refresh_cards()
    _refresh_approval(push=False)

    header = ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.THEATERS, color=ACCENT, size=22),
                    ft.Text(f"Screen Q&A · {project}", size=18, weight=ft.FontWeight.BOLD,
                            color=TEXT_PRIMARY)], spacing=10),
            ft.Text("Mỗi beat: Clip MP4 (YouTube) → clip dự phòng → ảnh → thẻ chữ. "
                    "Beat không chọn gì sẽ là thẻ chữ — không bao giờ lỗi.",
                    size=12, color=TEXT_MUTED),
            summary_row,
        ], spacing=8),
        padding=ft.padding.symmetric(horizontal=28, vertical=14),
    )
    center = ft.Column([
        header,
        ft.Container(content=notice_box, padding=ft.padding.symmetric(horizontal=28)),
        ft.Container(content=ft.ListView([cards_col], expand=True, spacing=10),
                     expand=True, padding=ft.padding.symmetric(horizontal=28, vertical=8)),
        ft.Container(
            content=ft.Row([
                secondary_button("Quay lại", lambda _e: on_go(2), icon=ft.Icons.ARROW_BACK),
                ft.Container(expand=True), status_text, approve_btn, continue_btn,
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=28, vertical=14),
            border=ft.border.only(top=ft.BorderSide(1, BORDER)),
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 5 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Review Beats (Screen)", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text("Không có panel truyện ở mode này. Chọn clip MP4 / ảnh cho từng beat, rồi Approve.",
                size=12, color=TEXT_MUTED),
        ft.Text("Chọn MP4 mở tab tìm khoảnh khắc (YouTube); beat được bump lên đầu hàng TTS để biết "
                "độ dài trước.", size=11, color=TEXT_MUTED),
        tts_text,
        secondary_button("Làm mới", _on_refresh, icon=ft.Icons.REFRESH),
        ft.Text("" if clips_on else "ENABLE_VIDEO_CLIPS=0: nút Chọn MP4 tắt (route /moments_review "
                "chỉ có khi bật cờ). Vẫn dùng được ảnh và thẻ chữ.", size=11, color=WARN),
    ], spacing=8, expand=True)
    return three_col(center, right, state=state, on_go=on_go,
                     header_title="Review Beats — Screen Q&A",
                     header_subtitle="Chọn hình cho từng beat trước khi TTS và render.")


def config_auto_backup() -> bool:
    """SCREEN_AUTO_BACKUP=0 turns the background backup-clip search off."""
    import os
    return os.getenv("SCREEN_AUTO_BACKUP", "1").strip().lower() not in ("0", "false", "no")
