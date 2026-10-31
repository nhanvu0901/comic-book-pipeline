"""Tests for stages/music_sync.py — CapCut reference music analysis and beat detection."""
from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path
import pytest
import numpy as np

from stages import music_sync


def _generate_synthetic_track(
    path: Path,
    bpm: float = 120.0,
    duration: float = 30.0,
    drop_time: float | None = 10.0,
    sample_rate: int = 22050,
) -> Path:
    """Generate a synthetic click/drum track with known BPM and an optional drop."""
    num_samples = int(duration * sample_rate)
    samples = np.zeros(num_samples, dtype=np.float32)

    beat_interval = 60.0 / bpm
    beats = np.arange(0, duration, beat_interval)

    # 4 beats per bar
    for idx, beat_t in enumerate(beats):
        beat_idx = int(beat_t * sample_rate)
        if beat_idx >= num_samples:
            continue
        is_downbeat = (idx % 4 == 0)

        # Before drop: quiet (amplitude 0.2); at and after drop: loud (amplitude 0.8)
        is_post_drop = (drop_time is not None and beat_t >= (drop_time - 0.05))
        amp = 0.8 if is_post_drop else 0.2
        freq = 150.0 if is_downbeat else 400.0

        click_len = min(int(0.08 * sample_rate), num_samples - beat_idx)
        t = np.arange(click_len) / sample_rate
        # Decaying sine burst
        click = np.sin(2 * np.pi * freq * t) * np.exp(-t * 40.0) * amp
        samples[beat_idx : beat_idx + click_len] += click

    # Normalize to int16 range
    peak = np.max(np.abs(samples))
    if peak > 0:
        samples = samples / peak * 0.95
    int_samples = (samples * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int_samples.tobytes())

    return path


def test_decode_and_cache(tmp_path):
    wav_path = tmp_path / "test_120bpm.wav"
    _generate_synthetic_track(wav_path, bpm=120.0, duration=15.0)

    cache_dir = tmp_path / "cache"
    analysis1 = music_sync.analyze_track(wav_path, cache_dir=cache_dir)
    assert analysis1.bpm > 0
    assert analysis1.duration_seconds == pytest.approx(15.0, abs=0.1)
    assert len(analysis1.beats) > 0
    assert len(analysis1.downbeats) > 0

    # Cache file should exist
    sha = music_sync._file_sha256(wav_path)
    cache_file = cache_dir / f"{sha}.json"
    assert cache_file.exists()

    # Second call loads from cache
    analysis2 = music_sync.analyze_track(wav_path, cache_dir=cache_dir)
    assert analysis2.bpm == analysis1.bpm
    assert analysis2.file_sha256 == analysis1.file_sha256


def test_bpm_detection_accuracy(tmp_path):
    # Test across two different standard tempos
    for target_bpm in (100.0, 128.0):
        wav_path = tmp_path / f"test_{int(target_bpm)}.wav"
        _generate_synthetic_track(wav_path, bpm=target_bpm, duration=20.0)
        analysis = music_sync.analyze_track(wav_path, cache_dir=tmp_path / "cache")
        # Accuracy within ±1.5 BPM
        assert analysis.bpm == pytest.approx(target_bpm, abs=1.5), f"Failed for {target_bpm} BPM, got {analysis.bpm}"


def test_drop_detection(tmp_path):
    wav_path = tmp_path / "drop_track.wav"
    target_bpm = 120.0
    # Drop occurs at 10.0s (bar 5 downbeat is at 5 * 2.0s = 10.0s at 120 BPM)
    _generate_synthetic_track(wav_path, bpm=target_bpm, duration=24.0, drop_time=10.0)
    analysis = music_sync.analyze_track(wav_path, cache_dir=tmp_path / "cache")

    assert len(analysis.drops) > 0
    strongest_drop = analysis.drops[0]
    # Strongest drop should land within 0.3s of 10.0s
    assert strongest_drop["time"] == pytest.approx(10.0, abs=0.3)


def test_pick_in_point_aligns_drop_to_hit(tmp_path):
    wav_path = tmp_path / "track_30s.wav"
    _generate_synthetic_track(wav_path, bpm=120.0, duration=30.0, drop_time=12.0)
    analysis = music_sync.analyze_track(wav_path, cache_dir=tmp_path / "cache")

    # Video is 15s long, strongest hit point is at 4.0s
    video_duration = 15.0
    hit_time = 4.0
    in_point, warn = music_sync.pick_in_point(analysis, video_duration, strongest_hit_time=hit_time)

    assert warn is None
    # in_point + hit_time should match drop_time (~12.0s)
    # So in_point should be ~8.0s
    assert in_point == pytest.approx(8.0, abs=0.3)
    assert in_point >= 0.0
    assert in_point + video_duration <= analysis.duration_seconds + 0.1


def test_pick_in_point_track_too_short_warns(tmp_path):
    wav_path = tmp_path / "short_track.wav"
    _generate_synthetic_track(wav_path, bpm=120.0, duration=10.0)
    analysis = music_sync.analyze_track(wav_path, cache_dir=tmp_path / "cache")

    video_duration = 20.0
    in_point, warn = music_sync.pick_in_point(analysis, video_duration, strongest_hit_time=2.0)

    assert warn is not None
    assert in_point == 0.0


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        music_sync.analyze_track(tmp_path / "nonexistent.mp3")


def test_corrupt_cache_reanalyzes(tmp_path):
    wav_path = tmp_path / "test.wav"
    _generate_synthetic_track(wav_path, bpm=120.0, duration=10.0)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    sha = music_sync._file_sha256(wav_path)
    (cache_dir / f"{sha}.json").write_text("{corrupt json", encoding="utf-8")

    analysis = music_sync.analyze_track(wav_path, cache_dir=cache_dir)
    assert analysis.bpm > 0


def test_video_container_mp4_input(tmp_path):
    import subprocess
    # Generate MP4 file with video stream and synthetic audio stream
    wav_path = tmp_path / "audio.wav"
    _generate_synthetic_track(wav_path, bpm=120.0, duration=15.0)

    mp4_path = tmp_path / "1031_test.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=15",
        "-i", str(wav_path),
        "-c:v", "libx264", "-c:a", "aac",
        "-shortest", str(mp4_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Should decode audio from MP4 and analyze correctly
    analysis = music_sync.analyze_track(mp4_path, cache_dir=tmp_path / "cache")
    assert analysis.bpm == pytest.approx(120.0, abs=1.5)
    assert analysis.duration_seconds == pytest.approx(15.0, abs=0.5)
    assert len(analysis.beats) > 0


