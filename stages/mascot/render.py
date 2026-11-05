"""Render the mascot onto the finished Short — the last step of the pipeline (UI step 9).

final.mp4 handling (Master's decision, design doc section 8, decision 4):
  * The mascot output is written straight to ``projects/<slug>/final.mp4``.
  * The first render moves the clean Stage-5 video to ``projects/<slug>/mascot/final_clean.mp4``
    (NOT under _stage5/, which "Delete _stage5 scratch" wipes). Every mascot render reads the
    clean copy, so mascots never stack.
  * ``mascot/render_state.json`` records the signature (size + mtime_ns) of the final.mp4 the
    mascot step wrote. If final.mp4 no longer matches it, Stage 5 re-rendered: that final.mp4
    is the new clean copy.
  * Disabling the mascot restores the clean copy to final.mp4.
  * final.mp4 is replaced atomically (render to a temp file in the same folder, verify with
    ffprobe, then os.replace). A failed render never leaves a broken final.mp4.

Output contract = Stage 5's: same W×H and fps as the source (1080×1920, 30 fps), H.264
yuv420p, CRF 18, audio stream copied untouched. Talk bounce reads the narration-only
``audio.wav`` when present (so music does not bounce it), else the source video's audio.
Frames are decoded with an ffmpeg rawvideo pipe, composited with PIL, piped to the encoder.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .config import MascotConfig

Progress = Callable[[float], None]      # 0..1
Log = Callable[[str], None]


def mascot_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "mascot"


def source_video(project_dir: Path) -> Path | None:
    """The CLEAN video to draw on: mascot/final_clean.mp4 when final.mp4 is our own mascot
    output, otherwise final.mp4 itself (fresh from Stage 5). None when neither exists.
    Pure lookup — never copies or moves anything."""
    raise NotImplementedError


def has_mascot_output(project_dir: Path) -> bool:
    """True when final.mp4 is the file the mascot step last wrote."""
    raise NotImplementedError


def ensure_clean_copy(project_dir: Path) -> Path:
    """Make mascot/final_clean.mp4 current (copy final.mp4 when it is a fresh Stage-5 render)
    and return it. Raises FileNotFoundError when there is no video at all."""
    raise NotImplementedError


def restore_clean(project_dir: Path) -> bool:
    """Put the clean copy back as final.mp4 (mascot off). True when something was restored."""
    raise NotImplementedError


def frame_at(project_dir: Path, t: float, *, width: int = 540) -> Path:
    """JPEG of the CLEAN video at time t, scaled to `width` (aspect kept), cached under
    mascot/cache/frames/ keyed by (source signature, t rounded to 1/30 s, width)."""
    raise NotImplementedError


def render_preview(project_dir: Path, cfg: MascotConfig, t_center: float, *, span: float = 3.0,
                   width: int = 540, progress: Progress | None = None, log: Log | None = None) -> Path:
    """A short low-res clip (span seconds around t_center, clamped to the video) WITH audio,
    written to mascot/preview.mp4 (overwritten each time). Same motion maths as the full
    render, scaled to the preview size, so what Master sees is what renders."""
    raise NotImplementedError


def render_final(project_dir: Path, cfg: MascotConfig, *, progress: Progress | None = None,
                 log: Log | None = None) -> Path:
    """Full render to final.mp4 (see module docstring). cfg.enabled False → restore_clean()
    and return final.mp4. Also saves cfg as mascot.json so the file on disk matches the video."""
    raise NotImplementedError
