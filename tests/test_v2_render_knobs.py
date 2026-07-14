"""MICRO_MOMENT_V2 render knobs (spec section B). Every knob defaults to the OLD behavior;
only a caller that SETS the env changes anything. Test #1 (byte-identical when unset) is the
hard pass condition — the recap/Q&A render must be unaffected unless the knob is set."""
from PIL import Image

import config
import stages.stage_5.pipeline as pipeline
import stages.stage_5.shots as shots
from stages.stage_5.schema import Shot


def _shot(sid, dur, *, src="p.png", motion="pan_right", w=700, h=1200,
          is_intro=False, cap="", scene_id=None):
    return Shot(shot_id=sid, scene_id=scene_id if scene_id is not None else sid,
                duration_seconds=dur, panel_bbox={"x": 10, "y": 20, "w": w, "h": h},
                source_image=src, motion=motion, caption_text=cap, is_intro=is_intro)


# ── (a) default OFF → build_shots / _time_split output is byte-identical ──────────────────────
def test_defaults_are_old_behavior():
    assert shots.SHOT_MAX_SECONDS == 0.0
    assert shots.PANEL_FIT_MODE == "contain"
    assert config.XFADE_TRANSITION == "dissolve"


def test_time_split_noop_when_off_returns_same_list():
    orig = [_shot(0, 9.0), _shot(1, 12.0)]
    out = shots._time_split_shots(orig, 0.0)
    assert out is orig                        # same object, untouched
    assert [s.duration_seconds for s in out] == [9.0, 12.0]


def test_build_shots_unchanged_when_knob_unset(monkeypatch):
    """A long single-scene shot stays ONE 9s shot when SHOT_MAX_SECONDS is 0 AND the loop
    tail is off — isolates the length-cap knob from the separate loop-tail carve (see
    test_build_shots_loop_tail_splits_last_shot below)."""
    assert shots.SHOT_MAX_SECONDS == 0.0
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    narr = {"scenes": [{"scene_id": 1, "text": "s", "target_seconds": 9.0,
                        "panel_bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},
                        "source_image": "p1.png"}]}
    built = shots.build_shots(narr, scene_timings=[], word_timestamps=[])
    assert len(built) == 1 and abs(built[0].duration_seconds - 9.0) < 1e-6


def test_build_shots_loop_tail_splits_last_shot(monkeypatch):
    """SHOT_MAX_SECONDS unset (0) but SEAMLESS_LOOP on + LOOP_TAIL_SECONDS > 0: the last shot
    still gets carved into [head, tail] (same panel) so _close_loop's echo rides a short tail,
    even though nothing is being length-capped. Total duration is preserved."""
    assert shots.SHOT_MAX_SECONDS == 0.0
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", True)
    monkeypatch.setattr(shots, "LOOP_TAIL_SECONDS", 1.8)
    narr = {"scenes": [{"scene_id": 1, "text": "s", "target_seconds": 9.0,
                        "panel_bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},
                        "source_image": "p1.png"}]}
    built = shots.build_shots(narr, scene_timings=[], word_timestamps=[])
    assert len(built) == 2
    assert abs(built[-1].duration_seconds - 1.8) < 1e-6
    assert abs(built[0].duration_seconds - 7.2) < 1e-6
    assert abs(sum(s.duration_seconds for s in built) - 9.0) < 1e-3


# ── (b) SHOT_MAX_SECONDS=3.5 → no shot > 3.5s, total unchanged, same panel preserved ──────────
def test_time_split_caps_duration_and_preserves_panel():
    orig = [_shot(0, 9.0, src="a.png", w=700, h=1200),
            _shot(1, 2.0, src="b.png", w=800, h=900),      # already short → not split
            _shot(2, 8.0, src="c.png", w=600, h=1000)]
    total_before = sum(s.duration_seconds for s in orig)
    out = shots._time_split_shots(orig, 3.5)

    assert all(s.duration_seconds <= 3.5 + 1e-6 for s in out), \
        [s.duration_seconds for s in out]
    assert abs(sum(s.duration_seconds for s in out) - total_before) < 1e-3
    assert [s.shot_id for s in out] == list(range(len(out)))   # contiguous ids

    # The 9s shot → same page/bbox on every fragment, but motion varies (no freeze).
    frags_a = [s for s in out if s.source_image == "a.png"]
    assert len(frags_a) >= 3
    assert all(f.panel_bbox == {"x": 10, "y": 20, "w": 700, "h": 1200} for f in frags_a)
    assert len({f.motion for f in frags_a}) > 1                # motion cycles
    # a distinct bbox dict per fragment (mutating one must not touch the source)
    frags_a[0].panel_bbox["w"] = 1
    assert orig[0].panel_bbox["w"] == 700
    # the already-short shot is untouched (single fragment, original duration + motion)
    frags_b = [s for s in out if s.source_image == "b.png"]
    assert len(frags_b) == 1 and frags_b[0].duration_seconds == 2.0 and frags_b[0].motion == "pan_right"


def test_build_shots_splits_when_knob_set(monkeypatch):
    monkeypatch.setattr(shots, "SHOT_MAX_SECONDS", 3.5)
    narr = {"scenes": [{"scene_id": 1, "text": "s", "target_seconds": 9.0,
                        "panel_bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},
                        "source_image": "p1.png"}]}
    built = shots.build_shots(narr, scene_timings=[], word_timestamps=[])
    assert len(built) >= 3
    assert all(s.duration_seconds <= 3.5 + 1e-6 for s in built)
    assert abs(sum(s.duration_seconds for s in built) - 9.0) < 1e-3


def test_loop_tail_is_short_and_intro_capped(monkeypatch):
    """Knob-4: intro/cold-open capped ≤ threshold; _close_loop clone rides a SHORT ~1.8s tail
    (not the whole long outro shot). Split runs BEFORE _close_loop → clone lands on the last
    fragment."""
    monkeypatch.setattr(shots, "SHOT_MAX_SECONDS", 3.5)
    monkeypatch.setattr(shots, "LOOP_TAIL_SECONDS", 1.8)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", True)
    intro = _shot(0, 8.0, src="open.png", motion="zoom_in", is_intro=True)
    body = _shot(1, 2.0, src="mid.png")
    outro = _shot(2, 6.0, src="close.png", cap="outro")
    out = shots._time_split_shots([intro, body, outro], 3.5, loop_tail=1.8)
    # intro capped
    intro_frags = [s for s in out if s.source_image == "open.png"]
    assert len(intro_frags) >= 3 and all(s.duration_seconds <= 3.5 + 1e-6 for s in intro_frags)
    assert intro_frags[0].is_intro and intro_frags[0].motion == "zoom_in"
    # last fragment is the ~1.8s loop tail
    assert abs(out[-1].duration_seconds - 1.8) < 1e-6
    # _close_loop then clones the opening panel onto that short tail (needs is_intro on shots[0])
    shots._close_loop(out)
    assert out[-1].source_image == "open.png" and out[-1].motion == "zoom_out"


# ── (c) PANEL_FIT_MODE=fill → landscape fills; critical-text keeps contain ─────────────────────
# These pin LANDSCAPE_COVER_MAX_ASPECT to isolate the PANEL_FIT_MODE (fill vs contain) branch from
# the separately-tuned letterbox threshold — the aspect-1.3 fixture must be a "blur candidate" for
# the fill/contain distinction to exist (R3 raised the default to 1.7, above 1.3).
def test_should_blur_bg_fill_vs_contain(monkeypatch):
    monkeypatch.setattr(shots, "LANDSCAPE_COVER_MAX_ASPECT", 1.2)
    # moderate landscape (aspect 1.3): contain → blur; fill → cover-crop; fill+keep → blur.
    assert shots._should_blur_bg(1300, 1000, fit_mode="contain") is True
    assert shots._should_blur_bg(1300, 1000, fit_mode="fill") is False
    assert shots._should_blur_bg(1300, 1000, fit_mode="fill", keep_contain=True) is True
    # extreme strip (aspect 3.5) loses > FILL_MAX_AREA_LOSS → stays contain even in fill mode.
    assert shots._should_blur_bg(3500, 1000, fit_mode="fill") is True
    # portrait splash → cover-fill in either mode (never blurred).
    assert shots._should_blur_bg(1983, 3047, fit_mode="fill") is False
    assert shots._should_blur_bg(1983, 3047, fit_mode="contain") is False


def _make_wide(path, w, h, edge=120):
    im = Image.new("RGB", (w, h), (0, 0, 255))                 # blue center
    im.paste(Image.new("RGB", (edge, h), (255, 0, 0)), (0, 0))          # red left edge
    im.paste(Image.new("RGB", (edge, h), (0, 255, 0)), (w - edge, 0))   # green right edge
    im.save(path)


def test_prepare_frame_fill_covercrops_landscape(tmp_path, monkeypatch):
    monkeypatch.setattr(shots, "PANEL_FIT_MODE", "fill")
    monkeypatch.setattr(shots, "LANDSCAPE_COVER_MAX_ASPECT", 1.2)   # make aspect-1.3 a blur candidate
    src, out = tmp_path / "wide.png", tmp_path / "frame.png"
    _make_wide(src, 1300, 1000)               # aspect 1.3 (would letterbox in contain mode)
    shots._prepare_panel_frame(src, out)
    with Image.open(out) as f:
        assert f.size == (shots.OUTPUT_W, shots.OUTPUT_H)
        yc = shots.OUTPUT_H // 2
        center = f.getpixel((shots.OUTPUT_W // 2, yc))
        left = f.getpixel((2, yc))
    # cover-fill: side edges cropped off → left output pixel is the blue center, not red.
    assert center[2] > 150 and center[0] < 100, f"center not blue: {center}"
    assert left[2] > 150 and left[0] < 100, f"left edge kept the red side (not filled): {left}"


def test_prepare_frame_fill_keeps_contain_for_critical_text(tmp_path, monkeypatch):
    monkeypatch.setattr(shots, "PANEL_FIT_MODE", "fill")
    monkeypatch.setattr(shots, "LANDSCAPE_COVER_MAX_ASPECT", 1.2)   # make aspect-1.3 a blur candidate
    src, out = tmp_path / "wide2.png", tmp_path / "frame2.png"
    _make_wide(src, 1300, 1000)
    shots._prepare_panel_frame(src, out, keep_contain=True)   # e.g. a gravestone panel
    with Image.open(out) as f:
        yc = shots.OUTPUT_H // 2
        left = f.getpixel((2, yc))
    # contain: the whole panel fits → left area is NOT the pure-blue center a cover-crop makes.
    assert not (left[2] > 150 and left[0] < 100), f"unexpectedly cover-cropped: {left}"


# ── (d) XFADE_TRANSITION=cut is a valid value → hard-cut concat path ──────────────────────────
def test_xfade_label_accepts_cut(monkeypatch):
    monkeypatch.setattr(config, "XFADE_TRANSITION", "cut")
    monkeypatch.setattr(config, "XFADE_SOFT_EDGES", False)
    monkeypatch.setattr(config, "XFADE_DURATION", 0.25)
    assert pipeline._xfade_label() == "cut"
    monkeypatch.setattr(config, "XFADE_TRANSITION", "dissolve")
    assert pipeline._xfade_label() == "dissolve 0.25s"


def test_cut_routes_to_concat_not_xfade(monkeypatch, tmp_path):
    """XFADE_TRANSITION=cut (+ soft-edges/flash off) assembles via the concat demuxer, never the
    xfade filter chain — a true 0-frame hard cut, timing preserved by concat (no tpad overlap)."""
    monkeypatch.setattr(config, "XFADE_TRANSITION", "cut")
    monkeypatch.setattr(config, "XFADE_DURATION", 0.25)
    monkeypatch.setattr(config, "XFADE_SOFT_EDGES", False)
    monkeypatch.setattr(config, "FLASH_ACCENTS", False)
    monkeypatch.setattr(config, "XFADE_ROTATE", "")
    monkeypatch.setattr(config, "FLASH_ACCENTS_MAX", 3)
    calls = {"concat": 0, "xfade": 0}
    monkeypatch.setattr(pipeline, "_concat",
                        lambda paths, out: (calls.__setitem__("concat", calls["concat"] + 1), out)[1])
    monkeypatch.setattr(pipeline, "_xfade_chain",
                        lambda *a, **k: calls.__setitem__("xfade", calls["xfade"] + 1))
    sh = [_shot(0, 2.0, scene_id=1), _shot(1, 2.0, scene_id=2)]   # two scene groups
    paths = [tmp_path / "s0.mp4", tmp_path / "s1.mp4"]
    pipeline._assemble_video(sh, paths, tmp_path / "out.mp4")
    assert calls["concat"] == 1 and calls["xfade"] == 0
