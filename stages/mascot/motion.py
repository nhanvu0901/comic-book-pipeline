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
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import MascotConfig
from .kit import Kit

MOTION_STRENGTH = {"calm": 0.6, "normal": 1.0, "lively": 1.45}


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
class Track:
    """Precomputed plan for one config: visible segments with their pose and position keys,
    accents and glides. Build once per render, query per frame."""
    frame_w: int
    frame_h: int
    duration: float
    segments: list
    strength: float
    talk_bounce: bool


def build_track(cfg: MascotConfig, kit: Kit, duration: float, frame_w: int, frame_h: int) -> Track:
    raise NotImplementedError


def state_at(track: Track, t: float, env: float = 0.0) -> SpriteState | None:
    """Transform at time t (seconds). env = 0..1 voice level for the talk bounce (ignored when
    track.talk_bounce is False). None = hidden."""
    raise NotImplementedError


def visible_intervals(track: Track) -> list[tuple[float, float]]:
    """[(t0, t1)] during which the mascot is on screen — for the UI timeline."""
    raise NotImplementedError
