"""Shared strict test double for `flet.Page`.

The old per-file doubles looked like:

    class FakePage:
        def show_dialog(self, d): self.dialogs.append(d)
        def __getattr__(self, name): return lambda *a, **k: None

That `__getattr__` turns EVERY unknown attribute access into a silent no-op
callable, so a wrong API name, a typo, or a method flet does not actually
have all PASS instead of failing the test. That is exactly how a bug like
"clicking delete does nothing" ships invisibly: the double can't tell the
difference between "the code called something real that just didn't do
anything interesting" and "the code called something that doesn't exist and
the click handler blew up before doing its job".

`StrictFakePage` implements only the handful of `flet.Page` behaviours the UI
screens under test actually exercise (view-stack management, the
show_dialog/pop_dialog pair, `update`, and `run_task`) and raises
`AttributeError` for anything else — see `_describe_missing` for how it tells
apart "not a real flet.Page attribute at all" (typo / wrong API) from "a real
Page attribute this double just hasn't been taught yet".

Note on `hasattr(ft.Page, name)`: flet.Page is built with the `@control`
dataclass decorator. A dataclass field only becomes a class-level attribute
(visible to `hasattr` on the class) when it has a plain `default=`; fields
with `default_factory=` (e.g. `views: list = field(default_factory=list)`,
`window: ...`) are instance-only and `hasattr(ft.Page, "views")` is False
even though `views` is a completely real, load-bearing Page attribute. Pure
`hasattr` would therefore misclassify real fields as typos, so this module
also checks `dataclasses.fields(ft.Page)` as a fallback source of truth.
"""
from __future__ import annotations

import dataclasses

import flet as ft


def _is_real_page_attr(name: str) -> bool:
    if hasattr(ft.Page, name):
        return True
    try:
        fields = dataclasses.fields(ft.Page)
    except TypeError:
        return False
    return any(f.name == name for f in fields)


class StrictFakePage:
    """Minimal, honest stand-in for `flet.Page`.

    - `views`: the real navigation stack UI code mutates directly
      (`page.views.clear()` / `page.views.append(...)`).
    - `dialogs`: NOT a real Page attribute — an append-only log of every
      dialog handed to `show_dialog`, kept for test assertions
      (`page.dialogs[-1]`). `pop_dialog` deliberately does not mutate it: the
      real Page's dialog overlay stack and this log serve different purposes
      (a live stack vs. a full history a test can inspect).
    - `tasks`: NOT a real Page attribute — records every `run_task` call so a
      test can execute the coroutine synchronously afterward (see
      `test_s1_research_scout_ui._run_recorded_task`) instead of depending on
      a running event loop.
    """

    def __init__(self) -> None:
        self.views: list = []
        self.dialogs: list = []
        self.tasks: list = []

    def show_dialog(self, d) -> None:
        self.dialogs.append(d)

    def pop_dialog(self, *_a) -> None:
        pass

    def update(self, *_a) -> None:
        pass

    def run_task(self, *args, **kwargs) -> None:
        self.tasks.append((args, kwargs))

    def __getattr__(self, name: str):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if _is_real_page_attr(name):
            raise AttributeError(
                f"StrictFakePage does not implement flet.Page.{name!r} — "
                "this is a real Page attribute the code under test just "
                "reached; add explicit support for it if that's expected."
            )
        raise AttributeError(
            f"{name!r} is not a real flet.Page attribute — check for a typo "
            "or a flet API this installed version does not have."
        )
