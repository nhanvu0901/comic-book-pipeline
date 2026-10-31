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


@dataclass
class HitPoint:
    shot_index: int
    time_seconds: float
    name: str
    strength: float = 0.5
    beat_time: float = 0.0
    target_cut: float = 0.0
    offset_from_beat: float = 0.0
    snapped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def select_hit_points(shots: list[Any], narration: dict[str, Any] | None = None) -> list[HitPoint]:
    """Select 3-5 key hit points using general rules (no comic-specific code):
    1. MP4 clip shots (strength 1.0)
    2. Hook's reveal cut (cut transitioning out of the cold-open / intro, strength 0.9)
    3. Climax / highest importance scene start (strength 0.85)
    4. Final line cut (start of the closing line/outro, strength 0.7)
    """
    if not shots or len(shots) < 2:
        return []

    # Calculate shot start times
    start_times = [0.0]
    for s in shots[:-1]:
        start_times.append(round(start_times[-1] + float(s.duration_seconds), 4))

    candidates: dict[int, HitPoint] = {}

    # 1. Hook reveal cut: boundary after the intro / cold open
    hook_cut_idx = None
    if getattr(shots[0], "is_intro", False):
        for idx in range(1, len(shots)):
            if not getattr(shots[idx], "is_intro", False):
                hook_cut_idx = idx
                break
    elif len(shots) > 1:
        # If is_intro is not explicitly set, shot 1 entrance is the opening hook reveal
        hook_cut_idx = 1

    if hook_cut_idx is not None and 0 < hook_cut_idx < len(shots):
        candidates[hook_cut_idx] = HitPoint(
            shot_index=hook_cut_idx,
            time_seconds=start_times[hook_cut_idx],
            name="Hook Reveal",
            strength=0.9,
        )

    # 2. MP4 clip shots
    for idx, s in enumerate(shots):
        if idx == 0:
            continue
        clip_path = getattr(s, "clip_path", "")
        clip_id = getattr(s, "clip_id", "")
        if clip_path or clip_id:
            name = f"Video Clip ({clip_id or Path(clip_path).stem})"
            candidates[idx] = HitPoint(
                shot_index=idx,
                time_seconds=start_times[idx],
                name=name,
                strength=1.0,
            )

    # 3. Scene starts with highest importance (e.g. climax)
    climax_scene_id = None
    scenes = (narration or {}).get("scenes") or []
    for sc in scenes:
        imp = str(sc.get("importance") or "").lower()
        if "climax" in imp:
            climax_scene_id = sc.get("scene_id")
            break

    # If no explicit importance field, choose scene in the dramatic peak region (~70% through narration)
    if climax_scene_id is None and len(scenes) >= 3:
        peak_idx = int(len(scenes) * 0.7)
        climax_scene_id = scenes[peak_idx].get("scene_id")

    if climax_scene_id is not None:
        for idx, s in enumerate(shots):
            if idx > 0 and getattr(s, "scene_id", None) == climax_scene_id:
                if idx not in candidates or candidates[idx].strength < 0.85:
                    candidates[idx] = HitPoint(
                        shot_index=idx,
                        time_seconds=start_times[idx],
                        name=f"Climax (Scene {climax_scene_id})",
                        strength=0.85,
                    )
                break

    # 4. Final line cut (entrance to last shot / outro)
    final_idx = len(shots) - 1
    if final_idx > 0 and (final_idx not in candidates or candidates[final_idx].strength < 0.7):
        candidates[final_idx] = HitPoint(
            shot_index=final_idx,
            time_seconds=start_times[final_idx],
            name="Final Line",
            strength=0.7,
        )

    # Sort by strength descending, take top 5
    ranked = sorted(candidates.values(), key=lambda hp: hp.strength, reverse=True)
    selected = ranked[:5]

    # Return in chronological order
    selected.sort(key=lambda hp: hp.shot_index)
    return selected


def _can_fit_clip(shot: Any, new_duration: float) -> bool:
    """Check whether a clip shot can satisfy speed [0.8, 1.25] and hold <= 0.3s."""
    clip_path = getattr(shot, "clip_path", "")
    if not clip_path:
        return True

    clip_in = float(getattr(shot, "clip_in", 0.0) or 0.0)
    clip_out = float(getattr(shot, "clip_out", 0.0) or 0.0)
    fit_to_beat = bool(getattr(shot, "clip_fit_to_beat", False))

    if clip_out > clip_in:
        span = clip_out - clip_in
        if fit_to_beat:
            speed = span / new_duration
            return 0.80 <= speed <= 1.25
        else:
            ext = min(span, new_duration)
            speed = max(0.8, min(1.25, ext / new_duration))
            hold = max(0.0, new_duration - ext / speed)
            return hold <= 0.30

    return True


def snap_shots_to_beats(
    shots: list[Any],
    hit_points: list[HitPoint],
    in_point: float,
    strong_beats: list[float],
    fps: float = 30.0,
    lead_frames: float = 1.5,
    tolerance: float = 0.20,
    min_duration: float = 0.40,
) -> list[HitPoint]:
    """Snap hit point boundaries to nearest strong beat within +-tolerance (default +-0.20s).
    Lands 1-2 frames before the beat.
    Maintains total video duration and never alters narration audio.
    Respects min_duration (0.40s) and clip-fit limits.
    """
    if not shots or not hit_points or not strong_beats:
        return hit_points

    # Precalculate start times of all shots
    lead_time = lead_frames / float(fps)
    video_beats = [round(b - in_point, 4) for b in strong_beats]

    for hp in hit_points:
        idx = hp.shot_index
        if idx <= 0 or idx >= len(shots):
            continue

        prev_shot = shots[idx - 1]
        cur_shot = shots[idx]

        # Current cut time
        cut_time = sum(float(shots[j].duration_seconds) for j in range(idx))

        # Find nearest beat in video timeline
        valid_beats = [vb for vb in video_beats if vb >= 0.0]
        if not valid_beats:
            continue

        nearest_beat = min(valid_beats, key=lambda vb: abs(vb - cut_time))
        target_cut = nearest_beat - lead_time
        delta = target_cut - cut_time

        if abs(delta) > tolerance:
            hp.snapped = False
            hp.beat_time = round(nearest_beat, 3)
            hp.target_cut = round(cut_time, 3)
            hp.offset_from_beat = round(cut_time - nearest_beat, 4)
            continue

        # Check floor and limits
        dur_prev_new = round(float(prev_shot.duration_seconds) + delta, 4)
        dur_cur_new = round(float(cur_shot.duration_seconds) - delta, 4)

        if dur_prev_new < min_duration or dur_cur_new < min_duration:
            hp.snapped = False
            continue

        if not _can_fit_clip(prev_shot, dur_prev_new) or not _can_fit_clip(cur_shot, dur_cur_new):
            hp.snapped = False
            continue

        # Apply snap
        prev_shot.duration_seconds = dur_prev_new
        cur_shot.duration_seconds = dur_cur_new

        hp.snapped = True
        hp.beat_time = round(nearest_beat, 3)
        hp.target_cut = round(target_cut, 3)
        hp.offset_from_beat = round(target_cut - nearest_beat, 4)

    return hit_points

