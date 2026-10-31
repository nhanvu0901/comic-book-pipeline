"""Music analysis and beat synchronization for CapCut reference tracks.

Decodes reference audio via ffmpeg to mono PCM, uses numpy for onset flux,
tempo (BPM), beat grid, downbeat alignment, and drop/energy detection.
Cached by audio file SHA-256.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TrackAnalysis:
    file_path: str
    file_sha256: str
    duration_seconds: float
    bpm: float
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    drops: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackAnalysis:
        return cls(
            file_path=str(data.get("file_path", "")),
            file_sha256=str(data.get("file_sha256", "")),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            bpm=float(data.get("bpm", 0.0)),
            beats=[float(b) for b in data.get("beats", [])],
            downbeats=[float(d) for d in data.get("downbeats", [])],
            drops=[dict(d) for d in data.get("drops", [])],
        )


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _require_ffmpeg() -> str:
    from config import FFMPEG_BIN
    if os.path.isabs(FFMPEG_BIN) and os.path.isfile(FFMPEG_BIN):
        return FFMPEG_BIN
    p = shutil.which(FFMPEG_BIN) or shutil.which("ffmpeg")
    if not p:
        raise FileNotFoundError(f"ffmpeg not found ({FFMPEG_BIN}). Check PATH.")
    return p


def decode_audio_pcm(audio_path: Path, sample_rate: int = 22050) -> tuple[np.ndarray, float]:
    """Decode audio file to 1D float32 mono array in range [-1.0, 1.0]."""
    ff = _require_ffmpeg()
    cmd = [
        ff, "-y", "-i", str(audio_path),
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ac", "1", "-ar", str(sample_rate),
        "-"
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"Failed to decode audio {audio_path}: {err}")

    raw = res.stdout
    if not raw:
        return np.zeros(0, dtype=np.float32), 0.0

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    duration = len(samples) / float(sample_rate)
    return samples, duration


def _compute_onset_envelope(samples: np.ndarray, sample_rate: int = 22050, hop_length: int = 256, n_fft: int = 1024) -> tuple[np.ndarray, float]:
    """Calculate half-wave rectified spectral flux as onset envelope."""
    if len(samples) < n_fft:
        return np.zeros(1, dtype=np.float32), float(sample_rate) / hop_length

    # Sliding window STFT
    frames = np.lib.stride_tricks.sliding_window_view(samples, n_fft)[::hop_length]
    window = np.hanning(n_fft).astype(np.float32)
    windowed = frames * window

    # RFFT magnitudes
    rfft = np.fft.rfft(windowed, axis=1)
    mag = np.abs(rfft)

    # Spectral flux: difference between consecutive frame magnitudes, positive only
    flux = np.sum(np.maximum(0.0, np.diff(mag, axis=0)), axis=1)
    # Normalize flux
    flux_max = np.max(flux) if len(flux) > 0 else 0.0
    if flux_max > 0:
        flux = flux / flux_max

    fps = float(sample_rate) / hop_length
    return flux, fps


def _estimate_bpm_and_phase(flux: np.ndarray, fps: float) -> tuple[float, float, list[float]]:
    """Estimate tempo (BPM), beat phase, and return all beat times (seconds)."""
    if len(flux) < int(fps * 2):  # less than 2s of flux
        return 120.0, 0.0, []

    fnorm = flux - np.mean(flux)
    autocorr = np.correlate(fnorm, fnorm, mode="full")
    autocorr = autocorr[len(autocorr) // 2 :]

    # Search BPM range 60 to 180
    bpms = np.linspace(60.0, 180.0, 241)
    lags = (60.0 / bpms) * fps

    scores = []
    for bpm, lag in zip(bpms, lags):
        ilag = int(round(lag))
        if 0 <= ilag < len(autocorr):
            # Perceptual tempo prior centered at 120 BPM
            prior = np.exp(-0.5 * ((np.log2(bpm / 120.0) / 0.5) ** 2))
            scores.append(autocorr[ilag] * prior)
        else:
            scores.append(0.0)

    best_idx = int(np.argmax(scores))
    ilag = int(round(lags[best_idx]))

    # Refine peak using parabolic interpolation
    if 1 <= ilag < len(autocorr) - 1:
        y0, y1, y2 = autocorr[ilag - 1], autocorr[ilag], autocorr[ilag + 1]
        denom = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        refined_lag = ilag + delta
    else:
        refined_lag = float(ilag)

    bpm = (60.0 * fps) / refined_lag
    beat_interval = 60.0 / bpm

    # Find optimal phase (offset) by scoring candidate phases
    phase_candidates = np.linspace(0.0, beat_interval, 50, endpoint=False)
    best_phase = 0.0
    best_phase_score = -1.0

    duration_sec = len(flux) / fps
    for pc in phase_candidates:
        beat_times = np.arange(pc, duration_sec, beat_interval)
        frame_indices = np.clip((beat_times * fps).astype(int), 0, len(flux) - 1)
        phase_score = float(np.sum(flux[frame_indices]))
        if phase_score > best_phase_score:
            best_phase_score = phase_score
            best_phase = pc

    all_beats = [float(b) for b in np.arange(best_phase, duration_sec, beat_interval)]
    return round(float(bpm), 2), round(float(best_phase), 4), all_beats


def _detect_downbeats_and_drops(
    samples: np.ndarray, sample_rate: int, beats: list[float]
) -> tuple[list[float], list[dict[str, Any]]]:
    """Identify downbeats (beat 1 of 4) and detect drop candidates."""
    if len(beats) < 4:
        return list(beats), []

    # Score 4 candidate downbeat offsets (0, 1, 2, 3)
    # Downbeats usually feature higher low-frequency energy (bass / kick drum)
    phase_scores = [0.0, 0.0, 0.0, 0.0]
    beat_energies = []

    for b in beats:
        idx = int(b * sample_rate)
        # Window of 80ms around the beat
        win_start = max(0, idx - int(0.02 * sample_rate))
        win_end = min(len(samples), idx + int(0.06 * sample_rate))
        win = samples[win_start:win_end]
        energy = float(np.sum(win ** 2)) if len(win) > 0 else 0.0
        beat_energies.append(energy)

    for offset in range(4):
        offset_energies = beat_energies[offset::4]
        phase_scores[offset] = float(np.mean(offset_energies)) if offset_energies else 0.0

    best_offset = int(np.argmax(phase_scores))
    downbeats = [beats[i] for i in range(best_offset, len(beats), 4)]

    # Detect drops:
    # A drop occurs at a downbeat where the energy jumps significantly compared to preceding bars,
    # or the downbeat with the highest energy peak.
    drops: list[dict[str, Any]] = []
    downbeat_indices = list(range(best_offset, len(beats), 4))

    for idx, d_idx in enumerate(downbeat_indices):
        d_time = beats[d_idx]
        cur_energy = beat_energies[d_idx]

        # Prior energy: average energy of 4 beats prior
        if d_idx >= 4:
            prior_energy = float(np.mean(beat_energies[d_idx - 4 : d_idx]))
        else:
            prior_energy = cur_energy * 0.5

        energy_jump = cur_energy - prior_energy
        score = float(cur_energy * 0.6 + max(0.0, energy_jump) * 0.4)
        drops.append({"time": round(float(d_time), 3), "score": round(score, 4), "jump": round(float(energy_jump), 4)})

    # Sort drops by score descending
    drops.sort(key=lambda d: d["score"], reverse=True)
    return downbeats, drops


def analyze_track(audio_path: Path, cache_dir: Path | None = None, force: bool = False) -> TrackAnalysis:
    """Analyze track tempo, beats, downbeats, and drops with SHA-256 caching."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    sha = _file_sha256(audio_path)

    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{sha}.json"
        if not force and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                return TrackAnalysis.from_dict(cached)
            except Exception as exc:
                logger.warning("Cache file %s corrupted (%s), reanalyzing", cache_file, exc)

    sample_rate = 22050
    samples, duration = decode_audio_pcm(audio_path, sample_rate=sample_rate)
    if len(samples) == 0:
        return TrackAnalysis(
            file_path=str(audio_path),
            file_sha256=sha,
            duration_seconds=0.0,
            bpm=0.0,
        )

    flux, fps = _compute_onset_envelope(samples, sample_rate=sample_rate)
    bpm, _phase, beats = _estimate_bpm_and_phase(flux, fps)
    downbeats, drops = _detect_downbeats_and_drops(samples, sample_rate, beats)

    analysis = TrackAnalysis(
        file_path=str(audio_path),
        file_sha256=sha,
        duration_seconds=round(duration, 3),
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        drops=drops,
    )

    if cache_dir is not None:
        try:
            cache_file = Path(cache_dir) / f"{sha}.json"
            cache_file.write_text(json.dumps(analysis.to_dict(), indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to save analysis cache %s: %s", cache_file, exc)

    return analysis


def pick_in_point(
    analysis: TrackAnalysis,
    video_duration: float,
    strongest_hit_time: float = 0.0,
) -> tuple[float, str | None]:
    """Pick music in-point (>=0) so the strongest drop lands on the strongest hit point.

    Returns:
        (in_point_seconds, warning_message_or_None)
    """
    if analysis.duration_seconds < video_duration:
        warn = (
            f"Reference track duration ({analysis.duration_seconds:.1f}s) is shorter "
            f"than video duration ({video_duration:.1f}s)."
        )
        return 0.0, warn

    max_in = max(0.0, analysis.duration_seconds - video_duration)

    # Find candidate drops that can align with strongest_hit_time
    valid_candidates: list[tuple[float, float]] = []
    for drop in analysis.drops:
        cand_in = drop["time"] - strongest_hit_time
        if 0.0 <= cand_in <= max_in + 1e-3:
            valid_candidates.append((drop["score"], min(max_in, max(0.0, cand_in))))

    if valid_candidates:
        valid_candidates.sort(key=lambda c: c[0], reverse=True)
        chosen_in = valid_candidates[0][1]
        return round(float(chosen_in), 3), None

    # Fallback: if no drop qualifies within bounds, pick 0.0
    return 0.0, None
