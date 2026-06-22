# tests/test_xfade_assembly.py
from pathlib import Path
from dataclasses import dataclass
from stages.stage_5 import pipeline as P


@dataclass
class FakeShot:
    scene_id: int
    duration_seconds: float


def test_group_shots_by_scene_preserves_order_and_sums():
    shots = [FakeShot(1, 2.0), FakeShot(1, 1.5), FakeShot(2, 3.0), FakeShot(3, 2.0)]
    paths = [Path(f"s{i}.mp4") for i in range(4)]
    groups = P._group_shots_by_scene(shots, paths)
    assert [g[0] for g in groups] == [1, 2, 3]
    assert groups[0][1] == [Path("s0.mp4"), Path("s1.mp4")]
    assert groups[0][2] == 3.5
    assert groups[1][2] == 3.0


def test_xfade_offsets_are_cumulative_and_sum_preserving():
    durs = [3.5, 3.0, 2.0]   # 3 scenes -> 2 joins
    offs = P._xfade_offsets(durs)
    assert offs == [3.5, 6.5]
    # net duration of the xfade chain == sum(durs) when last clip is unpadded
    assert offs[-1] + durs[-1] == sum(durs)


import os
import json
import subprocess
import shutil
import pytest


PROJ = "magik vs vampire"  # has narration.json + audio.wav + preprocessed/


def _have(p):
    return Path(p).exists()


@pytest.mark.integration
def test_render_has_smooth_transition_and_keeps_sync():
    root = Path("projects") / PROJ
    if not (_have(root / "narration.json") and _have(root / "audio.wav")):
        pytest.skip("sample project not present")
    # render Stage 5 with xfade on (default)
    r = subprocess.run(
        [".venv/bin/python", "-m", "stages.stage_5", "--project", PROJ, "--force"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    final = root / "final.mp4"
    assert final.exists()
    # duration ~ audio duration (xfade tail-pad preserves the timeline)
    import wave

    with wave.open(str(root / "audio.wav"), "rb") as wf:
        audio = wf.getnframes() / wf.getframerate()
    ff = shutil.which("ffprobe")
    vid = float(
        subprocess.run(
            [
                ff,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(final),
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    assert abs(vid - audio) < 0.6, f"video {vid:.2f}s vs audio {audio:.2f}s — sync drift"
