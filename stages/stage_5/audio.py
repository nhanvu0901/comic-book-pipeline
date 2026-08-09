"""Loudness-normalize TTS narration for video mux."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

# EBU R128 target. -14 LUFS is what YouTube normalises to, so hitting it exactly is what keeps
# our video as loud as the one the viewer just watched.
_TARGET_I = -14.0
_TARGET_TP = -1.0
_TARGET_LRA = 9.0


def mix_audio(
    tts_wav: Path,
    out_path: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Loudnorm the TTS narration to broadcast level and write out_path.

    TWO PASSES (changed 2026-07-29, Master approved). Single-pass loudnorm has to normalise the
    opening of the file before it has heard the rest, so it guesses and converges — and lands
    short. Measured on four shipped projects, all of which asked for I=-14:

        loki -15.4 · psylocke -15.7 · wolverine -15.5 · wonder-woman -16.2 LUFS

    1.4-2.2 LU under target, by a different amount each time. YouTube normalises playback to
    ~-14 LUFS, so undershooting means our videos play quieter than whatever the viewer watched
    before — and since the miss varies per file, it is ALSO a loudness inconsistency between our
    own videos. Pass 1 measures and prints JSON; pass 2 feeds those measurements back so ffmpeg
    applies one correct gain instead of guessing. Costs one extra read of the file.

    Note this fixes loudness ACROSS videos, not the prosody wobble WITHIN one — that is the TTS
    model itself and normalisation cannot touch it. See
    project_audio_inconsistency_diagnosed_2026-07-29.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log = progress or (lambda _m: None)

    ff = _require_ffmpeg()
    base = f"loudnorm=I={_TARGET_I}:TP={_TARGET_TP}:LRA={_TARGET_LRA}"

    measured = _measure(ff, tts_wav, base, log)
    af = base
    if measured:
        af = (f"{base}:measured_I={measured['input_i']}"
              f":measured_TP={measured['input_tp']}"
              f":measured_LRA={measured['input_lra']}"
              f":measured_thresh={measured['input_thresh']}"
              f":offset={measured['target_offset']}:linear=true")
        log(f"[stage5] loudnorm pass 1: measured I={measured['input_i']} LUFS, "
            f"LRA={measured['input_lra']} LU → applying a single corrective gain")
    else:
        # Measurement failed (odd ffmpeg build, unparseable output). One pass still produces
        # usable audio, just off target — better than failing the whole render for it.
        log("[stage5] loudnorm pass 1 gave no measurements — falling back to single-pass")

    log(f"[stage5] loudnorm TTS → {out_path.name}")
    _run([ff, "-y", "-i", str(tts_wav), "-af", af,
          "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(out_path)])
    return out_path


_MEASURED_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def _measure(ff: str, src: Path, base_filter: str, log: Callable[[str], None]) -> dict | None:
    """Pass 1: analyse only. `-f null` writes no audio, so this is a read, not an encode.

    loudnorm prints its JSON block to stderr. Returns None (never raises) when anything about
    that is unexpected — a normalisation nicety must not be able to fail a render."""
    res = subprocess.run(
        [ff, "-hide_banner", "-nostdin", "-i", str(src),
         "-af", f"{base_filter}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    blob = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr or "", re.S)
    if not blob:
        return None
    try:
        data = json.loads(blob.group(0))
    except json.JSONDecodeError:
        return None
    out = {}
    for k in _MEASURED_KEYS:
        v = data.get(k)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        # A digital-silence input measures -inf and would poison the second pass.
        if f != f or abs(f) == float("inf"):
            return None
        out[k] = v
    return out


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
