"""Opening a comic must not be a one-way door.

Before this, `_show_project_picker` was built once at launch and only when no project
resolved (ui/app.py `main`), while `render_current` clears `page.views` on every hop — so
once a project was open there was no route back to the project list for the whole session.
"""
import flet as ft

import ui  # noqa: F401 — patches flet 0.85 compat before layout imports it
from ui.layout import stepper_nav
from ui.state import PICKER_STAGE, AppState


def _walk(ctl):
    yield ctl
    for attr in ("controls", "content"):
        child = getattr(ctl, attr, None)
        if child is None:
            continue
        for c in (child if isinstance(child, list) else [child]):
            if isinstance(c, ft.Control):
                yield from _walk(c)


def _buttons(ctl):
    return [c for c in _walk(ctl) if isinstance(c, (ft.TextButton, ft.ElevatedButton))]


def _label(b) -> str:
    """flet 0.85 keeps a button's caption in `content`, not `text`."""
    c = getattr(b, "content", None)
    return c if isinstance(c, str) else str(getattr(b, "text", "") or "")


def test_sidebar_offers_a_way_back_to_the_project_list():
    seen: list[int] = []
    nav = stepper_nav(AppState(project_name="some-comic", current_stage=5), seen.append)

    back = [b for b in _buttons(nav) if "project" in _label(b).lower()]
    assert back, "an open project must expose a route back to the picker"
    back[0].on_click(None)
    assert seen == [PICKER_STAGE]


def test_no_back_button_before_a_project_is_open():
    """At launch the picker IS the screen — a button back to it would be a loop."""
    nav = stepper_nav(AppState(project_name="", current_stage=1), lambda _s: None)
    assert not [b for b in _buttons(nav) if "project" in _label(b).lower()]


def test_picker_stage_is_outside_the_real_stage_range():
    """goto_stage rejects anything outside 1..8, so the sentinel must not collide."""
    assert not 1 <= PICKER_STAGE <= 8
