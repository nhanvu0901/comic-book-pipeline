"""timeline_data / snap_time / waveform_png / thumb_strip_png on tiny synthetic projects."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from stages.mascot import render, timeline

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg/ffprobe not installed")


def _ff(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def make_video(path: Path, *, hard_cut_at: float | None = None, seconds: float = 4.0) -> None:
    """108x192 30fps video with a sine track; hard_cut_at = a red→testsrc cut at that second."""
    sine = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if hard_cut_at is None:
        _ff("-f", "lavfi", "-i", f"testsrc=size=108x192:rate=30:duration={seconds}", *sine,
            "-pix_fmt", "yuv420p", "-shortest", str(path))
    else:
        _ff("-f", "lavfi", "-i", f"color=c=red:s=108x192:r=30:d={hard_cut_at}",
            "-f", "lavfi", "-i", f"testsrc=size=108x192:rate=30:duration={seconds - hard_cut_at}",
            *sine, "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-map", "2:a",
            "-pix_fmt", "yuv420p", "-shortest", str(path))


def dur_of(path: Path) -> float:
    return timeline._probe_duration(path)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "source_video", lambda p: Path(p) / "final.mp4")
    return tmp_path


def write(project: Path, name: str, data) -> None:
    (project / name).write_text(json.dumps(data), encoding="utf-8")


# ── cuts: source priority ────────────────────────────────────────────────────────────────
def test_cuts_prefer_shots_json_when_total_matches(project):
    make_video(project / "final.mp4", hard_cut_at=2.0)
    d = dur_of(project / "final.mp4")
    write(project, "shots.json", [{"shot_id": 0, "duration_seconds": 1.0},
                                  {"shot_id": 1, "duration_seconds": 1.5},
                                  {"shot_id": 2, "duration_seconds": d - 2.5}])
    data = timeline.timeline_data(project)
    assert data["cuts"] == [0.0, 1.0, 2.5, round(d, 3)]
    assert not list((project / "mascot" / "cache").glob("cuts_*"))      # no scene detection needed


def test_shots_json_short_by_outro_tail_still_counts_and_marks_the_tail(project):
    make_video(project / "final.mp4", seconds=6.0)
    write(project, "shots.json", [{"duration_seconds": 1.5}, {"duration_seconds": 1.5}])   # 3 s + "outro"
    data = timeline.timeline_data(project)
    assert data["cuts"][:3] == [0.0, 1.5, 3.0] and data["cuts"][-1] == pytest.approx(6.0, abs=0.05)


def test_cuts_fall_back_to_shot_files_when_shots_json_disagrees(project):
    make_video(project / "final.mp4", seconds=20.0)                   # 20 s video
    (project / "shots").mkdir()
    make_video(project / "shots" / "shot_000.mp4", seconds=12.0)
    make_video(project / "shots" / "shot_001.mp4", seconds=8.0)
    write(project, "shots.json", [{"duration_seconds": 3.0}])            # 3 s: far off
    data = timeline.timeline_data(project)
    assert data["cuts"][1] == pytest.approx(12.0, abs=0.1)
    assert len(data["cuts"]) == 3


def test_cuts_fall_back_to_scene_detection_and_cache_it(project, monkeypatch):
    make_video(project / "final.mp4", hard_cut_at=2.0)
    data = timeline.timeline_data(project)
    assert any(abs(c - 2.0) < 0.1 for c in data["cuts"])
    assert data["cuts"][0] == 0.0 and data["cuts"][-1] == pytest.approx(dur_of(project / "final.mp4"), abs=0.01)
    assert list((project / "mascot" / "cache").glob("cuts_*.json"))
    calls = []
    real = timeline._run
    monkeypatch.setattr(timeline, "_run", lambda cmd, **k: (calls.append(cmd), real(cmd, **k))[1])
    timeline.timeline_data(project)
    assert not any("select=" in " ".join(c) for c in calls)         # detection not re-run


def test_cuts_last_resort_scene_starts_without_video(project):
    write(project, "scene_timings.json", [{"scene_id": 1, "start": 0.2, "end": 3.0},
                                          {"scene_id": 2, "start": 3.4, "end": 6.0}])
    data = timeline.timeline_data(project)
    assert data["duration"] == 6.0
    assert data["cuts"] == [0.0, 0.2, 3.4, 6.0]


def test_cuts_are_sorted_deduped_and_clamped(project):
    make_video(project / "final.mp4", seconds=4.0)
    d = dur_of(project / "final.mp4")
    write(project, "shots.json", [{"duration_seconds": 1.0}, {"duration_seconds": 0.0},
                                  {"duration_seconds": d - 1.0}])
    cuts = timeline.timeline_data(project)["cuts"]
    assert cuts == sorted(set(cuts)) and cuts[0] == 0.0 and cuts[-1] == pytest.approx(d, abs=0.001)
    assert all(0 <= c <= d + 1e-6 for c in cuts)


# ── beats / scenes / words ───────────────────────────────────────────────────────────────
def test_beats_are_mapped_to_the_first_scene_carrying_them(project):
    write(project, "scene_timings.json", [
        {"scene_id": 1, "start": 0.1, "end": 2.0}, {"scene_id": 2, "start": 2.5, "end": 4.0},
        {"scene_id": 3, "start": 4.5, "end": 6.0}, {"scene_id": 4, "start": 6.5, "end": 8.0}])
    write(project, "narration.json", {
        "beats": [{"id": 1, "function": "COLD_OPEN", "name": "a"},
                  {"id": 2, "function": "CLIMAX", "name": "b"},
                  {"id": 9, "function": "LANDING", "name": "no scene"}],
        "scenes": [{"scene_id": 1, "beat_id": 0}, {"scene_id": 2, "beat_id": 1},
                   {"scene_id": 3, "beat_id": 2}, {"scene_id": 4, "beat_id": 2}]})
    write(project, "word_timestamps.json", [{"word": "hi", "start": 0.1, "end": 0.4}])
    data = timeline.timeline_data(project)
    assert [(b["id"], b["function"], b["t"]) for b in data["beats"]] == [(1, "COLD_OPEN", 2.5), (2, "CLIMAX", 4.5)]
    assert data["scenes"][0] == {"scene_id": 1, "t": 0.1, "end": 2.0}
    assert data["words"] == [{"w": "hi", "t": 0.1, "end": 0.4}]


def test_empty_project_gives_empty_lists(project):
    data = timeline.timeline_data(project)
    assert data == {"duration": 0.0, "cuts": [0.0], "scenes": [], "beats": [], "words": []}


# ── snap_time ────────────────────────────────────────────────────────────────────────────
DATA = {"cuts": [0.0, 5.0, 10.0], "beats": [{"t": 5.1}, {"t": 7.0}], "words": [{"t": 5.15}, {"t": 7.2}, {"t": 9.0}]}


def test_snap_within_and_beyond_threshold():
    assert timeline.snap_time(7.1, DATA) == 7.0
    assert timeline.snap_time(8.0, DATA) == 8.0
    assert timeline.snap_time(7.26, DATA, threshold=0.25) == 7.2
    assert timeline.snap_time(7.6, DATA, threshold=0.25) == 7.6
    assert timeline.snap_time(7.6, DATA, threshold=0.5) == 7.2


def test_snap_nearest_wins_over_priority():
    assert timeline.snap_time(5.12, DATA) == 5.1       # beat (0.02) beats the cut (0.12)


def test_snap_priority_on_ties_and_kinds():
    tie = {"cuts": [5.0], "beats": [{"t": 5.0}], "words": [{"t": 5.0}]}
    assert timeline.snap_time(5.1, tie) == 5.0
    d = {"cuts": [4.9], "beats": [{"t": 5.1}], "words": [{"t": 5.1}]}
    assert timeline.snap_time(5.0, d) == pytest.approx(4.9)       # equal distance: cut first
    assert timeline.snap_time(5.0, d, kinds=("beats", "words")) == 5.1
    assert timeline.snap_time(5.0, d, kinds=()) == 5.0


# ── PNGs ─────────────────────────────────────────────────────────────────────────────────
def test_waveform_png_size_alpha_and_cache(project, monkeypatch):
    make_video(project / "final.mp4", seconds=4.0)
    p = timeline.waveform_png(project, 300, 40)
    assert p.parent == project / "mascot" / "cache" and p.suffix == ".png"
    im = Image.open(p)
    assert im.size == (300, 40) and im.mode == "RGBA"
    px = im.load()
    assert px[150, 0][3] == 0 or px[150, 20][3] > 0                      # transparent background / bar centre
    assert max(px[x, 20][3] for x in range(300)) == 204
    assert timeline.waveform_png(project, 300, 41) != p                  # size is part of the key
    monkeypatch.setattr(timeline, "_run", lambda *a, **k: pytest.fail("ffmpeg re-run on a cache hit"))
    assert timeline.waveform_png(project, 300, 40) == p


def test_waveform_uses_audio_wav_only_when_duration_matches(project):
    make_video(project / "final.mp4", seconds=4.0)
    _ff("-f", "lavfi", "-i", "sine=frequency=300:duration=4", str(project / "audio.wav"))
    assert timeline._audio_source(project) == project / "audio.wav"
    _ff("-f", "lavfi", "-i", "sine=frequency=300:duration=9", str(project / "audio.wav"))
    assert timeline._audio_source(project) == project / "final.mp4"


def test_waveform_cache_invalidates_when_the_source_changes(project):
    make_video(project / "final.mp4", seconds=4.0)
    a = timeline.waveform_png(project, 100, 20)
    make_video(project / "final.mp4", seconds=3.0)
    assert timeline.waveform_png(project, 100, 20) != a


def test_thumb_strip_size_columns_and_cache(project, monkeypatch):
    make_video(project / "final.mp4", hard_cut_at=2.0)
    p = timeline.thumb_strip_png(project, 200, 30)
    im = Image.open(p).convert("RGB")
    assert im.size == (200, 30) and p.parent == project / "mascot" / "cache"
    left, right = im.getpixel((20, 15)), im.getpixel((180, 15))
    assert left[0] > 150 and left[1] < 60 and left[2] < 60                # first shot is solid red
    assert right != left                                                   # second is the test pattern
    monkeypatch.setattr(timeline, "_run", lambda *a, **k: pytest.fail("ffmpeg re-run on a cache hit"))
    assert timeline.thumb_strip_png(project, 200, 30) == p


def test_thumb_strip_without_video_is_a_transparent_png_of_the_right_size(project):
    p = timeline.thumb_strip_png(project, 90, 10)
    im = Image.open(p)
    assert im.size == (90, 10) and im.getpixel((5, 5))[3] == 0
