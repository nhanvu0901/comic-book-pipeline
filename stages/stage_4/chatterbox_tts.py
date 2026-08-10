"""LOCAL Chatterbox TTS provider — same contract as resemble_tts.synthesize().

Careful with the name: `TTS_PROVIDER=resemble` is ALSO Chatterbox, but Resemble's hosted
one. This module is the open-source model running on this machine, and the reason to want
it is the emotion knob (`exaggeration`) the hosted API does not expose.

It runs in a SEPARATE venv (.venv-chatterbox) and is driven over a JSON job file — see
_chatterbox_worker.py for why (chatterbox-tts would drag huggingface_hub 1.x over the
Magi/transformers install that Stage 2 depends on).

WORD TIMESTAMPS. Chatterbox returns audio only. Rather than spread words evenly across a
whole 19-minute file — which would drift by many seconds and drag every panel cut with it —
this synthesizes ONE CHUNK PER SENTENCE and spreads each sentence's words inside its OWN
measured duration. Stage 3 emits one scene per sentence, so every scene boundary lands on
real audio and only the words inside a single sentence carry any error.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_VENV = Path(os.getenv("CHATTERBOX_VENV", _REPO / ".venv-chatterbox")) / "bin" / "python"
_WORKER = Path(__file__).resolve().parent / "_chatterbox_worker.py"

# Emotion knobs (Chatterbox's own scale). 0.5/0.5 is the model's neutral default; the
# narration can override per scene once we know what a long read sounds like.
CHATTERBOX_EXAGGERATION = float(os.getenv("CHATTERBOX_EXAGGERATION", "0.5"))
CHATTERBOX_CFG_WEIGHT = float(os.getenv("CHATTERBOX_CFG_WEIGHT", "0.5"))
CHATTERBOX_TEMPERATURE = float(os.getenv("CHATTERBOX_TEMPERATURE", "0.8"))
# Reference wav to clone. This DEFAULT is the whole reason the channel voice survives the
# switch to a local model: leave it empty and Chatterbox speaks in its own built-in voice, so
# the narrator would change silently between one render and the next — the exact failure that
# shipped three different voices across five videos before select_voice() was pinned off.
_DEFAULT_VOICE_WAV = _REPO / "assets" / "voices" / "arthur_ref.wav"
CHATTERBOX_VOICE_WAV = (os.getenv("CHATTERBOX_VOICE_WAV", "").strip()
                        or (str(_DEFAULT_VOICE_WAV) if _DEFAULT_VOICE_WAV.exists() else ""))
CHATTERBOX_DEVICE = os.getenv("CHATTERBOX_DEVICE", "").strip()
# A chunk much longer than this reads unevenly and risks the model losing the thread.
_MAX_CHUNK_CHARS = int(os.getenv("CHATTERBOX_MAX_CHARS", "320"))

_SENT_END = re.compile(r'(?<=[.!?])["”\')]*\s+(?=["“(\[]?[A-Z0-9])')


@dataclass
class ChatterboxResult:
    wav_bytes: bytes
    sample_rate: int
    word_timestamps: list[dict] = field(default_factory=list)


def available() -> bool:
    """True when the isolated venv exists. Checked before a run so the failure is a clear
    message instead of a FileNotFoundError deep inside subprocess."""
    return _VENV.exists()


def _chunks(text: str) -> list[str]:
    """One chunk per sentence; a sentence over the char cap splits again at a comma."""
    out: list[str] = []
    for sent in [s.strip() for s in _SENT_END.split(" ".join(str(text or "").split())) if s.strip()]:
        if len(sent) <= _MAX_CHUNK_CHARS:
            out.append(sent)
            continue
        buf = ""
        for part in re.split(r"(?<=,)\s+", sent):
            if buf and len(buf) + len(part) + 1 > _MAX_CHUNK_CHARS:
                out.append(buf)
                buf = part
            else:
                buf = f"{buf} {part}".strip()
        if buf:
            out.append(buf)
    return out


def _even_words(text: str, start: float, dur: float) -> list[dict]:
    """Spread one chunk's words across its own measured span. Error stays inside the
    sentence — the chunk's start and end are real."""
    words = text.split()
    if not words or dur <= 0:
        return []
    step = dur / len(words)
    return [{"word": w, "start": round(start + i * step, 4),
             "end": round(start + (i + 1) * step, 4)} for i, w in enumerate(words)]


def _read_wav(path: Path) -> tuple[bytes, int, int, int]:
    with wave.open(str(path), "rb") as wf:
        return (wf.readframes(wf.getnframes()), wf.getframerate(),
                wf.getsampwidth(), wf.getnchannels())


def _wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int, sampwidth: int, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def synthesize(
    text: str,
    *,
    voice_id: str | None = None,
    sample_rate: int = 44100,   # accepted for interface parity; the model's own rate wins
    timeout: int = 7200,        # a 19-minute longform read is ~380 chunks
    log=None,
    exaggeration: float | None = None,
    cfg_weight: float | None = None,
    **_ignored,
) -> ChatterboxResult:
    """Generate audio + word timestamps locally. Same result shape as the other providers."""
    _log = log or (lambda _m: None)
    if not text or not text.strip():
        raise ValueError("synthesize() called with empty text")
    if not available():
        raise RuntimeError(
            f"Chatterbox venv missing at {_VENV.parent.parent}. Create it with:\n"
            f"  python3 -m venv .venv-chatterbox && "
            f".venv-chatterbox/bin/python -m pip install chatterbox-tts")

    chunks = _chunks(text)
    ex = CHATTERBOX_EXAGGERATION if exaggeration is None else float(exaggeration)
    cfg = CHATTERBOX_CFG_WEIGHT if cfg_weight is None else float(cfg_weight)
    _log(f"[chatterbox] {len(chunks)} chunk(s), exaggeration={ex}, cfg_weight={cfg}"
         + (f", voice={Path(voice_id or CHATTERBOX_VOICE_WAV).name}"
            if (voice_id or CHATTERBOX_VOICE_WAV) else ", built-in voice"))

    tmp = Path(tempfile.mkdtemp(prefix="chatterbox_"))
    job = tmp / "job.json"
    job.write_text(json.dumps({
        "chunks": [{"text": c, "exaggeration": ex, "cfg_weight": cfg} for c in chunks],
        "out_dir": str(tmp / "wav"),
        "audio_prompt": (voice_id or CHATTERBOX_VOICE_WAV) or None,
        "temperature": CHATTERBOX_TEMPERATURE,
        "device": CHATTERBOX_DEVICE or None,
    }))

    proc = subprocess.Popen([str(_VENV), str(_WORKER), str(job)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    done, failed, tail = 0, [], []
    try:
        for line in proc.stdout:                       # progress, not a silent 40-minute wait
            line = line.strip()
            if not line:
                continue
            if not line.startswith("{"):
                # NOT skipped: the worker's stderr is merged in here, and dropping it is how
                # a hard import failure showed up as a bare "produced no audio".
                tail.append(line)
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("ready"):
                _log(f"[chatterbox] model loaded on {msg.get('device')} @ {msg.get('sr')}Hz")
            elif msg.get("error"):
                failed.append(int(msg.get("i", -1)))
                _log(f"[chatterbox] ⚠ chunk {msg.get('i')} failed: {msg['error'][:120]}")
            elif "sec" in msg:
                done += 1
                if done % 25 == 0 or done == len(chunks):
                    _log(f"[chatterbox] {done}/{len(chunks)} chunks")
        proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()

    wav_dir = tmp / "wav"
    pcm_parts: list[bytes] = []
    words: list[dict] = []
    offset = 0.0
    sr = sampwidth = channels = 0
    for i, chunk_text in enumerate(chunks):
        path = wav_dir / f"chunk_{i:05d}.wav"
        if not path.exists():
            continue                                   # a failed chunk: no audio, no words
        pcm, sr, sampwidth, channels = _read_wav(path)
        dur = len(pcm) / float(sr * sampwidth * channels)
        pcm_parts.append(pcm)
        words.extend(_even_words(chunk_text, offset, dur))
        offset += dur
    if not pcm_parts:
        raise RuntimeError("Chatterbox produced no audio. Worker output:\n  "
                           + "\n  ".join(tail[-12:] or ["(nothing)"]))
    if failed:
        # Loud, not silent: missing chunks mean missing WORDS, and Stage 5 cuts panels off
        # word positions, so a quiet gap here would desync the back half of the video.
        _log(f"[chatterbox] ⚠ {len(failed)} chunk(s) produced no audio and were DROPPED "
             f"from both the wav and the timestamps — the script is now incomplete")
    _log(f"[chatterbox] {offset:.1f}s of audio, {len(words)} word timestamps")
    return ChatterboxResult(
        wav_bytes=_wrap_pcm_as_wav(b"".join(pcm_parts), sample_rate=sr,
                                   sampwidth=sampwidth, channels=channels),
        sample_rate=sr, word_timestamps=words)
