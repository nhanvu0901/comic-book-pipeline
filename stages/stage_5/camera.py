"""Full-bleed camera for still shots (Stage 5, Shorts frame).

WHY (measured 2026-11-12, 8 of our Shorts vs 10 competitor Shorts, vmetrics.py):
  apparent motion  ours 5.2 %W/s (2.6-7.3)  vs theirs 30.9 (7.6-69)
  frozen share     ours 0.13              vs theirs 0.00
  padded seconds   ours 0.30              vs ≤0.095 for every pure-comic competitor
The old renderer first squeezed the panel into a 1080x1920 frame (cover-crop, or contain+blur
for any landscape panel at/over 1.7:1) and then moved INSIDE that frame (a 1.00→1.06 push or a
15 % pan). A push that small measures as zero motion, and a 16:9 tier became a strip over blur.

WHAT THIS DOES INSTEAD (the competitors' mechanism): the panel is scaled so the 9:16 window
fills the frame at full height (landscape) or full width (tall), and the window TRAVELS across
the art — horizontally on a wide panel, vertically on a tall one — with a small push-in as the
secondary move. Every rule below is a shape/size/score rule; nothing names a comic.

  • Framing: full-bleed unless the panel is an extreme strip (aspect outside
    [FULLBLEED_MIN_ASPECT, FULLBLEED_MAX_ASPECT]), needs more than the existing upscale cap
    (shots._BLUR_FALLBACK_SCALE) to fill, or carries critical baked text (Shot.keep_contain).
    Those keep the old contain+blur frame — and still move (the camera pushes into the sharp
    panel, which also shrinks the blur bands).
  • Subject: the main figure (largest Magi character box, else the ink/detail profile's central
    mass) must stay in frame for the WHOLE shot: when it fits in the window the window always
    contains it; when it is bigger, the window stays inside it. The resting point is the
    existing subject + bubble-avoid window score (shots._choose_crop_offset), so a shortened
    travel centres on the face, not on an empty bubble.
  • Speed: the estimated apparent speed is kept in [CAMERA_SPEED_MIN, CAMERA_SPEED_MAX] % of the
    frame width per second. Too fast → the travel is shortened around the subject. Too slow (a
    near-9:16 panel has nothing to travel across) → the camera zooms in step by step, which
    creates room to travel, up to CAMERA_ZOOM_MAX and the CAMERA_MAX_UPSCALE resolution cap.
  • Never frozen: the easing keeps ≥60 % of the mean velocity at both ends (no smoothstep stall).
  • Direction alternates between consecutive shots (shot_id parity); the opening shot starts on
    the subject framing and moves at once (CAMERA_SPEED_INTRO_MIN); the loop-close echo plays the
    opening plan in reverse so the video's last frame is its first frame.

Rendering: frames are produced with one cv2.warpAffine per frame (sub-pixel exact — no zoompan
whole-pixel jitter, so no 3-4x pre-upscale is needed) and piped into ffmpeg, which applies the
corner logo and encodes exactly like the old path (libx264, yuv420p, CFR, no audio).

FULLBLEED_MOTION=0 restores the old renderer. The long-form 16:9 frame (panel_walk) keeps the
old renderer regardless (Master 2026-07-29: long-form changes must not touch other modes, and
vice versa).
"""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from . import shots as _sh


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    """Float env knob clamped to [lo, hi]; junk or non-finite → default."""
    raw = os.getenv(name, "")
    try:
        v = float(raw) if raw.strip() else float(default)
    except ValueError:
        v = float(default)
    if not math.isfinite(v):
        v = float(default)
    return min(hi, max(lo, v))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


# ── knobs (env, clamped) ──────────────────────────────────────────────────────────────────────
FULLBLEED_MOTION = _env_bool("FULLBLEED_MOTION", True)
# Landscape panels up to this w/h pan at full height; wider strips fall back to contain+blur.
# 3.5 covers a full-width page tier (typically 2.2-3.5:1). At 3.5:1 the window shows ~16 % of the
# width at a time and the speed cap lets one shot travel across roughly a third to a half of it,
# centred on the main figure — lower this to keep more of a wide establishing strip in view (as a
# small letterboxed band) instead.
FULLBLEED_MAX_ASPECT = _env_float("FULLBLEED_MAX_ASPECT", 3.5, 1.0, 6.0)
# Tall panels down to this w/h pan at full width; thinner strips fall back to contain+blur.
FULLBLEED_MIN_ASPECT = _env_float("FULLBLEED_MIN_ASPECT", 0.2, 0.1, 0.5625)
# Apparent speed band, % of frame width per second (the vmetrics unit). Competitors: median 31.
CAMERA_SPEED_MIN = _env_float("CAMERA_SPEED_MIN", 16.0, 4.0, 60.0)
CAMERA_SPEED_MAX = max(CAMERA_SPEED_MIN, _env_float("CAMERA_SPEED_MAX", 40.0, 8.0, 90.0))
# The opening shot moves at least this fast (motion inside the first second).
CAMERA_SPEED_INTRO_MIN = _env_float("CAMERA_SPEED_INTRO_MIN", 22.0, 4.0, 80.0)
# Secondary push-in over a travelling shot (window shrinks by this fraction start→end).
CAMERA_PUSH = _env_float("CAMERA_PUSH", 0.08, 0.0, 0.3)
# Most the camera may zoom in (relative to the full-bleed window) to create room to move.
CAMERA_ZOOM_MAX = _env_float("CAMERA_ZOOM_MAX", 1.6, 1.05, 2.5)
# Most the SOURCE pixels may be magnified (fill scale x zoom) — keeps a zoom-in from turning a
# small panel into mush. Same spirit as shots._BLUR_FALLBACK_SCALE (2.5) plus a little zoom room.
CAMERA_MAX_UPSCALE = _env_float("CAMERA_MAX_UPSCALE", 3.0, 1.5, 6.0)
# Room kept around the main figure, as a fraction of the window size.
CAMERA_SUBJECT_MARGIN = _env_float("CAMERA_SUBJECT_MARGIN", 0.04, 0.0, 0.2)

# Speed model: apparent speed of a zoom ≈ ZOOM_SPEED_COEF x |d ln(zoom)/dt| x 100 (% W/s). The
# coefficient is the mean |offset from the zoom centre| of a 9:16 frame's pixels in frame-widths,
# as the vmetrics gradient estimator weighs it (calibrated on test renders, see tests).
ZOOM_SPEED_COEF = 0.35
# Easing keeps this share of a straight line's velocity at both ends → never a frozen stretch.
_EASE_FLOOR = 0.6
# Zoom ladder step when the camera needs more room to travel.
_ZOOM_STEP = 0.1


def enabled() -> bool:
    """Camera path on? Only for the portrait Shorts frame (long-form keeps the old renderer)."""
    return FULLBLEED_MOTION and _sh.OUTPUT_H > _sh.OUTPUT_W


# ── framing decision ──────────────────────────────────────────────────────────────────────────

def frame_mode(iw: int, ih: int, *, keep_contain: bool = False) -> tuple[str, str]:
    """("fullbleed" | "blur", reason) for an iw x ih image on the current output frame.

    Full-bleed = the 9:16 window fills the frame (full height for wide art, full width for tall
    art) and pans across the rest. Blur fallback only for: critical baked text (keep_contain), an
    extreme strip outside [FULLBLEED_MIN_ASPECT, FULLBLEED_MAX_ASPECT], or art so small that
    filling the frame would magnify it past shots._BLUR_FALLBACK_SCALE."""
    if iw <= 0 or ih <= 0:
        return "blur", "empty image"
    aspect = iw / ih
    # Critical baked text (a sign, a gravestone): keep the whole panel visible at once wherever the
    # old renderer did (landscape at/over LANDSCAPE_COVER_MAX_ASPECT); below that the old renderer
    # already cover-cropped it, so a full-bleed pan loses nothing it kept.
    if keep_contain and aspect >= _sh.LANDSCAPE_COVER_MAX_ASPECT:
        return "blur", "critical baked text (keep_contain) on a wide panel"
    if aspect > FULLBLEED_MAX_ASPECT:
        return "blur", f"aspect {aspect:.2f} > FULLBLEED_MAX_ASPECT {FULLBLEED_MAX_ASPECT:g}"
    if aspect < FULLBLEED_MIN_ASPECT:
        return "blur", f"aspect {aspect:.2f} < FULLBLEED_MIN_ASPECT {FULLBLEED_MIN_ASPECT:g}"
    fill = fill_scale(iw, ih)
    if fill > _sh._BLUR_FALLBACK_SCALE:
        return "blur", f"needs {fill:.2f}x to fill > cap {_sh._BLUR_FALLBACK_SCALE:g}x"
    return "fullbleed", f"aspect {aspect:.2f}, fill {fill:.2f}x"


def fill_scale(iw: int, ih: int) -> float:
    """Magnification that makes an iw x ih image FILL the output frame (cover scale)."""
    return max(_sh.OUTPUT_W / max(1, iw), _sh.OUTPUT_H / max(1, ih))


def will_letterbox(w: int, h: int, *, keep_contain: bool = False, upscaled: bool = False) -> bool:
    """Would a w x h panel crop render letterboxed (contain+blur)? The ONE predicate the cold-open
    gate and the opening-shot rule share with the renderer. `upscaled` = the crop will be AI-upscaled
    x4 first (config.PANEL_UPSCALE) when it needs magnification, as render_shot does."""
    if upscaled and _sh._needs_upscale(max(1, w), max(1, h)):
        w, h = w * 4, h * 4
    if not enabled():
        aspect = w / h if h else 0.0
        cover = fill_scale(w, h)
        return keep_contain or cover > _sh._BLUR_FALLBACK_SCALE or aspect >= _sh.LANDSCAPE_COVER_MAX_ASPECT
    return frame_mode(w, h, keep_contain=keep_contain)[0] != "fullbleed"


# ── plan ──────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Window:
    """A 9:16 camera window in ART pixels: centre (cx, cy) and width w (height = w * H / W)."""
    cx: float
    cy: float
    w: float


@dataclass(frozen=True)
class CameraPlan:
    art_w: int
    art_h: int
    start: Window
    end: Window
    ease: str            # "inout" (calm) | "out" (intro/action: fastest at the start)
    speed: float         # estimated mean apparent speed, % frame width per second
    axis: str            # "x" | "y" — main travel axis
    note: str = ""

    def reversed(self) -> "CameraPlan":
        ease = {"out": "in"}.get(self.ease, self.ease)
        return replace(self, start=self.end, end=self.start, ease=ease, note=self.note + " reversed")


def _aspect_out() -> float:
    return _sh.OUTPUT_W / _sh.OUTPUT_H


def _win_h(w: float) -> float:
    return w / _aspect_out()


def base_width(iw: int, ih: int) -> float:
    """Width of the largest 9:16 window that fits in the art (zoom 1 = full-bleed fill)."""
    ar = _aspect_out()
    return ih * ar if iw / ih >= ar else float(iw)


def _center_range(extent: float, win: float, subj: tuple[float, float] | None,
                  margin: float) -> tuple[float, float]:
    """Allowed window-CENTRE interval on one axis: inside the art, and keeping the subject span
    `subj` = (s0, s1) in frame — whole (plus margin) when it fits, else the window stays inside it."""
    lo, hi = win / 2.0, extent - win / 2.0
    if hi < lo:
        lo = hi = extent / 2.0
    if subj is None:
        return lo, hi
    s0, s1 = subj
    m = margin * win
    if (s1 - s0) + 2 * m <= win:
        clo, chi = s1 + m - win / 2.0, s0 - m + win / 2.0
    else:
        clo, chi = s0 + win / 2.0, s1 - win / 2.0
        if chi < clo:
            clo = chi = (s0 + s1) / 2.0
    nlo, nhi = max(lo, clo), min(hi, chi)
    if nlo > nhi:                       # subject rule vs art bounds: nearest feasible point
        c = min(max((clo + chi) / 2.0, lo), hi)
        return c, c
    return nlo, nhi


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def estimate_speed(start: Window, end: Window, duration: float) -> float:
    """Mean apparent speed (% of frame width per second) of a straight start→end move: the window
    centre's travel in OUTPUT pixels (at the geometric-mean scale) plus the zoom term."""
    T = max(1e-3, duration)
    wm = math.sqrt(max(1e-6, start.w) * max(1e-6, end.w))
    k = _sh.OUTPUT_W / wm                                # output px per art px
    trans = math.hypot(end.cx - start.cx, end.cy - start.cy) * k / _sh.OUTPUT_W * 100.0 / T
    zoom = ZOOM_SPEED_COEF * abs(math.log(max(1e-6, start.w) / max(1e-6, end.w))) * 100.0 / T
    return trans + zoom


def _zoom_ladder(z_from: float, z_cap: float) -> list[float]:
    out, z = [], z_from
    while z < z_cap - 1e-6:
        out.append(round(z, 4))
        z += _ZOOM_STEP
    out.append(round(max(z_from, z_cap), 4))
    return out


def plan_camera(iw: int, ih: int, duration: float, *,
                subject: tuple[float, float, float, float] | None = None,
                rest: tuple[float, float] | None = None,
                direction: int = 1, intro: bool = False, action: bool = False,
                max_zoom: float | None = None,
                avoid: list[tuple[float, float, float, float]] | None = None) -> CameraPlan:
    """Pure camera planner for an iw x ih art image (no I/O).

    subject   (x0, y0, x1, y1) of the main figure in art px, or None (no constraint)
    rest      the subject/bubble-optimal window centre at zoom 1 (shots._choose_crop_offset), or
              None (subject centre, else art centre)
    direction +1 / -1: travel towards +axis (right / down) or -axis
    intro     start EXACTLY on the zoom-1 rest framing (duration-independent, so the loop echo can
              end on it) and move fast from frame 0
    max_zoom  resolution cap on the zoom (CAMERA_MAX_UPSCALE / fill scale)"""
    T = max(0.2, float(duration))
    d = 1 if direction >= 0 else -1
    bw = base_width(iw, ih)
    z_cap = max(1.0, min(CAMERA_ZOOM_MAX, max_zoom if max_zoom else CAMERA_ZOOM_MAX))
    push = 1.0 + CAMERA_PUSH
    vmin = max(CAMERA_SPEED_MIN, CAMERA_SPEED_INTRO_MIN if intro else 0.0) * (1.25 if action else 1.0)
    vmax = max(vmin, CAMERA_SPEED_MAX * (1.25 if action else 1.0))
    ease = "out" if (intro or action) else "inout"
    sx = (subject[0], subject[2]) if subject else None
    sy = (subject[1], subject[3]) if subject else None
    if rest is None:
        rest = (((subject[0] + subject[2]) / 2, (subject[1] + subject[3]) / 2) if subject
                else (iw / 2.0, ih / 2.0))
    margin = CAMERA_SUBJECT_MARGIN

    def ranges(w: float):
        h = _win_h(w)
        return (_center_range(iw, w, sx, margin), _center_range(ih, h, sy, margin))

    def at(w: float, cx: float, cy: float) -> Window:
        (xlo, xhi), (ylo, yhi) = ranges(w)
        return Window(_clamp(cx, xlo, xhi), _clamp(cy, ylo, yhi), w)

    # Main axis = the one with more room (in output px) at zoom 1; the other is tried second.
    slack_x = (iw - bw) / bw
    slack_y = (ih - _win_h(bw)) / bw
    axes = ("x", "y") if slack_x >= slack_y else ("y", "x")

    def shrink(start: Window, end: Window, fixed_start: bool) -> tuple[Window, Window]:
        """Shorten the move until it is no faster than vmax (binary search on the travel share f).
        The shortened move is the sub-segment [a, a+f] of the full start→end line: a = 0 for a
        fixed (intro) start, else the sub-segment whose middle is nearest the `rest` framing —
        so a short move plays over the subject, not over an empty edge of the panel."""
        if estimate_speed(start, end, T) <= vmax:
            return start, end
        dx, dy = end.cx - start.cx, end.cy - start.cy
        n2 = dx * dx + dy * dy
        u_rest = ((rest[0] - start.cx) * dx + (rest[1] - start.cy) * dy) / n2 if n2 > 0 else 0.5

        def sub(f: float) -> tuple[Window, Window]:
            a = 0.0 if fixed_start else _clamp(u_rest - f / 2, 0.0, 1.0 - f)
            s = start if fixed_start else at(start.w, start.cx + dx * a, start.cy + dy * a)
            e = at(end.w, start.cx + dx * (a + f), start.cy + dy * (a + f))
            return s, e

        lo_f, hi_f = 0.0, 1.0
        best = sub(0.0)
        for _ in range(30):
            f = (lo_f + hi_f) / 2
            s, e = sub(f)
            if estimate_speed(s, e, T) > vmax:
                hi_f = f
            else:
                lo_f, best = f, (s, e)
        return best

    def travel(axis: str, z0: float, z1: float, start: Window | None) -> tuple[Window, Window]:
        w0, w1 = bw / z0, bw / z1
        (x0r, y0r), (x1r, y1r) = ranges(w0), ranges(w1)
        if start is None:
            if axis == "x":
                s = Window(x0r[0] if d > 0 else x0r[1], _clamp(rest[1], *y0r), w0)
            else:
                s = Window(_clamp(rest[0], *x0r), y0r[0] if d > 0 else y0r[1], w0)
        else:
            s = start
        # The end goes to the far side of its range: the travel direction for a free start; for a
        # fixed (intro) start, whichever side leaves more room.
        if axis == "x":
            dd = d if start is None else (1 if (x1r[1] - s.cx) >= (s.cx - x1r[0]) else -1)
            e = Window(x1r[1] if dd > 0 else x1r[0], _clamp(rest[1], *y1r), w1)
        else:
            dd = d if start is None else (1 if (y1r[1] - s.cy) >= (s.cy - y1r[0]) else -1)
            e = Window(_clamp(rest[0], *x1r), y1r[1] if dd > 0 else y1r[0], w1)
        return s, e

    best: tuple[float, Window, Window, str] | None = None
    if intro:
        # Opening frame (duration-independent, so the loop echo can end on it): a full-bleed
        # zoom-1 window that keeps the subject — at an end of the main axis' keep range (room to
        # travel without zooming in) or at the rest framing — whichever shows the least bubble
        # area (avoid boxes); ties go to the start with the most room to travel.
        xr, yr = ranges(bw)
        if axes[0] == "x":
            cands = [Window(xr[0], _clamp(rest[1], *yr), bw), Window(xr[1], _clamp(rest[1], *yr), bw)]
        else:
            cands = [Window(_clamp(rest[0], *xr), yr[0], bw), Window(_clamp(rest[0], *xr), yr[1], bw)]
        cands.append(at(bw, *rest))

        def bubble(win: Window) -> float:
            x0, y0, x1, y1 = (win.cx - win.w / 2, win.cy - _win_h(win.w) / 2,
                              win.cx + win.w / 2, win.cy + _win_h(win.w) / 2)
            a = 0.0
            for bx0, by0, bx1, by1 in avoid or []:
                ix, iy = min(x1, bx1) - max(x0, bx0), min(y1, by1) - max(y0, by0)
                if ix > 0 and iy > 0:
                    a += ix * iy
            return a / max(1e-6, win.w * _win_h(win.w))

        def room(win: Window) -> float:
            r = xr if axes[0] == "x" else yr
            c = win.cx if axes[0] == "x" else win.cy
            return max(r[1] - c, c - r[0])

        start = min(cands, key=lambda w: (round(bubble(w), 2), -round(room(w), 1)))
        for z1 in _zoom_ladder(min(push, z_cap), z_cap):
            for axis in axes:
                s, e = travel(axis, 1.0, z1, start)
                v = estimate_speed(s, e, T)
                if best is None or v > best[0] + 1e-9:
                    best = (v, s, e, axis)
                if v >= vmin:
                    s, e = shrink(s, e, fixed_start=True)
                    return CameraPlan(iw, ih, s, e, ease, round(estimate_speed(s, e, T), 2), axis,
                                      f"intro z1={z1:g}")
        v, s, e, axis = best
        return CameraPlan(iw, ih, s, e, ease, round(v, 2), axis, "intro below CAMERA_SPEED_MIN")

    for z0 in _zoom_ladder(1.0, max(1.0, z_cap / push)):
        z1 = min(z_cap, z0 * push)
        for axis in axes:
            s, e = travel(axis, z0, z1, None)
            v = estimate_speed(s, e, T)
            if best is None or v > best[0] + 1e-9:
                best = (v, s, e, axis)
            if v >= vmin:
                s, e = shrink(s, e, fixed_start=False)
                return CameraPlan(iw, ih, s, e, ease, round(estimate_speed(s, e, T), 2), axis,
                                  f"z {z0:g}->{z1:g}")
    v, s, e, axis = best
    return CameraPlan(iw, ih, s, e, ease, round(v, 2), axis, "below CAMERA_SPEED_MIN")


def ease_value(t: float, ease: str) -> float:
    """Progress 0→1 for normalized time t. Velocity never drops under _EASE_FLOOR of the mean at
    either end (a plain smoothstep stalls → measured as frozen frames)."""
    t = _clamp(t, 0.0, 1.0)
    k = 1.0 - _EASE_FLOOR
    if ease == "out":                       # fast start, settles (velocity 1.4 → 0.6 of mean)
        return (1 - k) * t + k * (1 - (1 - t) ** 2)
    if ease == "in":                        # mirror of "out" (a reversed intro)
        return (1 - k) * t + k * t * t
    if ease == "linear":
        return t
    return (1 - k) * t + k * (t * t * (3 - 2 * t))     # inout: 0.6 → 1.2 → 0.6 of mean


def window_at(plan: CameraPlan, t: float) -> Window:
    """Window at normalized time t: centre moves along the eased line; width interpolates
    geometrically (a constant-rate zoom reads as uniform)."""
    u = ease_value(t, plan.ease)
    s, e = plan.start, plan.end
    w = s.w * (e.w / s.w) ** u if s.w > 0 and e.w > 0 else s.w + (e.w - s.w) * u
    return Window(s.cx + (e.cx - s.cx) * u, s.cy + (e.cy - s.cy) * u, w)


def frame_speeds(plan: CameraPlan, frames: int, fps: float) -> list[float]:
    """Per-frame apparent speed (% W/s) of a rendered plan — what the tests and logs check for
    "never frozen"."""
    if frames < 2:
        return []
    out = []
    dt = 1.0 / fps
    prev = window_at(plan, 0.0)
    for i in range(1, frames):
        cur = window_at(plan, i / (frames - 1))
        out.append(estimate_speed(prev, cur, dt))
        prev = cur
    return out


# ── subject ───────────────────────────────────────────────────────────────────────────────────

def main_subject(iw: int, ih: int, char_boxes: list[dict] | None,
                 profile: tuple | None = None) -> tuple[float, float, float, float] | None:
    """Main figure box (x0, y0, x1, y1) in art px: the largest character box (a lone hand is a
    tiny box, so area picks the person); else the central 60 % of the ink/detail profile's mass
    on each axis; else None."""
    best = None
    for b in char_boxes or []:
        x0 = max(0.0, float(b.get("x", 0))); y0 = max(0.0, float(b.get("y", 0)))
        x1 = min(float(iw), x0 + float(b.get("w", 0))); y1 = min(float(ih), y0 + float(b.get("h", 0)))
        area = (x1 - x0) * (y1 - y0)
        if x1 > x0 and y1 > y0 and (best is None or area > best[0]):
            best = (area, (x0, y0, x1, y1))
    if best is not None:
        return best[1]
    if not profile or profile[0] is None or profile[1] is None:
        return None
    try:
        import numpy as np
        out = []
        for v, full in ((profile[0], iw), (profile[1], ih)):
            v = np.asarray(v, dtype=np.float64)
            tot = float(v.sum())
            if v.size == 0 or tot <= 0:
                return None
            c = np.cumsum(v) / tot
            n = v.size
            q0 = float(np.searchsorted(c, 0.2)) / n * full
            q1 = float(np.searchsorted(c, 0.8) + 1) / n * full
            out.append((q0, min(float(full), q1)))
        return (out[0][0], out[1][0], out[0][1], out[1][1])
    except Exception:
        return None


# ── render ────────────────────────────────────────────────────────────────────────────────────

def _blur_contain_frame(im):
    """contain+blur 1080x1920 frame (the old fallback look) and the sharp panel's rect in it."""
    from PIL import ImageFilter
    W, H = _sh.OUTPUT_W, _sh.OUTPUT_H
    iw, ih = im.size
    cover = fill_scale(iw, ih)
    bw, bh = max(W, int(round(iw * cover))), max(H, int(round(ih * cover)))
    bg = im.resize((bw, bh), _sh.Image.LANCZOS)
    bx, by = (bw - W) // 2, (bh - H) // 2
    bg = bg.crop((bx, by, bx + W, by + H)).filter(ImageFilter.GaussianBlur(radius=40))
    fg_scale = min(min(W / iw, H / ih), _sh._FG_MAX_SCALE)
    fw, fh = max(1, int(round(iw * fg_scale))), max(1, int(round(ih * fg_scale)))
    fg = im.resize((fw, fh), _sh.Image.LANCZOS)
    px, py = (W - fw) // 2, (H - fh) // 2
    bg.paste(fg, (px, py))
    return bg, (px, py, px + fw, py + fh), fg_scale


def prepare_plan(src_png: Path, duration: float, *, shot_id: int = 0,
                 avoid_boxes: list[dict] | None = None, char_boxes: list[dict] | None = None,
                 keep_contain: bool = False, intro: bool = False, action: bool = False,
                 reverse: bool = False, work_png: Path | None = None,
                 log: Callable[[str], None] | None = None):
    """Decide framing + camera plan for one still. Returns (art_png, plan, mode, reason) where
    art_png is the image the plan refers to (the panel itself, or its contain+blur frame)."""
    from PIL import Image
    with Image.open(src_png) as im0:
        im = im0.convert("RGB")
    iw, ih = im.size
    mode, reason = frame_mode(iw, ih, keep_contain=keep_contain)
    if mode == "blur":
        frame, fg_rect, fg_scale = _blur_contain_frame(im)
        art_png = work_png or src_png.with_name(src_png.stem + "_blurbg.png")
        frame.save(art_png, "PNG")
        aw, ah = frame.size
        subject = tuple(float(v) for v in fg_rect)
        rest = ((fg_rect[0] + fg_rect[2]) / 2, (fg_rect[1] + fg_rect[3]) / 2)
        max_zoom = CAMERA_MAX_UPSCALE / max(1e-6, fg_scale)
    else:
        art_png, aw, ah = src_png, iw, ih
        bw = base_width(iw, ih)
        cover = _sh.OUTPUT_W / bw
        new_w, new_h = max(_sh.OUTPUT_W, int(round(iw * cover))), max(_sh.OUTPUT_H, int(round(ih * cover)))
        scaled_avoid = [{"x": int(b["x"] * cover), "y": int(b["y"] * cover),
                         "w": int(b["w"] * cover), "h": int(b["h"] * cover)}
                        for b in (avoid_boxes or [])]
        prof = (None, None)
        if _sh.SUBJECT_AWARE_CROP:
            prof = _sh._char_box_profile(iw, ih, char_boxes or [])
            if prof[0] is None:
                prof = _sh._panel_subject_profile(im)
        x0, y0 = _sh._choose_crop_offset(new_w, new_h, _sh.OUTPUT_W, _sh.OUTPUT_H, scaled_avoid,
                                         detail_cols=prof[0], detail_rows=prof[1])
        rest = ((x0 + _sh.OUTPUT_W / 2) / cover, (y0 + _sh.OUTPUT_H / 2) / cover)
        subject = main_subject(iw, ih, char_boxes, prof)
        max_zoom = CAMERA_MAX_UPSCALE / max(1e-6, cover)
    direction = 1 if shot_id % 2 == 0 else -1
    avoid_rects = ([] if mode == "blur" else
                   [(float(b["x"]), float(b["y"]), float(b["x"]) + float(b["w"]), float(b["y"]) + float(b["h"]))
                    for b in (avoid_boxes or [])])
    plan = plan_camera(aw, ah, duration, subject=subject, rest=rest, direction=direction,
                       intro=intro or reverse, action=action and not reverse, max_zoom=max_zoom,
                       avoid=avoid_rects)
    if reverse:
        plan = plan.reversed()
    if log:
        log(f"[stage5]   camera: {mode} ({reason}); {plan.axis}-travel {plan.note}, "
            f"~{plan.speed:.0f}%W/s")
    return art_png, plan, mode, reason


def render_plan(art_png: Path, plan: CameraPlan, out_path: Path, frames: int, *,
                corner_logo: Path | None = None, preset: str = "medium", crf: int = 18) -> Path:
    """Render `frames` frames of `plan` over `art_png` to an H.264 shot that matches the old
    zoompan shots' contract (OUTPUT_W x OUTPUT_H, FPS CFR, yuv420p, no audio, no SAR/colour tags)."""
    import cv2
    import numpy as np
    W, H, fps = _sh.OUTPUT_W, _sh.OUTPUT_H, _sh.FPS
    img = cv2.imread(str(art_png), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"camera: cannot read {art_png}")
    ih, iw = img.shape[:2]
    if (iw, ih) != (plan.art_w, plan.art_h):
        raise RuntimeError(f"camera: plan is for {plan.art_w}x{plan.art_h}, image is {iw}x{ih}")
    # Prescale once so the SMALLEST window maps ~1:1 onto the output: every frame then samples at
    # ≤ (1+CAMERA_PUSH)x reduction — sharp, no per-frame aliasing — and the resize is done by a
    # proper filter (area down / Lanczos up), not by the per-frame bilinear sampler.
    p = W / max(1e-6, min(plan.start.w, plan.end.w))
    if abs(p - 1.0) > 0.01:
        nw, nh = max(1, int(round(iw * p))), max(1, int(round(ih * p)))
        img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if p < 1 else cv2.INTER_LANCZOS4)
        sx, sy = nw / iw, nh / ih
    else:
        sx = sy = 1.0
    ff = _sh._require_ffmpeg()
    cmd = [ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-framerate", str(fps), "-i", "-"]
    if corner_logo is not None:
        cmd += ["-i", str(corner_logo), "-filter_complex", "[0:v][1:v]overlay=W-w-36:36[v]",
                "-map", "[v]"]
    cmd += ["-frames:v", str(frames), "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-r", str(fps), "-an", str(out_path)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        try:
            for i in range(frames):
                win = window_at(plan, i / (frames - 1) if frames > 1 else 0.0)
                s = win.w * sx / W                              # source px per output px
                x0 = (win.cx - win.w / 2) * sx
                y0 = (win.cy - _win_h(win.w) / 2) * sy
                m = np.array([[s, 0.0, x0 + 0.5 * s - 0.5],
                              [0.0, s, y0 + 0.5 * s - 0.5]], dtype=np.float64)
                frame = cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                       borderMode=cv2.BORDER_REFLECT101)
                proc.stdin.write(frame.tobytes())
            proc.stdin.close()
        except BrokenPipeError:
            pass
        rc = proc.wait()
        if rc != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace")[-800:]
            raise RuntimeError(f"ffmpeg (camera) failed rc={rc}: {tail}")
    return out_path
