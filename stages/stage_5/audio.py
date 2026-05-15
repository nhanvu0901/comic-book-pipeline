"""Mix TTS narration with optional background music + sidechain ducking."""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable


MIX_FILTER = (
    "[1:a]volume=0.22,aloop=loop=-1:size=2e+09[bgm];"
    "[bgm][0:a]sidechaincompress=threshold=0.04:ratio=8:attack=5:release=250[ducked];"
    "[ducked][0:a]amix=inputs=2:duration=first:dropout_transition=0[mixed];"
    "[mixed]loudnorm=I=-14:TP=-1.0:LRA=9[out]"
)


def mix_audio(
    tts_wav: Path,
    bgm_path: Path | None,
    out_path: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Mix narration + BGM (with ducking + loudnorm); fall back to a TTS-only copy."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if bgm_path is None or not Path(bgm_path).exists():
        if progress:
            progress(f"[stage5] no BGM — copying TTS to {out_path.name}")
        shutil.copyfile(tts_wav, out_path)
        return out_path

    ff = _require_ffmpeg()
    cmd = [
        ff, "-y",
        "-i", str(tts_wav),
        "-i", str(bgm_path),
        "-filter_complex", MIX_FILTER,
        "-map", "[out]",
        "-ac", "2",
        "-ar", "48000",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    if progress:
        progress(f"[stage5] mixing TTS + BGM (sidechain duck + loudnorm) → {out_path.name}")
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
