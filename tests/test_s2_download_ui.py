"""Stage 2 must repair missing Q&A reader URLs before downloading pages."""

from __future__ import annotations

import asyncio

import flet as ft

import ui.screens.s2_download as s2_download
from stages.stage_1 import answer_research
from ui.state import AppState

from tests.ui_test_doubles import StrictFakePage as FakePage


def _walk(control, depth: int = 0):
    if control is None or depth > 60:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in getattr(control, attr, None) or []:
            yield from _walk(child, depth + 1)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content, depth + 1)


def _by_key(root, key: str):
    return next(control for control in _walk(root) if getattr(control, "key", None) == key)


def _run_task(page: FakePage):
    (func,), _kwargs = page.tasks[-1]
    asyncio.run(func())


def _build(monkeypatch, missing, repair):
    page = FakePage()
    state = AppState(project_name="qa-project")
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _project: list(missing), raising=False)
    monkeypatch.setattr(s2_download, "repair_scout_readers", repair, raising=False)
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)
    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)
    return page, root


def test_missing_reader_panel_names_rank_entity_and_source_and_blocks_download(monkeypatch):
    page, root = _build(
        monkeypatch,
        [{"rank": 2, "entity": "Deadpool", "source_comic": "Deadpool #3 (2008)", "reader_url": ""}],
        lambda *_args, **_kwargs: [],
    )

    field = _by_key(root, "missing-reader-2")
    download = _by_key(root, "stage1-download")
    assert field.label == "#2 — Deadpool — Deadpool #3 (2008)"
    assert download.disabled is True
    assert _by_key(root, "repair-reader-urls")
    assert not page.tasks


def test_partial_repair_preserves_typed_url_and_keeps_download_blocked(monkeypatch):
    missing = [{"rank": 1, "entity": "Wade", "source_comic": "Deadpool #1 (2012)", "reader_url": ""}]
    calls = []

    def repair(project, reader_urls=None, log=print):
        calls.append((project, reader_urls))
        return list(missing)

    page, root = _build(monkeypatch, missing, repair)
    field = _by_key(root, "missing-reader-1")
    field.value = "https://batcave.biz/reader/12/34"

    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)

    assert calls == [("qa-project", {1: "https://batcave.biz/reader/12/34"})]
    assert field.value == "https://batcave.biz/reader/12/34"
    assert _by_key(root, "stage1-download").disabled is True


def test_successful_repair_enables_stage_one_download(monkeypatch):
    missing = [{"rank": 1, "entity": "Wade", "source_comic": "Deadpool #1 (2012)", "reader_url": ""}]
    page, root = _build(monkeypatch, missing, lambda *_args, **_kwargs: [])

    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)

    assert _by_key(root, "stage1-download").disabled is False


def test_second_repair_submits_only_the_ranks_still_missing(monkeypatch):
    first = [
        {"rank": 1, "entity": "One", "source_comic": "One #1", "reader_url": ""},
        {"rank": 2, "entity": "Two", "source_comic": "Two #2", "reader_url": ""},
    ]
    second = [{"rank": 2, "entity": "Two", "source_comic": "Two #2", "reader_url": ""}]
    calls = []

    def repair(project, reader_urls=None, log=print):
        calls.append((project, reader_urls))
        return list(second) if len(calls) == 1 else []

    page, root = _build(monkeypatch, first, repair)
    _by_key(root, "missing-reader-1").value = "https://batcave.biz/reader/1/1"
    _by_key(root, "missing-reader-2").value = "https://batcave.biz/reader/2/2"
    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)
    _by_key(root, "missing-reader-2").value = "https://batcave.biz/reader/2/2"
    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)

    assert calls == [
        ("qa-project", {
            1: "https://batcave.biz/reader/1/1",
            2: "https://batcave.biz/reader/2/2",
        }),
        ("qa-project", {2: "https://batcave.biz/reader/2/2"}),
    ]


def test_reader_inspection_error_blocks_download_without_inventing_a_rank_zero_item(monkeypatch):
    page = FakePage()
    state = AppState(project_name="qa-project")
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(
        s2_download, "get_scout_missing_readers",
        lambda _project: (_ for _ in ()).throw(ValueError("answer_context.json is malformed")),
    )
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)

    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)

    assert _by_key(root, "stage1-download").disabled is True
    assert not [control for control in _walk(root) if getattr(control, "key", None) == "missing-reader-0"]
    assert "answer_context.json is malformed" in "\n".join(
        str(control.value or "") for control in _walk(root) if isinstance(control, ft.Text)
    )


def test_click_time_reader_inspection_error_stays_in_the_repair_panel(monkeypatch):
    """A stale initial inspection must not escape the async Download task."""

    page = FakePage()
    state = AppState(project_name="qa-project")
    calls = []

    def inspect(_project):
        calls.append(_project)
        if len(calls) == 1:
            return []
        raise ValueError("answer_context.json changed while this screen was open")

    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", inspect)
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)

    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)
    _by_key(root, "stage1-download").on_click(None)
    _run_task(page)

    assert calls == ["qa-project", "qa-project"]
    assert _by_key(root, "stage1-download").disabled is True
    assert "answer_context.json changed while this screen was open" in "\n".join(
        str(control.value or "") for control in _walk(root) if isinstance(control, ft.Text)
    )


def test_ui_repair_uses_the_real_persisted_reader_contract(monkeypatch, tmp_path):
    project_root = tmp_path / "qa-project"
    project_root.mkdir()
    answer = {
        "items": [
            {"rank": 1, "entity": "One", "source_comic": "One #1", "reader_url": ""},
            {"rank": 2, "entity": "Two", "source_comic": "Two #2", "reader_url": "https://batcave.biz/reader/2/2"},
        ]
    }
    (project_root / "answer_context.json").write_text(__import__("json").dumps(answer))
    (project_root / "comic_context.json").write_text("{}")
    monkeypatch.setattr(answer_research, "get_project_dirs", lambda _name: {"root": project_root})
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)
    page = FakePage()
    state = AppState(project_name="qa-project")

    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)
    _by_key(root, "missing-reader-1").value = "https://batcave.biz/reader/1/1"
    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)

    saved = __import__("json").loads((project_root / "answer_context.json").read_text())
    assert [item["rank"] for item in saved["items"]] == [1, 2]
    assert [item["reader_url"] for item in saved["items"]] == [
        "https://batcave.biz/reader/1/1", "https://batcave.biz/reader/2/2",
    ]
    assert _by_key(root, "stage1-download").disabled is False


def test_normal_project_has_no_missing_reader_panel_and_download_stays_enabled(monkeypatch):
    _page, root = _build(monkeypatch, [], lambda *_args, **_kwargs: [])

    assert _by_key(root, "stage1-download").disabled is False
    assert not [control for control in _walk(root) if getattr(control, "key", None) == "repair-reader-urls"]
