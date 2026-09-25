"""Every ft.X(...) keyword and every handler assigned on a Dropdown must exist in the
installed flet. A wrong one only fails when that control is BUILT — the Stage 7
swap-panel dialog passed Dropdown(on_change=...) and crashed the whole session the first
time anyone opened it, and no test built that dialog."""
import ast
import dataclasses
import inspect
from pathlib import Path

import ui  # noqa: F401 — the repo's flet compat shims, as the app applies them
import flet as ft

ROOT = Path(__file__).resolve().parents[1]
UI_DIRS = [ROOT / "ui", ROOT / "art_ui"]


def _params(cls):
    names = set()
    try:
        names |= {f.name for f in dataclasses.fields(cls)}
    except TypeError:
        pass
    try:
        sig = inspect.signature(cls)
    except (TypeError, ValueError):
        return names
    if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
        return None
    return names | set(sig.parameters)


def _flet_obj(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not (isinstance(node, ast.Name) and node.id == "ft"):
        return None, None
    obj = ft
    for name in reversed(parts):
        obj = getattr(obj, name, None)
        if obj is None:
            break
    return obj, ".".join(reversed(parts))


def _ui_sources():
    for base in UI_DIRS:
        for path in sorted(base.rglob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_flet_constructor_keywords_exist():
    problems = []
    for path, tree in _ui_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            obj, dotted = _flet_obj(node.func)
            if dotted and obj is None:
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno} ft.{dotted} does not exist")
                continue
            if not inspect.isclass(obj):
                continue
            allowed = _params(obj)
            if allowed is None:
                continue
            problems += [f"{path.relative_to(ROOT)}:{node.lineno} ft.{dotted}({kw.arg}=...)"
                         for kw in node.keywords if kw.arg and kw.arg not in allowed]
    assert not problems, "not parameters of the installed flet:\n" + "\n".join(problems)


def test_no_on_change_handler_is_assigned_to_a_dropdown():
    """Assigning a non-existent event after construction does not raise — the handler is
    just never called. Dropdown's event is on_select."""
    problems = []
    for path, tree in _ui_sources():
        dropdowns = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and _flet_obj(node.value.func)[0] is ft.Dropdown):
                dropdowns |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if (isinstance(t, ast.Attribute) and t.attr == "on_change"
                            and isinstance(t.value, ast.Name) and t.value.id in dropdowns):
                        problems.append(f"{path.relative_to(ROOT)}:{node.lineno} {t.value.id}.on_change")
    assert not problems, "\n".join(problems)
