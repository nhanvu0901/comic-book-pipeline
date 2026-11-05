"""UI step 9 (Mascot): built against a strict fake page with every engine call faked.

The engine modules (stages/mascot/kit|motion|render|timeline|suggest) are implemented by
other work; these tests only pin the screen's behaviour against their documented contract."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest
from PIL import Image

import ui  # noqa: F401 - the repo's flet compat shims
import ui.screens.s9_mascot as s9
from stages.mascot import config as mcfg
from stages.mascot import kit as mkit
from stages.mascot import motion as mmotion
from stages.mascot import render as mrender
from stages.mascot import suggest as msuggest
from stages.mascot import timeline as mtimeline
from tests.ui_test_doubles import StrictFakePage as FakePage
from ui.app import STAGE_BUILDERS
from ui.state import STAGE_NAMES, AppState

ROOT = Path(__file__).resolve().parents[1]
DURATION = 10.0
# the real engine functions, captured before any fixture swaps them for fakes
_REAL = {
    mkit: {n: getattr(mkit, n) for n in ("list_kits", "load_kit", "resolve_pose",
                                         "sprite_metrics", "sprite_png")},
    msuggest: {"suggest_cues": msuggest.suggest_cues},
    mmotion: {n: getattr(mmotion, n) for n in ("build_track", "visible_intervals")},
}


def _walk(control, depth: int = 0):
    if control is None or depth > 80:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in getattr(control, attr, None) or []:
            yield from _walk(child, depth + 1)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content, depth + 1)


def _by_key(root, key: str):
    return next(c for c in _walk(root) if getattr(c, "key", None) == key)


def _button(root, label: str):
    return next(c for c in _walk(root)
                if isinstance(getattr(c, "content", None), str) and c.content == label)


def _texts(root) -> list[str]:
    return [c.value for c in _walk(root) if isinstance(c, ft.Text) and c.value]


def _drain(page: FakePage) -> None:
    while page.tasks:
        (func, *args), _kw = page.tasks.pop(0)
        asyncio.run(func(*args))


def _delta(dx=0.0, dy=0.0):
    return SimpleNamespace(local_delta=SimpleNamespace(x=dx, y=dy))


def _tap(x):
    return SimpleNamespace(local_position=SimpleNamespace(x=x, y=5.0))


def _png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(path)
    return path


def _fake_kit(tmp: Path) -> mkit.Kit:
    f = tmp / "x.png"
    bodies = {n: mkit.BodyPart(n, f, None, (0, 0), 10, 5, n.title()) for n in ("stand", "point")}
    faces = {n: mkit.OverlayPart(n, f, (0, 0), n.title()) for n in ("happy", "shock")}
    accs = {"monocle": mkit.OverlayPart("monocle", f, (0, 0), "Monocle", True)}
    return mkit.Kit("fake_kit", "Fake kit", tmp, (100, 200), bodies, faces, accs, {},
                    "stand", "happy")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    pdir = projects / "demo"
    pdir.mkdir(parents=True)
    (pdir / "final.mp4").write_bytes(b"not really a video")
    monkeypatch.setattr(s9, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(s9, "PREVIEW_DEBOUNCE", 0)
    monkeypatch.setattr(s9, "SAVE_DEBOUNCE", 0)
    # never touch the repo's channel defaults
    monkeypatch.setattr(mcfg, "CHANNEL_DEFAULTS_PATH", tmp_path / "channel_defaults.json")

    kit = _fake_kit(tmp_path)
    calls = SimpleNamespace(frames=[], rendered=[], suggested=0)
    monkeypatch.setattr(mkit, "list_kits", lambda root=None: ["fake_kit"])
    monkeypatch.setattr(mkit, "load_kit", lambda name, root=None: kit)
    monkeypatch.setattr(mkit, "resolve_pose",
                        lambda k, b, f: (b if b in k.bodies else k.default_body,
                                         f if f in k.faces else k.default_face))
    monkeypatch.setattr(mkit, "sprite_metrics",
                        lambda k, body, h: {"w": 100, "h": 200, "feet_x": 50, "feet_y": 180})
    monkeypatch.setattr(
        mkit, "sprite_png",
        lambda k, b, f, acc, *, outline, shadow, height_px, cache_dir:
            _png(Path(cache_dir) / f"{b}-{f}-{'-'.join(acc)}-{outline}-{shadow}-{height_px}.png"))
    monkeypatch.setattr(mrender, "frame_at",
                        lambda p, t, *, width=540: calls.frames.append(t) or _png(
                            Path(p) / "mascot" / "cache" / "frames" / f"{t:.3f}.png"))
    monkeypatch.setattr(mrender, "has_mascot_output", lambda p: False)

    def fake_render_final(p, cfg, *, progress=None, log=None):
        calls.rendered.append(cfg.enabled)
        mcfg.save_config(p, cfg)
        return Path(p) / "final.mp4"
    monkeypatch.setattr(mrender, "render_final", fake_render_final)
    monkeypatch.setattr(mmotion, "build_track", lambda cfg, k, d, w, h: SimpleNamespace())
    monkeypatch.setattr(mmotion, "visible_intervals", lambda track: [(0.0, 5.0)])
    data = {"duration": DURATION, "cuts": [2.0, 6.0], "scenes": [],
            "beats": [{"id": "b1", "function": "HOOK", "name": "hook", "t": 0.2}],
            "words": [{"w": "hi", "t": 0.3, "end": 0.5}]}
    monkeypatch.setattr(mtimeline, "timeline_data", lambda p: data)
    monkeypatch.setattr(
        mtimeline, "snap_time",
        lambda t, d, **k: min(d["cuts"], key=lambda c: abs(c - t)) if
        min(abs(c - t) for c in d["cuts"]) <= 0.25 else t)
    monkeypatch.setattr(mtimeline, "thumb_strip_png",
                        lambda p, w, h: _png(Path(p) / "mascot" / "cache" / "thumbs.png"))
    monkeypatch.setattr(mtimeline, "waveform_png",
                        lambda p, w, h: _png(Path(p) / "mascot" / "cache" / "wave.png"))

    def fake_suggest(p, cfg, k, d=None):
        calls.suggested += 1
        return [mcfg.Cue(t=0.2, move="rise", body="point", face="happy"),
                mcfg.Cue(t=5.0, move="sink")]
    monkeypatch.setattr(msuggest, "suggest_cues", fake_suggest)

    page = FakePage()
    state = AppState(project_name="demo", current_stage=9)
    monkeypatch.setattr(s9, "save_state", lambda _s: None)
    return SimpleNamespace(page=page, state=state, pdir=pdir, calls=calls, kit=kit,
                           went=[], changed=[])


def _build(env, *, drain=True):
    root = s9.build(env.page, env.state, on_go=env.went.append,
                    on_state_change=lambda: env.changed.append(1))
    if drain:
        _drain(env.page)
    return root


def _saved(env) -> dict:
    return json.loads((env.pdir / "mascot.json").read_text(encoding="utf-8"))


def _cue(d: dict, move: str) -> dict:
    return next(c for c in d["cues"] if c["move"] == move)


# ── registration / labels ────────────────────────────────────────────────────────────────
def test_stage_9_is_registered():
    assert STAGE_NAMES[9] == "Mascot"
    assert STAGE_BUILDERS[9] is s9.build


def test_step_labels_follow_the_stage_count():
    for path in (ROOT / "ui" / "screens").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"STEP \d OF \d", text), path.name
    assert len(STAGE_NAMES) == 9


def test_mark_dirty_cascades_through_stage_9():
    state = AppState(project_name="p")
    state.approved["9"] = True
    state.mark_dirty(8)
    assert state.is_dirty(9)


def test_screen_header_says_of_9(env):
    root = _build(env)
    assert "STEP 9 OF 9" in _texts(root)


# ── states ───────────────────────────────────────────────────────────────────────────────
def test_missing_final_shows_message_and_a_way_back(env):
    (env.pdir / "final.mp4").unlink()
    root = _build(env)
    assert "Chưa có final.mp4 — hãy render ở bước 8 trước" in _texts(root)
    _button(root, "Về bước 8").on_click(None)
    assert env.went == [8]


def test_clean_copy_alone_is_enough(env):
    (env.pdir / "final.mp4").unlink()
    (env.pdir / "mascot").mkdir()
    (env.pdir / "mascot" / "final_clean.mp4").write_bytes(b"x")
    root = _build(env)
    assert "Chưa có final.mp4 — hãy render ở bước 8 trước" not in _texts(root)
    _by_key(root, "sprite-active")


def test_screen_builds_and_first_open_suggests_and_saves(env):
    root = _build(env)
    assert env.calls.suggested == 1
    assert len(_saved(env)["cues"]) == 2
    sprite = _by_key(root, "sprite-active")
    assert sprite.visible
    # the frame came from the (fake) engine, off the UI thread
    assert env.calls.frames


def test_second_open_does_not_suggest_again(env):
    _build(env)
    _build(env)
    assert env.calls.suggested == 1


def test_an_engine_failure_is_logged_not_fatal(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(mrender, "frame_at", boom)
    root = _build(env)
    _by_key(root, "sprite-active")          # still there


# ── drag ─────────────────────────────────────────────────────────────────────────────────
def test_drag_end_snaps_and_saves_the_global_position(env):
    root = _build(env)
    _by_key(root, "apply-all").value = True
    sprite = _by_key(root, "sprite-active")
    sprite.on_pan_start(SimpleNamespace())
    sprite.on_pan_update(_delta(0.61 * s9.PREVIEW_W, -0.01 * s9.PREVIEW_H))
    sprite.on_pan_end(SimpleNamespace())
    pos = _saved(env)["position"]
    assert pos == {"preset": "bottom_right", "x": 0.8, "y": 1.0}
    assert all(c.get("position") is None for c in _saved(env)["cues"])


def test_drag_away_from_presets_is_custom(env):
    root = _build(env)
    _by_key(root, "apply-all").value = True
    sprite = _by_key(root, "sprite-active")
    sprite.on_pan_start(SimpleNamespace())
    sprite.on_pan_update(_delta(0.1 * s9.PREVIEW_W, -0.3 * s9.PREVIEW_H))
    sprite.on_pan_end(SimpleNamespace())
    pos = _saved(env)["position"]
    assert pos["preset"] == "custom"
    assert pos["x"] == pytest.approx(0.3, abs=1e-3)
    assert pos["y"] == pytest.approx(0.7, abs=1e-3)


def test_drag_edits_the_selected_cue_and_locks_it(env):
    root = _build(env)                      # "Áp cho mọi cue" is off; the first cue is selected
    before = _saved(env)["position"]
    sprite = _by_key(root, "sprite-active")
    sprite.on_pan_start(SimpleNamespace())
    sprite.on_pan_update(_delta(0.3 * s9.PREVIEW_W, 0.0))
    sprite.on_pan_end(SimpleNamespace())
    saved = _saved(env)
    assert saved["position"] == before
    rise = _cue(saved, "rise")
    assert rise["position"] == {"preset": "bottom_center", "x": 0.5, "y": 1.0}
    assert rise["locked"] is True
    assert _cue(saved, "sink")["locked"] is False


def test_snap_position_rules():
    assert s9.snap_position(0.21, 0.985) == mcfg.Position("bottom_left", 0.2, 1.0)
    assert s9.snap_position(0.21, 0.9).preset == "custom"          # x snapped, feet off the edge
    assert s9.snap_position(0.35, 1.0) == mcfg.Position("custom", 0.35, 1.0)


def test_event_helpers_accept_old_and_new_flet_names():
    assert s9._event_delta(_delta(3, 4)) == (3, 4)
    assert s9._event_delta(SimpleNamespace(delta_x=1, delta_y=2)) == (1, 2)
    assert s9._event_delta(SimpleNamespace(primary_delta=5)) == (5, 0)
    assert s9._event_pos(_tap(7)) == (7, 5)
    assert s9._event_pos(SimpleNamespace(local_x=1, local_y=2)) == (1, 2)


def test_resize_handle_clamps_and_saves(env):
    root = _build(env)
    h = _by_key(root, "sprite-handle")
    h.on_pan_start(SimpleNamespace())
    h.on_pan_update(_delta(5000, -5000))
    h.on_pan_end(SimpleNamespace())
    assert _saved(env)["size_pct"] == mcfg.SIZE_PCT_MAX


# ── timeline ─────────────────────────────────────────────────────────────────────────────
def test_adding_a_cue_inherits_the_pose_and_picks_the_move(env):
    root = _build(env)
    _by_key(root, "timeline-detector").on_tap_down(_tap(3.0 / DURATION * s9.TL_W))
    _button(root, "+ Cue tại đây").on_click(None)
    cues = _saved(env)["cues"]
    new = next(c for c in cues if abs(c["t"] - 3.0) < 0.01)
    assert new["move"] == "hold"                       # visible at 3 s
    assert "body" not in new and "face" not in new     # None = keep the previous cue's pose
    assert new["locked"] is True
    # ...and the effective pose really is the previous cue's
    cfg = mcfg.load_config(env.pdir)
    plan = s9.preview_plan(cfg, env.kit, 3.0)
    assert (plan["active"]["body"], plan["active"]["face"]) == ("point", "happy")

    _by_key(root, "timeline-detector").on_tap_down(_tap(7.0 / DURATION * s9.TL_W))
    _button(root, "+ Cue tại đây").on_click(None)
    late = next(c for c in _saved(env)["cues"] if abs(c["t"] - 7.0) < 0.01)
    assert late["move"] == "rise"                      # hidden at 7 s


def test_double_tap_adds_a_cue(env):
    root = _build(env)
    det = _by_key(root, "timeline-detector")
    det.on_tap_down(_tap(4.0 / DURATION * s9.TL_W))
    det.on_double_tap(SimpleNamespace())
    assert any(abs(c["t"] - 4.0) < 0.01 for c in _saved(env)["cues"])


def _drag_chip(root, cue_id: str, to_t: float, from_t: float):
    chip = _by_key(root, f"chip-{cue_id}")
    chip.on_horizontal_drag_start(SimpleNamespace())
    chip.on_horizontal_drag_update(_delta((to_t - from_t) / DURATION * s9.TL_W, 0))
    chip.on_horizontal_drag_end(SimpleNamespace())


def test_retiming_a_chip_snaps_and_locks(env):
    root = _build(env)
    sink = _cue(_saved(env), "sink")
    assert sink["locked"] is False
    _drag_chip(root, sink["id"], 2.1, 5.0)
    moved = _cue(_saved(env), "sink")
    assert moved["t"] == 2.0                           # snapped to the cut at 2.0 s
    assert moved["locked"] is True


def test_retiming_without_snap_keeps_the_exact_time(env):
    root = _build(env)
    _by_key(root, "snap-cb").value = False
    sink = _cue(_saved(env), "sink")
    _drag_chip(root, sink["id"], 2.1, 5.0)
    assert _cue(_saved(env), "sink")["t"] == pytest.approx(2.1, abs=1e-3)


def test_manual_cue_edits_lock_the_cue(env):
    root = _build(env)
    sink = _cue(_saved(env), "sink")
    _by_key(root, f"chip-{sink['id']}").on_tap(SimpleNamespace())      # select it
    move = _by_key(root, "cue-move")
    move.value = "slide_out"
    move.on_select(SimpleNamespace())
    edited = next(c for c in _saved(env)["cues"] if c["id"] == sink["id"])
    assert edited["move"] == "slide_out" and edited["locked"] is True


def test_delete_cue(env):
    root = _build(env)
    sink = _cue(_saved(env), "sink")
    _by_key(root, f"chip-{sink['id']}").on_tap(SimpleNamespace())
    _button(root, "Xoá cue").on_click(None)
    assert [c["move"] for c in _saved(env)["cues"]] == ["rise"]


def test_clear_suggestions_keeps_only_locked_cues(env):
    root = _build(env)
    sink = _cue(_saved(env), "sink")
    _drag_chip(root, sink["id"], 5.5, 5.0)             # locks the sink cue
    _button(root, "Xoá gợi ý").on_click(None)
    assert [c["move"] for c in _saved(env)["cues"]] == ["sink"]


# ── actions ──────────────────────────────────────────────────────────────────────────────
def test_render_final_approves_stage_9(env):
    root = _build(env)
    _button(root, "Render final.mp4").on_click(None)
    _drain(env.page)
    assert env.calls.rendered == [True]
    assert env.state.is_approved(9)
    assert env.changed


def test_disable_mascot_renders_with_enabled_false(env):
    root = _build(env)
    _button(root, "Tắt mascot (khôi phục bản sạch)").on_click(None)
    _drain(env.page)
    assert env.calls.rendered == [False]
    assert _saved(env)["enabled"] is False
    assert not env.state.is_approved(9)


def test_a_failed_render_is_shown_and_the_screen_survives(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(mrender, "render_final", boom)
    root = _build(env)
    _button(root, "Render final.mp4").on_click(None)
    _drain(env.page)
    assert "Render lỗi" in _by_key(root, "mascot-status").value
    assert not env.state.is_approved(9)


def test_size_slider_is_debounced_and_saved(env):
    root = _build(env)
    slider = _by_key(root, "cfg-size")
    slider.value = 31.0
    slider.on_change(SimpleNamespace())
    _drain(env.page)
    assert _saved(env)["size_pct"] == 31.0


def test_save_channel_defaults_writes_no_cues(env, tmp_path):
    root = _build(env)
    _button(root, "Lưu làm mặc định cho kênh").on_click(None)
    data = json.loads((tmp_path / "channel_defaults.json").read_text(encoding="utf-8"))
    assert "cues" not in data


# ── follow-ups from the live web test ────────────────────────────────────────────────────
def test_exit_cue_ghost_uses_the_position_before_it(env):
    cfg = mcfg.MascotConfig(position=mcfg.Position("bottom_left", 0.2, 1.0))
    cfg.cues = [mcfg.Cue(t=1.0, move="rise", position=mcfg.Position("custom", 0.7, 0.9)),
                mcfg.Cue(t=4.0, move="sink"),
                mcfg.Cue(t=6.0, move="rise")]
    plan = s9.preview_plan(cfg, env.kit, 2.0)             # active = rise, ghost = sink
    sink_ghost = plan["ghosts"][0]
    assert (sink_ghost["x"], sink_ghost["y"]) == (0.7, 0.9)   # not the global 0.2 / 1.0
    plan = s9.preview_plan(cfg, env.kit, 4.5)             # the active cue is the exit itself
    assert (plan["active"]["x"], plan["active"]["y"]) == (0.7, 0.9)


def test_close_cues_get_separate_chip_rows(env):
    root = _build(env)
    for t in (5.2, 5.3):                           # with the sink at 5 s: 3 cues inside one chip width
        _by_key(root, "timeline-detector").on_tap_down(_tap(t / DURATION * s9.TL_W))
        _button(root, "+ Cue tại đây").on_click(None)
    chips = [c for c in _walk(root) if str(getattr(c, "key", "")).startswith("chip-")]
    by_row = {}
    for c in chips:
        by_row.setdefault(c.top, []).append(c.left)
    assert len(by_row) >= 3
    for lefts in by_row.values():
        lefts.sort()
        assert all(b - a >= s9.CHIP_W for a, b in zip(lefts, lefts[1:]))


def test_a_closed_session_is_ignored(env):
    root = _build(env)

    def dead(*a, **k):
        raise AttributeError("'NoneType' object has no attribute 'loop'")
    env.page.run_task = dead
    slider = _by_key(root, "cfg-size")
    slider.on_change(SimpleNamespace())            # schedules a save + preview: must not raise
    _by_key(root, "timeline-detector").on_tap_down(_tap(100))


def test_layout_constants_fit_a_900px_window():
    assert (s9.PREVIEW_W, s9.PREVIEW_H) == (315, 560)


def test_the_screen_works_with_the_real_engine(env, monkeypatch):
    """Real kit, sprites, motion and suggester; only the ffmpeg-backed calls stay faked."""
    for mod, names in _REAL.items():
        for n, fn in names.items():
            monkeypatch.setattr(mod, n, fn)
    monkeypatch.setattr(mrender, "frame_at", lambda p, t, *, width=540: _png(
        Path(p) / "mascot" / "cache" / "frames" / f"{t:.3f}.png"))
    monkeypatch.setattr(mtimeline, "timeline_data", lambda p: {
        "duration": 20.0, "cuts": [4.0, 9.0], "scenes": [],
        "beats": [{"id": "b", "function": "HOOK", "name": "h", "t": 0.5}], "words": []})
    monkeypatch.setattr(mtimeline, "thumb_strip_png", lambda p, w, h: _png(
        Path(p) / "mascot" / "cache" / "t.png"))
    monkeypatch.setattr(mtimeline, "waveform_png", lambda p, w, h: _png(
        Path(p) / "mascot" / "cache" / "w.png"))
    root = _build(env)
    assert _by_key(root, "sprite-active").visible
    assert _saved(env)["cues"]
    assert (env.pdir / "mascot" / "cache" / "sprites").glob("*.png")
