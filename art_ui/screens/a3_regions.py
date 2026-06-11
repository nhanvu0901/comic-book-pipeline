# art_ui/screens/a3_regions.py
"""A3: VLM region proposal — image preview with proportional bbox overlays so
the user can sanity-check the zoom targets before narration."""
from typing import Callable

import flet as ft

from ui.theme import ACCENT, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN
from ui.bridge import format_exception, run_blocking

from .. import bridge
from ..layout import art_shell, log_list, primary_button, secondary_button
from ..state import ArtAppState, save_state

_DISPLAY_W = 360  # px width of each preview image; bboxes scale proportionally


def _page_preview(p: dict) -> ft.Control:
    dims = p.get("image_dimensions") or {}
    img_w = max(1, int(dims.get("width", 1)))
    img_h = max(1, int(dims.get("height", 1)))
    scale = _DISPLAY_W / img_w
    disp_h = int(img_h * scale)

    overlays: list[ft.Control] = [
        ft.Image(src=p.get("source_image", ""), width=_DISPLAY_W, height=disp_h,
                 fit=ft.BoxFit.FILL),
    ]
    for pn in p.get("panels") or []:
        b = pn.get("bbox") or {}
        overlays.append(ft.Container(
            left=b.get("x", 0) * scale, top=b.get("y", 0) * scale,
            width=b.get("w", 0) * scale, height=b.get("h", 0) * scale,
            border=ft.border.all(2, ACCENT), border_radius=2,
            content=ft.Container(
                content=ft.Text(str(pn.get("index", "")), size=10, color="#ffffff"),
                bgcolor=ACCENT, padding=ft.padding.symmetric(horizontal=4),
                width=18, height=16,
            ),
            alignment=ft.Alignment.TOP_LEFT,
        ))

    legend = ft.Column([
        ft.Text(f"{pn.get('index')}: {pn.get('description', '')}", size=11,
                color=TEXT_MUTED, selectable=True)
        for pn in (p.get("panels") or [])
    ], spacing=2)

    return ft.Column([
        ft.Text(p.get("issue_label", ""), size=13, weight=ft.FontWeight.W_600,
                color=TEXT_PRIMARY),
        ft.Stack(overlays, width=_DISPLAY_W, height=disp_h),
        legend,
    ], spacing=8)


def build(page: ft.Page, state: ArtAppState, *,
          on_go: Callable[[int], None], on_state_change: Callable[[], None]) -> ft.Control:
    lv, push_log = log_list(page)
    status = ft.Text("Click Detect to propose regions via VLM.", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    previews = ft.Column(spacing=24, scroll=ft.ScrollMode.AUTO, expand=True)

    def _mount_previews():
        previews.controls = [_page_preview(p) for p in bridge.load_art_pages(state.project_name)]

    if bridge.load_art_pages(state.project_name):
        _mount_previews()
        status.value = "Regions already detected — Re-run (force) to redo."

    async def _execute(force: bool):
        running.visible = True
        status.value = "Proposing regions (VLM chain)…"
        status.color = WARN
        page.update()
        try:
            pages = await run_blocking(bridge.run_regions, state.project_name, force, push_log)
        except Exception as e:
            running.visible = False
            status.value = "Failed — see log."
            status.color = DANGER
            push_log(format_exception(e))
            page.update()
            return
        running.visible = False
        n = sum(len(p.get("panels") or []) for p in pages)
        model = (pages or [{}])[0].get("vlm_model_used", "")
        status.value = f"{n} region(s) via {model or 'grid-fallback'}."
        status.color = SUCCESS if model and model != "grid-fallback" else WARN
        _mount_previews()
        state.mark_approved(3)
        save_state(state)
        page.update()
        on_state_change()

    center = ft.Column([
        ft.Container(content=previews,
                     padding=ft.padding.symmetric(horizontal=28, vertical=16), expand=True),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status], spacing=10),
                ft.Container(content=lv, height=140, border=ft.border.all(1, BORDER),
                             border_radius=6),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=12),
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 3 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("Detect Regions", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text("The narrator zooms into these. grid-fallback = VLM proposals were weak.",
                size=12, color=TEXT_MUTED),
        ft.Container(height=12),
        primary_button("Detect Regions", lambda _e: page.run_task(_execute, False),
                       icon=ft.Icons.CROP_FREE),
        ft.Container(height=8),
        secondary_button("Re-run (force)", lambda _e: page.run_task(_execute, True),
                         icon=ft.Icons.REFRESH),
        ft.Container(height=8),
        secondary_button("Next: Narration →", lambda _e: on_go(4),
                         icon=ft.Icons.ARROW_FORWARD),
    ], spacing=8, expand=True)

    return art_shell(center, right, state=state, on_go=on_go,
                     header_title="Detect Regions",
                     header_subtitle="VLM proposes story-significant regions; verify the boxes make sense.")
