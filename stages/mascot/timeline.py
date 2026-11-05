"""What the cue timeline shows and snaps to — built from data every project already has.

Sources (each optional; missing files just mean fewer markers):
  * duration ......... ffprobe of render.source_video()
  * cuts ............. shot boundaries: cumulative ``duration_seconds`` of projects/<slug>/shots.json
                       when present, else cumulative durations of shots/shot_*.mp4, else the
                       scene starts from scene_timings.json
  * scenes ........... scene_timings.json [{scene_id, start, end}]
  * beats ............ narration.json "beats" [{id, function, name}] mapped to time through the
                       scenes that carry that beat_id (start of the first such scene)
  * words ............ word_timestamps.json [{word, start, end}]
"""
from __future__ import annotations

from pathlib import Path


def timeline_data(project_dir: Path) -> dict:
    """{"duration": float,
        "cuts":   [t, ...],
        "scenes": [{"scene_id", "t", "end"}],
        "beats":  [{"id", "function", "name", "t"}],
        "words":  [{"w", "t", "end"}]}   — all times in seconds, sorted."""
    raise NotImplementedError


def snap_time(t: float, data: dict, *, threshold: float = 0.25,
              kinds: tuple[str, ...] = ("cuts", "beats", "words")) -> float:
    """The nearest cut / beat start / word start within `threshold` s, else t unchanged.
    Cuts win over beats over words when equally near."""
    raise NotImplementedError


def waveform_png(project_dir: Path, width: int, height: int) -> Path:
    """Narration waveform (audio.wav, else the video's audio) as a transparent PNG, cached
    under mascot/cache/ keyed by (audio signature, width, height)."""
    raise NotImplementedError


def thumb_strip_png(project_dir: Path, width: int, height: int) -> Path:
    """One frame per shot (taken at each shot's middle, from the clean video) laid out along
    the time axis to `width` px, cached under mascot/cache/. Works for every mode because it
    reads the video, not mode-specific files."""
    raise NotImplementedError
