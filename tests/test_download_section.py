import subprocess
from pathlib import Path
import pytest
import config

from stages import clip_fetch
from stages.stage_5 import clips


def test_ytdlp_section_args_contains_download_sections_and_keyframes(tmp_path):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    start = 10.0
    beat_dur = 4.0
    # Formula: end = start + beat_dur * CLIP_SPEED_MAX + margin (default margin=2.0)
    expected_end = start + beat_dur * config.CLIP_SPEED_MAX + 2.0  # 10 + 4*1.25 + 2 = 17.0

    args = clip_fetch.ytdlp_section_args(url, tmp_path, start=start, beat_duration=beat_dur)
    joined = " ".join(args)

    assert "--download-sections" in joined
    assert f"*{start:.2f}-{expected_end:.2f}" in joined
    assert "--force-keyframes-at-cuts" in joined
    assert "--js-runtimes node" in joined
    assert "--merge-output-format mp4" in joined


def test_render_clip_preview_geometry_and_frames(tmp_path):
    import shutil
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg required")

    # Generate a 5s synthetic test video (640x360, 25fps)
    src_mp4 = tmp_path / "src.mp4"
    subprocess.run([
        ff, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
        "-t", "5.0", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src_mp4)
    ], check=True, capture_output=True)

    preview_mp4 = tmp_path / "preview.mp4"
    beat_dur = 2.0
    clips.render_clip_preview(src_mp4, start=1.0, beat_duration=beat_dur, out_path=preview_mp4)

    assert preview_mp4.exists()
    info = clips.probe_video(preview_mp4)
    assert info["width"] == 1080
    assert info["height"] == 1920
    assert abs(info["duration"] - beat_dur) < 0.1


# ─── fetch_clip_section: the section path, its failure message, and the whole-video fallback ──────

import subprocess as _sp


def _fake_run_factory(monkeypatch, *, section_ok: bool, src_for_section=None, calls=None):
    """subprocess.run stand-in for the yt-dlp section command. The real ffmpeg (normalize) is untouched."""
    def fake(cmd, *, timeout=None):
        assert "--download-sections" in cmd and timeout == clip_fetch.SECTION_TIMEOUT
        calls.append("section")
        if section_ok:
            return _sp.CompletedProcess(cmd, 0, stdout=str(src_for_section) + "\n", stderr="")
        return _sp.CompletedProcess(cmd, 1, stdout="", stderr=(
            "WARNING: Your yt-dlp version (2026.07.04) is older than 90 days!\n"
            "ERROR: ffmpeg exited with code 3436169992\n"))
    monkeypatch.setattr(clip_fetch, "_run_capture", fake)


def _source(tmp_path, dur=12.0):
    import shutil
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg required")
    p = tmp_path / "whole.mp4"
    _sp.run([ff, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30", "-t", f"{dur}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True, capture_output=True)
    return p


def test_a_section_download_is_normalised_and_cached(tmp_path, monkeypatch):
    src = _source(tmp_path, 7.0)
    calls = []
    _fake_run_factory(monkeypatch, section_ok=True, src_for_section=src, calls=calls)
    out = clip_fetch.fetch_clip_section("https://www.youtube.com/watch?v=abcDEF12345", tmp_path / "clips",
                                        start=3.0, beat_duration=4.0, log=lambda m: None)
    assert out.name == "abcDEF12345_3.0_4.0.mp4" and out.is_file() and calls == ["section"]
    assert clips.probe_video(out)["duration"] == pytest.approx(7.0, abs=0.2)
    clip_fetch.fetch_clip_section("https://www.youtube.com/watch?v=abcDEF12345", tmp_path / "clips",
                                  start=3.0, beat_duration=4.0, log=lambda m: None)
    assert calls == ["section"], "cached: no second download"


def test_a_failed_section_falls_back_to_the_whole_video_cut_to_the_same_window(tmp_path, monkeypatch):
    whole = _source(tmp_path, 20.0)
    calls = []
    _fake_run_factory(monkeypatch, section_ok=False, calls=calls)
    monkeypatch.setattr(clip_fetch, "download", lambda url, out_dir, **kw: calls.append("whole") or whole)
    logs = []
    out = clip_fetch.fetch_clip_section("https://www.youtube.com/watch?v=abcDEF12345", tmp_path / "clips",
                                        start=5.0, beat_duration=4.0, log=logs.append)
    assert calls == ["section", "whole"]
    # same window the section would have been: [start, start + beat*CLIP_SPEED_MAX + margin] = 5 .. 12 s
    assert clips.probe_video(out)["duration"] == pytest.approx(4.0 * config.CLIP_SPEED_MAX + 2.0, abs=0.2)
    assert any("falling back" in m and "ffmpeg exited" in m for m in logs)
    assert not any("WARNING" in m for m in logs), "the reason is the ERROR line, not the version warning"


def test_the_fallback_cuts_at_the_picked_moment_not_at_zero(tmp_path, monkeypatch):
    import numpy as np
    whole = _source(tmp_path, 20.0)                  # testsrc burns the running time into the frames
    _fake_run_factory(monkeypatch, section_ok=False, calls=[])
    monkeypatch.setattr(clip_fetch, "download", lambda url, out_dir, **kw: whole)
    out = clip_fetch.fetch_clip_section("https://www.youtube.com/watch?v=abcDEF12345", tmp_path / "clips",
                                        start=8.0, beat_duration=3.0, log=lambda m: None)
    import shutil

    def frame(path, t):
        raw = _sp.run([shutil.which("ffmpeg"), "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1",
                       "-vf", "scale=64:36,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).astype(float)
    # the first frame of the cut equals the whole video's frame at 8.0 s, not its frame at 0 s
    d_at_start = np.abs(frame(out, 0.0) - frame(whole, 8.0)).mean()
    d_at_zero = np.abs(frame(out, 0.0) - frame(whole, 0.0)).mean()
    assert d_at_start < 6 and d_at_zero > d_at_start * 2


def test_both_failing_reports_the_error_line_and_the_hint(tmp_path, monkeypatch):
    calls = []
    _fake_run_factory(monkeypatch, section_ok=False, calls=calls)
    monkeypatch.setattr(clip_fetch, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network")))
    with pytest.raises(RuntimeError) as ei:
        clip_fetch.fetch_clip_section("https://www.youtube.com/watch?v=abcDEF12345", tmp_path / "clips",
                                      start=1.0, beat_duration=2.0, log=lambda m: None)
    msg = str(ei.value)
    assert "ffmpeg exited with code 3436169992" in msg and "pip install -U yt-dlp" in msg and "no network" in msg


def test_a_stalled_download_is_killed_with_its_children_and_falls_back(tmp_path, monkeypatch):
    """The Windows E2E hung for 10+ minutes on an idle ffmpeg. _run_capture must time out, kill the WHOLE tree
    (yt-dlp -> python -> ffmpeg), and the pick then takes the next strategy instead of waiting forever."""
    import sys
    import time
    child_pid = tmp_path / "grandchild.pid"
    script = (f"import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              f"open(r'{child_pid}', 'w').write(str(p.pid)); time.sleep(60)")
    t0 = time.time()
    with pytest.raises(clip_fetch.DownloadTimeout):
        clip_fetch._run_capture([sys.executable, "-c", script], timeout=2)
    assert time.time() - t0 < 15
    gc = int(child_pid.read_text())
    time.sleep(0.5)
    import os
    try:
        os.kill(gc, 0)
        alive = True
    except OSError:
        alive = False
    assert not alive, "the grandchild (the ffmpeg of a real run) must die with its parent"


def test_a_section_that_times_out_falls_back_to_the_whole_video(tmp_path, monkeypatch):
    whole = _source(tmp_path, 15.0)
    calls = []

    def stalled(cmd, *, timeout=None):
        calls.append("section")
        raise clip_fetch.DownloadTimeout("no result after 180s — killed")
    monkeypatch.setattr(clip_fetch, "_run_capture", stalled)
    monkeypatch.setattr(clip_fetch, "download", lambda url, out_dir, **kw: calls.append("whole") or whole)
    logs = []
    out = clip_fetch.fetch_clip_section("https://www.youtube.com/watch?v=abcDEF12345", tmp_path / "clips",
                                        start=2.0, beat_duration=3.0, log=logs.append)
    assert calls == ["section", "whole"] and out.is_file()
    assert any("no result after 180s" in m for m in logs)
