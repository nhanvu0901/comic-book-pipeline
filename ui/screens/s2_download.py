"""
Screen 2: Download Comic — scrape pages from batcave.biz.

Shows a thumbnail grid of downloaded pages and a progress log.
Clear button deletes raw_comic/ so the user can re-download.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Callable

import flet as ft

from .. import bridge
from ..bridge import (asset_src, 
    format_exception, load_raw_pages, run_blocking,
    return_scout_project_to_research, run_stage_download, run_stage_download_from_url,
    run_stage_download_saga,
)
from ..clipboard import BLOCKED_HINT, copy_text
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS,
    TEXT_MUTED, TEXT_PRIMARY, WARN,
)
from utils.clear_stage import clear_stage_2


def _load_comic_info(project_name: str) -> dict[str, str]:
    if not project_name:
        return {}
    p_dir = Path(__file__).resolve().parents[2] / "projects" / project_name
    c_path = p_dir / "comic_context.json"
    if c_path.exists():
        try:
            data = json.loads(c_path.read_text(encoding="utf-8"))
            title = str(data.get("title") or "")
            series = str(data.get("series") or "")
            issues = data.get("issue") or data.get("issues")
            issue = str(issues[0] if isinstance(issues, list) and issues else issues or "")
            year = str(data.get("year") or "")
            return {"title": title, "series": series, "issue": issue, "year": year}
        except Exception:
            pass
    a_path = p_dir / "answer_context.json"
    if a_path.exists():
        try:
            data = json.loads(a_path.read_text(encoding="utf-8"))
            question = str(data.get("question") or "")
            return {"title": question, "series": "", "issue": "", "year": ""}
        except Exception:
            pass
    return {"title": project_name, "series": "", "issue": "", "year": ""}


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
    download_busy = [False]
    direct_download_busy = [False]
    return_busy = [False]
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

    # Load existing manifest if any — and show the empty-state hint when there is none,
    # instead of a blank centre column.
    render_grid(load_raw_pages(state.project_name) if state.project_name else [])

    download_button = primary_button(
        "Download (from Stage 1)", lambda _e: None, icon=ft.Icons.DOWNLOAD,
    )
    download_button.key = "stage1-download"
    repair_button = primary_button(
        "Save URLs & retry resolution", lambda _e: None, icon=ft.Icons.REFRESH,
    )
    repair_button.key = "repair-reader-urls"

    clipboard = ft.Clipboard()
    clipboard_attached = {"done": False}

    def _ensure_clipboard():
        if clipboard_attached["done"]:
            return
        try:
            services = getattr(page, "services", None)
            if services is not None and clipboard not in services:
                services.append(clipboard)
            clipboard_attached["done"] = True
        except Exception:
            pass

    async def _async_copy(text: str, label_name: str = ""):
        # Report the copy's REAL outcome — it runs after the click handler returns, so
        # announcing success in the handler lied whenever the browser refused.
        what = f"{label_name}: {text}" if label_name else text
        _show_snack(f"Copied {what}" if await copy_text(clipboard, text)
                    else f"Could not copy {label_name or 'text'} — {BLOCKED_HINT}.")

    def _show_snack(msg: str):
        try:
            overlay = getattr(page, "overlay", None)
            if overlay is not None:
                sb = ft.SnackBar(content=ft.Text(msg))
                overlay.append(sb)
                sb.open = True
                page.update()
        except Exception:
            pass

    def _copy_to_clipboard(text: str, label_name: str = ""):
        _ensure_clipboard()
        try:
            page.run_task(_async_copy, text, label_name)
        except Exception:
            _show_snack(f"Could not copy {label_name or 'text'} — {BLOCKED_HINT}.")

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
                    # The row above already names the item; a label carrying the whole
                    # title overflowed the field and printed over itself.
                    field = ft.TextField(
                        key=f"missing-reader-{rank}",
                        label=f"Reader URL for #{rank}",
                        value=str(row.get("reader_url") or ""),
                        hint_text="https://batcave.biz/reader/123/456",
                        border_color=BORDER,
                        focused_border_color=ACCENT,
                        text_size=11,
                    )
                    reader_fields[rank] = field
                field.disabled = repair_busy[0] or download_busy[0]

                title_to_copy = source_comic if source_comic and source_comic != "Unknown comic" else entity
                copy_row = ft.Row([
                    ft.Text(f"#{rank} — {entity} — {source_comic}", size=11,
                            weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, selectable=True,
                            expand=True, key=f"missing-reader-title-{rank}"),
                    ft.IconButton(
                        icon=ft.Icons.CONTENT_COPY,
                        icon_size=14,
                        tooltip=f"Copy '{title_to_copy}'",
                        style=ft.ButtonStyle(padding=ft.padding.all(4)),
                        on_click=(lambda t=title_to_copy: lambda _e: _copy_to_clipboard(t, "comic title"))(),
                    ),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                controls.append(ft.Column([copy_row, field], spacing=2))
            controls.append(repair_button)
        missing_panel.controls = controls
        needs_stage_one_reapproval = (
            state.returned_scout_project == state.project_name
            and not state.is_approved(1)
        )
        blocked = (bool(rows) or bool(reader_error[0]) or repair_busy[0]
                   or download_busy[0] or needs_stage_one_reapproval)
        download_button.disabled = blocked
        repair_button.disabled = (
            not rows or repair_busy[0] or download_busy[0] or needs_stage_one_reapproval
        )
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
        if download_busy[0] or repair_busy[0] or return_busy[0]:
            return
        if not state.project_name:
            status_text.value = "No project loaded — go back to Stage 1."
            status_text.color = DANGER
            page.update()
            return
        if state.returned_scout_project == state.project_name and not state.is_approved(1):
            status_text.value = "Approve the restored Stage 1 selection before downloading again."
            status_text.color = WARN
            page.update()
            return
        download_busy[0] = True
        _refresh_missing_panel(update=True)
        # Re-read the persisted Q&A context at click time. The visible list can
        # be stale after another repair action, but a download must never run on
        # unresolved items or silently renumber/drop them.
        try:
            try:
                remaining = list(get_scout_missing_readers(state.project_name))
            except ValueError as exc:
                reader_error[0] = str(exc)
                missing_readers[0] = []
                status_text.value = "Could not inspect selected issues."
                status_text.color = DANGER
                return
            except Exception as exc:
                reader_error[0] = "Could not inspect selected issues — see log."
                missing_readers[0] = []
                status_text.value = reader_error[0]
                status_text.color = DANGER
                push_log(format_exception(exc))
                return
            reader_error[0] = ""
            if remaining:
                missing_readers[0] = remaining
                status_text.value = "Repair the missing reader URLs before downloading."
                status_text.color = WARN
                return
            running.visible = True
            status_text.value = "Downloading comic pages…"
            status_text.color = WARN
            page.update()

            try:
                manifest = await run_blocking(run_stage_download, state.project_name, push_log)
            except Exception as exc:
                running.visible = False
                status_text.value = "Failed — see log."
                status_text.color = DANGER
                push_log(format_exception(exc))
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
        finally:
            download_busy[0] = False
            _refresh_missing_panel(update=True)

    def run_click(_e):
        page.run_task(_execute)

    async def _repair_missing_readers():
        if repair_busy[0] or return_busy[0] or not state.project_name or not missing_readers[0]:
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
        if not repair_busy[0] and not return_busy[0]:
            page.run_task(_repair_missing_readers)

    download_button.on_click = run_click
    repair_button.on_click = repair_click

    async def _return_to_stage_one() -> None:
        """Restore the disk-backed scout session without issuing new research calls."""
        if return_busy[0] or download_busy[0] or repair_busy[0] or direct_download_busy[0]:
            return
        if not state.project_name:
            status_text.value = "No project loaded — cannot restore its Stage 1 research."
            status_text.color = DANGER
            page.update()
            return
        # The first return already detached this project's session.  Stage 1
        # may now be showing either its candidate checklist or production
        # form, while the user briefly visits Stage 2 through the sidebar.
        # Resume that exact in-memory/persisted association instead of asking
        # the backend to detach it a second time (which is valid only from the
        # original completed session state).
        if (
            state.returned_scout_project == state.project_name
            and state.returned_scout_session_id == state.scout_session_id
            and state.scout_session_id
            and not state.is_approved(1)
        ):
            try:
                save_state(state)
                on_go(1)
            except Exception as exc:
                status_text.value = str(exc) or "Could not save the restored Stage 1 session."
                status_text.color = DANGER
                page.update()
            return
        return_busy[0] = True
        running.visible = True
        status_text.value = "Restoring saved Stage 1 research…"
        status_text.color = WARN
        page.update()
        try:
            session = await run_blocking(
                return_scout_project_to_research, state.project_name,
            )
            state.return_to_research(session.id, session.mode.value, session.user_intent)
            save_state(state)
            on_go(1)
        except Exception as exc:
            running.visible = False
            status_text.value = str(exc) or "Could not restore the original research session."
            status_text.color = DANGER
            page.update()
        finally:
            return_busy[0] = False

    def return_to_stage_one_click(_e):
        if return_busy[0] or download_busy[0] or repair_busy[0] or direct_download_busy[0]:
            return
        page.run_task(_return_to_stage_one)

    def guarded_go(stage: int) -> None:
        # three_col owns the sidebar.  Supplying this wrapper makes its Stage 1
        # row perform precisely the same restore action as the explicit button.
        if return_busy[0]:
            return
        if stage == 1:
            return_to_stage_one_click(None)
            return
        on_go(stage)

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
    # Short labels: a Switch label does not wrap, and the long ones were cut off at the
    # rail's edge. The paragraph above the form explains what each one does.
    enrich_switch = ft.Switch(
        label="Enrich context from wiki",
        value=False, active_color=ACCENT,
    )
    saga_switch = ft.Switch(
        label="Crossover saga",
        value=False, active_color=ACCENT,
    )
    max_issues_field = ft.TextField(
        label="Max saga issues",
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
        if return_busy[0] or direct_download_busy[0]:
            return
        raw = (url_field.value or "").strip()
        proj = (url_project_field.value or "").strip()
        if not raw:
            status_text.value = "Paste at least one URL first."
            status_text.color = DANGER
            page.update()
            return
        if not proj:
            tokens = [t.strip() for t in raw.replace(",", "\n").split() if t.strip()]
            if tokens:
                first = tokens[0]
                m_reader = re.search(r"/reader/(\d+)/(\d+)", first)
                m_series = re.search(r"/(\d+)-([a-z0-9-]+?)\.html", first)
                if m_series:
                    proj = re.sub(r"-+", "_", m_series.group(2).strip("-"))
                elif m_reader:
                    proj = f"comic_{m_reader.group(1)}_{m_reader.group(2)}"
                else:
                    proj = f"comic_url_{int(time.time())}"
                url_project_field.value = proj
            else:
                status_text.value = "Paste at least one URL first."
                status_text.color = DANGER
                page.update()
                return
        state.project_name = proj
        save_state(state)
        direct_download_busy[0] = True

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
        else:
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
        finally:
            direct_download_busy[0] = False

    def run_url_click(_e):
        page.run_task(_execute_url)

    dl_url_button = primary_button("Download from URL(s)", run_url_click, icon=ft.Icons.LINK)
    dl_url_button.key = "download-from-url"

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def _do_clear(_e):
        if return_busy[0]:
            return
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
        if return_busy[0]:
            return
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
    comic_info = _load_comic_info(state.project_name)
    display_title = comic_info.get("title") or state.project_name
    series_issue = f"{comic_info.get('series', '')} {comic_info.get('issue', '')}".strip()
    comic_card_controls: list[ft.Control] = []
    if display_title:
        sub_items: list[ft.Control] = []
        if series_issue and series_issue != display_title:
            sub_items.append(
                ft.Row([
                    ft.Text(series_issue, size=11, color=ACCENT, selectable=True, expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CONTENT_COPY,
                        icon_size=13,
                        tooltip=f"Copy '{series_issue}'",
                        style=ft.ButtonStyle(padding=ft.padding.all(2)),
                        on_click=(lambda s=series_issue: lambda _e: _copy_to_clipboard(s, "series"))(),
                    ),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
            )
        comic_card_controls.append(
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text("CURRENT PROJECT / COMIC", size=9, weight=ft.FontWeight.BOLD, color=ACCENT),
                        ft.Container(expand=True),
                        ft.IconButton(
                            icon=ft.Icons.CONTENT_COPY,
                            icon_size=13,
                            tooltip="Copy title",
                            style=ft.ButtonStyle(padding=ft.padding.all(2)),
                            on_click=(lambda t=display_title: lambda _e: _copy_to_clipboard(t, "title"))(),
                        ),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Text(display_title, size=12, weight=ft.FontWeight.BOLD,
                            color=TEXT_PRIMARY, selectable=True),
                    *sub_items,
                ], spacing=2),
                bgcolor=BG_ELEVATED,
                border=ft.border.all(1, BORDER),
                border_radius=6,
                padding=10,
            )
        )

    return_button = secondary_button(
        "Return to Stage 1 research", return_to_stage_one_click,
        icon=ft.Icons.ARROW_BACK,
    )
    return_button.key = "return-to-stage1"

    right = ft.Column([
        ft.Text("STEP 2 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Download Comic", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Downloads comic pages from batcave.biz using the URL found in Stage 1. "
            "Pages are saved to raw_comic/ and cached — re-runs skip existing files.",
            size=12, color=TEXT_MUTED,
        ),
        *comic_card_controls,
        ft.Container(height=16),
        missing_panel,
        download_button,
        ft.Container(height=8),
        return_button,
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
        dl_url_button,

        ft.Container(height=14),
        primary_button("Continue to Stage 3 →", approve_and_go,
                       disabled=not state.is_approved(2) or bool(missing_readers[0])
                                or bool(reader_error[0])),
    ], spacing=8, expand=True, scroll=ft.ScrollMode.AUTO)

    return three_col(
        center, right, state=state, on_go=guarded_go,
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
