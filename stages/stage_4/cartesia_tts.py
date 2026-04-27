"""
Cartesia TTS via HTTP SSE (no SDK dep — robust across versions).

Sends ONE request with the full transcript, streams back base64 PCM chunks
and word-level timestamps, and packages everything into a WAV file + a list
of (word, start, end) tuples.

Endpoint:  POST https://api.cartesia.ai/tts/sse
Auth:      X-API-Key header
"""
import base64
import io
import json
import wave
from dataclasses import dataclass

import requests

from config import CARTESIA_API_KEY, CARTESIA_MODEL, CARTESIA_VOICE_ID


_CARTESIA_URL = "https://api.cartesia.ai/tts/sse"
_CARTESIA_VERSION = "2024-11-13"


@dataclass
class CartesiaResult:
    wav_bytes: bytes
    sample_rate: int
    word_timestamps: list[dict]   # [{"word": str, "start": float, "end": float}]


def synthesize(
    text: str,
    *,
    voice_id: str | None = None,
    model: str | None = None,
    speed: float = 1.0,
    sample_rate: int = 44100,
    timeout: int = 120,
) -> CartesiaResult:
    """
    Generate TTS audio + word timestamps for `text`.

    Args:
        text:         script to speak. Newlines/punctuation preserved.
        voice_id:     Cartesia voice UUID. Defaults to CARTESIA_VOICE_ID
                      ("Comic Vocal" — cloned from comic.wav).
        model:        Cartesia model. Defaults to CARTESIA_MODEL (sonic-2).
        speed:        Speaking speed 0.7-1.3. 1.0 = natural.
        sample_rate:  Output WAV sample rate. 44100 is our default for quality.
        timeout:      Request timeout in seconds.

    Returns:
        CartesiaResult with .wav_bytes (WAV header included), .sample_rate,
        and .word_timestamps (list of dicts with word/start/end).
    """
    if not CARTESIA_API_KEY:
        raise RuntimeError("CARTESIA_API_KEY is empty — add it to .env")
    if not text or not text.strip():
        raise ValueError("synthesize() called with empty text")

    body = {
        "model_id": model or CARTESIA_MODEL,
        "transcript": text,
        "voice": {
            "mode": "id",
            "id": voice_id or CARTESIA_VOICE_ID,
            "__experimental_controls": {"speed": speed},
        },
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate,
        },
        "add_timestamps": True,
    }
    headers = {
        "X-API-Key": CARTESIA_API_KEY,
        "Cartesia-Version": _CARTESIA_VERSION,
        "Content-Type": "application/json",
    }

    pcm_chunks: list[bytes] = []
    words: list[dict] = []

    with requests.post(_CARTESIA_URL, headers=headers, json=body,
                       stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Cartesia SSE failed {r.status_code}: {r.text[:400]}")

        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue

            t = evt.get("type", "")
            if t in ("chunk", "audio_chunk") and evt.get("data"):
                try:
                    pcm_chunks.append(base64.b64decode(evt["data"]))
                except (ValueError, TypeError):
                    continue
            elif t in ("word_timestamps", "timestamps"):
                wt = evt.get("word_timestamps") or evt
                ws = wt.get("words", []) or []
                ss = wt.get("start", []) or []
                es = wt.get("end", []) or []
                for w, s, e in zip(ws, ss, es):
                    words.append({"word": str(w), "start": float(s), "end": float(e)})

    if not pcm_chunks:
        raise RuntimeError("Cartesia returned no audio chunks.")

    wav_bytes = _wrap_pcm_as_wav(b"".join(pcm_chunks), sample_rate=sample_rate, sampwidth=2)
    return CartesiaResult(wav_bytes=wav_bytes, sample_rate=sample_rate, word_timestamps=words)


def _wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int, sampwidth: int = 2, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
