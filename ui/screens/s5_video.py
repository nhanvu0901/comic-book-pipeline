"""
Screen 5: Final video assembly + review.

Runs Stage 5 (ffmpeg Ken Burns + captions + audio), shows final.mp4 in a
player, lets the user open the output folder or re-run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import flet as ft
from flet_video import Video, VideoMedia

from config import PROJECTS_ROOT
from ..bridge import asset_src, format_exception, run_blocking, run_stage_5
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)
from utils.clear_stage import clear_stage_5


def _has_desktop() -> bool:
    """False for a Windows process in session 0 — the app started over SSH or as a
    service. Nothing it opens can appear on screen, and the opener lingers for a minute."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        session = ctypes.c_ulong()
        found = ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    except Exception:
        return True     # can't tell: try to open, as before
    return not found or session.value != 0


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    final_path = PROJECTS_ROOT / state.project_name / "final.mp4" if state.project_name else None
    existing = final_path and final_path.exists()
    if existing and not state.is_approved(8):
        state.mark_approved(8)
        save_state(state)

    video_slot = ft.Container(expand=True, alignment=ft.Alignment.CENTER)
    status_text = ft.Text(
        "final.mp4 ready — click Download or press Play below" if existing else "Click Assemble to build the video.",
        color=TEXT_MUTED, size=12, selectable=True,
    )
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    lv, push_log = log_list(page)

    def _mount_video(path: Path):
        res_src = asset_src(path)
        v = Video(
            playlist=[VideoMedia(resource=res_src)],
            autoplay=False,
            show_controls=True,
            width=405,   # 9:16 at reasonable screen size
            height=720,
        )
        video_slot.content = v

    if existing:
        _mount_video(final_path)
    else:
        video_slot.content = ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.MOVIE_OUTLINED, size=64, color=TEXT_MUTED),
                ft.Text("Not assembled yet.", color=TEXT_MUTED, size=13),
            ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    async def _execute():
        running.visible = True
        status_text.value = "Rendering with ffmpeg…"
        status_text.color = WARN
        page.update()
        try:
            result_path = await run_blocking(run_stage_5, state.project_name, push_log)
        except (Exception, SystemExit) as e:
            # SystemExit: review gate block (ensure_reviewed) — see s4_tts.py note.
            running.visible = False
            status_text.value = "Blocked by review gate — see log." if isinstance(e, SystemExit) \
                else "Failed — see log."
            status_text.color = DANGER
            push_log(str(e) if isinstance(e, SystemExit) else format_exception(e))
            page.update()
            return

        running.visible = False
        p = Path(result_path)
        size_mb = p.stat().st_size / (1024 * 1024)
        status_text.value = f"Rendered {p.name} ({size_mb:.1f} MB)."
        status_text.color = SUCCESS
        _mount_video(p)

        state.mark_approved(8)
        save_state(state)
        page.update()
        on_state_change()

    def assemble_click(_e):
        page.run_task(_execute)

    def open_folder(_e):
        if not state.project_name:
            return
        folder = PROJECTS_ROOT / state.project_name
        if not _has_desktop():
            status_text.value = (f"This server has no desktop session to open folders in "
                                 f"(it was started over SSH or as a service). The project "
                                 f"folder is at {folder}")
            status_text.color = TEXT_MUTED
            page.update()
            return
        # Start the opener, never wait on it: os.startfile did not return when the app ran
        # without a desktop, freezing this handler; and explorer.exe exits 1 even on
        # success, so its exit code says nothing either.
        opener = {"win32": "explorer", "darwin": "open"}.get(sys.platform, "xdg-open")
        try:
            subprocess.Popen([opener, str(folder)])
        except Exception as e:
            status_text.value = f"Could not open the project folder ({e}). It is at {folder}"
            status_text.color = DANGER
        else:
            # The window opens on the machine running the app — over the LAN that is the
            # server, not this browser — so always say which folder, and where.
            status_text.value = f"Opening on the server: {folder}"
            status_text.color = TEXT_MUTED
        page.update()

    def start_over(_e):
        # A blank Stage 1 for the next project. This used to clear approvals and save —
        # wiping the finished project's stages — and then reopen that same project.
        state.start_new_project()
        on_go(1)

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def _do_clear_5(dialog):
        try:
            removed = clear_stage_5(state.project_name)
        except Exception as e:
            page.pop_dialog()
            _show_snack(str(e))
            return
        page.pop_dialog()
        if removed:
            _show_snack(
                f"Removed {len(removed)} item(s): "
                + ", ".join(p.name for p in removed)
            )
        else:
            _show_snack("Nothing to clear.")
        fp = (PROJECTS_ROOT / state.project_name / "final.mp4"
              if state.project_name else None)
        if fp and fp.exists():
            _mount_video(fp)
            status_text.value = "final.mp4 ready — click Download or press Play below"
        else:
            video_slot.content = ft.Container(
                content=ft.Column([
                    ft.Icon(ft.Icons.MOVIE_OUTLINED, size=64, color=TEXT_MUTED),
                    ft.Text("Not assembled yet.", color=TEXT_MUTED, size=13),
                ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                alignment=ft.Alignment.CENTER,
                expand=True,
            )
            status_text.value = "Click Assemble to build the video."
        status_text.color = TEXT_MUTED
        page.update()

    def open_clear_dialog(_e):
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Clear render scratch"),
            content=ft.Text("Delete _stage5/ scratch (keeps final.mp4)?"),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _e: page.pop_dialog()),
                secondary_button("Clear", lambda _e: _do_clear_5(dialog)),
            ],
        )
        page.show_dialog(dialog)

    center = ft.Column([
        ft.Container(content=video_slot,
                     padding=ft.padding.symmetric(horizontal=28, vertical=16),
                     expand=True),
        ft.Container(
            content=ft.Column([
                ft.Row([running, ft.Container(status_text, expand=True)], spacing=10),
                ft.Container(content=lv, height=120, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)

    right_controls = [
        ft.Text("STEP 8 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Final Video", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "1080×1920 9:16 H.264 MP4 with Ken Burns on panels and Chatterbox narration audio.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=16),
    ]

    if existing and final_path:
        size_mb = final_path.stat().st_size / (1024 * 1024)
        dl_url = asset_src(final_path)

        def _do_download(_e):
            try:
                page.run_task(page.launch_url, dl_url)
            except Exception:
                pass

        right_controls.extend([
            primary_button(
                f"Download final.mp4 ({size_mb:.1f} MB)",
                on_click=_do_download,
                url=dl_url,
                icon=ft.Icons.DOWNLOAD,
            ),
            ft.Container(height=4),
            ft.TextButton(
                "🔗 Open / Save final.mp4 in new tab",
                url=dl_url,
                style=ft.ButtonStyle(padding=ft.padding.all(0)),
            ),
            ft.Container(height=8),
            secondary_button("Re-assemble Video", assemble_click, icon=ft.Icons.REFRESH),
        ])

        # CapCut Music Sync outputs
        sync_txt_path = PROJECTS_ROOT / state.project_name / "music_sync.txt" if state.project_name else None
        preview_path = PROJECTS_ROOT / state.project_name / "preview_with_music.mp4" if state.project_name else None
        sync_json_path = PROJECTS_ROOT / state.project_name / "music_sync.json" if state.project_name else None
        if sync_txt_path and sync_txt_path.exists():
            def _open_sync_sheet(_e):
                dialog = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("CapCut Music Sync Sheet"),
                    content=ft.Container(
                        content=ft.Text(sync_txt_path.read_text(encoding="utf-8", errors="replace"), size=11, selectable=True),
                        width=600, height=400,
                    ),
                    actions=[ft.TextButton("Đóng", on_click=lambda _e: page.pop_dialog())],
                )
                page.show_dialog(dialog)

            right_controls.extend([
                ft.Container(height=8),
                secondary_button("Xem Sync Sheet CapCut", _open_sync_sheet, icon=ft.Icons.DESCRIPTION_OUTLINED),
            ])
            if preview_path and preview_path.exists():
                p_url = asset_src(preview_path)
                p_mb = preview_path.stat().st_size / (1024 * 1024)
                right_controls.extend([
                    ft.Container(height=4),
                    secondary_button(f"Preview có nhạc ({p_mb:.1f} MB)", url=p_url, icon=ft.Icons.PLAY_CIRCLE_OUTLINE),
                ])
        elif sync_json_path and sync_json_path.exists():
            def _open_sync_info(_e):
                try:
                    data = json.loads(sync_json_path.read_text(encoding="utf-8"))
                    snapped = data.get("snapped_count", 0)
                    total = data.get("total_hit_points", len(data.get("hit_points", [])))
                    track = data.get("track_name", "N/A")
                    in_pt = data.get("in_point_timecode", f"{data.get('in_point_seconds', 0.0)}s")
                    v_lufs = data.get("voice_loudness_lufs")
                    m_lufs = data.get("music_loudness_lufs")
                    gain = data.get("applied_gain_db")
                    bake_failed = data.get("bake_failed", False)
                    bake_error = data.get("bake_error")
                    bake_applied = data.get("bake_applied", data.get("bake_into_final", False))

                    if bake_failed:
                        bake_status = f"THẤT BẠI ({bake_error or 'Lỗi trộn nhạc'} - video giữ nguyên giọng đọc)"
                    elif bake_applied:
                        bake_status = "Đã bật (final.mp4 đã trộn nhạc)"
                    else:
                        bake_status = "Tắt (final.mp4 chỉ chứa giọng đọc)"

                    info_lines = [
                        f"🎵 Track: {track} (BPM: {data.get('bpm', 0.0):.1f})",
                        f"⏱ In-point: {in_pt}",
                        f"✨ Hit points đã khớp beat: {snapped}/{total}",
                        f"🔊 Trộn sẵn vào final.mp4: {bake_status}",
                    ]
                    if v_lufs is not None and m_lufs is not None:
                        gain_s = f"{gain:+.1f} dB" if gain is not None else "N/A"
                        info_lines.append(f"🎚 Âm lượng: Thoại {v_lufs:.1f} LUFS | Nhạc {m_lufs:.1f} LUFS (Gain: {gain_s})")
                    info_lines.append("\nChi tiết các điểm cắt:")
                    for hp in data.get("hit_points", []):
                        st = "khớp beat" if hp.get("snapped") else "giữ nguyên"
                        info_lines.append(f"  • [{hp.get('timecode')}] Shot {hp.get('shot_id')}: {hp.get('name')} ({st})")

                    content_text = "\n".join(info_lines)
                except Exception as ex:
                    content_text = f"Không thể đọc music_sync.json: {ex}"

                dialog = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Thông tin đồng bộ nhạc CapCut"),
                    content=ft.Container(
                        content=ft.Text(content_text, size=11, selectable=True),
                        width=600, height=400,
                    ),
                    actions=[ft.TextButton("Đóng", on_click=lambda _e: page.pop_dialog())],
                )
                page.show_dialog(dialog)

            right_controls.extend([
                ft.Container(height=8),
                secondary_button("Xem thông tin nhạc CapCut", _open_sync_info, icon=ft.Icons.MUSIC_NOTE),
            ])
    else:
        right_controls.extend([
            primary_button("Assemble Video", assemble_click, icon=ft.Icons.MOVIE_FILTER),
        ])

    right_controls.extend([
        ft.Container(height=8),
        secondary_button("Clear…", open_clear_dialog, icon=ft.Icons.DELETE_OUTLINE),
        ft.Container(height=8),
        secondary_button("Open Project Folder", open_folder, icon=ft.Icons.FOLDER_OPEN),
        ft.Container(height=20),
        ft.Text("WHEN YOU'RE DONE", size=10, color=TEXT_MUTED),
        secondary_button("Start a new project", start_over, icon=ft.Icons.ADD),
    ])

    right = ft.Column(right_controls, spacing=8, expand=True)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Review & Export",
        header_subtitle="Play the rendered Short. If something's off, go back to any earlier stage.",
    )
