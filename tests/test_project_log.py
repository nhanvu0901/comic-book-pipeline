"""Stage logs kept in the project's own folder (projects/<name>/logs/).

The on-screen log lives only in the browser tab and keeps 300 lines, so after a reload —
or when the app runs on the server and the question comes from someone else — the lines
that explain a failed download or a refused script were gone."""
import asyncio
import json
import re

import flet as ft

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import ui.project_log as project_log
import ui.screens.s2_download as s2_download
import ui.screens.s3_narrate as s3_narrate
import ui.state as ui_state
from stages.user_errors import ScriptMappingError
from ui.layout import log_list
from ui.state import AppState

from tests.ui_test_doubles import StrictFakePage as FakePage

_STAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ")


def _walk(control):
    if control is None:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in (getattr(control, attr, None) or []):
            yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


def test_lines_are_appended_with_a_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(project_log, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    path = project_log.stage_log_path("p", "stage2_download")

    project_log.append_log(path, "first")
    project_log.append_log(path, "two\nlines")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [_STAMP.sub("", ln) for ln in lines] == ["first", "two", "lines"]
    assert all(_STAMP.match(ln) for ln in lines)
    assert path == tmp_path / "p" / "logs" / "stage2_download.log"


def test_no_project_or_a_missing_project_folder_writes_nothing(tmp_path, monkeypatch):
    """A log must never create a project folder (stray folders are how test runs and
    typos littered projects/)."""
    monkeypatch.setattr(project_log, "PROJECTS_ROOT", tmp_path)
    assert project_log.stage_log_path("", "stage4_narration") is None
    project_log.append_log(project_log.stage_log_path("ghost", "stage4_narration"), "x")
    project_log.append_log(None, "x")
    assert list(tmp_path.iterdir()) == []


def test_a_log_that_cannot_be_written_never_breaks_the_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(project_log, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "logs").write_text("a file where the folder should be")
    project_log.append_log(project_log.stage_log_path("p", "stage2_download"), "x")


def test_the_screen_log_also_goes_to_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(project_log, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    path = project_log.stage_log_path("p", "stage2_download")
    _lv, push = log_list(FakePage(), log_path=lambda: path)

    push("[download] chapter 1 of 3")

    assert path.read_text(encoding="utf-8").rstrip().endswith("[download] chapter 1 of 3")


def test_stage2_writes_its_log_into_the_project(tmp_path, monkeypatch):
    monkeypatch.setattr(project_log, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _p: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _p: [])

    def _download(project, raw, issues, enrich, log):
        log("[url-mode] 1 reader URL(s)")
        raise RuntimeError("batcave said no")

    monkeypatch.setattr(s2_download, "run_stage_download_from_url", _download)
    page = FakePage()
    root = s2_download.build(page, AppState(project_name="", current_stage=2),
                             on_go=lambda _s: None, on_state_change=lambda: None)
    fields = {c.label: c for c in _walk(root) if isinstance(c, ft.TextField)}
    fields["Comic URL(s)"].value = "https://batcave.biz/reader/1/11"
    fields["Project name (created if new)"].value = "new_comic"
    next(c for c in _walk(root) if getattr(c, "key", None) == "download-from-url").on_click(None)
    (func,), _kw = page.tasks[-1]
    asyncio.run(func())

    text = (tmp_path / "new_comic" / "logs" / "stage2_download.log").read_text(encoding="utf-8")
    assert "[url-mode] 1 reader URL(s)" in text
    assert "batcave said no" in text


def _stage4(tmp_path, monkeypatch, parse):
    monkeypatch.setattr(project_log, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(s3_narrate, "saved_script_for_editor", lambda _p: ("", ""))
    monkeypatch.setattr(s3_narrate, "_item_count", lambda _p: 3)
    monkeypatch.setattr(s3_narrate, "parse_and_save_script", parse)
    (tmp_path / "p").mkdir(exist_ok=True)
    root = s3_narrate.build(FakePage(), AppState(project_name="p", current_stage=4),
                            on_go=lambda _s: None, on_state_change=lambda: None)
    script = next(c for c in _walk(root) if isinstance(c, ft.TextField) and c.multiline)
    approve = next(c for c in _walk(root) if isinstance(c, ft.ElevatedButton)
                   and c.content == "Approve & Continue →")
    return root, script, approve


def test_stage4_keeps_the_pasted_script_and_the_refusal_in_the_project_log(tmp_path, monkeypatch):
    """A refused script used to exist only in the browser's text box."""
    def _refuse(_project, _text, *, log):
        log("[stage4-gemini] parsing")
        raise ScriptMappingError("The script does not fit the 3 answer item(s).\nFound 6 paragraph(s)")

    _root, script, approve = _stage4(tmp_path, monkeypatch, _refuse)
    script.value = "Hook line.\n\nParagraph one.\n\nParagraph two."
    asyncio.run(approve.on_click(None))

    text = (tmp_path / "p" / "logs" / "stage4_narration.log").read_text(encoding="utf-8")
    for expected in ("Hook line.", "Paragraph one.", "Paragraph two.", "[stage4-gemini] parsing",
                     "does not fit the 3 answer item(s)", "Found 6 paragraph(s)"):
        assert expected in text, expected


def test_stage4_offers_the_last_pasted_script_again_after_a_reload(tmp_path, monkeypatch):
    def _refuse(_project, _text, *, log):
        raise ScriptMappingError("nope")

    _root, script, approve = _stage4(tmp_path, monkeypatch, _refuse)
    script.value = "Hook.\n\nOnly paragraph."
    asyncio.run(approve.on_click(None))

    _root2, script2, _approve2 = _stage4(tmp_path, monkeypatch, _refuse)
    assert script2.value == "Hook.\n\nOnly paragraph."
