import subprocess
import shutil
from pathlib import Path
import pytest
import config

from stages.stage_5 import clips, shots, pipeline
from stages.stage_5.schema import Shot

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg required")


def _make_source(path: Path, dur: float = 3.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
        "-t", f"{dur:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)
    ], check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_clip_muted_and_contract_compliant(tmp_path):
    src = _make_source(tmp_path / "src.mp4", dur=2.0)
    shot = Shot(
        shot_id=0, scene_id=1, duration_seconds=1.5,
        panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100},
        source_image="dummy.png", motion="zoom_in",
        clip_path=str(src), clip_in=0.5, clip_out=2.0, clip_id="c1"
    )
    out = tmp_path / "clip_shot.mp4"
    clips.render_clip_shot(shot, out)

    assert out.exists()
    info = clips.probe_video(out)
    assert info["width"] == 1080
    assert info["height"] == 1920
    assert abs(info["duration"] - 1.5) < 0.1

    # Verify muted (no audio stream)
    res = subprocess.run([
        FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", str(out)
    ], capture_output=True, text=True)
    assert res.stdout.strip() == "", "Clip shot must be muted (no audio streams)"


@needs_ffmpeg
def test_fit_math_speed_and_hold_bounds(tmp_path):
    # Source is 1.0s long. Beat requires 1.4s.
    # Available = 1.0s. Needed = 1.4s.
    # Required speed = 1.0 / 1.4 = 0.714 -> clamped to CLIP_SPEED_MIN = 0.8.
    # At speed 0.8: duration is 1.0 / 0.8 = 1.25s.
    # Remaining shortfall: 1.4 - 1.25 = 0.15s <= CLIP_MAX_HOLD = 0.3s.
    src = _make_source(tmp_path / "short_src.mp4", dur=1.0)
    shot = Shot(
        shot_id=1, scene_id=1, duration_seconds=1.4,
        panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100},
        source_image="dummy.png", motion="zoom_in",
        clip_path=str(src), clip_in=0.0, clip_out=1.0, clip_id="c2"
    )
    out = tmp_path / "fitted_clip.mp4"
    clips.render_clip_shot(shot, out)

    # Output matches exact shot contract frames (1.4s * 30 = 42 frames)
    clips.verify_shot_contract(out, 42)


@needs_ffmpeg
def test_frozen_tail_capped_at_max_hold(tmp_path, monkeypatch):
    # The cap only exists on the Q&A clip path; the flag-OFF path is the legacy one.
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    # Source is 1.0s long. Beat requires 2.0s.
    # Shortfall is 2.0 - (1.0 / 0.8) = 0.75s > CLIP_MAX_HOLD (0.3s).
    # Must raise ValueError to trigger panel fallback.
    from PIL import Image
    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1200), (200, 100, 50)).save(page)

    src = _make_source(tmp_path / "too_short.mp4", dur=1.0)
    shot = Shot(
        shot_id=1, scene_id=1, duration_seconds=2.0,
        panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
        source_image=str(page), motion="zoom_in",
        clip_path=str(src), clip_in=0.0, clip_out=1.0, clip_id="c_fail"
    )
    out = tmp_path / "fail_clip.mp4"
    with pytest.raises(ValueError, match="CLIP_MAX_HOLD"):
        clips.render_clip_shot(shot, out)

    # Calling via shots.render_shot safely catches ValueError and falls back to panel
    panel_out = tmp_path / "fallback_shot.mp4"
    res = shots.render_shot(shot, panel_out)
    assert res.exists()
    assert shot.clip_fallback != ""
    assert "CLIP_MAX_HOLD" in shot.clip_fallback


@needs_ffmpeg
def test_hard_cut_before_and_after_clip_shots(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(shots, "PANEL_UPSCALE", False)

    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1200), (40, 100, 160)).save(page)

    src = _make_source(tmp_path / "c.mp4", dur=2.0)

    # 3 shots: Panel 1 (scene 1) -> Clip (scene 2) -> Panel 2 (scene 3)
    s1 = Shot(shot_id=0, scene_id=1, duration_seconds=1.0, panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
              source_image=str(page), motion="zoom_in")
    s2 = Shot(shot_id=1, scene_id=2, duration_seconds=1.0, panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
              source_image="dummy.png", motion="zoom_in",
              clip_path=str(src), clip_in=0.0, clip_out=1.0, clip_id="clip_mid")
    s3 = Shot(shot_id=2, scene_id=3, duration_seconds=1.0, panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
              source_image=str(page), motion="zoom_out")

    p1 = shots.render_shot(s1, tmp_path / "shot_0.mp4")
    p2 = shots.render_shot(s2, tmp_path / "shot_1.mp4")
    p3 = shots.render_shot(s3, tmp_path / "shot_2.mp4")

    out_silent = tmp_path / "assembled.mp4"
    pipeline._assemble_video([s1, s2, s3], [p1, p2, p3], out_silent, project="test_cuts")

    assert out_silent.exists()
    info = clips.probe_video(out_silent)
    assert abs(info["duration"] - 3.0) < 0.2


# ─── fit(): the pure fit math shared by the renderer and frozen_tail_seconds ──────────────

def test_fit_trim_extend_speed_hold_tuples():
    f = clips.fit
    # 1. TRIM — the window is at least as long as the shot: play `dur`, untouched.
    assert f(5.0, 5.0, 3.0) == (3.0, 1.0, 0.0)
    # 2. EXTEND — a short window with real footage behind it keeps playing at 1.0x.
    assert f(1.0, 3.0, 2.0) == (2.0, 1.0, 0.0)
    # 3. SPEED — footage 20% short is slowed to 0.8x, no hold needed.
    used, speed, hold = f(1.6, 1.6, 2.0)
    assert (used, round(speed, 6), round(hold, 6)) == (1.6, 0.8, 0.0)
    # 4. HOLD — slowed to the 0.8 floor and still short: freeze the rest (<= 0.3s).
    used, speed, hold = f(1.0, 1.0, 1.4)
    assert (used, speed, round(hold, 3)) == (1.0, 0.8, 0.15)
    # right at the cap is fine, past it is not
    assert round(f(1.0, 1.0, 1.55)[2], 3) == 0.30
    with pytest.raises(clips.ClipTooShort, match="CLIP_MAX_HOLD"):
        f(1.0, 1.0, 1.6)
    with pytest.raises(ValueError, match="CLIP_MAX_HOLD"):      # ClipTooShort IS a ValueError
        f(0.5, 0.5, 2.0)
    # no footage at all -> fallback, never a divide-by-zero
    with pytest.raises(clips.ClipTooShort):
        f(0.0, 0.0, 1.0)


def test_fit_never_speeds_up_and_respects_the_knobs():
    # a longer clip is TRIMMED, not rushed (CLIP_SPEED_MAX only sizes the download margin)
    assert clips.fit(10.0, 10.0, 2.0).speed == 1.0
    # tighter knobs change the answer: with a 0.1s cap the 0.15s hold of the case above fails
    with pytest.raises(clips.ClipTooShort):
        clips.fit(1.0, 1.0, 1.4, max_hold=0.1)
    # a lower speed floor absorbs it instead
    assert clips.fit(1.0, 1.0, 1.4, speed_min=0.7) == pytest.approx((1.0, 1.0 / 1.4, 0.0))


def test_fit_invariants_over_a_sweep():
    import itertools
    cap = config.CLIP_MAX_HOLD
    for span, dur in itertools.product([0.2, 0.5, 0.9, 1.0, 1.3, 1.9, 2.5, 4.0], [0.4, 0.8, 1.2, 1.6, 2.0, 3.3]):
        for avail in (span, span + 0.7, 10.0):
            try:
                used, speed, hold = clips.fit(span, avail, dur)
            except clips.ClipTooShort:
                # the only allowed failure: even all footage at the speed floor + a capped hold is short
                ext = min(max(avail, span), dur)
                assert dur - ext / config.CLIP_SPEED_MIN > cap - 1e-3
                continue
            assert used / speed + hold == pytest.approx(dur, abs=1e-6), (span, avail, dur)
            assert config.CLIP_SPEED_MIN - 1e-9 <= speed <= 1.0 + 1e-9
            assert 0.0 <= hold <= cap + 1e-4
            assert used <= max(span, avail) + 1e-9


def _flag_on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)


def _frames(path: Path) -> int:
    res = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return int(res.stdout.strip())


@needs_ffmpeg
def test_every_fit_branch_renders_exactly_the_frames_the_shot_needs(tmp_path, monkeypatch):
    """trim / extend / slow / slow+hold at awkward (non frame-aligned) durations: the output must
    hit the shot contract frame-for-frame — fps/setpts rounding must not leave it a frame short."""
    _flag_on(monkeypatch)
    long_src = _make_source(tmp_path / "long.mp4", dur=5.0)
    short_src = _make_source(tmp_path / "short.mp4", dur=1.0)
    cases = [(long_src, d) for d in (0.97, 1.45, 1.63, 2.07, 2.5)]       # trim (open-ended)
    cases += [(short_src, d) for d in (1.05, 1.18, 1.25, 1.33, 1.41, 1.5)]  # slow, slow+hold
    for n, (src, dur) in enumerate(cases):
        shot = Shot(shot_id=n, scene_id=1, duration_seconds=dur,
                    panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100}, source_image="dummy.png",
                    motion="zoom_in", clip_path=str(src), clip_in=0.0, clip_out=0.0, clip_id=f"c{n}")
        out = clips.render_clip_shot(shot, tmp_path / f"s{n}.mp4")      # verify_shot_contract inside
        assert _frames(out) == max(1, round(max(0.4, dur) * 30)), (src.name, dur)


@needs_ffmpeg
def test_render_logs_the_speed_and_hold_the_plan_chose(tmp_path, monkeypatch):
    _flag_on(monkeypatch)
    src = _make_source(tmp_path / "s.mp4", dur=1.0)
    shot = Shot(shot_id=1, scene_id=1, duration_seconds=1.4,
                panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100}, source_image="dummy.png",
                motion="zoom_in", clip_path=str(src), clip_in=0.0, clip_out=0.0, clip_id="c")
    seen: list[str] = []
    clips.render_clip_shot(shot, tmp_path / "o.mp4", progress=seen.append)
    assert "speed=0.80x" in seen[-1] and "hold=0.15s" in seen[-1]


@needs_ffmpeg
def test_frozen_tail_seconds_is_the_hold_the_renderer_applies(tmp_path, monkeypatch):
    """The M1 remainder: with the flag ON, frozen_tail_seconds must reflect the speed change — the
    old formula (shot - window) said 0.4s here, the renderer holds only 0.15s."""
    _flag_on(monkeypatch)
    src = _make_source(tmp_path / "s.mp4", dur=1.0)
    shot = Shot(shot_id=1, scene_id=1, duration_seconds=1.4,
                panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100}, source_image="dummy.png",
                motion="zoom_in", clip_path=str(src), clip_in=0.0, clip_out=1.0, clip_id="c")
    assert clips.frozen_tail_seconds(shot) == 0.15
    out = clips.render_clip_shot(shot, tmp_path / "o.mp4")
    res = subprocess.run([FFMPEG, "-hide_banner", "-i", str(out), "-vf",
                          "freezedetect=n=-50dB:d=0.1", "-f", "null", "-"],
                         capture_output=True, text=True)
    events = [(ln.split("freeze_")[1].split(":")[0], float(ln.rsplit(":", 1)[1]))
              for ln in res.stderr.splitlines() if "lavfi.freezedetect.freeze_start" in ln
              or "lavfi.freezedetect.freeze_end" in ln]
    # the clip moves for 1.25s (1.0s of footage at 0.8x — testsrc dwells on a few frames, so
    # earlier short freezes are fine), then the last frame holds to the very end of the shot
    kind, at = events[-1]
    assert kind == "start" and 1.15 <= at <= 1.32, res.stderr[-400:]
    # a clip that falls back to its panel holds nothing
    shot.clip_fallback = "ValueError: boom"
    assert clips.frozen_tail_seconds(shot) == 0.0
    # open-ended + long footage -> nothing held
    long_src = _make_source(tmp_path / "l.mp4", dur=4.0)
    shot2 = Shot(shot_id=2, scene_id=1, duration_seconds=1.4,
                 panel_bbox={"x": 0, "y": 0, "w": 100, "h": 100}, source_image="dummy.png",
                 motion="zoom_in", clip_path=str(long_src), clip_in=0.5, clip_out=0.0, clip_id="c2")
    assert clips.frozen_tail_seconds(shot2) == 0.0


@needs_ffmpeg
def test_a_shot_after_the_fixed_window_ran_out_falls_back_to_its_panel(tmp_path, monkeypatch):
    """Flag-ON counterpart of the legacy 'hold the out-point' rule: past a fixed `end` there is no
    footage, and a >0.3s freeze is not allowed — the shot renders its panel and says why."""
    _flag_on(monkeypatch)
    from PIL import Image
    monkeypatch.setattr(shots, "PANEL_UPSCALE", False)
    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1200), (90, 30, 140)).save(page)
    src = _make_source(tmp_path / "s.mp4", dur=2.0)
    shot = Shot(shot_id=1, scene_id=1, duration_seconds=1.0,
                panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200}, source_image=str(page),
                motion="zoom_in", clip_path=str(src), clip_in=1.0, clip_out=1.0, clip_id="ran_out")
    with pytest.raises(clips.ClipTooShort):
        clips.render_clip_shot(shot, tmp_path / "x.mp4")
    out = shots.render_shot(shot, tmp_path / "panel.mp4")
    assert out.exists() and "CLIP_MAX_HOLD" in shot.clip_fallback
    assert clips.frozen_tail_seconds(shot) == 0.0


@needs_ffmpeg
def test_preview_is_rendered_through_the_same_fit(tmp_path, monkeypatch):
    _flag_on(monkeypatch)
    src = _make_source(tmp_path / "s.mp4", dur=4.0)
    out = clips.render_clip_preview(src, start=0.5, beat_duration=2.3, out_path=tmp_path / "pv.mp4")
    assert _frames(out) == round(2.3 * 30)
    short = _make_source(tmp_path / "short.mp4", dur=1.0)
    with pytest.raises(clips.ClipTooShort):
        clips.render_clip_preview(short, start=0.0, beat_duration=3.0, out_path=tmp_path / "bad.mp4")


def _center_rgb(video: Path, frame_idx: int, tmp: Path) -> tuple[int, int, int]:
    from PIL import Image
    png = tmp / f"f{frame_idx}.png"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{frame_idx})",
                    "-frames:v", "1", str(png)], check=True)
    with Image.open(png) as im:
        return im.convert("RGB").getpixel((im.width // 2, im.height // 2))


def _is_near(px, want, tol=14) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(px, want))


@needs_ffmpeg
def test_hard_cuts_around_clips_are_real_and_only_with_the_flag(tmp_path, monkeypatch):
    """Panel(red) -> clip(blue) -> panel(green), 1s each. Flag ON: the frame before each boundary
    is the pure outgoing colour and the frame at it the pure incoming one (a hard cut). Flag OFF:
    the same project assembles with the stable dissolve, so the boundary frames are blends."""
    from PIL import Image
    monkeypatch.setattr(shots, "PANEL_UPSCALE", False)
    red, green, blue = (200, 40, 40), (40, 200, 40), (40, 40, 200)

    def page(name, rgb):
        p = tmp_path / name
        Image.new("RGB", (800, 1200), rgb).save(p)
        return str(p)

    clip_src = tmp_path / "blue.mp4"
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=0x2828c8:size=640x360:rate=30",
                    "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip_src)],
                   check=True, capture_output=True)

    def build(flag: bool):
        monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", flag)
        full = {"x": 0, "y": 0, "w": 800, "h": 1200}
        s1 = Shot(shot_id=0, scene_id=1, duration_seconds=1.0, panel_bbox=full,
                  source_image=page("a.png", red), motion="zoom_in")
        s2 = Shot(shot_id=1, scene_id=2, duration_seconds=1.0, panel_bbox=full,
                  source_image=page("x.png", red), motion="zoom_in",
                  clip_path=str(clip_src), clip_in=0.0, clip_out=0.0, clip_id="mid")
        s3 = Shot(shot_id=2, scene_id=3, duration_seconds=1.0, panel_bbox=full,
                  source_image=page("b.png", green), motion="zoom_out")
        sl = [s1, s2, s3]
        d = tmp_path / f"flag_{int(flag)}"
        paths = [shots.render_shot(s, d / f"shot_{i}.mp4") for i, s in enumerate(sl)]
        return pipeline._assemble_video(sl, paths, d / "out.mp4", project="")

    on = build(True)
    assert _is_near(_center_rgb(on, 29, tmp_path), red), "frame before the cut must be pure panel"
    # a dissolve would bleed the outgoing panel into the first frames of the clip: here the clip
    # is pure from its very first frame to its last
    assert all(_is_near(_center_rgb(on, i, tmp_path), blue) for i in (30, 33, 37, 59))
    assert _is_near(_center_rgb(on, 60, tmp_path), green), "first panel frame after the clip is pure"
    assert all(_is_near(_center_rgb(on, i, tmp_path), green) for i in (63, 67))

    off = build(False)
    # flag OFF: the stable xfade dissolve — the incoming clip fades in over the first 0.25s
    fade = [_center_rgb(off, i, tmp_path) for i in (31, 33, 35)]
    assert not all(_is_near(px, blue) for px in fade), "flag OFF must keep the stable dissolve"


def _color_tags(path: Path) -> tuple:
    res = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=color_range,color_space,color_transfer,color_primaries", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return tuple(v if v not in ("", "unknown", "unspecified") else None for v in res.stdout.strip().split(","))


@needs_ffmpeg
def test_a_tagged_source_does_not_leak_its_colour_tags_into_the_shot(tmp_path, monkeypatch):
    """A YouTube source is tagged bt709/tv, a panel shot carries no tags. Concatenated, that is a colour-parameter
    change mid-stream (ffmpeg 8.1 reconfigures its filter graph there; one Windows run crashed at that spot), so a
    clip shot must carry exactly what a panel shot carries — nothing."""
    from PIL import Image
    _flag_on(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_UPSCALE", False)
    tagged = tmp_path / "tagged.mp4"
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30", "-t", "2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-color_range", "tv", str(tagged)], check=True, capture_output=True)
    assert _color_tags(tagged)[:2] == ("tv", "bt709")                        # the premise: the source IS tagged
    shot = Shot(shot_id=0, scene_id=1, duration_seconds=1.0, panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
                source_image="x.png", motion="zoom_in", clip_path=str(tagged), clip_id="t")
    clip_tags = _color_tags(clips.render_clip_shot(shot, tmp_path / "clip.mp4"))
    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1200), (90, 30, 140)).save(page)
    panel = Shot(shot_id=1, scene_id=2, duration_seconds=1.0, panel_bbox={"x": 0, "y": 0, "w": 800, "h": 1200},
                 source_image=str(page), motion="zoom_in")
    panel_tags = _color_tags(shots.render_shot(panel, tmp_path / "panel.mp4"))
    assert clip_tags == panel_tags == (None, None, None, None)
    # ... and the concatenation therefore carries one set of parameters end to end
    cat = pipeline._concat([tmp_path / "panel.mp4", tmp_path / "clip.mp4", tmp_path / "panel.mp4"], tmp_path / "cat.mp4")
    assert _color_tags(cat) == (None, None, None, None)
    # flag OFF keeps the legacy graph (stable path untouched): the tags still ride along there
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    legacy = clips.render_clip_shot(shot, tmp_path / "legacy.mp4")
    assert _color_tags(legacy)[:2] == ("tv", "bt709")
