"""
Screen 2: Download Comic — scrape pages from batcave.biz.

Shows a thumbnail grid of downloaded pages and a progress log.
Clear button deletes raw_comic/ so the user can re-download.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import flet as ft

from .. import bridge
from ..bridge import (asset_src, 
    format_exception, load_raw_pages, run_blocking,
    run_stage_download, run_stage_download_from_url, run_stage_download_saga,
)
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS,
    TEXT_MUTED, TEXT_PRIMARY, WARN,
)
from utils.clear_stage import clear_stage_2


def get_scout_missing_readers(project_name: str) -> list[dict]:
    """Bridge wrapper kept local so this screen owns only its presentation."""

    return bridge.get_scout_missing_readers(project_name)


def repair_scout_readers(
    project_name: str, reader_urls: dict[int, str] | None = None, log=print,
) -> list[dict]:
    """Persist supplied reader URLs and retry deterministic resolution."""

    return bridge.repair_scout_readers(project_name, reader_urls, log)


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    grid_ctl = ft.Container(expand=True)

    lv, push_log = log_list(page)
    status_text = ft.Text("", color=TEXT_MUTED, size=12)
    running = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
    summary_text = ft.Text("", size=12, color=TEXT_MUTED)
    missing_readers: list[list[dict]] = [[]]
    # An inspection failure is not a missing item. Stuffing it into the rows as
    # a rank-0 entry drew a text field for an item that does not exist and
    # invited a URL to be filed against it.
    reader_error: list[str] = [""]
    reader_fields: dict[int, ft.TextField] = {}
    repair_busy = [False]
    missing_panel = ft.Column(spacing=8)

    def render_grid(manifest: list[dict]):
        if not manifest:
            grid_ctl.content = ft.Container(
                content=ft.Text("No pages downloaded yet — click Download to start.",
                                color=TEXT_MUTED, size=13),
                alignment=ft.Alignment.CENTER, expand=True,
            )
            return

        tiles: list[ft.Control] = []
        total_pages = 0
        for chapter in manifest:
            pages = chapter.get("pages", [])
            total_pages += len(pages)
            for img_path_str in pages:
                img_path = Path(img_path_str)
                tiles.append(_thumbnail(img_path, chapter["label"]))

        summary_text.value = (
            f"{total_pages} pages across {len(manifest)} chapter(s)"
        )

        grid_ctl.content = ft.Column(
            [
                ft.Row(
                    [summary_text, ft.Container(expand=True)],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(height=8),
                ft.GridView(
                    tiles,
                    expand=True,
                    runs_count=5,
                    max_extent=150,
                    child_aspect_ratio=0.6,
                    spacing=8,
                    run_spacing=8,
                ),
            ],
            spacing=4, expand=True,
        )

    # Load existing manifest if any
    if state.project_name:
        existing = load_raw_pages(state.project_name)
        if existing:
            render_grid(existing)

    download_button = primary_button(
        "Download (from Stage 1)", lambda _e: None, icon=ft.Icons.DOWNLOAD,
    )
    download_button.key = "stage1-download"
    repair_button = primary_button(
        "Save URLs & retry resolution", lambda _e: None, icon=ft.Icons.REFRESH,
    )
    repair_button.key = "repair-reader-urls"

    def _refresh_missing_panel(*, update: bool = False) -> None:
        rows = missing_readers[0]
        controls: list[ft.Control] = []
        # Drop cached fields for ranks that are no longer missing, so a rank
        # repaired in an earlier round cannot be resubmitted from a stale box.
        for rank in [r for r in reader_fields if r not in {int(row.get("rank", 0)) for row in rows}]:
            reader_fields.pop(rank, None)
        if reader_error[0]:
            controls.extend([
                ft.Text("Could not inspect selected issues", size=12, color=DANGER,
                        weight=ft.FontWeight.BOLD),
                ft.Text(reader_error[0], size=11, color=TEXT_MUTED, selectable=True),
            ])
        if rows:
            controls.extend([
                ft.Text("Reader URL needed", size=12, color=WARN,
                        weight=ft.FontWeight.BOLD),
                ft.Text(
                    "These selected Q&A issues cannot be downloaded yet. Paste a "
                    "batcave reader URL where you have one, then retry resolution.",
                    size=11, color=TEXT_MUTED,
                ),
            ])
            for row in rows:
                rank = int(row.get("rank", 0))
                entity = str(row.get("entity") or "Unknown item")
                source_comic = str(row.get("source_comic") or "Unknown comic")
                field = reader_fields.get(rank)
                if field is None:
                    field = ft.TextField(
                        key=f"missing-reader-{rank}",
                        label=f"#{rank} — {entity} — {source_comic}",
                        value=str(row.get("reader_url") or ""),
                        hint_text="https://batcave.biz/reader/123/456",
                        border_color=BORDER,
                        focused_border_color=ACCENT,
                        text_size=11,
                    )
                    reader_fields[rank] = field
                controls.append(field)
            controls.append(repair_button)
        missing_panel.controls = controls
        blocked = bool(rows) or bool(reader_error[0]) or repair_busy[0]
        download_button.disabled = blocked
        repair_button.disabled = not rows or repair_busy[0]
        if update:
            page.update()

    if state.project_name:
        try:
            missing_readers[0] = list(get_scout_missing_readers(state.project_name))
        except Exception as exc:
            reader_error[0] = str(exc)
            missing_readers[0] = []
    _refresh_missing_panel()

    async def _execute():
        if not state.project_name:
            status_text.value = "No project loaded — go back to Stage 1."
            status_text.color = DANGER
            page.update()
            return
        # Re-read the persisted Q&A context at click time. The visible list can
        # be stale after another repair action, but a download must never run on
        # unresolved items or silently renumber/drop them.
        remaining = list(get_scout_missing_readers(state.project_name))
        if remaining:
            missing_readers[0] = remaining
            status_text.value = "Repair the missing reader URLs before downloading."
            status_text.color = WARN
            _refresh_missing_panel(update=True)
            return
        running.visible = True
        status_text.value = "Downloading comic pages…"
        status_text.color = WARN
        page.update()

        try:
            manifest = await run_blocking(run_stage_download, state.project_name, push_log)
        except Exception as e:
            running.visible = False
            status_text.value = "Failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return

        render_grid(manifest)
        state.mark_approved(2)
        state.current_stage = max(state.current_stage, 3)
        save_state(state)

        running.visible = False
        total = sum(len(ch.get("pages", [])) for ch in manifest)
        status_text.value = f"Download complete — {total} pages."
        status_text.color = SUCCESS
        page.update()
        on_state_change()

    def run_click(_e):
        page.run_task(_execute)

    async def _repair_missing_readers():
        if repair_busy[0] or not state.project_name or not missing_readers[0]:
            return
        repair_busy[0] = True
        status_text.value = "Saving reader URLs and resolving remaining issues…"
        status_text.color = WARN
        _refresh_missing_panel(update=True)
        # Walk the rows still missing, not the field cache: a rank repaired in
        # an earlier round keeps its box until the next refresh, and resending
        # it would overwrite an item that is already resolved.
        supplied: dict[int, str] = {}
        for row in missing_readers[0]:
            rank = int(row.get("rank", 0))
            field = reader_fields.get(rank)
            value = str(field.value or "").strip() if field is not None else ""
            if value:
                supplied[rank] = value
        try:
            remaining = await run_blocking(
                repair_scout_readers, state.project_name, supplied or None, push_log,
            )
        except ValueError as exc:
            status_text.value = str(exc)
            status_text.color = DANGER
        except Exception as exc:
            status_text.value = "Repair failed — see log."
            status_text.color = DANGER
            push_log(format_exception(exc))
        else:
            missing_readers[0] = list(remaining)
            if missing_readers[0]:
                status_text.value = f"{len(missing_readers[0])} reader URL(s) still need repair."
                status_text.color = WARN
            else:
                status_text.value = "Reader URLs ready — download can continue."
                status_text.color = SUCCESS
        finally:
            repair_busy[0] = False
            _refresh_missing_panel(update=True)

    def repair_click(_e):
        if not repair_busy[0]:
            page.run_task(_repair_missing_readers)

    download_button.on_click = run_click
    repair_button.on_click = repair_click

    # ─── URL-direct mode (skip Stage 1) ─────────────────────────────────────
    url_field = ft.TextField(
        label="Comic URL(s)",
        hint_text="One series URL, OR multiple reader URLs (space/newline/comma separated)",
        multiline=True, min_lines=2, max_lines=4,
        border_color=BORDER, focused_border_color=ACCENT, text_size=12,
    )
    issues_field = ft.TextField(
        label="Issues (only when series URL)",
        hint_text="e.g. #1-3, #1,#3,#5  (leave blank for ALL)",
        border_color=BORDER, focused_border_color=ACCENT, text_size=12,
    )
    enrich_switch = ft.Switch(
        label="Enrich context from wiki (slower, better narration)",
        value=True, active_color=ACCENT,
    )
    saga_switch = ft.Switch(
        label="Crossover saga — weave issues into ONE story (per-issue context)",
        value=False, active_color=ACCENT,
    )
    max_issues_field = ft.TextField(
        label="Max issues (saga + series URL)",
        value="5", width=200,
        border_color=BORDER, focused_border_color=ACCENT, text_size=12,
    )
    url_project_field = ft.TextField(
        label="Project name (created if new)",
        hint_text="e.g. dark_venom_2023",
        value=state.project_name or "",
        border_color=BORDER, focused_border_color=ACCENT, text_size=12,
    )

    async def _execute_url():
        raw = (url_field.value or "").strip()
        proj = (url_project_field.value or "").strip()
        if not raw:
            status_text.value = "Paste at least one URL first."
            status_text.color = DANGER
            page.update()
            return
        if not proj:
            status_text.value = "Project name is required for URL-direct mode."
            status_text.color = DANGER
            page.update()
            return
        state.project_name = proj
        save_state(state)

        running.visible = True
        status_text.value = ("Crossover-saga download — per-issue context…"
                             if saga_switch.value else
                             "URL-direct download — bootstrapping context…")
        status_text.color = WARN
        page.update()

        try:
            if saga_switch.value:
                try:
                    max_iss = max(1, int((max_issues_field.value or "5").strip()))
                except ValueError:
                    max_iss = 5
                manifest = await run_blocking(
                    run_stage_download_saga, proj, raw, max_iss, push_log,
                )
            else:
                manifest = await run_blocking(
                    run_stage_download_from_url,
                    proj, raw, (issues_field.value or "").strip(),
                    bool(enrich_switch.value), push_log,
                )
        except Exception as e:
            running.visible = False
            status_text.value = "Failed — see log."
            status_text.color = DANGER
            push_log(format_exception(e))
            page.update()
            return

        render_grid(manifest)
        state.mark_approved(2)
        state.current_stage = max(state.current_stage, 3)
        save_state(state)

        running.visible = False
        total = sum(len(ch.get("pages", [])) for ch in manifest)
        status_text.value = f"URL-direct download complete — {total} pages."
        status_text.color = SUCCESS
        page.update()
        on_state_change()

    def run_url_click(_e):
        page.run_task(_execute_url)

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def _do_clear(_e):
        try:
            removed = clear_stage_2(
                state.project_name, raw=True, preprocessed=False,
            )
        except Exception as e:
            _show_snack(str(e))
            return
        if removed:
            _show_snack(f"Cleared raw_comic/ ({len(removed)} item(s))")
        else:
            _show_snack("Nothing to clear.")
        render_grid([])
        page.update()

    def approve_and_go(_e):
        state.mark_approved(2)
        state.current_stage = 3
        save_state(state)
        on_go(3)

    # Center column
    center = ft.Column([
        ft.Container(
            content=grid_ctl,
            padding=ft.padding.symmetric(horizontal=28, vertical=16),
            expand=True,
        ),
        ft.Container(
            content=ft.Column([
                ft.Row([running, status_text], spacing=10),
                ft.Container(
                    content=lv, height=140,
                    border=ft.border.all(1, BORDER), border_radius=6,
                ),
            ], spacing=8),
            padding=ft.padding.symmetric(horizontal=28, vertical=16),
        ),
    ], spacing=0, expand=True)

    # Right column
    right = ft.Column([
        ft.Text("STEP 2 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Download Comic", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Downloads comic pages from batcave.biz using the URL found in Stage 1. "
            "Pages are saved to raw_comic/ and cached — re-runs skip existing files.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=16),
        missing_panel,
        download_button,
        ft.Container(height=8),
        secondary_button("Clear downloads", _do_clear, icon=ft.Icons.DELETE_OUTLINE),

        ft.Container(height=18),
        ft.Divider(height=1, color=BORDER),
        ft.Container(height=8),
        ft.Text("OR — URL-DIRECT (skip Stage 1)", size=10,
                color=TEXT_MUTED, weight=ft.FontWeight.BOLD),
        ft.Text(
            "Paste a series URL with --issues, or multiple reader URLs (one per issue). "
            "Context is fetched silently from wiki/fandom. Turn on Crossover saga to "
            "fetch a SEPARATE context per issue and weave them into one story.",
            size=11, color=TEXT_MUTED,
        ),
        ft.Container(height=6),
        url_project_field,
        url_field,
        issues_field,
        enrich_switch,
        saga_switch,
        max_issues_field,
        ft.Container(height=4),
        primary_button("Download from URL(s)", run_url_click, icon=ft.Icons.LINK),

        ft.Container(height=14),
        primary_button("Continue to Stage 3 →", approve_and_go,
                       disabled=not state.is_approved(2) or bool(missing_readers[0])
                                or bool(reader_error[0])),
    ], spacing=8, expand=True, scroll=ft.ScrollMode.AUTO)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Download Comic",
        header_subtitle="Scrape comic pages from batcave.biz before preprocessing.",
    )


def _thumbnail(img_path: Path, label: str) -> ft.Control:
    name = img_path.name if img_path.exists() else "?"
    img_ctl = (
        ft.Image(src=asset_src(img_path), width=130, height=180, fit=ft.BoxFit.COVER,
                 border_radius=4)
        if img_path.exists()
        else ft.Container(width=130, height=180, bgcolor=BG_PANEL, border_radius=4)
    )
    return ft.Container(
        content=ft.Column([
            img_ctl,
            ft.Row([
                ft.Text(name[:18], size=9, color=TEXT_MUTED,
                        weight=ft.FontWeight.BOLD, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Container(expand=True),
                ft.Text(label, size=8, color=ACCENT),
            ], spacing=4),
        ], spacing=4),
        padding=4,
        border=ft.border.all(1, BORDER), border_radius=6, bgcolor=BG_ELEVATED,
    )
