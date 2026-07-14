"""Loudness-normalize TTS narration for video mux."""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable


def mix_audio(
    tts_wav: Path,
    out_path: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Loudnorm the TTS narration to broadcast level and write out_path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ff = _require_ffmpeg()
    cmd = [
        ff, "-y",
        "-i", str(tts_wav),
        "-af", "loudnorm=I=-14:TP=-1.0:LRA=9",
        "-ac", "2",
        "-ar", "48000",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    if progress:
        progress(f"[stage5] loudnorm TTS → {out_path.name}")
    _run(cmd)
    return out_path


def _require_ffmpeg() -> str:
    from config import FFMPEG_BIN
    if os.path.isabs(FFMPEG_BIN) and os.path.isfile(FFMPEG_BIN):
        return FFMPEG_BIN
    p = shutil.which(FFMPEG_BIN) or shutil.which("ffmpeg")
    if not p:
        raise FileNotFoundError(f"ffmpeg not found (FFMPEG_BIN={FFMPEG_BIN}). Check .env or PATH.")
    return p


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = (res.stderr or "")[-2000:]
        raise RuntimeError(
            f"ffmpeg failed (exit {res.returncode})\ncmd: {' '.join(cmd)}\nstderr:\n{tail}"
        )
