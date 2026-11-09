"""Tests for Deliverable 2 (< 35s Fast-Short parameter) and Deliverable 3 (comic_vs_show mode)."""
import json
from pathlib import Path
import pytest
import flet as ft

import ui  # noqa: F401
import stages.stage_3.gemini_prompt as gp
from stages.stage_3.comic_vs_show import COMIC_VS_SHOW_MODE, is_comic_vs_show, BLUE_OCEAN_FRANCHISES
from stages.stage_3.explore_answer import _exp_band
from stages.stage_3.micro_moment import (
    _MICRO_WORDS_MIN,
    _MICRO_WORDS_MAX,
    _MICRO_SHORT_35S_WORDS_MIN,
    _MICRO_SHORT_35S_WORDS_MAX,
    _validate_micro_scenes,
)
from stages.stage_3.spoken_rules import SPOKEN_TEXT_RULES
from stages.stage_3.schema import Beat
from ui.state import AppState
from tests.ui_test_doubles import StrictFakePage as FakePage
from stages.research_scout.models import ResearchSession, SessionState, ScoutMode


class _FakeControl:
    def __init__(self, value):
        self.value = value


class _FakeEvent:
    def __init__(self, value):
        self.control = _FakeControl(value)


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


def _find_by_key(control, key):
    for node in _walk(control):
        if getattr(node, "key", None) == key:
            return node
    return None


def _find_counter(control):
    for node in _walk(control):
        if isinstance(node, ft.Text) and "word" in str(node.value):
            return node
    return None


def test_app_state_defaults():
    state = AppState()
    assert hasattr(state, "short_35s")
    assert state.short_35s is False
    assert hasattr(state, "comic_vs_show")
    assert state.comic_vs_show is False


def test_is_short_35s_active():
    assert gp.is_short_35s_active({}, {}) is False
    assert gp.is_short_35s_active({}, {}, short_35s=True) is True
    assert gp.is_short_35s_active({"short_35s": True}, {}) is True
    assert gp.is_short_35s_active({}, {"short_35s": True}) is True
    # Explicit argument takes precedence
    assert gp.is_short_35s_active({"short_35s": True}, {}, short_35s=False) is False


def test_is_comic_vs_show():
    assert is_comic_vs_show({}, {}) is False
    assert is_comic_vs_show({}, {}, mode_override="comic_vs_show") is True
    assert is_comic_vs_show({"comic_vs_show": True}, {}, mode_override="micro_moment") is False
    assert is_comic_vs_show({"comic_vs_show": True}, {}) is True
    assert is_comic_vs_show({"comic_vs_show": False}, {}) is False
    # Explicit false in comic_ctx takes precedence over stale pipeline_mode
    assert is_comic_vs_show({"comic_vs_show": False, "pipeline_mode": "comic_vs_show"}, {}) is False
    assert is_comic_vs_show({}, {"comic_vs_show": False, "pipeline_mode": "comic_vs_show"}) is False
    assert is_comic_vs_show({"pipeline_mode": "comic_vs_show"}, {}) is True
    assert is_comic_vs_show({}, {"comic_vs_show": True}) is True
    assert "The Boys" in BLUE_OCEAN_FRANCHISES
    assert "Invincible" in BLUE_OCEAN_FRANCHISES
    assert "MCU" in BLUE_OCEAN_FRANCHISES
    assert "DCU" in BLUE_OCEAN_FRANCHISES


def test_exp_band_short_35s():
    normal_1 = _exp_band(1, short_35s=False)
    short_1 = _exp_band(1, short_35s=True)
    assert normal_1 == (84, 84)
    assert short_1 == (60, 75)

    short_3 = _exp_band(3, short_35s=True)
    assert short_3 == (60, 84)

    short_4 = _exp_band(4, short_35s=True)
    assert short_4 == (80, 95)


def test_micro_moment_validator_short_35s():
    fake_beats = [Beat(id=1, function="SETUP", name="setup", characters_active=["Peter Parker"])]

    # 70 words is too short for 80-105w band
    short_text = "Peter Parker is trapped under heavy steel. " + " ".join(["word"] * 63)
    scenes_short = [{"text": short_text, "panel": "p1.png"}]
    issues = _validate_micro_scenes(
        "Peter Parker faces his doom.",
        scenes_short,
        fake_beats,
        words_min=_MICRO_SHORT_35S_WORDS_MIN,
        words_max=_MICRO_SHORT_35S_WORDS_MAX,
    )
    assert any(f"outside {_MICRO_SHORT_35S_WORDS_MIN}-{_MICRO_SHORT_35S_WORDS_MAX}" in i for i in issues)

    # 110 words is too long for 80-105w band
    long_text = "Peter Parker is trapped under heavy steel. " + " ".join(["word"] * 105)
    scenes_long = [{"text": long_text, "panel": "p1.png"}]
    issues = _validate_micro_scenes(
        "Peter Parker faces his doom.",
        scenes_long,
        fake_beats,
        words_min=_MICRO_SHORT_35S_WORDS_MIN,
        words_max=_MICRO_SHORT_35S_WORDS_MAX,
    )
    assert any(f"outside {_MICRO_SHORT_35S_WORDS_MIN}-{_MICRO_SHORT_35S_WORDS_MAX}" in i for i in issues)

    # 90 words is within 80-105w band
    good_text = "Peter Parker is trapped under heavy steel. " + " ".join(["word"] * 85)
    scenes_good = [{"text": good_text, "panel": "p1.png"}]
    issues = _validate_micro_scenes(
        "Peter Parker faces his doom.",
        scenes_good,
        fake_beats,
        words_min=_MICRO_SHORT_35S_WORDS_MIN,
        words_max=_MICRO_SHORT_35S_WORDS_MAX,
    )
    assert not any("micro band" in i for i in issues)


def test_generate_gemini_prompt_short_35s(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "test_short"
    proj_dir.mkdir(parents=True)

    comic_ctx = {
        "title": "Invincible #1",
        "pipeline_mode": "micro_moment",
        "characters": ["Invincible", "Omni-Man"],
        "target_moment": "Omni-Man betrays the Guardians of the Globe.",
        "short_35s": True,
    }
    (proj_dir / "comic_context.json").write_text(json.dumps(comic_ctx, indent=2), encoding="utf-8")

    prompt, _ = gp.generate_gemini_writer_prompt("test_short")
    assert gp.FAST_SHORT_35S_OVERRIDE_BLOCK in prompt
    assert "80–105 words" in prompt
    assert "< 35 SECONDS" in prompt


def test_generate_gemini_prompt_comic_vs_show(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "test_cvs"
    proj_dir.mkdir(parents=True)

    comic_ctx = {
        "title": "The Boys #19",
        "franchise": "The Boys",
        "characters": ["Homelander", "Black Noir"],
        "comic_vs_show": True,
        "show_adaptation": {
            "title": "The Boys Season 3",
            "show_version": "Soldier Boy is revealed to be Homelander's biological father.",
            "comic_version": "Black Noir was a secret clone of Homelander engineered to kill him.",
            "divergence_summary": "The show replaced the clone plot with Soldier Boy's lineage.",
        },
    }
    (proj_dir / "comic_context.json").write_text(json.dumps(comic_ctx, indent=2), encoding="utf-8")

    prompt, path = gp.generate_gemini_writer_prompt("test_cvs")
    assert path.name == "test_cvs_writer_prompt.md"
    assert "GEMINI PROMPT — COMIC VS SHOW / MOVIE COMPARISON WRITER" in prompt
    assert "The Boys" in prompt
    assert "Soldier Boy is revealed to be Homelander's biological father." in prompt
    assert "Black Noir was a secret clone of Homelander engineered to kill him." in prompt
    assert SPOKEN_TEXT_RULES in prompt
    # No fast-short override by default
    assert gp.FAST_SHORT_35S_OVERRIDE_BLOCK not in prompt

    # Now enable short_35s as well
    prompt_short, _ = gp.generate_gemini_writer_prompt("test_cvs", short_35s=True)
    assert gp.FAST_SHORT_35S_OVERRIDE_BLOCK in prompt_short
    assert "GEMINI PROMPT — COMIC VS SHOW / MOVIE COMPARISON WRITER" in prompt_short


def test_ui_controls_presence(tmp_path, monkeypatch):
    import ui.screens.s1_research_scout as s1_scout
    import ui.screens.s1_identify as s1_ident
    import ui.screens.s3_narrate as s3_narr

    page = FakePage()
    state = AppState()

    # 1. s1_identify has both controls
    c_ident = s1_ident.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    assert _find_by_key(c_ident, "short-35s-identify") is not None
    assert _find_by_key(c_ident, "comic-vs-show-identify") is not None

    # 2. s3_narrate has both controls
    c_narr = s3_narr.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    assert _find_by_key(c_narr, "short-35s-narrate") is not None
    assert _find_by_key(c_narr, "comic-vs-show-narrate") is not None

    # 3. s1_research_scout has controls rendered in production gates bubble
    monkeypatch.setattr(s1_scout, "RESEARCH_SESSIONS_ROOT", tmp_path / "sessions")
    session = ResearchSession(
        id="test-session",
        mode=ScoutMode.QA,
        user_intent="test intent",
        state=SessionState.PRODUCTION_GATES,
    )
    from stages.research_scout.storage import SessionStore
    store = SessionStore(s1_scout.RESEARCH_SESSIONS_ROOT)
    store.save(session)
    state.scout_session_id = session.id

    c_scout = s1_scout.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    assert _find_by_key(c_scout, "short-35s") is not None
    assert _find_by_key(c_scout, "comic-vs-show") is not None


def test_interactive_ui_toggle_narrate(tmp_path, monkeypatch):
    import ui.screens.s3_narrate as s3_narr
    import ui.state as ui_state
    monkeypatch.setattr(s3_narr, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)

    proj_name = "test_narrate_toggle"
    proj_dir = tmp_path / proj_name
    proj_dir.mkdir(parents=True)
    comic_ctx = {
        "title": "Invincible #1",
        "characters": ["Invincible", "Omni-Man"],
        "pipeline_mode": "micro_moment",
        "short_35s": False,
        "comic_vs_show": False,
    }
    ctx_path = proj_dir / "comic_context.json"
    ctx_path.write_text(json.dumps(comic_ctx, indent=2), encoding="utf-8")

    page = FakePage()
    state = AppState(project_name=proj_name)

    control = s3_narr.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    short_cb = _find_by_key(control, "short-35s-narrate")
    cvs_cb = _find_by_key(control, "comic-vs-show-narrate")
    counter = _find_counter(control)
    assert counter is not None

    # 1. Toggle short_35s ON
    short_cb.on_change(_FakeEvent(True))
    assert state.short_35s is True
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["short_35s"] is True
    assert "(<35s mode)" in counter.value
    prompt, _ = gp.generate_gemini_writer_prompt(proj_name)
    assert gp.FAST_SHORT_35S_OVERRIDE_BLOCK in prompt

    # 2. Toggle short_35s OFF
    short_cb.on_change(_FakeEvent(False))
    assert state.short_35s is False
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["short_35s"] is False
    assert "(<35s mode)" not in counter.value
    prompt, _ = gp.generate_gemini_writer_prompt(proj_name)
    assert gp.FAST_SHORT_35S_OVERRIDE_BLOCK not in prompt

    # 3. Toggle comic_vs_show ON
    cvs_cb.on_change(_FakeEvent(True))
    assert state.comic_vs_show is True
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["comic_vs_show"] is True
    prompt, _ = gp.generate_gemini_writer_prompt(proj_name)
    assert "GEMINI PROMPT — COMIC VS SHOW / MOVIE COMPARISON WRITER" in prompt

    # 4. Toggle comic_vs_show OFF
    cvs_cb.on_change(_FakeEvent(False))
    assert state.comic_vs_show is False
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["comic_vs_show"] is False
    prompt, _ = gp.generate_gemini_writer_prompt(proj_name)
    assert "GEMINI PROMPT — COMIC VS SHOW / MOVIE COMPARISON WRITER" not in prompt


def test_interactive_ui_toggle_identify(tmp_path, monkeypatch):
    import ui.screens.s1_identify as s1_ident
    monkeypatch.setattr(s1_ident, "PROJECTS_ROOT", tmp_path)

    proj_name = "test_ident_toggle"
    proj_dir = tmp_path / proj_name
    proj_dir.mkdir(parents=True)
    comic_ctx = {
        "title": "Invincible #1",
        "pipeline_mode": "micro_moment",
        "short_35s": False,
        "comic_vs_show": False,
    }
    ctx_path = proj_dir / "comic_context.json"
    ctx_path.write_text(json.dumps(comic_ctx, indent=2), encoding="utf-8")

    page = FakePage()
    state = AppState(project_name=proj_name)

    control = s1_ident.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    short_cb = _find_by_key(control, "short-35s-identify")
    cvs_cb = _find_by_key(control, "comic-vs-show-identify")

    # Toggle short_35s ON and OFF
    short_cb.on_change(_FakeEvent(True))
    assert state.short_35s is True
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["short_35s"] is True

    short_cb.on_change(_FakeEvent(False))
    assert state.short_35s is False
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["short_35s"] is False

    # Toggle comic_vs_show ON and OFF
    cvs_cb.on_change(_FakeEvent(True))
    assert state.comic_vs_show is True
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["comic_vs_show"] is True

    cvs_cb.on_change(_FakeEvent(False))
    assert state.comic_vs_show is False
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["comic_vs_show"] is False


def test_interactive_ui_toggle_research_scout(tmp_path, monkeypatch):
    import ui.screens.s1_research_scout as s1_scout
    monkeypatch.setattr(s1_scout, "RESEARCH_SESSIONS_ROOT", tmp_path / "sessions")
    session = ResearchSession(
        id="test-session-toggle",
        mode=ScoutMode.QA,
        user_intent="test intent",
        state=SessionState.PRODUCTION_GATES,
    )
    from stages.research_scout.storage import SessionStore
    store = SessionStore(s1_scout.RESEARCH_SESSIONS_ROOT)
    store.save(session)

    page = FakePage()
    state = AppState(scout_session_id=session.id)

    control = s1_scout.build(page, state, on_go=lambda _: None, on_state_change=lambda: None)
    short_cb = _find_by_key(control, "short-35s")
    cvs_cb = _find_by_key(control, "comic-vs-show")

    short_cb.on_change(_FakeEvent(True))
    assert state.short_35s is True

    short_cb.on_change(_FakeEvent(False))
    assert state.short_35s is False

    cvs_cb.on_change(_FakeEvent(True))
    assert state.comic_vs_show is True

    cvs_cb.on_change(_FakeEvent(False))
    assert state.comic_vs_show is False


def test_full_9_stage_ui_boot_and_prompt_dialog_with_new_modes(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import MagicMock
    import ui.app as app
    import ui.screens.s3_narrate as s3_narr
    import ui.state as ui_state

    monkeypatch.setattr(s3_narr, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)

    proj_name = "test_full_boot"
    proj_dir = tmp_path / proj_name
    proj_dir.mkdir(parents=True)
    (proj_dir / "comic_context.json").write_text(
        json.dumps(
            {
                "title": "The Boys #40",
                "characters": ["Billy Butcher", "Homelander"],
                "pipeline_mode": "micro_moment",
                "target_moment": "Billy Butcher confronts Homelander in the Oval Office.",
                "short_35s": True,
                "comic_vs_show": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    page = FakePage()
    page.window = MagicMock()
    page.services = []
    page.overlay = []
    asyncio.run(app.main(page))
    assert page.views

    state = AppState(
        project_name=proj_name,
        current_stage=2,
        short_35s=True,
        comic_vs_show=True,
    )
    for stage_num, builder in app.STAGE_BUILDERS.items():
        ctl = builder(page, state, on_go=lambda _s: None, on_state_change=lambda: None)
        assert ctl is not None, f"Stage {stage_num} failed to build"
