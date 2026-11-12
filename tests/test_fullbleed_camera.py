"""Full-bleed camera, clip auto-fill and the opening-shot rule (Stage 5, 2026-11-12).

Measured gap (vmetrics, 8 of our Shorts vs 10 competitors): apparent motion 5.2 vs 30.9 % of the
frame width per second, frozen share 0.13 vs 0.00, padded seconds 0.30 vs ≤0.095, and the newest
videos opened on a letterboxed film clip. These tests pin the GENERAL rules that close it:
  • every still moves visibly for its whole duration (never a frozen stretch);
  • a landscape panel becomes a full-height pan instead of a strip over blur;
  • blur stays only as the fallback for extreme strips / too-small art;
  • the main figure never leaves the frame; direction alternates between shots;
  • a clip with no manual crop fills the frame around its most active region (manual crop wins;
    a source too small to fill keeps the contain fit);
  • the first shot of the video fills the frame, or the best full-bleed alternative opens it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from stages.stage_5 import camera as cam
from stages.stage_5 import clips, shots
from stages.stage_5.schema import Shot

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")

W, H = shots.OUTPUT_W, shots.OUTPUT_H


def _win_rect(win: cam.Window) -> tuple[float, float, float, float]:
    h = win.w * H / W
    return win.cx - win.w / 2, win.cy - h / 2, win.cx + win.w / 2, win.cy + h / 2


# ── motion is never zero ────────────────────────────────────────────────────────────────────

SHAPES = [(3413, 1920), (1900, 800), (1080, 1920), (1500, 1500), (1080, 4000), (700, 2600)]


@pytest.mark.parametrize("iw,ih", SHAPES)
@pytest.mark.parametrize("dur", [0.6, 1.5, 3.0, 6.0])
def test_every_still_moves_for_its_whole_duration(iw, ih, dur):
    """No frame pair is near-static (vmetrics counts < 1 %W/s as frozen) — the old smoothstep
    push stalled at both ends and a 1.00→1.06 push over 4 s measured as zero."""
    plan = cam.plan_camera(iw, ih, dur, max_zoom=1.6)
    speeds = cam.frame_speeds(plan, max(2, int(round(dur * 30))), 30)
    assert min(speeds) > 3.0, (plan, min(speeds))
    assert plan.speed >= 0.6 * cam.CAMERA_SPEED_MIN


@pytest.mark.parametrize("iw,ih", [(3413, 1920), (1900, 800), (1080, 1920), (1500, 1500)])
def test_speed_stays_in_the_competitor_band(iw, ih):
    plan = cam.plan_camera(iw, ih, 2.5, max_zoom=1.6)
    assert cam.CAMERA_SPEED_MIN - 1e-6 <= plan.speed <= cam.CAMERA_SPEED_MAX + 1e-6


def test_ease_keeps_velocity_at_both_ends():
    for ease in ("inout", "out", "in"):
        v0 = cam.ease_value(0.01, ease) / 0.01
        v1 = (1 - cam.ease_value(0.99, ease)) / 0.01
        assert v0 >= 0.55 and v1 >= 0.55, (ease, v0, v1)


# ── landscape → full-bleed pan; blur only as the fallback ───────────────────────────────────

def test_landscape_panel_is_a_full_height_horizontal_pan():
    iw, ih = 1920, 1080                        # 16:9 — the old renderer letterboxed this (≥1.7)
    assert cam.frame_mode(iw, ih)[0] == "fullbleed"
    plan = cam.plan_camera(iw, ih, 2.5, max_zoom=2.0)
    assert plan.axis == "x"
    # zoom-1 window is the full art height: the frame is filled edge to edge
    assert plan.start.w * H / W == pytest.approx(ih, rel=1e-6)
    assert abs(plan.end.cx - plan.start.cx) > 0.25 * plan.start.w   # it really travels
    for t in np.linspace(0, 1, 11):
        x0, y0, x1, y1 = _win_rect(cam.window_at(plan, t))
        assert x0 >= -1 and y0 >= -1 and x1 <= iw + 1 and y1 <= ih + 1   # never off the art


def test_tall_panel_travels_vertically():
    plan = cam.plan_camera(800, 3000, 2.5, max_zoom=2.0)
    assert plan.axis == "y" and abs(plan.end.cy - plan.start.cy) > 0


@pytest.mark.parametrize("iw,ih,why", [(4000, 900, "aspect"), (300, 2000, "aspect"), (300, 400, "fill")])
def test_blur_fallback_only_for_extreme_strips_and_tiny_art(iw, ih, why):
    mode, reason = cam.frame_mode(iw, ih)
    assert mode == "blur" and why in reason


def test_critical_text_keeps_contain_only_where_the_old_renderer_did():
    assert cam.frame_mode(2000, 1000, keep_contain=True)[0] == "blur"       # wide: whole panel
    assert cam.frame_mode(1200, 1000, keep_contain=True)[0] == "fullbleed"  # old path cropped too


def test_will_letterbox_is_the_old_rule_when_the_camera_is_off(monkeypatch):
    monkeypatch.setattr(cam, "FULLBLEED_MOTION", False)
    assert cam.will_letterbox(1920, 1080) is True           # aspect 1.78 ≥ 1.7
    assert cam.will_letterbox(1300, 1000) is False
    monkeypatch.setattr(cam, "FULLBLEED_MOTION", True)
    assert cam.will_letterbox(1920, 1080) is False


def test_blur_fallback_frame_still_moves(tmp_path):
    src = tmp_path / "strip.png"
    Image.new("RGB", (4400, 1000), (0, 0, 255)).save(src)
    art, plan, mode, _ = cam.prepare_plan(src, 2.5, shot_id=3)
    assert mode == "blur"
    with Image.open(art) as im:
        assert im.size == (W, H)
    assert plan.speed > 5 and plan.end.w < plan.start.w     # pushes into the sharp strip


# ── subject, direction, opening and loop seam ───────────────────────────────────────────────

def test_main_figure_never_leaves_the_frame():
    subject = (1300.0, 300.0, 1800.0, 1900.0)    # a figure narrower than the window
    for d in (1, -1):
        plan = cam.plan_camera(3413, 1920, 3.0, subject=subject, direction=d, max_zoom=1.6)
        for t in np.linspace(0, 1, 21):
            x0, y0, x1, y1 = _win_rect(cam.window_at(plan, t))
            assert x0 <= subject[0] + 1 and x1 >= subject[2] - 1, (d, t)


def test_a_figure_wider_than_the_window_keeps_filling_it():
    subject = (500.0, 0.0, 2600.0, 1920.0)
    plan = cam.plan_camera(3413, 1920, 3.0, subject=subject, max_zoom=1.6)
    for t in np.linspace(0, 1, 21):
        x0, _, x1, _ = _win_rect(cam.window_at(plan, t))
        assert x0 >= subject[0] - 1 and x1 <= subject[2] + 1


def test_main_subject_prefers_the_biggest_character_box():
    boxes = [{"x": 10, "y": 10, "w": 40, "h": 40},              # a lone hand
             {"x": 400, "y": 100, "w": 300, "h": 800}]          # the figure
    assert cam.main_subject(1500, 1000, boxes) == (400.0, 100.0, 700.0, 900.0)


def test_direction_alternates_between_consecutive_shots(tmp_path):
    src = tmp_path / "wide.png"
    Image.new("RGB", (3200, 1800), (120, 120, 120)).save(src)
    moves = []
    for sid in (4, 5, 6):
        _, plan, _, _ = cam.prepare_plan(src, 2.5, shot_id=sid)
        moves.append(np.sign(plan.end.cx - plan.start.cx))
    assert moves[0] == -moves[1] == moves[2] != 0


def test_intro_starts_on_the_rest_framing_and_moves_fast():
    rest = (1700.0, 960.0)
    a = cam.plan_camera(3413, 1920, 1.2, rest=rest, intro=True, max_zoom=1.6)
    b = cam.plan_camera(3413, 1920, 4.0, rest=rest, intro=True, max_zoom=1.6)
    assert a.start == b.start, "the opening framing must not depend on the shot length"
    assert a.start.w == pytest.approx(1080), "the opening frame is full-bleed (zoom 1)"
    # an empty bubble over one end of the panel → the opening frame starts elsewhere
    bubble = [(0.0, 0.0, 1100.0, 600.0)]
    c = cam.plan_camera(3413, 1920, 1.2, rest=rest, intro=True, max_zoom=1.6, avoid=bubble)
    assert c.start.cx - c.start.w / 2 >= 1100 - 1
    assert a.speed >= cam.CAMERA_SPEED_INTRO_MIN - 1e-6
    v = cam.frame_speeds(a, 36, 30)
    assert v[0] >= max(v[-5:]), "intro is fastest at the start (motion inside the first second)"


def test_loop_echo_ends_on_the_opening_frame(tmp_path):
    src = tmp_path / "p.png"
    Image.new("RGB", (2400, 1500), (90, 30, 30)).save(src)
    _, intro, _, _ = cam.prepare_plan(src, 2.0, shot_id=0, intro=True)
    _, echo, _, _ = cam.prepare_plan(src, 1.0, shot_id=17, reverse=True)
    assert echo.end == intro.start


@needs_ffmpeg
def test_render_plan_fills_every_frame_and_honours_the_contract(tmp_path):
    src = tmp_path / "wide.png"
    arr = np.zeros((900, 1600, 3), np.uint8)
    arr[:, :, 0] = np.linspace(0, 255, 1600, dtype=np.uint8)[None, :]   # horizontal ramp
    arr[:, :, 1] = 120                    # bright enough that no column reads as "ink" subject
    arr[:, :, 2] = 200
    Image.fromarray(arr).save(src)
    _, plan, mode, _ = cam.prepare_plan(src, 1.0, shot_id=2)
    assert mode == "fullbleed" and plan.axis == "x"
    out = tmp_path / "shot.mp4"
    cam.render_plan(src, plan, out, 30, preset="ultrafast")
    clips.verify_shot_contract(out, 30)
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-vf", "scale=108:192",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 192, 108, 3).astype(int)
    assert fr[:, :, :, 2].min() > 150, "a blue-filled panel: no black/blur band anywhere"
    assert np.abs(fr[-1, :, :, 0] - fr[0, :, :, 0]).mean() > 5, "the picture moved"


# ── clips: auto-fill, manual override, contain fallback ─────────────────────────────────────

def _clip_frames(n=8, h=90, w=160, hot=0.8, bars=0):
    """Synthetic gray clip: flat field + a moving textured blob centred at `hot` of the width."""
    rng = np.random.default_rng(0)
    f = np.full((n, h, w), 90, np.float32)
    cx = int(hot * w)
    for t in range(n):
        f[t, 20:70, cx - 12:cx + 12] = rng.integers(0, 255, (50, 24))
    if bars:
        f[:, :bars, :] = 0
        f[:, h - bars:, :] = 0
    return f


def test_auto_fill_centres_on_the_active_region():
    crop = clips.choose_fill_crop(_clip_frames(hot=0.8), 1920, 1080)
    assert set(crop) == {"cx", "cy"} and crop["cx"] > 0.6


def test_auto_fill_keeps_centre_on_a_uniform_clip():
    f = np.full((6, 90, 160), 100, np.float32)
    assert clips.choose_fill_crop(f, 1920, 1080)["cx"] == pytest.approx(0.5, abs=0.02)


def test_auto_fill_refuses_a_source_too_small_to_fill():
    assert clips.CLIP_MAX_UPSCALE < 1920 / 720               # 720p needs 2.67x
    assert clips.choose_fill_crop(_clip_frames(), 1280, 720) == {}


def test_auto_fill_crops_inside_burned_in_bars():
    # 2560x1440 file holding a 2560x1100 picture between black bars → a 9:16 region inside it
    crop = clips.choose_fill_crop(_clip_frames(h=90, bars=10), 2560, 1440)
    assert set(crop) == {"x", "y", "w", "h"}
    assert crop["y"] > 0.05 and crop["y"] + crop["h"] < 0.95
    x, y, w, h = clips.crop_region(2560, 1440, crop)
    assert abs((w / h) / shots.TARGET_ASPECT - 1) < 0.02


def test_auto_crop_makes_the_filter_graph_fill_without_blur():
    crop = {"cx": 0.7, "cy": 0.5, "auto": True}
    g = clips.clip_filter_graph(1920, 1080, crop, 0.0)
    assert "gblur" not in g and "crop=608:1080" in g
    assert "gblur" in clips.clip_filter_graph(1920, 1080, {}, 0.0), "no crop → contain+blur"


def _clip_shot(path, **kw):
    return Shot(shot_id=0, scene_id=1, duration_seconds=kw.pop("dur", 1.0), panel_bbox={},
                source_image="", motion="zoom_in", clip_path=str(path), clip_id="c", **kw)


@needs_ffmpeg
def test_render_uses_auto_fill_but_a_manual_crop_wins(tmp_path, monkeypatch):
    src = tmp_path / "c.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1920x1080:rate=30:duration=2", "-pix_fmt", "yuv420p", str(src)],
                   check=True)
    seen = []
    real_graph = clips.clip_filter_graph
    monkeypatch.setattr(clips, "clip_filter_graph",
                        lambda iw, ih, crop, *a, **k: seen.append(crop) or real_graph(iw, ih, crop, *a, **k))
    clips.render_clip_shot(_clip_shot(src), tmp_path / "a.mp4", preset="ultrafast")
    assert seen[-1].get("auto") is True and "cx" in seen[-1]

    def boom(*a, **k):
        raise AssertionError("auto-fill must not run when a manual crop is set")
    monkeypatch.setattr(clips, "auto_fill_crop", boom)
    clips.render_clip_shot(_clip_shot(src, clip_crop={"cx": 0.2, "cy": 0.5}), tmp_path / "b.mp4",
                           preset="ultrafast")
    assert seen[-1] == {"cx": 0.2, "cy": 0.5}


def test_auto_fill_off_switch(monkeypatch):
    monkeypatch.setattr(clips, "CLIP_AUTO_FILL", False)
    assert clips.auto_fill_crop(Path("/nope.mp4"), 1920, 1080, 0.0, 1.0) == {}


def test_one_crop_per_run_of_shots_playing_the_same_clip(monkeypatch):
    calls = []
    monkeypatch.setattr(clips, "probe_video", lambda p: {"width": 1920, "height": 1080, "duration": 9})
    monkeypatch.setattr(clips, "auto_fill_crop",
                        lambda p, iw, ih, t0, span, log=print: calls.append((t0, span)) or {"cx": 0.4, "cy": 0.5, "auto": True})
    run = [_clip_shot("/x.mp4", dur=1.0, clip_in=0.0), _clip_shot("/x.mp4", dur=1.5, clip_in=1.0),
           Shot(shot_id=2, scene_id=2, duration_seconds=1, panel_bbox={}, source_image="p", motion="zoom_in"),
           _clip_shot("/x.mp4", dur=0.5, clip_in=0.0)]                 # the loop echo
    assert clips.assign_auto_crops(run, log=lambda m: None) == 3
    assert calls == [(0.0, 2.5)], "one analysis over the run's whole footage; the echo reuses it"
    assert run[0].clip_crop == run[1].clip_crop == run[3].clip_crop


# ── opening-shot rule ───────────────────────────────────────────────────────────────────────

def _panel_png(tmp_path, name, w, h):
    p = tmp_path / name
    Image.new("RGB", (w, h), (200, 50, 50)).save(p)
    return str(p)


def _still(sid, src, w, h, dur=2.0, **kw):
    return Shot(shot_id=sid, scene_id=sid + 1, duration_seconds=dur,
                panel_bbox={"x": 0, "y": 0, "w": w, "h": h}, source_image=src, motion="zoom_in", **kw)


def test_opening_clip_that_cannot_fill_gets_a_full_bleed_preroll(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "clip_fills_frame", lambda s, log=print: False)
    src = _panel_png(tmp_path, "p.png", 1400, 1600)
    first = _still(0, src, 1400, 1600, dur=5.0, is_intro=True)
    first.clip_path, first.clip_id, first.clip_in, first.clip_out = "/c.mp4", "c", 0.0, 6.0
    first.clip_fit_to_beat = True
    out = shots._enforce_opening_shot([first, _still(1, src, 1400, 1600)], log=lambda m: None)
    assert len(out) == 3 and [s.shot_id for s in out] == [0, 1, 2]
    pre, rest = out[0], out[1]
    assert not pre.clip_path and pre.source_image == src and pre.is_intro
    assert pre.duration_seconds == pytest.approx(shots.OPENING_FULLBLEED_SECONDS)
    assert rest.clip_path == "/c.mp4" and not rest.is_intro
    assert pre.duration_seconds + rest.duration_seconds == pytest.approx(5.0)
    # the fit-to-beat window shrinks with the shot, so the clip is not fast-forwarded
    assert rest.clip_out == pytest.approx(6.0 * rest.duration_seconds / 5.0, abs=1e-3)


def test_opening_too_short_to_split_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "clip_fills_frame", lambda s, log=print: False)
    src = _panel_png(tmp_path, "p.png", 1400, 1600)
    first = _still(0, src, 1400, 1600, dur=2.4)
    first.clip_path, first.clip_id = "/c.mp4", "c"
    out = shots._enforce_opening_shot([first], log=lambda m: None)
    assert len(out) == 1 and not out[0].clip_path and out[0].duration_seconds == 2.4


def test_every_shot_inside_the_opening_window_is_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "clip_fills_frame", lambda s, log=print: False)
    good = _panel_png(tmp_path, "good.png", 1400, 1600)
    a = _still(0, good, 1400, 1600, dur=1.0)
    b = _still(1, good, 1400, 1600, dur=4.0)
    b.clip_path, b.clip_id = "/c.mp4", "c"                      # starts at 1.0 s, can't fill
    out = shots._enforce_opening_shot([a, b], log=lambda m: None)
    assert [bool(s.clip_path) for s in out] == [False, False, True]
    assert out[1].duration_seconds == pytest.approx(shots.OPENING_FULLBLEED_SECONDS - 1.0)


def test_a_shot_with_only_a_sliver_inside_the_window_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "clip_fills_frame", lambda s, log=print: False)
    good = _panel_png(tmp_path, "good.png", 1400, 1600)
    a = _still(0, good, 1400, 1600, dur=shots.OPENING_FULLBLEED_SECONDS - 0.3)
    b = _still(1, good, 1400, 1600, dur=1.5)
    b.clip_path, b.clip_id = "/c.mp4", "c"
    out = shots._enforce_opening_shot([a, b], log=lambda m: None)
    assert len(out) == 2 and out[1].clip_path == "/c.mp4"


def test_opening_strip_borrows_the_next_full_bleed_still(tmp_path):
    strip = _panel_png(tmp_path, "strip.png", 4400, 1000)       # extreme strip → would letterbox
    good = _panel_png(tmp_path, "good.png", 1400, 1600)
    msgs = []
    out = shots._enforce_opening_shot([_still(0, strip, 4400, 1000, dur=5.0),
                                       _still(1, good, 1400, 1600)], log=msgs.append)
    assert out[0].source_image == good and out[1].source_image == strip
    assert any("opening rule" in m for m in msgs)


def test_opening_that_fills_is_untouched(tmp_path):
    src = _panel_png(tmp_path, "p.png", 1400, 1600)
    sh = [_still(0, src, 1400, 1600), _still(1, src, 1400, 1600)]
    assert shots._enforce_opening_shot(list(sh), log=lambda m: None) == sh


def test_opening_rule_logs_when_nothing_can_fill(tmp_path):
    strip = _panel_png(tmp_path, "strip.png", 4400, 1000)
    msgs = []
    out = shots._enforce_opening_shot([_still(0, strip, 4400, 1000)], log=msgs.append)
    assert len(out) == 1 and any("kept" in m for m in msgs)
