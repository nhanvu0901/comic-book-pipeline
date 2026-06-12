# art_ui/screens/a1_select.py
"""A1: pick artwork(s) — from art_candidates.csv (art-scout output), an existing
art project, or manual Met objectIDs — choose a mode, name the project."""
from typing import Callable

import flet as ft

from art_pipeline.config import ART_LF_MODES, ART_MODES, ArtMode
from ui.theme import ACCENT, BG_ELEVATED, BORDER, TEXT_MUTED, TEXT_PRIMARY

from ..bridge import load_candidates
from ..layout import art_shell, primary_button, secondary_button
from ..state import ArtAppState, list_art_projects, load_state, save_state, slugify


# Extra long-form modes not in the short-form ART_MODES list
_LF_EXTRA_MODES: list[ArtMode] = [
    ArtMode("painting_story", "Painting Story (long-form)", "8-12 min long-form painting story."),
]
_ALL_MODE_OPTIONS = [
    *[ft.dropdown.Option(m.key, text=m.label) for m in ART_MODES],
    *[ft.dropdown.Option(m.key, text=m.label) for m in _LF_EXTRA_MODES
      if m.key not in {x.key for x in ART_MODES}],
]


def build(page: ft.Page, state: ArtAppState, *,
          on_go: Callable[[int], None], on_state_change: Callable[[], None]) -> ft.Control:
    ids_tf = ft.TextField(
        label="Met objectIDs (comma-separated)",
        value=",".join(str(i) for i in state.object_ids),
        dense=True,
    )
    name_tf = ft.TextField(label="Project name", value=state.project_name, dense=True)
    theme_tf = ft.TextField(label="Theme (listicle mode only)", value=state.theme, dense=True)
    mode_dd = ft.Dropdown(
        label="Mode",
        value=state.mode or "painting_deep_dive",
        options=_ALL_MODE_OPTIONS,
    )
    length_dd = ft.Dropdown(
        label="Length",
        value=state.length or "short",
        options=[
            ft.dropdown.Option("short", "Short (60-75s, 9:16)"),
            ft.dropdown.Option("longform", "Long-form (8-12 min, 16:9)"),
        ],
        dense=True,
    )
    error_text = ft.Text("", color="#e06060", size=12)

    def pick_candidate(row: dict):
        ids_tf.value = str(row.get("object_id", ""))
        name_tf.value = slugify(row.get("title", ""))
        page.update()

    candidates = load_candidates()
    cand_rows: list[ft.Control] = []
    for row in candidates:
        if (row.get("status") or "").strip() not in ("", "queued"):
            continue
        cand_rows.append(ft.Container(
            content=ft.Column([
                ft.Text(f"{row.get('title','')} — {row.get('artist','')} ({row.get('year','')})",
                        size=13, color=TEXT_PRIMARY, weight=ft.FontWeight.W_600),
                ft.Text(row.get("story_hook", ""), size=11, color=TEXT_MUTED),
                ft.Text(f"objectID {row.get('object_id','')} · {row.get('wiki_grounding','')} · {row.get('yt_coverage','')}",
                        size=10, color=TEXT_MUTED),
            ], spacing=3),
            padding=12, border=ft.border.all(1, BORDER), border_radius=6,
            ink=True, on_click=lambda _e, r=row: pick_candidate(r),
        ))
    if not cand_rows:
        cand_rows.append(ft.Text(
            "art_candidates.csv is empty — run the art-scout agent to find candidates, "
            "or enter Met objectIDs manually on the right.",
            color=TEXT_MUTED, size=12))

    proj_rows: list[ft.Control] = []
    for name in list_art_projects():
        def _open(_e, n=name):
            loaded = load_state(n)
            state.__dict__.update(loaded.__dict__)
            on_go(max(2, state.current_stage))
        proj_rows.append(ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN, size=16, color=TEXT_MUTED),
                            ft.Text(name, size=13, color=TEXT_PRIMARY)], spacing=8),
            padding=10, border=ft.border.all(1, BORDER), border_radius=6,
            ink=True, on_click=_open,
        ))

    def start(_e):
        raw = (ids_tf.value or "").replace(" ", "")
        try:
            ids = [int(x) for x in raw.split(",") if x]
        except ValueError:
            error_text.value = "objectIDs must be integers"
            page.update()
            return
        if not ids:
            error_text.value = "Enter at least one Met objectID"
            page.update()
            return
        chosen_length = length_dd.value or "short"
        chosen_mode = mode_dd.value or "painting_deep_dive"
        if chosen_length == "longform" and chosen_mode not in ART_LF_MODES:
            error_text.value = (
                f"Long-form requires mode painting_story or artist_journey "
                f"(got '{chosen_mode}'). Switch the Mode dropdown."
            )
            page.update()
            return
        error_text.value = ""
        state.project_name = slugify(name_tf.value or f"art-{ids[0]}")
        state.object_ids = ids
        state.mode = chosen_mode
        state.theme = theme_tf.value or ""
        state.length = chosen_length
        state.current_stage = 2
        save_state(state)
        on_go(2)

    center = ft.Column([
        ft.Container(
            content=ft.Column([
                ft.Text("CANDIDATES (art-scout)", size=10, color=TEXT_MUTED),
                ft.Column(cand_rows, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True),
                ft.Container(height=10),
                ft.Text("EXISTING ART PROJECTS", size=10, color=TEXT_MUTED),
                ft.Column(proj_rows or [ft.Text("none yet", color=TEXT_MUTED, size=12)],
                          spacing=6),
            ], spacing=8, expand=True),
            padding=ft.padding.symmetric(horizontal=28, vertical=16), expand=True,
        ),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 1 OF 6", size=10, color=TEXT_MUTED),
        ft.Text("Select Artwork", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Container(height=10),
        ids_tf, name_tf, mode_dd, length_dd, theme_tf, error_text,
        ft.Container(height=8),
        primary_button("Start →", start, icon=ft.Icons.PLAY_ARROW),
    ], spacing=10, expand=True, scroll=ft.ScrollMode.AUTO)

    return art_shell(center, right, state=state, on_go=on_go,
                     header_title="Select Artwork",
                     header_subtitle="Pick a scouted candidate, reopen a project, or enter Met objectIDs.")
