"""Whole-sprite motion: cues → per-frame transform (no skeletal animation).

Ported from the approved demo (todo/avatar-narrator/demo/demo.py: mascot_state, the easing
helpers, enter/exit/accents/pose-swap pop, idle float + breathe, talk bounce).

Rules (see config.MOVES_*):
  * The mascot is hidden until the first ENTER (or the first non-EXIT cue, which then acts as
    "rise"). An EXIT hides it until the next ENTER/non-EXIT cue.
  * Between cues it HOLDS the last body/face, with the idle float/breathe always running.
  * A cue that changes body or face while visible swaps the pose at the scale peak of a short
    pop (0.32 s, centred just before the cue time) — on every move, not just "pop".
  * A cue with its own position while visible glides there (~0.35 s ease-in-out); an ENTER
    appears directly at its position.
  * "peek" enters from / "slide_out" leaves toward the side edge nearest to the position x.
  * Motion strength (config.MOTION_LEVELS) scales the idle and accent amplitudes:
    calm 0.6, normal 1.0, lively 1.45.
  * The feet never go below the configured feet line: the idle float only lifts.

Implementation notes: every pixel amplitude is scaled by frame_h / 1920 (the demo's frame), so
a 540x960 preview moves exactly like the 1080x1920 render. The pop is symmetric around the cue
time (t-0.16 .. t+0.16): the pose swaps at the peak, i.e. exactly at the cue time. Scale and
rotation are applied about the FEET point, so the feet line stays put.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import MOVES_ENTER, MOVES_EXIT, MascotConfig, Position
from .kit import Kit, resolve_pose, sprite_height_px, sprite_metrics

MOTION_STRENGTH = {"calm": 0.6, "normal": 1.0, "lively": 1.45}

REF_H = 1920.0            # the demo frame height every pixel amplitude was tuned on
ENTER_DUR = 0.45
EXIT_DUR = 0.40
GLIDE_DUR = 0.35
POP_DUR = 0.32
POP_AMP = 0.16
ACCENT_DUR = {"hop": 0.38, "tilt": 0.7, "shake": 0.6}


@dataclass
class SpriteState:
    body: str
    face: str
    cx: float          # sprite centre x, in frame px
    feet_y: float      # feet line y, in frame px
    sx: float          # scale multipliers on top of the configured size
    sy: float
    rot: float         # degrees, counter-clockwise positive (PIL convention)
    alpha: float       # 0..1
    blur: float        # motion-blur radius px (enter/exit only)


@dataclass
class Key:
    """A cue seen from a visible segment: the pose/position held from time t on."""
    t: float
    body: str
    face: str
    x: float                    # fractions of the frame (Position semantics)
    y: float
    src_x: float                # where the glide to (x, y) starts from
    src_y: float
    pop: bool = False           # pose change or an explicit "pop": play the scale pulse


@dataclass
class Segment:
    t0: float                   # enter time
    t1: float | None            # exit time (None = stays to the end)
    enter: str                  # "rise" | "peek"
    exit: str | None            # "sink" | "slide_out" | None
    keys: list[Key] = field(default_factory=list)
    accents: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class Track:
    """Precomputed plan for one config: visible segments with their pose and position keys,
    accents and glides. Build once per render, query per frame."""
    frame_w: int
    frame_h: int
    duration: float
    segments: list
    strength: float
    talk_bounce: bool
    sprite_w: float = 0.0       # compose() size at this frame size, px
    sprite_h: float = 0.0


# ── easing (from the demo) ───────────────────────────────────────────────────────────────
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def out_back(u, k=1.70158):
    u = clamp(u) - 1
    return 1 + (k + 1) * u ** 3 + k * u ** 2


def in_back(u, k=1.70158):
    u = clamp(u)
    return (k + 1) * u ** 3 - k * u ** 2


def in_out_sine(u):
    return 0.5 - 0.5 * math.cos(math.pi * clamp(u))


def _pos_at(key: Key, t: float) -> tuple[float, float]:
    u = in_out_sine((t - key.t) / GLIDE_DUR)
    return key.src_x + (key.x - key.src_x) * u, key.src_y + (key.y - key.src_y) * u


def _key_at(seg: Segment, t: float) -> Key:
    k = seg.keys[0]
    for c in seg.keys[1:]:
        if c.t <= t:
            k = c
        else:
            break
    return k


def build_track(cfg: MascotConfig, kit: Kit, duration: float, frame_w: int, frame_h: int) -> Track:
    strength = MOTION_STRENGTH.get(cfg.motion, 1.0)
    h = sprite_height_px(cfg.size_pct, frame_h)
    d_body, d_face = resolve_pose(kit, None, None)
    m = sprite_metrics(kit, d_body, h)
    track = Track(frame_w=int(frame_w), frame_h=int(frame_h), duration=float(duration), segments=[],
                  strength=strength, talk_bounce=bool(cfg.talk_bounce),
                  sprite_w=float(m["w"]), sprite_h=float(m["h"]))
    body, face = d_body, d_face
    seg: Segment | None = None
    for cue in cfg.sorted_cues():
        pos = cue.position if cue.position is not None else cfg.position
        px, py = pos.x, pos.y
        nb, nf = resolve_pose(kit, cue.body or body, cue.face or face)
        if seg is None:
            if cue.move in MOVES_EXIT:
                continue                                  # nothing to hide
            body, face = nb, nf
            seg = Segment(t0=cue.t, t1=None, enter=cue.move if cue.move in MOVES_ENTER else "rise",
                          exit=None, keys=[Key(cue.t, body, face, px, py, px, py)])
            track.segments.append(seg)
            continue
        if cue.move in MOVES_EXIT:
            seg.t1, seg.exit = cue.t, cue.move
            seg = None
            continue
        prev = seg.keys[-1]
        sx, sy = _pos_at(prev, cue.t)
        changed = (nb, nf) != (body, face)
        body, face = nb, nf
        seg.keys.append(Key(cue.t, body, face, px, py, sx, sy, pop=changed or cue.move == "pop"))
        if cue.move in ACCENT_DUR:
            seg.accents.append((cue.move, cue.t))
    return track


def _segment_end(track: Track, i: int) -> float:
    seg = track.segments[i]
    end = track.duration if seg.t1 is None else seg.t1 + EXIT_DUR
    if i + 1 < len(track.segments):
        end = min(end, track.segments[i + 1].t0)
    return end


def _edge_sign(cx_frac: float) -> int:
    """-1 = the left edge is nearer, +1 = the right one."""
    return -1 if cx_frac < 0.5 else 1


def state_at(track: Track, t: float, env: float = 0.0) -> SpriteState | None:
    """Transform at time t (seconds). env = 0..1 voice level for the talk bounce (ignored when
    track.talk_bounce is False). None = hidden."""
    si = -1
    for i in range(len(track.segments) - 1, -1, -1):
        if t >= track.segments[i].t0:
            si = i
            break
    if si < 0 or t >= _segment_end(track, si):
        return None
    seg = track.segments[si]
    s = track.frame_h / REF_H
    st = track.strength
    key = _key_at(seg, t)
    fx, fy = _pos_at(key, t)
    cx = fx * track.frame_w
    feet = fy * track.frame_h
    sx = sy = 1.0
    rot = 0.0
    blur = 0.0

    # idle: drift, float (lifts only), tilt and breathe: never fully still
    cx += 4 * s * st * math.sin(2 * math.pi * t / 3.7)
    feet -= 7 * s * st * (1 + math.sin(2 * math.pi * t / 2.6))
    rot += 1.6 * st * math.sin(2 * math.pi * t / 3.3)
    breathe = 0.012 * st * math.sin(2 * math.pi * t / 2.2)
    sx += breathe
    sy -= breathe
    if track.talk_bounce:
        e = clamp(env)
        sy += 0.035 * e
        sx -= 0.015 * e

    sprite_h, sprite_w = track.sprite_h, track.sprite_w
    # enter
    ue = (t - seg.t0) / ENTER_DUR
    if 0 <= ue < 1:
        inv = 1 - out_back(ue)
        if seg.enter == "peek":
            sg = _edge_sign(fx)
            dist = (cx if sg < 0 else track.frame_w - cx) + sprite_w * 0.6
            cx += sg * dist * inv
            rot += -sg * 14 * inv
        else:
            feet += 1.15 * sprite_h * inv
            rot += -10 * inv
        blur = 10 * s * (1 - clamp(ue * 1.6))
    # exit
    if seg.t1 is not None:
        ux = (t - seg.t1) / EXIT_DUR
        if ux > 0:
            if ux >= 1:
                return None
            ib = in_back(ux)
            if seg.exit == "slide_out":
                ex = _pos_at(_key_at(seg, seg.t1), seg.t1)[0]
                sg = _edge_sign(ex)
                dist = ((fx * track.frame_w) if sg < 0 else track.frame_w - fx * track.frame_w) + sprite_w * 0.6
                cx += sg * dist * ib
                rot += sg * 12 * ib
            else:
                feet += 1.15 * sprite_h * ib
            blur = max(blur, 9 * s * clamp(ux * 1.5))

    # pose swap: the pop peaks at the key time, where the pose changes
    body, face = key.body, key.face
    for k in seg.keys[1:]:
        if k.pop:
            up = (t - k.t + POP_DUR / 2) / POP_DUR
            if 0 <= up <= 1:
                kk = math.sin(math.pi * up) * POP_AMP
                sx += kk
                sy += kk
    # accents
    for name, ta in seg.accents:
        ua = (t - ta) / ACCENT_DUR[name]
        if 0 <= ua <= 1:
            if name == "hop":
                feet -= 70 * s * st * math.sin(math.pi * ua)
                if ua > 0.85:
                    sy -= 0.08 * (1 - ua) / 0.15
                    sx += 0.05 * (1 - ua) / 0.15
            elif name == "tilt":
                rot += 9 * st * math.sin(math.pi * ua)
            else:
                decay = math.exp(-4.5 * ua)
                cx += 16 * s * st * math.sin(2 * math.pi * 16 * ua) * decay
                rot += 4 * st * math.sin(2 * math.pi * 11 * ua) * decay
    return SpriteState(body=body, face=face, cx=cx, feet_y=feet, sx=sx, sy=sy, rot=rot,
                       alpha=1.0, blur=blur)


def visible_intervals(track: Track) -> list[tuple[float, float]]:
    """[(t0, t1)] during which the mascot is on screen — for the UI timeline."""
    out = []
    for i, seg in enumerate(track.segments):
        end = _segment_end(track, i)
        if end > seg.t0:
            out.append((seg.t0, end))
    return out
