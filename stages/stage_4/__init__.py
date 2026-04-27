"""
Stage 4: Cartesia TTS.

Takes narration.json (Stage 3 output) and produces:
  - audio.wav           — 44.1 kHz PCM, single channel
  - word_timestamps.json — [{word, start, end}] from Cartesia's native alignment
  - scene_timings.json   — scene_id → (start, end) aggregated from words
  - caption_chunks.json  — sentence/phrase chunks (auto-break at 6-8 words)
                           with timing, ready for the video captioner

Uses the Cartesia HTTP SSE endpoint directly (no SDK dep) so we're not locked
to a specific cartesia-python version. Voice defaults to "Comic Vocal" — a
clone of comic.wav (CARTESIA_VOICE_ID) on sonic-2. Speed is configurable.
"""
from .cli import main

__all__ = ["main"]
