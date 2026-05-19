"""
Flet 0.85+ compatibility shim.

Flet 0.85 dropped the convenience helpers `ft.padding.symmetric/only/all`,
`ft.border.all/only`, `ft.margin.only`, and `ft.border_radius.only` in favor of
constructing `ft.Padding/Border/Margin/BorderRadius` directly. This patches
them back so the rest of the UI code keeps working unchanged.

Importing this module has the side effect of patching `flet`. Import it once,
as early as possible (before any UI screen module imports flet).
"""
import flet as ft


def _patch() -> None:
    # ── ft.padding ────────────────────────────────────────────────────────
    if not hasattr(ft.padding, "symmetric"):
        def _padding_symmetric(*, horizontal: float = 0, vertical: float = 0):
            return ft.Padding(left=horizontal, right=horizontal,
                              top=vertical, bottom=vertical)
        ft.padding.symmetric = _padding_symmetric

    if not hasattr(ft.padding, "only"):
        def _padding_only(*, left: float = 0, top: float = 0,
                          right: float = 0, bottom: float = 0):
            return ft.Padding(left=left, top=top, right=right, bottom=bottom)
        ft.padding.only = _padding_only

    if not hasattr(ft.padding, "all"):
        def _padding_all(value: float):
            return ft.Padding(left=value, top=value, right=value, bottom=value)
        ft.padding.all = _padding_all

    # ── ft.border ─────────────────────────────────────────────────────────
    if not hasattr(ft.border, "all"):
        def _border_all(width: float, color):
            side = ft.BorderSide(width, color)
            return ft.Border(top=side, bottom=side, left=side, right=side)
        ft.border.all = _border_all

    if not hasattr(ft.border, "only"):
        def _border_only(top=None, bottom=None, left=None, right=None):
            return ft.Border(top=top, bottom=bottom, left=left, right=right)
        ft.border.only = _border_only

    # ── ft.margin ─────────────────────────────────────────────────────────
    if not hasattr(ft.margin, "only"):
        def _margin_only(*, left: float = 0, top: float = 0,
                         right: float = 0, bottom: float = 0):
            return ft.Margin(left=left, top=top, right=right, bottom=bottom)
        ft.margin.only = _margin_only

    if not hasattr(ft.margin, "all"):
        def _margin_all(value: float):
            return ft.Margin(left=value, top=value, right=value, bottom=value)
        ft.margin.all = _margin_all

    if not hasattr(ft.margin, "symmetric"):
        def _margin_symmetric(*, horizontal: float = 0, vertical: float = 0):
            return ft.Margin(left=horizontal, right=horizontal,
                             top=vertical, bottom=vertical)
        ft.margin.symmetric = _margin_symmetric

    # ── ft.border_radius ──────────────────────────────────────────────────
    if not hasattr(ft.border_radius, "only"):
        def _br_only(*, top_left: float = 0, top_right: float = 0,
                     bottom_left: float = 0, bottom_right: float = 0):
            return ft.BorderRadius(top_left=top_left, top_right=top_right,
                                   bottom_left=bottom_left, bottom_right=bottom_right)
        ft.border_radius.only = _br_only

    if not hasattr(ft.border_radius, "all"):
        def _br_all(value: float):
            return ft.BorderRadius(top_left=value, top_right=value,
                                   bottom_left=value, bottom_right=value)
        ft.border_radius.all = _br_all


_patch()
