"""Stage 5: ffmpeg video assembly.

Reads Stage-1..4 artifacts (narration.json, audio.wav, word_timestamps.json)
and produces final.mp4 — 1080x1920 9:16 H.264 30fps with:
  - 1-3 Ken Burns shots per narration scene (zoom_in / pan_right / zoom_out)
  - Loudness-normalized TTS narration (no BGM)

No captions are burned in (Master 2026-07-10: captioning is done externally in
CapCut). caption_chunks.json / word_timestamps.json are still produced by
Stage 4 and consumed here purely as shot-timing granularity.

Requires ffmpeg on PATH (no pip ffmpeg dep).
"""
from .pipeline import assemble_project
from .cli import main

__all__ = ["assemble_project", "main"]
