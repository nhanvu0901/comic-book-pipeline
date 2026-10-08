"""The stable-path render scenarios the P0 baseline is made of — ONE definition, run by both
tests/test_p0_baseline.py (against HEAD, ENABLE_VIDEO_CLIPS off) and scripts/p0_baseline_capture.py
(against a checkout of commit 6788212, the P0 baseline).

Byte-identity is only meaningful like-with-like: the encoded mp4 carries the x264 build's version string
and shots.json embeds the OS's path separators, so a hash from one machine/ffmpeg proves nothing on
another. A baseline is therefore a set of hashes PER PLATFORM (platform_key()), each captured by running
6788212 on that platform with the same ffmpeg — never derived from HEAD.

This file must run unchanged against 6788212 (it only touches APIs that existed there)."""
from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_PLAIN = "baseline_fixture_p0"
PROJECT_WHIP = "baseline_fixture_whip7"     # seeds 3 whip boundaries (0, 2, 3) of its 5 scenes


def platform_key() -> str:
    """sys.platform | ffmpeg version | x264 build — what decides an encoded mp4's bytes."""
    ff = shutil.which("ffmpeg") or "ffmpeg"
    ver = subprocess.run([ff, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    m = re.search(r"version (\S+)", ver)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.mp4"
        subprocess.run([ff, "-y", "-f", "lavfi", "-i", "color=c=black:size=64x64:rate=5", "-t", "0.4",
                        "-c:v", "libx264", str(out)], capture_output=True, check=True)
        x = re.search(rb"x264 - core (\d+)(?: r(\d+))?", out.read_bytes())
    x264 = f"x264-core{x.group(1).decode()}" + (f"-r{x.group(2).decode()}" if x and x.group(2) else "") if x else "x264-?"
    return f"{sys.platform}|ffmpeg-{m.group(1) if m else '?'}|{x264}"


_PIN = dict(XFADE_DURATION=0.25, XFADE_TRANSITION="dissolve", XFADE_SOFT_EDGES=True,
            XFADE_ROTATE="dissolve,slideleft,slideright", FLASH_ACCENTS=False, FLASH_ACCENTS_MAX=3,
            TRANSITION_WHIP_PROB=0.5, TRANSITION_WHIP_SECONDS=0.24, ENABLE_VIDEO_CLIPS=False)
_MISSING = object()


@contextlib.contextmanager
def pinned():
    """Pin every knob that shapes the render (so a developer's .env can't move a hash; the flag OFF),
    and put them back afterwards — the pins must not leak into the rest of a test session."""
    import config
    import stages.stage_5.shots as _shots
    saved = {k: getattr(config, k, _MISSING) for k in _PIN}
    saved_upscale = _shots.PANEL_UPSCALE
    for k, v in _PIN.items():
        setattr(config, k, v)
    _shots.PANEL_UPSCALE = False       # no Real-ESRGAN: its presence differs per machine and must not move a hash
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                delattr(config, k)
            else:
                setattr(config, k, v)
        _shots.PANEL_UPSCALE = saved_upscale


def _pages(d: Path):
    import numpy as np
    from PIL import Image
    p1, p2 = d / "page_1.png", d / "page_2.png"
    arr1 = np.full((1800, 1200, 3), 40, dtype=np.uint8)
    arr1[200:800, 200:1000] = [200, 100, 50]
    Image.fromarray(arr1).save(p1)
    arr2 = np.full((1800, 1200, 3), 60, dtype=np.uint8)
    arr2[300:900, 100:900] = [50, 150, 220]
    Image.fromarray(arr2).save(p2)
    return p1, p2


def _render(shot_list, d: Path, project: str) -> dict[str, str]:
    from stages.stage_5 import pipeline, shots
    shots_dir = d / "shots"
    paths = [shots.render_shot(s, shots_dir / f"shot_{s.shot_id:03d}.mp4") for s in shot_list]
    shots_json = d / "shots.json"
    pipeline._write_shots_log(shot_list, [], shots_dir, shots_json, lambda m: None)
    silent = d / "video_silent.mp4"
    pipeline._assemble_video(shot_list, paths, silent, project=project)
    return {"shots_json_sha256": hashlib.sha256(shots_json.read_bytes()).hexdigest(),
            "video_silent_sha256": hashlib.sha256(silent.read_bytes()).hexdigest()}


def scenario_plain(d: Path) -> dict[str, str]:
    """2 scenes / 3 panel shots (the original P0 fixture)."""
    from stages.stage_5.schema import Shot
    p1, p2 = _pages(d)
    sl = [
        Shot(shot_id=0, scene_id=1, duration_seconds=1.0, panel_bbox={"x": 200, "y": 200, "w": 800, "h": 600},
             source_image=str(p1), motion="zoom_in", caption_text="Intro beat"),
        Shot(shot_id=1, scene_id=1, duration_seconds=1.0, panel_bbox={"x": 200, "y": 200, "w": 800, "h": 600},
             source_image=str(p1), motion="pan_down", caption_text="Next beat"),
        Shot(shot_id=2, scene_id=2, duration_seconds=1.0, panel_bbox={"x": 100, "y": 300, "w": 800, "h": 600},
             source_image=str(p2), motion="zoom_out", caption_text="Climax beat"),
    ]
    return _render(sl, d, PROJECT_PLAIN)


def scenario_whip(d: Path) -> dict[str, str]:
    """5 one-shot scenes whose project name seeds whip bridges at boundaries 0, 2 and 3."""
    from stages.stage_5.schema import Shot
    p1, p2 = _pages(d)
    motions = ["zoom_in", "pan_down", "zoom_out", "zoom_in", "pan_down"]
    sl = []
    for i in range(5):
        page, bbox = (p1, {"x": 200, "y": 200, "w": 800, "h": 600}) if i % 2 == 0 else \
                     (p2, {"x": 100, "y": 300, "w": 800, "h": 600})
        sl.append(Shot(shot_id=i, scene_id=i + 1, duration_seconds=1.2, panel_bbox=bbox,
                       source_image=str(page), motion=motions[i], caption_text=f"beat {i}"))
    return _render(sl, d, PROJECT_WHIP)


def scenario_legacy_clip(d: Path) -> dict[str, str]:
    """panel → clip shot (clip SHORTER than its shot: the legacy full-length freeze) → panel. The
    legacy opt-in clip feature must render and assemble exactly as before while the flag is off."""
    from stages.stage_5.schema import Shot
    p1, p2 = _pages(d)
    src = d / "src_clip.mp4"
    ff = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30", "-t", "1.0",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], capture_output=True, check=True)
    bbox = {"x": 200, "y": 200, "w": 800, "h": 600}
    sl = [
        Shot(shot_id=0, scene_id=1, duration_seconds=1.0, panel_bbox=bbox, source_image=str(p1),
             motion="zoom_in", caption_text="before"),
        Shot(shot_id=1, scene_id=2, duration_seconds=1.6, panel_bbox=bbox, source_image=str(p1),
             motion="zoom_in", caption_text="clip", clip_path=str(src), clip_in=0.2, clip_out=0.9,
             clip_id="legacy_clip_1", clip_source_url="https://example.invalid/x"),
        Shot(shot_id=2, scene_id=3, duration_seconds=1.0, panel_bbox={"x": 100, "y": 300, "w": 800, "h": 600},
             source_image=str(p2), motion="zoom_out", caption_text="after"),
    ]
    return _render(sl, d, "baseline_fixture_clip")


SCENARIOS = {"plain": scenario_plain, "whip": scenario_whip, "legacy_clip": scenario_legacy_clip}
# directory (under the projects root) each scenario renders in. `plain` keeps the ORIGINAL P0 fixture's
# directory, so its shots.json — which embeds these paths — is the very same bytes the first baseline hashed.
_DIRS = {"plain": "p0_baseline_fixture", "whip": "p0_baseline_fixture_whip", "legacy_clip": "p0_baseline_fixture_clip"}


def run_all(projects_dir: Path = Path("projects")) -> dict[str, dict[str, str]]:
    """Run every scenario in its own clean directory under `projects_dir` (RELATIVE: the paths are part
    of shots.json, so callers must pass the same relative directory every time)."""
    out: dict[str, dict[str, str]] = {}
    with pinned():
        for name, fn in SCENARIOS.items():
            d = Path(projects_dir) / _DIRS[name]
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
            try:
                out[name] = fn(d)
            finally:
                shutil.rmtree(d, ignore_errors=True)
    return out
