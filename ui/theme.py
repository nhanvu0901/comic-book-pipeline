"""
Dark theme + palette constants for the Flet UI.
"""
import flet as ft


# Brand palette — soft dark with a single accent
BG = "#0f1115"
BG_PANEL = "#161a22"
BG_ELEVATED = "#1e2430"
TEXT_PRIMARY = "#e9ecf1"
TEXT_MUTED = "#8a93a6"
BORDER = "#2a3040"
ACCENT = "#4f9cff"
ACCENT_HOVER = "#6aaeff"
SUCCESS = "#3ecf8e"
WARN = "#f4b400"
DANGER = "#e06060"

# Stage status colors
STATUS_PENDING = "#5a6474"
STATUS_RUNNING = "#f4b400"
STATUS_REVIEW = "#4f9cff"
STATUS_DONE = "#3ecf8e"
STATUS_DIRTY = "#f4b400"


def apply_theme(page: ft.Page) -> None:
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.theme = ft.Theme(
        color_scheme_seed=ACCENT,
        use_material3=True,
        font_family="Inter",
    )
    page.padding = 0
    page.spacing = 0
