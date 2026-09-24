"""Regression tests for defects found by driving the live UI on the Windows server
(2026-09-24): the stepper hid Final Video, disabled primary buttons looked enabled, copy
buttons announced copies the browser refused, the Stage 1 rail could not scroll and
printed raw ids for earlier rounds, Stage 6 drew an empty card, and a hand-edited
context crashed preprocessing."""
import asyncio

import flet as ft

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import ui.screens.s4_tts as s4_tts
from stages.stage_1.tools.summarize_context import format_for_vlm
from ui import app
from ui.clipboard import copy_text
from ui.layout import primary_button, stepper_nav
from ui.state import AppState, STAGE_NAMES
from tests.ui_test_doubles import StrictFakePage


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


def test_stepper_lists_every_routed_stage_including_final_video():
    nav = stepper_nav(AppState(project_name="p"), on_go=lambda _s: None)
    titles = [n.value for n in _walk(nav) if isinstance(n, ft.Text) and n.value in STAGE_NAMES.values()]
    assert titles == [STAGE_NAMES[s] for s in sorted(app.STAGE_BUILDERS)]
    assert "Final Video" in titles


def test_stage_one_is_named_after_its_screen():
    assert STAGE_NAMES[1] == "Research Scout"


def test_disabled_primary_button_is_styled_differently_from_enabled():
    button = primary_button("Go", disabled=True)
    bg = button.style.bgcolor
    assert isinstance(bg, dict)
    assert bg[ft.ControlState.DISABLED] != bg[ft.ControlState.DEFAULT]
    # No flat bgcolor overriding the per-state colours.
    assert not button.bgcolor


class _Clipboard:
    def __init__(self, fail):
        self.fail, self.text = fail, None

    async def set(self, text):
        if self.fail:
            raise RuntimeError("PlatformException(copy_fail, Clipboard is not available)")
        self.text = text


def test_copy_reports_the_real_outcome():
    ok = _Clipboard(fail=False)
    assert asyncio.run(copy_text(ok, "hello")) is True and ok.text == "hello"
    assert asyncio.run(copy_text(_Clipboard(fail=True), "hello")) is False


def test_tts_screen_hides_the_info_card_until_there_is_something_to_list(tmp_path, monkeypatch):
    monkeypatch.setattr(s4_tts, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    root = s4_tts.build(StrictFakePage(), AppState(project_name="p", current_stage=6),
                        on_go=lambda _s: None, on_state_change=lambda: None)
    cards = [n for n in _walk(root) if isinstance(n, ft.Container) and isinstance(n.content, ft.Column)
             and n.content.controls == [] and n.border is not None]
    assert cards and all(c.visible is False for c in cards)


def test_vlm_roster_accepts_characters_written_as_plain_names():
    text = format_for_vlm({"characters": ["Deadpool", {"name": "Hulk", "role": "brute"}]})
    assert "- Deadpool" in text and "- Hulk: brute" in text


def test_prompt_dialog_text_box_uses_the_dialogs_full_width(tmp_path, monkeypatch):
    """Copying is blocked on the LAN address, so people select the prompt in this box by
    hand. It sat in a ~300px column inside a 650px dialog."""
    import ui.screens.s3_narrate as s3_narrate

    monkeypatch.setattr(s3_narrate, "saved_script_for_editor", lambda _p: ("", ""))
    monkeypatch.setattr(s3_narrate, "_item_count", lambda _p: 0, raising=False)
    monkeypatch.setattr(s3_narrate, "generate_gemini_writer_prompt",
                        lambda _p: ("PROMPT TEXT", tmp_path / "p_writer_prompt.md"))

    async def _no_copy(*_a, **_k):
        return False

    monkeypatch.setattr(s3_narrate, "copy_text", _no_copy)
    page = StrictFakePage()
    page.services = []
    root = s3_narrate.build(page, AppState(project_name="p", current_stage=4),
                            on_go=lambda _s: None, on_state_change=lambda: None)
    copy_button = next(c for c in _walk(root) if isinstance(c, ft.ElevatedButton)
                       and c.content == "Copy Gemini Prompt")
    page.overlay = []
    asyncio.run(copy_button.on_click(None))

    dialog = page.dialogs[-1]
    column = dialog.content.content
    box = next(c for c in column.controls if isinstance(c, ft.TextField))
    assert box.value == "PROMPT TEXT"
    assert column.horizontal_alignment == ft.CrossAxisAlignment.STRETCH


def test_preprocess_screen_says_what_to_do_when_there_are_no_pages_yet(monkeypatch):
    """The centre pane was blank on arrival; its placeholder appeared only after Clear."""
    import ui.screens.s2_preprocess as s2_preprocess

    monkeypatch.setattr(s2_preprocess, "load_preprocessed", lambda _p: [])
    root = s2_preprocess.build(StrictFakePage(), AppState(project_name="p", current_stage=3),
                               on_go=lambda _s: None, on_state_change=lambda: None)

    hints = [str(c.value) for c in _walk(root) if isinstance(c, ft.Text)
             and "Run Preprocessing" in str(c.value)]
    assert hints and "Download Comic" in hints[0]
