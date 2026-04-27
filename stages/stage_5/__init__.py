"""
Stage 5: ffmpeg video assembly.

Reads all the Stage-1..4 artifacts from a project folder and produces
final.mp4 — a 1080×1920 9:16 H.264 30fps Short with:
  - Ken Burns pan/zoom into each scene's panel bbox
  - MrBeast-style ALL-CAPS captions burned in via overlay
  - Cartesia TTS audio as the narration track
  - Hard cuts between scenes (no crossfades)

Requires ffmpeg on PATH. Uses subprocess directly (no moviepy).
"""
from .cli import main

__all__ = ["main"]
