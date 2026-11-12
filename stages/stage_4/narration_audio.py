"""Post-processing of the FINAL narration wav: where speech starts, how fast it is read.

Two general mechanisms, both provider-agnostic (they read the rendered wav, never the TTS
engine's own idea of its timing):

* LEADING-SILENCE TRIM. Every TTS engine we use opens its file with ~0.15-0.25 s of model
  silence (measured on shipped Shorts: first sound at 0.21 s, first 100 ms at -58 dBFS), and
  YouTube decides in the first second whether a viewer swipes. `trim_leading_silence` finds
  the first SUSTAINED sound above a threshold, keeps a few milliseconds of pre-roll so the first
  consonant's attack is not clipped, and drops whole frames before it — sample-exact, no
  re-encode. The caller shifts every timing by exactly the returned amount (`shift_words`).

* ADAPTIVE PACE. A fixed atempo gives a different words-per-second for every voice and script.
  `choose_atempo` turns a measured pace into the factor that reaches a target, clamped so the
  voice never sounds chipmunked.

The sound detector works on short RMS windows and requires a short RUN of loud windows, so a
lone click or breath blip in the model silence does not count as speech.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Detector geometry. 5 ms windows resolve a consonant onset; a 15 ms run (3 windows) is the
# shortest stretch we call "sound" — shorter blips are clicks or breath noise in model silence.
_WIN_S = 0.005
_MIN_RUN_WINDOWS = 3

# Hard rails for the env knobs — values outside these are misconfiguration, not taste.
_THRESH_DB_RANGE = (-80.0, -20.0)
_KEEP_MS_RANGE = (0.0, 200.0)
_MAX_TRIM_S_RANGE = (0.0, 10.0)
_ATEMPO_LEGAL = (0.5, 2.0)          # ffmpeg's single-stage atempo range
_TARGET_WPS_RANGE = (1.0, 8.0)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


@dataclass(frozen=True)
class WavData:
    samples: np.ndarray      # float32, mono mixdown in [-1, 1]
    sample_rate: int
    n_frames: int


def read_wav_mono(path: Path) -> WavData:
    """Decode an integer-PCM wav (8/16/24/32-bit, any channel count) to a float mono mixdown."""
    with wave.open(str(path), "rb") as wf:
        sr, sw, ch, n = wf.getframerate(), wf.getsampwidth(), wf.getnchannels(), wf.getnframes()
        raw = wf.readframes(n)
    if sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / float(1 << 23)
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if ch > 1:
        x = x[: (len(x) // ch) * ch].reshape(-1, ch).mean(axis=1)
    return WavData(samples=x, sample_rate=sr, n_frames=len(x))


def _window_db(x: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    win = max(1, int(round(sr * _WIN_S)))
    n = len(x) // win
    if n == 0:
        return np.zeros(0, dtype=np.float32), win
    blocks = x[: n * win].reshape(n, win).astype(np.float64)
    rms = np.sqrt((blocks ** 2).mean(axis=1))
    return 20.0 * np.log10(rms + 1e-12), win


def _first_run(loud: np.ndarray, run: int) -> int | None:
    """Index of the first window that starts `run` consecutive loud windows."""
    if len(loud) < run:
        return None
    c = np.convolve(loud.astype(np.int32), np.ones(run, dtype=np.int32), mode="valid")
    hits = np.flatnonzero(c >= run)
    return int(hits[0]) if hits.size else None


def sound_span(data: WavData, threshold_db: float) -> tuple[float, float] | None:
    """(first, last) second of SUSTAINED sound above `threshold_db` dBFS, or None (all quiet)."""
    db, win = _window_db(data.samples, data.sample_rate)
    loud = db > threshold_db
    first = _first_run(loud, _MIN_RUN_WINDOWS)
    if first is None:
        return None
    last_rev = _first_run(loud[::-1], _MIN_RUN_WINDOWS)
    last_end = len(loud) - (last_rev or 0)            # exclusive window index
    sr = float(data.sample_rate)
    return first * win / sr, min(data.n_frames, last_end * win) / sr


def leading_trim_frames(data: WavData, *, threshold_db: float, keep_ms: float,
                        max_trim_s: float) -> int:
    """How many frames of leading silence to drop (0 when the sound already starts at once,
    when nothing is loud, or when the knobs disable it)."""
    threshold_db = _clamp(threshold_db, *_THRESH_DB_RANGE)
    keep_ms = _clamp(keep_ms, *_KEEP_MS_RANGE)
    max_trim_s = _clamp(max_trim_s, *_MAX_TRIM_S_RANGE)
    span = sound_span(data, threshold_db)
    if span is None or max_trim_s <= 0:
        return 0
    onset = span[0]
    cut_s = min(max_trim_s, max(0.0, onset - keep_ms / 1000.0))
    return int(cut_s * data.sample_rate)


def trim_leading_silence(path: Path, *, threshold_db: float, keep_ms: float,
                         max_trim_s: float) -> float:
    """Drop the wav's leading silence in place; return the seconds removed (exactly
    frames / sample_rate). The remaining frames are copied byte-for-byte — no re-encode."""
    data = read_wav_mono(path)
    cut = leading_trim_frames(data, threshold_db=threshold_db, keep_ms=keep_ms,
                              max_trim_s=max_trim_s)
    if cut <= 0:
        return 0.0
    with wave.open(str(path), "rb") as wf:
        params = wf.getparams()
        frame_bytes = params.sampwidth * params.nchannels
        raw = wf.readframes(params.nframes)
    tmp = path.with_suffix(".trim.wav")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(params.nchannels)
        wf.setsampwidth(params.sampwidth)
        wf.setframerate(params.framerate)
        wf.writeframes(raw[cut * frame_bytes:])
    tmp.replace(path)
    return cut / float(params.framerate)


def shift_words(words: list[dict], offset_s: float) -> list[dict]:
    """Move every word `offset_s` seconds earlier (clamped at 0) — the companion of a trim."""
    if offset_s <= 0:
        return words
    out = []
    for w in words:
        d = dict(w)
        d["start"] = round(max(0.0, float(w["start"]) - offset_s), 4)
        d["end"] = round(max(0.0, float(w["end"]) - offset_s), 4)
        out.append(d)
    return out


def measured_wps(n_words: int, span: tuple[float, float] | None) -> float | None:
    """Words per second over the audible span (first sound to last sound) — pauses between
    sentences count, the silence before the first and after the last word does not."""
    if not span or n_words <= 0:
        return None
    dur = span[1] - span[0]
    return n_words / dur if dur > 0.05 else None


def resolve_target_wps(value) -> float | None:
    """The configured target pace, or None when unset / not a positive number."""
    if value is None:
        return None
    try:
        v = float(str(value).strip())
    except ValueError:
        return None
    if not v > 0:
        return None
    return _clamp(v, *_TARGET_WPS_RANGE)


def tempo_bounds(lo: float, hi: float) -> tuple[float, float]:
    """The atempo clamp, kept inside ffmpeg's legal range and ordered."""
    lo = _clamp(lo, *_ATEMPO_LEGAL)
    hi = _clamp(hi, *_ATEMPO_LEGAL)
    return (lo, hi) if lo <= hi else (hi, lo)


def choose_atempo(raw_wps: float, target_wps: float, lo: float, hi: float) -> float:
    """The atempo that takes `raw_wps` (pace at atempo 1.0) to `target_wps`, clamped to [lo, hi]."""
    lo, hi = tempo_bounds(lo, hi)
    if raw_wps <= 0:
        return lo
    return round(_clamp(target_wps / raw_wps, lo, hi), 4)
