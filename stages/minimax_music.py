"""Authenticated adapter for the project's private MiniMax Music 3 HF Space."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from gradio_client import Client

from config import HF_MUSIC_GUIDANCE, HF_MUSIC_HEADROOM, HF_MUSIC_SPACE, HF_MUSIC_STEPS


def _probe_duration(path: Path) -> float:
    probe = shutil.which("ffprobe")
    if not probe or not path.is_file():
        return 0.0
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=False,
        )
        return float((result.stdout or "").strip()) if result.returncode == 0 else 0.0
    except (OSError, ValueError):
        return 0.0


def _transcode_and_validate(source: Path, out_path: Path, duration_seconds: float, *, log=print) -> Path | None:
    """Convert downloaded audio to MP3 only when it covers the completed video."""
    out_path.unlink(missing_ok=True)
    source_duration = _probe_duration(source)
    if source_duration < duration_seconds:
        log(f"[music] MiniMax returned {source_duration:.2f}s for {duration_seconds:.2f}s video — narration-only")
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log("[music] ffmpeg unavailable for MiniMax transcode — narration-only")
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out_path.stem}.", suffix=".mp3", dir=out_path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(source), "-vn", "-codec:a", "libmp3lame",
             "-q:a", "2", str(tmp)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or _probe_duration(tmp) < duration_seconds:
            detail = (result.stderr or "transcode duration invalid")[-500:]
            log(f"[music] MiniMax transcode invalid ({detail}) — narration-only")
            return None
        tmp.replace(out_path)
        return out_path
    finally:
        tmp.unlink(missing_ok=True)
        if not out_path.exists():
            out_path.unlink(missing_ok=True)


def generate_music(state: dict, duration_seconds: float, out_path: Path, *, log=print) -> Path | None:
    """Generate one Studio score, or cleanly return None so Stage 5 can ship without music."""
    out_path.unlink(missing_ok=True)
    try:
        client = Client(
            HF_MUSIC_SPACE,
            token=os.getenv("HF_TOKEN") or None,
            verbose=False,
            httpx_kwargs={"timeout": 180.0},
        )
        result = client.predict(
            state=state,
            duration=float(duration_seconds),
            seed=0,
            randomize_seed=True,
            headroom=HF_MUSIC_HEADROOM,
            steps=HF_MUSIC_STEPS,
            guidance=HF_MUSIC_GUIDANCE,
            api_name="/studio_generate",
        )
        source = Path(result[2])
        if not source.is_file():
            raise RuntimeError("Space returned no downloadable audio")
        return _transcode_and_validate(source, out_path, float(duration_seconds), log=log)
    except Exception as exc:  # remote queues/quotas must never fail Stage 5
        out_path.unlink(missing_ok=True)
        log(f"[music] MiniMax failed ({exc}) — narration-only")
        return None
