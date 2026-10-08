"""ui/screens/s_screen_gate.py
Screen Q&A Visual Review & Beat Selection Screen.
Independent of comic panels: each beat can pick an MP4 clip (via /moments_review),
an HD Still image, or a graphic Text Card (fallback).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import flet as ft

import config
from ui.layout import primary_button, secondary_button
from ui.state import AppState
from ui.theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)
from ui.web_routes import add_moment_picked_listener


_screen_listeners: list[Callable[[dict[str, Any]], None]] = []


def add_screen_listener(cb: Callable[[dict[str, Any]], None]) -> None:
    if cb not in _screen_listeners:
        _screen_listeners.append(cb)
    # Also hook into the global moment picked listener
    add_moment_picked_listener(cb)


def _manifest_file(project: str) -> Path:
    return config.PROJECTS_ROOT / project / "review" / "clips" / "clips.json"


def _custom_images_file(project: str) -> Path:
    return config.PROJECTS_ROOT / project / "review" / "custom" / "custom_images.json"


def load_screen_beats(project: str) -> list[dict[str, Any]]:
    """Extract beats from narration.json for screen_qa."""
    nar_file = config.PROJECTS_ROOT / project / "narration.json"
    if not nar_file.exists():
        return []
    try:
        data = json.loads(nar_file.read_text("utf-8"))
    except Exception:
        return []

    scenes = data.get("scenes") or []
    beats: list[dict[str, Any]] = []

    # Also check screen_context for query hints
    ctx_file = config.PROJECTS_ROOT / project / "screen_context.json"
    ctx_queries: dict[str, str] = {}
    if ctx_file.exists():
        try:
            cdata = json.loads(ctx_file.read_text("utf-8"))
            for item in cdata.get("items", []):
                ent = item.get("entity", "")
                if ent and item.get("visual_query"):
                    ctx_queries[ent.lower()] = item["visual_query"]
        except Exception:
            pass

    for sc in scenes:
        sid = int(sc.get("scene_id", 1))
        sc_text = str(sc.get("text", "")).strip()
        vbs = sc.get("visual_beats") or []
        if not vbs:
            vbs = [{"beat_id": f"{sid}:1", "text": sc_text}]

        for b_idx, vb in enumerate(vbs, start=1):
            if isinstance(vb, dict):
                bid = str(vb.get("beat_id") or f"{sid}:{b_idx}")
                b_text = str(vb.get("text", "")).strip()
                query = str(vb.get("query") or vb.get("visual_query") or "").strip()
            else:
                bid = f"{sid}:{b_idx}"
                b_text = str(vb).strip()
                query = ""

            if not query:
                # Attempt to match entity query
                for ent, q in ctx_queries.items():
                    if ent in b_text.lower():
                        query = q
                        break
            if not query:
                words = b_text.split()
                query = " ".join(words[:6]) if len(words) > 6 else b_text

            beats.append({
                "beat_id": bid,
                "scene_id": sid,
                "text": b_text,
                "query": query,
                "target_seconds": float(sc.get("target_seconds", 4.0)) / len(vbs),
                "is_intro": bool(sc.get("is_intro", False)) and (b_idx == 1),
                "is_outro": bool(sc.get("is_outro", False)) and (b_idx == len(vbs)),
            })
    return beats


def load_beat_clips(project: str) -> dict[str, dict[str, Any]]:
    mf = _manifest_file(project)
    if not mf.exists():
        return {}
    try:
        data = json.loads(mf.read_text("utf-8"))
        res = {}
        for c in data.get("clips", []):
            if c.get("enabled", True) is not False and c.get("beat"):
                res[str(c["beat"])] = c
        return res
    except Exception:
        return {}


def set_beat_clip(project: str, beat: str, video_id: str, start: float, duration: float = 3.0) -> None:
    mf = _manifest_file(project)
    mf.parent.mkdir(parents=True, exist_ok=True)
    data = {"clips": []}
    if mf.exists():
        try:
            data = json.loads(mf.read_text("utf-8"))
        except Exception:
            data = {"clips": []}

    clips = data.setdefault("clips", [])
    updated = False
    norm_file = f"review/clips/{video_id}.mp4"
    for c in clips:
        if str(c.get("beat")) == str(beat):
            c["id"] = video_id
            c["file"] = norm_file
            c["start"] = start
            c["end"] = start + duration
            c["source_url"] = f"https://www.youtube.com/watch?v={video_id}"
            c["enabled"] = True
            updated = True
            break
    if not updated:
        clips.append({
            "id": video_id,
            "file": norm_file,
            "beat": beat,
            "start": start,
            "end": start + duration,
            "source_url": f"https://www.youtube.com/watch?v={video_id}",
            "enabled": True,
        })
    mf.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


def load_beat_stills(project: str) -> dict[str, str]:
    cf = _custom_images_file(project)
    if not cf.exists():
        return {}
    try:
        data = json.loads(cf.read_text("utf-8"))
        res = {}
        for k, v in data.items():
            if isinstance(v, str):
                res[str(k)] = v
            elif isinstance(v, dict):
                res[str(k)] = str(v.get("file", ""))
        return res
    except Exception:
        return {}


def set_beat_still(project: str, beat: str, image_path: str) -> None:
    cf = _custom_images_file(project)
    cf.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if cf.exists():
        try:
            data = json.loads(cf.read_text("utf-8"))
        except Exception:
            data = {}
    data[str(beat)] = image_path
    cf.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


def set_beat_card(project: str, beat: str) -> None:
    """Clear clip and still so beat falls back to Text Card."""
    # Remove from clips
    mf = _manifest_file(project)
    if mf.exists():
        try:
            data = json.loads(mf.read_text("utf-8"))
            data["clips"] = [c for c in data.get("clips", []) if str(c.get("beat")) != str(beat)]
            mf.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass

    # Remove from stills
    cf = _custom_images_file(project)
    if cf.exists():
        try:
            data = json.loads(cf.read_text("utf-8"))
            if str(beat) in data:
                del data[str(beat)]
                cf.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass


def clear_beat_selection(project: str, beat: str) -> None:
    set_beat_card(project, beat)


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    project = state.project_name
    beats = load_screen_beats(project) if project else []

    if not beats:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text("Chưa có kịch bản Screen Q&A cho dự án này.", size=18, color=WARN),
                    ft.Text("Vui lòng chạy Stage 3 để tạo kịch bản narration.json với mode='screen_qa'.", size=14, color=TEXT_MUTED),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=40,
        )

    clips = load_beat_clips(project)
    stills = load_beat_stills(project)

    # Listen to pubsub for external updates from /moments_review
    def _on_moment_picked(payload: dict[str, Any]):
        if payload.get("project") == project:
            bk = str(payload.get("beat"))
            clips[bk] = {
                "id": payload.get("video_id"),
                "start": float(payload.get("start", 0.0)),
                "source_url": payload.get("url") or f"https://www.youtube.com/watch?v={payload.get('video_id')}",
            }
            page.update()

    add_screen_listener(_on_moment_picked)

    # Summary counts
    clip_count = len(clips)
    still_count = len(stills)
    card_count = max(0, len(beats) - clip_count - still_count)

    header = ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.THEATERS, color=ACCENT, size=24),
                                ft.Text(f"Screen Q&A Visual Review: {project}", size=20, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                            ],
                            spacing=10,
                        ),
                        ft.Text(
                            "Chọn hình ảnh cho từng beat: Video Clip MP4 (YouTube), Ảnh tĩnh HD (Ken Burns), hoặc Thẻ chữ đồ họa.",
                            size=13, color=TEXT_MUTED,
                        ),
                    ],
                    expand=True,
                ),
                ft.Row(
                    [
                        ft.Container(
                            content=ft.Text(f"🎬 {clip_count} Clip", size=12, color=SUCCESS, weight=ft.FontWeight.W_600),
                            bgcolor=BG_ELEVATED, border_radius=6, padding=ft.padding.symmetric(6, 10),
                        ),
                        ft.Container(
                            content=ft.Text(f"🖼 {still_count} Still", size=12, color=ACCENT, weight=ft.FontWeight.W_600),
                            bgcolor=BG_ELEVATED, border_radius=6, padding=ft.padding.symmetric(6, 10),
                        ),
                        ft.Container(
                            content=ft.Text(f"📄 {card_count} Thẻ chữ", size=12, color=TEXT_MUTED, weight=ft.FontWeight.W_600),
                            bgcolor=BG_ELEVATED, border_radius=6, padding=ft.padding.symmetric(6, 10),
                        ),
                    ],
                    spacing=8,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        padding=ft.padding.symmetric(16, 20),
        bgcolor=BG_PANEL,
        border=ft.border.all(1, BORDER),
        border_radius=8,
        margin=ft.margin.only(bottom=16),
    )

    beat_controls = []
    for b in beats:
        bid = b["beat_id"]
        b_query = b["query"]
        b_text = b["text"]
        has_clip = bid in clips
        has_still = bid in stills

        if has_clip:
            status_chip = ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color=SUCCESS, size=16),
                        ft.Text(f"MP4: {clips[bid].get('id')} @ {float(clips[bid].get('start', 0.0)):.1f}s", size=12, color=SUCCESS, weight=ft.FontWeight.W_600),
                    ],
                    spacing=6,
                ),
                bgcolor=BG_ELEVATED, border_radius=6, padding=ft.padding.symmetric(4, 8),
            )
        elif has_still:
            status_chip = ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.IMAGE, color=ACCENT, size=16),
                        ft.Text(f"Still: {Path(stills[bid]).name}", size=12, color=ACCENT, weight=ft.FontWeight.W_600),
                    ],
                    spacing=6,
                ),
                bgcolor=BG_ELEVATED, border_radius=6, padding=ft.padding.symmetric(4, 8),
            )
        else:
            status_chip = ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.TEXT_SNIPPET, color=TEXT_MUTED, size=16),
                        ft.Text("Thẻ chữ (Auto fallback)", size=12, color=TEXT_MUTED),
                    ],
                    spacing=6,
                ),
                bgcolor=BG_ELEVATED, border_radius=6, padding=ft.padding.symmetric(4, 8),
            )

        def make_pick_clip_handler(beat_key=bid, query_str=b_query):
            def handler(_e):
                url = f"/moments_review?project={project}&beat={beat_key}&q={query_str}"
                page.launch_url(url)
            return handler

        def make_set_card_handler(beat_key=bid):
            def handler(_e):
                set_beat_card(project, beat_key)
                if beat_key in clips:
                    del clips[beat_key]
                if beat_key in stills:
                    del stills[beat_key]
                page.update()
            return handler

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(f"Beat {bid} (Cảnh {b['scene_id']})", size=14, weight=ft.FontWeight.BOLD, color=ACCENT),
                            ft.Text(f"~{b['target_seconds']:.1f}s", size=12, color=TEXT_MUTED),
                            ft.Container(expand=True),
                            status_chip,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Text(b_text, size=14, color=TEXT_PRIMARY, selectable=True),
                    ft.Text(f"🔍 Từ khóa tìm kiếm: {b_query}", size=12, color=TEXT_MUTED, italic=True),
                    ft.Row(
                        [
                            ft.ElevatedButton(
                                "Chọn MP4",
                                icon=ft.Icons.VIDEO_FILE,
                                on_click=make_pick_clip_handler(),
                                style=ft.ButtonStyle(bgcolor=SUCCESS if has_clip else BG_ELEVATED),
                            ),
                            ft.OutlinedButton(
                                "Dùng Thẻ Chữ",
                                icon=ft.Icons.TEXT_FIELDS,
                                on_click=make_set_card_handler(),
                            ),
                            ft.IconButton(
                                ft.Icons.DELETE_OUTLINE,
                                tooltip="Hủy lựa chọn (về thẻ chữ)",
                                icon_color=DANGER if (has_clip or has_still) else TEXT_MUTED,
                                on_click=make_set_card_handler(),
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
            ),
            bgcolor=BG_PANEL,
            border=ft.border.all(1, BORDER),
            border_radius=8,
            padding=16,
            margin=ft.margin.only(bottom=10),
        )
        beat_controls.append(card)

    footer = ft.Container(
        content=ft.Row(
            [
                secondary_button("Quay Lại", on_click=lambda _e: on_go(2)),
                ft.Container(expand=True),
                primary_button("Tiếp Tục & Render Video", on_click=lambda _e: on_go(8)),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        padding=ft.padding.symmetric(16, 20),
        bgcolor=BG_PANEL,
        border=ft.border.all(1, BORDER),
        border_radius=8,
        margin=ft.margin.only(top=16),
    )

    return ft.ListView(
        controls=[
            header,
            *beat_controls,
            footer,
        ],
        expand=True,
        padding=20,
    )
