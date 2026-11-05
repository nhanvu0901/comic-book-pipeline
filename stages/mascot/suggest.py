"""'Gợi ý tự động': propose cues from the beat structure — general rules, never per comic.

Rules (design doc section 3):
  * first beat (cold open / hook) → "rise" with the kit preset "point" near the first word
  * climax / twist / reveal beats → "shake" with the preset "shock" at the beat start
  * landing / resolution beats → "pop" with "think" (or "sly" when the kit has it)
  * hide between highlights so the mascot is on screen roughly half the video ("sink" at the
    next cut a few seconds after a highlight, "peek" back for the next one)
  * the end → "slide_out" ~0.5 s before the video ends
  * every time is snapped to the nearest cut / beat / word start (timeline.snap_time)
  * LOCKED cues are kept exactly; no new cue is placed within 0.8 s of a locked cue;
    unlocked cues from an earlier suggestion are replaced
Missing beats → fall back to scene starts; missing everything → enter at 0.2 s, exit at the end.
Only pose names that exist in the kit are used (kit.resolve_pose / kit.presets).
"""
from __future__ import annotations

from pathlib import Path

from .config import Cue, MascotConfig
from .kit import Kit


def suggest_cues(project_dir: Path, cfg: MascotConfig, kit: Kit, data: dict | None = None) -> list[Cue]:
    """The full new cue list (locked cues + fresh suggestions), sorted by t. `data` is
    timeline.timeline_data(project_dir) when the caller already has it."""
    raise NotImplementedError


def clear_suggestions(cfg: MascotConfig) -> list[Cue]:
    """'Xoá gợi ý': only the locked cues."""
    return [c for c in cfg.sorted_cues() if c.locked]
