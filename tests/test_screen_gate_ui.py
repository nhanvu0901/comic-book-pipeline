"""ui/screens/s_screen_gate.py — the screen_qa select-beat screen, built against a mock page and
walked as a control tree (the real-browser run is the Playwright test on the Windows server)."""
from __future__ import annotations

import ast
import asyncio
import json
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

import config
import ui  # noqa: F401 — the repo's flet compat shims, as the app applies them
from stages import review_gate as rg
from stages.stage_5 import screen_selection as sel
from ui.screens import s_screen_gate
from ui.state import AppState

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "screen_qa_project"
ROOT_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    # the stage-5 module imported PROJECTS_ROOT by value; the gate itself uses config's
    root = tmp_path / "proj_screen"
    (root / "review" / "clips").mkdir(parents=True)
    (root / "review" / "custom").mkdir(parents=True)
    (root / "narration.json").write_text((FIXTURE_DIR / "narration.json").read_text())
    (root / "screen_context.json").write_text((FIXTURE_DIR / "screen_context.json").read_text())
    # no background TTS thread in unit tests
    import stages.stage_4.background_tts as bt
    monkeypatch.setattr(bt, "start_background_tts", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(bt, "bump_priority_beat", lambda *a, **k: None, raising=False)
    return root


def _page() -> MagicMock:
    page = MagicMock()
    page.services = []
    page.overlay = []
    page.launch_url = AsyncMock()
    page.tasks = []

    def run_task(fn, *a, **k):
        page.tasks.append((fn, a, k))
    page.run_task.side_effect = run_task
    return page


def _drain(page: MagicMock) -> None:
    """Run every coroutine the screen handed to page.run_task, like Flet's loop would."""
    while page.tasks:
        fn, a, k = page.tasks.pop(0)
        asyncio.run(fn(*a, **k))


def _build(project: Path, page=None, on_go=None):
    state = AppState(project_name=project.name)
    state.pipeline_mode = "screen_qa"
    page = page or _page()
    gone = []
    ctl = s_screen_gate.build(page, state, on_go=on_go or gone.append, on_state_change=lambda: None)
    return ctl, page, state, gone


def _walk(ctl):
    yield ctl
    for attr in ("controls", "content"):
        child = getattr(ctl, attr, None)
        if isinstance(child, list):
            for c in child:
                yield from _walk(c)
        elif child is not None and hasattr(child, "__dict__"):
            yield from _walk(child)


def _texts(ctl) -> list[str]:
    return [c.value for c in _walk(ctl) if isinstance(c, ft.Text) and c.value]


def _buttons(ctl, label: str) -> list:
    out = []
    for c in _walk(ctl):
        if isinstance(c, (ft.ElevatedButton, ft.OutlinedButton)):
            text = getattr(c, "text", None) or getattr(c, "content", None)
            if text == label:
                out.append(c)
    return out


# ─── building ─────────────────────────────────────────────────────────────────

def test_the_screen_lists_every_beat_with_its_review_key_and_words(project):
    ctl, *_ = _build(project)
    texts = _texts(ctl)
    for key in ("1:0", "1:1", "2:0", "2:1", "3:0", "3:1"):
        assert any(f"[{key}]" in t for t in texts), key
    assert any("Scott Lang reveals that five hours" in t for t in texts)
    assert any("Hook · intro" in t or "mảnh 1" in t for t in texts)
    assert "Thẻ chữ (tự động)" in texts          # nothing picked → card, no error


def test_a_project_without_screen_narration_shows_the_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "empty").mkdir()
    state = AppState(project_name="empty")
    ctl = s_screen_gate.build(_page(), state, on_go=lambda s: None, on_state_change=lambda: None)
    assert any("Chưa có kịch bản" in t for t in _texts(ctl))


def test_existing_picks_are_shown_as_chips(project):
    (project / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "VID1", "file": "review/clips/VID1.mp4", "beat": "1:0", "start": 0, "end": 3,
         "source_start": 61.5, "backup": {"id": "VID2-backup", "file": "x"}}]}))
    (project / "review" / "custom" / "s.png").write_bytes(b"x")
    sel.lock_still(project, "2:0", "review/custom/s.png")
    ctl, *_ = _build(project)
    texts = _texts(ctl)
    assert "MP4 VID1 @ 61.5s" in texts
    assert "dự phòng: VID2-backup" in texts
    assert "Ảnh: s.png" in texts


def test_flag_off_disables_the_mp4_button_but_stills_and_cards_still_work(project, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    ctl, *_ = _build(project)
    mp4 = _buttons(ctl, "Chọn MP4")
    assert mp4 and all(b.disabled for b in mp4)
    assert all(not b.disabled for b in _buttons(ctl, "Ảnh"))
    assert any("ENABLE_VIDEO_CLIPS=0" in t for t in _texts(ctl))


# ─── the P1 pick tab ──────────────────────────────────────────────────────────

def test_moments_url_encodes_the_free_text_query():
    url = s_screen_gate.moments_url("my proj", "1:0", "Ant-Man & the van #1 100%")
    assert url == "/moments_review?project=my%20proj&beat=1%3A0&q=Ant-Man%20%26%20the%20van%20%231%20100%25"


def test_choose_mp4_opens_the_p1_route_for_exactly_that_beat_with_its_query(project):
    ctl, page, *_ = _build(project)
    btn = _buttons(ctl, "Chọn MP4")[2]               # third beat = 2:0
    btn.on_click(None)
    _drain(page)
    page.launch_url.assert_awaited_once()
    url = page.launch_url.await_args.args[0]
    assert url.startswith("/moments_review?project=proj_screen&beat=2%3A0&q=")
    assert "Tony%20Stark%20mobius%20strip%20simulation" in url        # the writer's query


def test_an_edited_search_query_is_what_gets_sent(project):
    ctl, page, *_ = _build(project)
    fields = [c for c in _walk(ctl) if isinstance(c, ft.TextField)]
    f = fields[0]
    f.value = "custom query & words"
    f.on_change(MagicMock(control=f))
    _buttons(ctl, "Chọn MP4")[0].on_click(None)
    _drain(page)
    assert page.launch_url.await_args.args[0].endswith("q=custom%20query%20%26%20words")


def test_a_pick_event_reloads_the_screen_and_searches_a_backup_in_the_background(project, monkeypatch):
    ctl, page, state, _ = _build(project)
    sel.set_approved(project, True)
    (project / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "VID9", "file": "review/clips/VID9.mp4", "beat": "2:1", "start": 0, "end": 2,
         "source_start": 12.0}]}))
    called = threading.Event()
    seen = {}

    def fake_backup(root, key, **kw):
        seen.update(key=key, query=kw.get("query"), seconds=kw.get("seconds"))
        called.set()
    monkeypatch.setattr(sel, "suggest_backup", fake_backup)
    from ui import web_routes
    web_routes._broadcast_moment_picked({"project": "proj_screen", "beat": "2:1",
                                         "video_id": "VID9", "start": 12.0})
    _drain(page)
    assert "MP4 VID9 @ 12.0s" in _texts(ctl)               # the card list was rebuilt in place
    assert not sel.is_approved(project)                    # a pick after Approve withdraws it
    assert called.wait(5)
    assert seen["key"] == "2:1" and seen["query"] == "Tony Stark Eureka quantum GPS"
    assert seen["seconds"] and seen["seconds"] > 0
    time.sleep(0.2)
    _drain(page)


def test_a_pick_for_another_project_is_ignored(project, monkeypatch):
    ctl, page, *_ = _build(project)
    monkeypatch.setattr(sel, "suggest_backup", lambda *a, **k: pytest.fail("not this project"))
    from ui import web_routes
    web_routes._broadcast_moment_picked({"project": "someone_else", "beat": "1:0"})
    assert not page.tasks


def test_rebuilding_the_screen_replaces_its_listener_instead_of_piling_them_up(project):
    from ui import web_routes
    page = _page()
    before = len(web_routes._listeners)
    for _ in range(4):
        _build(project, page)
    assert len(web_routes._listeners) == before + 1
    s_screen_gate._LISTENERS.pop(id(page), None)


# ─── approval → the real review gate ──────────────────────────────────────────

def test_approve_satisfies_the_gate_and_enables_continue_to_tts(project):
    ctl, page, state, gone = _build(project)
    cont = _buttons(ctl, "Continue → TTS")[0]
    assert cont.disabled
    with pytest.raises(SystemExit):
        rg.ensure_reviewed(project, log=lambda m: None)
    _buttons(ctl, "Approve")[0].on_click(None)
    assert sel.is_approved(project)
    rg.ensure_reviewed(project, log=lambda m: None)
    assert not cont.disabled and state.is_approved(5)
    assert _buttons(ctl, "Un-approve") and not _buttons(ctl, "Approve")   # the LABEL flips too
    _buttons(ctl, "Un-approve")[0].on_click(None)
    assert not sel.is_approved(project) and _buttons(ctl, "Approve") and cont.disabled
    _buttons(ctl, "Approve")[0].on_click(None)
    cont.on_click(None)
    assert gone == [6]                       # TTS (stage 6), not straight to the video (8)


def test_choosing_a_card_clears_the_beat_and_withdraws_the_approval(project):
    (project / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "V", "file": "review/clips/V.mp4", "beat": "1:0", "start": 0, "end": 2}]}))
    ctl, page, state, _ = _build(project)
    sel.set_approved(project, True)
    _buttons(ctl, "Thẻ chữ")[0].on_click(None)
    assert sel.load_clips(project) == {}
    assert not sel.is_approved(project)


def test_adding_a_still_goes_through_the_review_sidecar_and_locks_the_beat(project):
    ctl, page, *_ = _build(project)
    picker = next(s for s in page.services if isinstance(s, ft.FilePicker))
    f = MagicMock(path=None, bytes=b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    f.name = "frame.png"
    picker.pick_files = AsyncMock(return_value=[f])
    _buttons(ctl, "Ảnh")[1].on_click(None)             # beat 1:1
    _drain(page)
    stills = sel.load_stills(project)
    assert list(stills) == ["1:1"]
    assert (project / stills["1:1"]).is_file() and stills["1:1"].startswith("review/custom/")
    from stages.stage_5 import shots as sh
    nar = json.loads((project / "narration.json").read_text())
    assert sh._resolve_custom_images(str(project), nar) == {"1:1": str(project / stills["1:1"])}


def test_a_narration_changed_since_the_picks_clears_them_and_says_so(project):
    sel.sync_with_narration(project)
    (project / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "V", "file": "review/clips/V.mp4", "beat": "1:0", "start": 0, "end": 2}]}))
    nar = json.loads((project / "narration.json").read_text())
    nar["scenes"][0]["text"] += " More."
    (project / "narration.json").write_text(json.dumps(nar))
    ctl, *_ = _build(project)
    assert sel.load_clips(project) == {}
    assert any("Kịch bản đã đổi" in t for t in _texts(ctl))


# ─── flet 0.84 (Mac) vs 0.86 (server) hazards ─────────────────────────────────

def test_every_icon_the_screen_uses_exists_in_the_installed_flet():
    tree = ast.parse((ROOT_DIR / "ui" / "screens" / "s_screen_gate.py").read_text())
    missing = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                and node.value.attr == "Icons" and getattr(node.value.value, "id", "") == "ft"):
            if not hasattr(ft.Icons, node.attr):
                missing.append(node.attr)
    assert not missing, missing


def test_spacing_helpers_are_never_called_positionally():
    """ui/_flet_compat re-creates ft.padding/margin.symmetric as KEYWORD-only on flet 0.85+: a
    positional call passes on a 0.84 Mac and raises TypeError on the 0.86 server."""
    bad = []
    for path in sorted((ROOT_DIR / "ui").rglob("*.py")) + sorted((ROOT_DIR / "art_ui").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("symmetric", "only")
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr in ("padding", "margin") and node.args):
                bad.append(f"{path.relative_to(ROOT_DIR)}:{node.lineno}")
    assert not bad, bad


def test_a_screen_project_is_listed_in_the_project_picker(tmp_path, monkeypatch):
    """screen_qa projects have a screen_context.json and no comic_context.json — without this
    they never appear in the picker and the screen above is unreachable."""
    import ui.state as ui_state
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "comic_proj").mkdir()
    (tmp_path / "comic_proj" / "comic_context.json").write_text("{}")
    (tmp_path / "screen_proj").mkdir()
    (tmp_path / "screen_proj" / "screen_context.json").write_text("{}")
    (tmp_path / "junk").mkdir()
    assert ui_state.list_projects() == ["comic_proj", "screen_proj"]


def test_the_comic_review_gate_hands_a_screen_project_to_this_screen(project, monkeypatch):
    import ui.bridge as bridge
    from ui.screens import s_review_gate
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", project.parent)   # bridge binds it by value
    state = AppState(project_name=project.name)
    ctl = s_review_gate.build(_page(), state, on_go=lambda s: None, on_state_change=lambda: None)
    assert any("[1:0]" in t for t in _texts(ctl))                  # the screen's own beat cards
