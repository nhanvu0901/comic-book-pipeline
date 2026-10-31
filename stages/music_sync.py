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
    """Decode audio file or video container (mp3, wav, m4a, mp4, mov) to 1D float32 mono array in range [-1.0, 1.0]."""
    ff = _require_ffmpeg()
    cmd = [
        ff, "-y", "-i", str(audio_path),
        "-vn",
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
    hit_points: list[HitPoint] | None = None,
    fps: float = 30.0,
    tolerance: float = 0.20,
) -> tuple[float, str | None]:
    """Pick music in-point (>=0) within [0, track_duration - video_duration] to maximise
    the snappable weighted hit points over the full valid in-point range at fine resolution.
    Higher weight for clips and reveal cuts, bonus when strongest drop lands on top hit point.

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
    lead = 1.5 / float(fps)

    hps = list(hit_points) if hit_points else []
    if not hps and strongest_hit_time > 0.0:
        hps = [HitPoint(shot_index=1, time_seconds=strongest_hit_time, name="Strongest Hit", strength=1.0)]

    all_beats = analysis.beats or []
    downbeats = analysis.downbeats or []
    drops = analysis.drops or []

    if not hps or not all_beats:
        valid_candidates: list[tuple[float, float]] = []
        for drop in drops:
            cand_in = drop["time"] - strongest_hit_time
            if 0.0 <= cand_in <= max_in + 1e-3:
                valid_candidates.append((drop["score"], min(max_in, max(0.0, cand_in))))
        if valid_candidates:
            valid_candidates.sort(key=lambda c: c[0], reverse=True)
            return round(float(valid_candidates[0][1]), 3), None
        return 0.0, None

    # Search candidates across valid range:
    # 1. Fine grid with 0.02s step
    candidates: set[float] = set()
    grid_points = np.arange(0.0, max_in + 1e-4, 0.02)
    for g in grid_points:
        candidates.add(round(float(g), 4))

    # 2. Beat alignments: beat - (hit_point + lead)
    for b in all_beats:
        for hp in hps:
            c = b - (hp.time_seconds + lead)
            if 0.0 <= c <= max_in:
                candidates.add(round(float(c), 4))

    # 3. Drop alignments
    top_hp = max(hps, key=lambda h: h.strength)
    for d in drops:
        c = d["time"] - (top_hp.time_seconds + lead)
        if 0.0 <= c <= max_in:
            candidates.add(round(float(c), 4))

    candidates.add(0.0)
    candidates.add(round(max_in, 4))

    beats_arr = np.array(all_beats, dtype=float)
    downbeats_arr = np.array(downbeats, dtype=float) if downbeats else np.array([], dtype=float)
    strongest_drop = max(drops, key=lambda d: d.get("score", 0.0)) if drops else None

    best_cand = 0.0
    best_score = -1e9

    for cand_in in sorted(candidates):
        score = 0.0
        for hp in hps:
            t_track = cand_in + hp.time_seconds + lead
            dists = np.abs(beats_arr - t_track)
            idx = int(np.argmin(dists))
            min_dist = float(dists[idx])
            b = float(beats_arr[idx])
            if min_dist <= tolerance:
                is_downbeat = False
                if len(downbeats_arr) > 0:
                    is_downbeat = bool(np.min(np.abs(downbeats_arr - b)) < 0.02)
                mult = 2.0 if is_downbeat else 1.0
                closeness = 1.0 - 0.5 * (min_dist / tolerance)
                score += hp.strength * mult * closeness

        if strongest_drop is not None:
            t_track_top = cand_in + top_hp.time_seconds + lead
            drop_dist = abs(strongest_drop.get("time", 0.0) - t_track_top)
            if drop_dist <= tolerance:
                drop_score = float(strongest_drop.get("score", 1.0))
                score += 1.5 * drop_score * (1.0 - 0.5 * (drop_dist / tolerance))

        score -= 1e-4 * (cand_in / (max_in + 1.0))

        if score > best_score:
            best_score = score
            best_cand = cand_in

    return round(float(best_cand), 3), None


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


_CLIP_DUR_CACHE: dict[str, float] = {}


def _get_clip_duration(clip_path: str | Path) -> float:
    """Get the source duration of a clip file in seconds, cached to avoid repeated ffprobe calls."""
    if not clip_path:
        return 0.0
    key = str(clip_path)
    if key in _CLIP_DUR_CACHE:
        return _CLIP_DUR_CACHE[key]

    dur = 0.0
    try:
        from stages.stage_5.clips import probe_video
        p = Path(clip_path)
        if p.is_file():
            probe = probe_video(p)
            dur = float(probe.get("duration", 0.0) or 0.0)
    except Exception:
        dur = 0.0

    _CLIP_DUR_CACHE[key] = dur
    return dur


def _can_fit_clip(shot: Any, new_duration: float) -> bool:
    """Check whether a clip shot can satisfy speed [0.8, 1.25] and hold <= 0.3s
    using the exact same logic as stage_5.clips:plan_clip_shot()."""
    clip_path = getattr(shot, "clip_path", "")
    if not clip_path:
        return True

    if new_duration < 0.40:
        return False

    try:
        import copy
        from stages.stage_5.clips import plan_clip_shot
        dummy = copy.copy(shot)
        dummy.duration_seconds = new_duration
        src_dur = float(getattr(shot, "src_dur", 0.0) or getattr(shot, "clip_duration", 0.0) or 0.0)
        if src_dur <= 0.0:
            src_dur = _get_clip_duration(clip_path)
        _clip_in, fit_ = plan_clip_shot(dummy, src_dur=src_dur)
        if fit_.speed < 0.80 - 1e-4 or fit_.speed > 1.25 + 1e-4:
            return False
        if fit_.hold > 0.30 + 1e-4:
            return False
        return True
    except Exception:
        return False


def snap_shots_to_beats(
    shots: list[Any],
    hit_points: list[HitPoint],
    in_point: float,
    strong_beats: list[float],
    all_beats: list[float] | None = None,
    downbeats: list[float] | None = None,
    fps: float = 30.0,
    lead_frames: float = 1.5,
    tolerance: float = 0.20,
    min_duration: float = 0.40,
) -> list[HitPoint]:
    """Snap hit point boundaries to nearest beat within +-tolerance (default +-0.20s).
    Priority: downbeat > strong beat > any beat.
    Lands 1-2 frames before the beat.
    Maintains total video duration and never alters narration audio.
    Respects min_duration (0.40s) and clip-fit limits.
    """
    if not shots or not hit_points:
        return hit_points

    beats_pool_all = list(all_beats) if all_beats is not None else list(strong_beats)
    beats_pool_strong = list(strong_beats) if strong_beats is not None else beats_pool_all
    beats_pool_down = list(downbeats) if downbeats is not None else []

    if not beats_pool_all:
        return hit_points

    lead_time = lead_frames / float(fps)

    for hp in hit_points:
        idx = hp.shot_index
        if idx <= 0 or idx >= len(shots):
            continue

        prev_shot = shots[idx - 1]
        cur_shot = shots[idx]

        # Current cut time in video timeline
        cut_time = sum(float(shots[j].duration_seconds) for j in range(idx))

        def _find_nearest_in_pool(pool: list[float]) -> tuple[float | None, float]:
            cand_b = None
            cand_delta = float("inf")
            for b in pool:
                vb = b - in_point
                if vb < 0.0:
                    continue
                target = vb - lead_time
                d = target - cut_time
                if abs(d) <= tolerance and abs(d) < abs(cand_delta):
                    cand_delta = d
                    cand_b = vb
            return cand_b, cand_delta

        chosen_b = None
        chosen_delta = 0.0

        if beats_pool_down:
            chosen_b, chosen_delta = _find_nearest_in_pool(beats_pool_down)

        if chosen_b is None and beats_pool_strong:
            chosen_b, chosen_delta = _find_nearest_in_pool(beats_pool_strong)

        if chosen_b is None and beats_pool_all:
            chosen_b, chosen_delta = _find_nearest_in_pool(beats_pool_all)

        if chosen_b is None:
            hp.snapped = False
            valid_all = [b - in_point for b in beats_pool_all if b - in_point >= 0.0]
            if valid_all:
                nearest = min(valid_all, key=lambda vb: abs(vb - cut_time))
                hp.beat_time = round(nearest, 3)
                hp.target_cut = round(cut_time, 3)
                hp.offset_from_beat = round(cut_time - nearest, 4)
            continue

        target_cut = chosen_b - lead_time
        delta = chosen_delta

        dur_prev_new = round(float(prev_shot.duration_seconds) + delta, 4)
        dur_cur_new = round(float(cur_shot.duration_seconds) - delta, 4)

        if dur_prev_new < min_duration or dur_cur_new < min_duration:
            hp.snapped = False
            hp.beat_time = round(chosen_b, 3)
            hp.target_cut = round(cut_time, 3)
            hp.offset_from_beat = round(cut_time - chosen_b, 4)
            continue

        if not _can_fit_clip(prev_shot, dur_prev_new) or not _can_fit_clip(cur_shot, dur_cur_new):
            hp.snapped = False
            hp.beat_time = round(chosen_b, 3)
            hp.target_cut = round(cut_time, 3)
            hp.offset_from_beat = round(cut_time - chosen_b, 4)
            continue

        # Apply snap
        prev_shot.duration_seconds = dur_prev_new
        cur_shot.duration_seconds = dur_cur_new

        hp.snapped = True
        hp.time_seconds = round(target_cut, 3)
        hp.beat_time = round(chosen_b, 3)
        hp.target_cut = round(target_cut, 3)
        hp.offset_from_beat = round(target_cut - chosen_b, 4)

    return hit_points


def format_timecode(seconds: float, fps: float = 30.0) -> str:
    """Format seconds into an unambiguous timecode string:
    'X.XX s (HH:MM:SS:FF @30fps)', e.g. '4.65 s (00:00:04:20 @30fps)'.
    """
    if seconds < 0:
        seconds = 0.0
    fps_int = int(round(fps))
    total_frames = int(round(seconds * fps))
    total_sec = total_frames // fps_int
    frames = total_frames % fps_int
    hh = total_sec // 3600
    mm = (total_sec % 3600) // 60
    ss = total_sec % 60
    return f"{seconds:.2f} s ({hh:02d}:{mm:02d}:{ss:02d}:{frames:02d} @{fps_int}fps)"


def get_capcut_music_config(project_dir: Path) -> dict[str, Any]:
    """Read capcut_music section from project's music.json if present."""
    p = Path(project_dir) / "music.json"
    if p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                cc = doc.get("capcut_music")
                if isinstance(cc, dict):
                    return cc
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def is_capcut_music_enabled(project_dir: Path) -> bool:
    """True if project has CapCut music toggle enabled."""
    cfg = get_capcut_music_config(project_dir)
    return bool(cfg.get("enabled", False))


def build_sync_sheet(
    project_dir: Path,
    analysis: TrackAnalysis,
    in_point: float,
    hit_points: list[HitPoint],
    warning: str | None = None,
    ducking_db: float = -16.0,
    fade_in: float = 1.0,
    fade_out: float = 2.0,
) -> tuple[dict[str, Any], str]:
    """Generate music_sync.json (machine) and music_sync.txt (Vietnamese, for Master)."""
    project_dir = Path(project_dir)
    project_name = project_dir.name
    track_path = Path(analysis.file_path)
    track_name = track_path.name

    snapped_count = sum(1 for hp in hit_points if hp.snapped)
    kept_count = len(hit_points) - snapped_count

    json_doc: dict[str, Any] = {
        "track_name": track_name,
        "track_file": analysis.file_path,
        "track_sha256": analysis.file_sha256,
        "bpm": analysis.bpm,
        "timeline_placement": "00:00:00",
        "in_point_seconds": round(in_point, 3),
        "in_point_timecode": format_timecode(in_point),
        "suggested_ducking_db": float(ducking_db),
        "suggested_fade_in_seconds": float(fade_in),
        "suggested_fade_out_seconds": float(fade_out),
        "warning": warning,
        "snapped_count": snapped_count,
        "kept_count": kept_count,
        "total_hit_points": len(hit_points),
        "hit_points": [
            {
                "shot_id": hp.shot_index,
                "name": hp.name,
                "time_seconds": round(hp.time_seconds, 3),
                "timecode": format_timecode(hp.time_seconds),
                "target_cut": round(hp.target_cut, 3) if hp.snapped else round(hp.time_seconds, 3),
                "snapped": hp.snapped,
                "beat_time": round(hp.beat_time, 3),
                "offset_from_beat": round(hp.offset_from_beat, 4),
            }
            for hp in hit_points
        ],
        "preview_video": "preview_with_music.mp4",
    }

    # Vietnamese human-readable sync sheet
    lines = [
        "=" * 80,
        "HƯỚNG DẪN ĐỒNG BỘ NHẠC CAPCUT (CAPCUT MUSIC SYNC SHEET)",
        "=" * 80,
        f"Dự án: {project_name}",
        f"File nhạc tham chiếu: {track_name}",
        f"BPM: {analysis.bpm:.1f}",
        f"Trạng thái đồng bộ: {snapped_count}/{len(hit_points)} hit points đã khớp beat ({kept_count} giữ nguyên nhịp thoại)",
        "",
        "1. CÁCH ĐẶT NHẠC TRÊN TIMELINE CAPCUT:",
        "   - Kéo file nhạc vào timeline, bắt đầu ngay tại mốc: 00:00:00 (đầu video).",
        f"   - Điểm cắt đầu track (In-point): {format_timecode(in_point)}",
        f"     -> Cắt bỏ {in_point:.2f}s đầu của track (hoặc đặt timeline CapCut tại {format_timecode(in_point)}).",
        f"   - Âm lượng nhạc đề xuất: {ducking_db:.0f} dB so với giọng đọc (giọng đọc là chính, nhạc nền nhẹ).",
        f"   - Fade in: {fade_in:.1f}s ở đầu video | Fade out: {fade_out:.1f}s ở cuối video.",
        "",
        f"2. CÁC ĐIỂM HIT POINT ĐÃ ĐỒNG BỘ VỚI NHẠC ({snapped_count}/{len(hit_points)} ĐÃ KHỚP BEAT):",
        f"   - Tổng kết: {snapped_count} điểm đã khớp beat (cắt sớm 1-2 frames), {kept_count} điểm giữ nguyên nhịp thoại gốc.",
        "   - Chi tiết từng điểm cắt:",
    ]

    for hp in hit_points:
        status_str = "đã khớp beat" if hp.snapped else "giữ nguyên (quá xa beat hoặc giới hạn shot)"
        lines.append(
            f"   - [{format_timecode(hp.time_seconds)}] Shot {hp.shot_index}: {hp.name} "
            f"(Beat: {hp.beat_time:.2f}s, lệch: {hp.offset_from_beat:+.3f}s, {status_str})"
        )

    if warning:
        lines.extend([
            "",
            f"LƯU Ý: {warning}",
        ])

    lines.extend([
        "",
        "3. VIDEO PREVIEW KIỂM TRA:",
        "   - Đã xuất bản xem thử: preview_with_music.mp4 (đã mix nhạc mẫu ở in-point để kiểm tra sync).",
        "   - Video chính (final.mp4) chỉ chứa giọng đọc (narration-only) để import vào CapCut.",
        "=" * 80,
    ])

    txt_content = "\n".join(lines) + "\n"

    try:
        (project_dir / "music_sync.json").write_text(json.dumps(json_doc, indent=2, ensure_ascii=False), encoding="utf-8")
        (project_dir / "music_sync.txt").write_text(txt_content, encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write music sync sheet: %s", exc)

    return json_doc, txt_content


def _probe_duration(path: Path) -> float:
    ff = shutil.which("ffprobe")
    if not ff:
        return 0.0
    res = subprocess.run(
        [ff, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float((res.stdout or "0").strip())
    except ValueError:
        return 0.0


def render_preview_with_music(
    final_path: Path,
    track_path: Path,
    in_point: float,
    out_path: Path,
    ducking_db: float = -16.0,
    log=print,
) -> Path | None:
    """Render preview_with_music.mp4: reference track mixed in at in_point, ducked under voice."""
    ff = _require_ffmpeg()
    duration = _probe_duration(final_path)
    if duration <= 0:
        return None

    fade_out_start = max(0.0, duration - 2.0)
    af_filter = (
        f"[1:a]volume={ducking_db}dB,"
        f"afade=t=in:st=0:d=1.0,"
        f"afade=t=out:st={fade_out_start:.2f}:d=2.0[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    cmd = [
        ff, "-y",
        "-i", str(final_path),
        "-ss", f"{in_point:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(track_path),
        "-filter_complex", af_filter,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            log(f"[music_sync] preview render failed: {res.stderr[-500:]}")
            return None
        return out_path
    except Exception as exc:
        log(f"[music_sync] preview render exception: {exc}")
        return None


# In-memory stash between Stage 5 shot planning and post-encode sync sheet generation
_STAGE5_SYNC_STATE: dict[str, dict[str, Any]] = {}


def prepare_capcut_music_sync(
    project_name: str,
    root: Path,
    shots: list[Any],
    narration: dict[str, Any],
    log=print,
) -> list[Any]:
    """If CapCut music is enabled, analyze track, pick in-point, and snap hit points to beats.
    If disabled (default), returns shots unmodified (byte-identity).
    """
    root = Path(root)
    cfg = get_capcut_music_config(root)
    if not cfg.get("enabled"):
        _STAGE5_SYNC_STATE.pop(project_name, None)
        return shots

    filename = cfg.get("filename") or cfg.get("file")
    if not filename:
        log("[music_sync] CapCut music enabled but no track filename configured")
        return shots

    track_path = root / filename
    if not track_path.exists():
        # Might be absolute or located in capcut_reference
        track_path = Path(filename)
        if not track_path.exists():
            cand = root / "capcut_reference" / Path(filename).name
            if cand.exists():
                track_path = cand
            else:
                log(f"[music_sync] CapCut music track not found: {filename}")
                return shots

    try:
        analysis = analyze_track(track_path, cache_dir=root / ".music_cache")
        hit_points = select_hit_points(shots, narration)

        total_video_dur = sum(float(s.duration_seconds) for s in shots)
        strongest_hit_time = 0.0
        if hit_points:
            strongest_hp = max(hit_points, key=lambda hp: hp.strength)
            strongest_hit_time = strongest_hp.time_seconds

        in_point, warn = pick_in_point(
            analysis,
            total_video_dur,
            strongest_hit_time=strongest_hit_time,
            hit_points=hit_points,
        )
        if warn:
            log(f"[music_sync] warning: {warn}")

        snapped_hits = snap_shots_to_beats(
            shots,
            hit_points,
            in_point,
            strong_beats=analysis.downbeats if analysis.downbeats else analysis.beats,
            all_beats=analysis.beats,
            downbeats=analysis.downbeats,
        )

        _STAGE5_SYNC_STATE[project_name] = {
            "analysis": analysis,
            "in_point": in_point,
            "hit_points": snapped_hits,
            "warning": warn,
            "track_path": track_path,
        }

        snapped_count = sum(1 for hp in snapped_hits if hp.snapped)
        log(f"[music_sync] CapCut music aligned: BPM={analysis.bpm:.1f}, in-point={format_timecode(in_point)}, "
            f"snapped {snapped_count}/{len(snapped_hits)} hit points")

        return shots
    except Exception as exc:
        log(f"[music_sync] CapCut music planning failed ({exc}); falling back to standard shots")
        return shots


def finish_capcut_music_sync(
    project_name: str,
    root: Path,
    final_path: Path,
    log=print,
) -> None:
    """Generate music_sync.json/txt and render preview_with_music.mp4 if CapCut music was active."""
    state = _STAGE5_SYNC_STATE.pop(project_name, None)
    if not state:
        return

    root = Path(root)
    final_path = Path(final_path)
    analysis = state["analysis"]
    in_point = state["in_point"]
    hit_points = state["hit_points"]
    warning = state["warning"]
    track_path = state["track_path"]

    # 1. Build and write music_sync.json & music_sync.txt
    build_sync_sheet(root, analysis, in_point, hit_points, warning=warning)
    log(f"[music_sync] wrote sync sheet: {root / 'music_sync.txt'}")

    # 2. Render preview_with_music.mp4
    preview_path = root / "preview_with_music.mp4"
    log(f"[music_sync] rendering preview video → {preview_path.name}...")
    res = render_preview_with_music(final_path, track_path, in_point, preview_path, log=log)
    if res:
        log(f"[music_sync] preview ready: {preview_path}")
    else:
        log("[music_sync] preview video render failed (final.mp4 is unaffected)")


