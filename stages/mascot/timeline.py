"""What the cue timeline shows and snaps to — built from data every project already has.

Sources (each optional; missing files just mean fewer markers):
  * duration ......... ffprobe of render.source_video()
  * cuts ............. shot boundaries, first source that agrees with the video duration wins:
                       1. cumulative ``duration_seconds`` of shots.json,
                       2. cumulative durations of shots/shot_*.mp4 (ffprobe),
                       3. ffmpeg scene detection on a downscaled decode (cached),
                       4. the scene starts from scene_timings.json.
                       0 and the duration are always bounds.
                       "Agrees" = total within 0.5 s of the duration, or SHORTER by at most
                       OUTRO_TAIL_MAX (Stage 5 appends the channel outro card, which is not a shot).
  * scenes ........... scene_timings.json [{scene_id, start, end}]
  * beats ............ narration.json "beats" [{id, function, name}] mapped to time through the
                       scenes that carry that beat_id (start of the first such scene)
  * words ............ word_timestamps.json [{word, start, end}]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import render

MATCH_TOL = 0.5          # s: summed shot durations vs the video duration
OUTRO_TAIL_MAX = 6.0     # s: a shorter total is still accepted (outro card is not in shots.json)
SCENE_THRESHOLD = 0.25   # ffmpeg scene-detection score
MIN_CUT_GAP = 0.05       # s: cuts closer than this are merged
MIN_THUMB_PX = 3         # shots narrower than this are merged into their neighbour
WAVE_RATE = 8000         # Hz of the mono decode used for the waveform
WAVE_RGBA = (235, 235, 235, 204)   # light grey at ~80 % alpha


# ── helpers ──────────────────────────────────────────────────────────────────────────────
def _run(cmd: list[str], *, binary: bool = False) -> subprocess.CompletedProcess:
    """Single choke point for every ffmpeg/ffprobe call (tests monkeypatch it)."""
    return subprocess.run(cmd, capture_output=True, text=not binary, check=False)


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _f(v, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if x == x else default


def _probe_duration(path: Path) -> float:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(path)])
    try:
        return max(0.0, float((r.stdout or "").strip().splitlines()[0]))
    except (ValueError, IndexError):
        return 0.0


def _cache_dir(project_dir: Path) -> Path:
    d = Path(project_dir) / "mascot" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _signature(path: Path) -> str:
    try:
        st = path.stat()
        return f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        return f"{path}|missing"


def _hash(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _save_png_atomic(img, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    try:
        img.save(tmp, format="PNG")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── cuts ─────────────────────────────────────────────────────────────────────────────────
def _cum(durations: list[float]) -> tuple[list[float], float]:
    t, out = 0.0, []
    for d in durations:
        t += max(0.0, d)
        out.append(t)
    return out[:-1], t          # internal boundaries, total


def _agrees(total: float, duration: float) -> bool:
    if total <= 0 or duration <= 0:
        return False
    gap = duration - total
    return abs(gap) <= MATCH_TOL or 0 < gap <= OUTRO_TAIL_MAX


def _with_tail(inner: list[float], total: float, duration: float) -> list[float]:
    """The boundary where the shots end and the outro card starts is a real cut too."""
    return inner + ([total] if total < duration - MATCH_TOL else [])


def _shots_json_cuts(project_dir: Path, duration: float) -> list[float] | None:
    data = _read_json(Path(project_dir) / "shots.json")
    if isinstance(data, dict):
        data = data.get("shots")
    if not isinstance(data, list) or not data:
        return None
    durs = [_f(e.get("duration_seconds")) for e in data if isinstance(e, dict)]
    inner, total = _cum(durs)
    return _with_tail(inner, total, duration) if _agrees(total, duration) else None


def _shot_files_cuts(project_dir: Path, duration: float) -> list[float] | None:
    files = sorted((Path(project_dir) / "shots").glob("shot_*.mp4"))
    if not files:
        return None
    inner, total = _cum([_probe_duration(p) for p in files])
    return _with_tail(inner, total, duration) if _agrees(total, duration) else None


def _detect_cuts(project_dir: Path, video: Path) -> list[float]:
    cache = _cache_dir(project_dir) / f"cuts_{_hash(_signature(video), SCENE_THRESHOLD)}.json"
    cached = _read_json(cache)
    if isinstance(cached, list):
        return [float(x) for x in cached]
    r = _run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(video), "-an",
              "-vf", f"scale=160:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo",
              "-f", "null", "-"])
    cuts = sorted({round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr or "")})
    if r.returncode == 0:
        _atomic_write(cache, json.dumps(cuts).encode())
    return cuts


def _finish_cuts(cuts, duration: float) -> list[float]:
    """Add the 0 / duration bounds; clamp, sort and dedupe."""
    pts = [0.0] + [min(max(0.0, c), duration) if duration > 0 else max(0.0, c) for c in cuts]
    if duration > 0:
        pts.append(duration)
    out: list[float] = []
    for c in sorted(round(c, 3) for c in pts):
        if not out or c - out[-1] >= MIN_CUT_GAP:
            out.append(c)
        elif duration > 0 and c == round(duration, 3):      # keep the end bound exactly
            out[-1] = c
    return out


# ── public API ───────────────────────────────────────────────────────────────────────────
def timeline_data(project_dir: Path) -> dict:
    """{"duration": float,
        "cuts":   [t, ...],
        "scenes": [{"scene_id", "t", "end"}],
        "beats":  [{"id", "function", "name", "t"}],
        "words":  [{"w", "t", "end"}]}   — all times in seconds, sorted."""
    project_dir = Path(project_dir)
    video = render.source_video(project_dir)
    have_video = bool(video) and Path(video).is_file()
    duration = _probe_duration(Path(video)) if have_video else 0.0

    scenes = []
    raw_scenes = _read_json(project_dir / "scene_timings.json")
    if isinstance(raw_scenes, dict):
        raw_scenes = raw_scenes.get("scenes")
    for s in raw_scenes if isinstance(raw_scenes, list) else []:
        if isinstance(s, dict) and "start" in s:
            st = _f(s.get("start"))
            scenes.append({"scene_id": s.get("scene_id"), "t": round(st, 3),
                           "end": round(max(st, _f(s.get("end"), st)), 3)})
    scenes.sort(key=lambda s: s["t"])

    words = []
    raw_words = _read_json(project_dir / "word_timestamps.json")
    for w in raw_words if isinstance(raw_words, list) else []:
        if isinstance(w, dict) and "start" in w:
            st = _f(w.get("start"))
            words.append({"w": str(w.get("word", "")), "t": round(st, 3),
                          "end": round(max(st, _f(w.get("end"), st)), 3)})
    words.sort(key=lambda w: w["t"])

    beats = []
    narr = _read_json(project_dir / "narration.json")
    if isinstance(narr, dict):
        start_of = {s["scene_id"]: s["t"] for s in scenes}
        first_t: dict = {}                            # beat_id -> earliest scene start
        for sc in narr.get("scenes") or []:
            if not isinstance(sc, dict) or sc.get("beat_id") is None:
                continue
            st = start_of.get(sc.get("scene_id"))
            if st is not None:
                first_t[sc["beat_id"]] = min(st, first_t.get(sc["beat_id"], st))
        for b in narr.get("beats") or []:
            if isinstance(b, dict) and b.get("id") in first_t:
                beats.append({"id": b["id"], "function": str(b.get("function") or ""),
                              "name": str(b.get("name") or ""), "t": first_t[b["id"]]})
        beats.sort(key=lambda b: b["t"])

    if duration <= 0:      # no video: the best end we can infer
        duration = max([0.0] + [s["end"] for s in scenes] + [w["end"] for w in words])

    cuts = None
    if duration > 0:
        cuts = _shots_json_cuts(project_dir, duration)
        if cuts is None:
            cuts = _shot_files_cuts(project_dir, duration)
        if cuts is None and have_video:
            cuts = _detect_cuts(project_dir, Path(video)) or None
    if cuts is None:
        cuts = [s["t"] for s in scenes]
    return {"duration": round(duration, 3), "cuts": _finish_cuts(cuts, duration),
            "scenes": scenes, "beats": beats, "words": words}


def snap_time(t: float, data: dict, *, threshold: float = 0.25,
              kinds: tuple[str, ...] = ("cuts", "beats", "words")) -> float:
    """The nearest cut / beat start / word start within `threshold` s, else t unchanged.
    Cuts win over beats over words when equally near."""
    best: tuple[float, int, float] | None = None       # (distance, priority, time)
    for prio, kind in enumerate(("cuts", "beats", "words")):
        if kind not in kinds:
            continue
        for item in data.get(kind) or []:
            c = _f(item.get("t")) if isinstance(item, dict) else _f(item)
            d = abs(c - t)
            if d <= threshold and (best is None or (d, prio) < best[:2]):
                best = (d, prio, c)
    return t if best is None else best[2]


def _audio_source(project_dir: Path) -> Path | None:
    video = render.source_video(project_dir)
    wav = Path(project_dir) / "audio.wav"
    if video and Path(video).is_file():
        if wav.is_file() and abs(_probe_duration(wav) - _probe_duration(Path(video))) <= MATCH_TOL:
            return wav
        return Path(video)
    return wav if wav.is_file() else None


def waveform_png(project_dir: Path, width: int, height: int) -> Path:
    """Narration waveform (audio.wav, else the video's audio) as a transparent PNG, cached
    under mascot/cache/ keyed by (audio signature, width, height)."""
    import numpy as np
    from PIL import Image, ImageDraw

    width, height = max(1, int(width)), max(1, int(height))
    cache = _cache_dir(project_dir)
    src = _audio_source(project_dir)
    out = cache / f"wave_{_hash(_signature(src) if src else 'none', width, height)}.png"
    if out.is_file():
        return out
    peaks = np.zeros(width)
    if src is not None:
        r = _run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(src), "-vn",
                  "-ac", "1", "-ar", str(WAVE_RATE), "-f", "s16le", "-"], binary=True)
        raw = r.stdout or b""
        pcm = np.abs(np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32))
        if pcm.size:
            edges = np.linspace(0, pcm.size, width + 1).astype(int)
            peaks = np.array([pcm[a:max(b, a + 1)].max() if a < pcm.size else 0.0
                              for a, b in zip(edges[:-1], edges[1:])])
            ref = float(np.percentile(peaks, 99)) or float(peaks.max()) or 1.0
            peaks = np.clip(peaks / ref, 0.0, 1.0)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    mid = height / 2.0
    for x, p in enumerate(peaks):
        half = max(0.5, float(p) * mid)
        draw.line([(x, mid - half), (x, mid + half - 1)], fill=WAVE_RGBA)
    _save_png_atomic(img, out)
    return out


def thumb_strip_png(project_dir: Path, width: int, height: int) -> Path:
    """One frame per shot (taken at each shot's middle, from the clean video) laid out along
    the time axis to `width` px, cached under mascot/cache/. Works for every mode because it
    reads the video, not mode-specific files."""
    import io
    from PIL import Image

    width, height = max(1, int(width)), max(1, int(height))
    cache = _cache_dir(project_dir)
    video = render.source_video(project_dir)
    out = cache / f"thumbs_{_hash(_signature(Path(video)) if video else 'none', width, height)}.png"
    if out.is_file():
        return out
    strip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if video and Path(video).is_file():
        data = timeline_data(project_dir)
        dur, cuts = data["duration"], data["cuts"]
        if dur > 0 and len(cuts) >= 2:
            spans: list[list[float]] = []      # merge shots narrower than MIN_THUMB_PX
            for a, b in zip(cuts[:-1], cuts[1:]):
                if spans and (spans[-1][1] - spans[-1][0]) / dur * width < MIN_THUMB_PX:
                    spans[-1][1] = b
                else:
                    spans.append([a, b])
            for a, b in spans:
                x0, x1 = round(a / dur * width), round(b / dur * width)
                w = x1 - x0
                if w < 1:
                    continue
                r = _run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
                          "-ss", f"{(a + b) / 2.0:.3f}", "-i", str(video), "-frames:v", "1",
                          "-vf", f"scale={w}:{height}:force_original_aspect_ratio=increase,crop={w}:{height}",
                          "-f", "image2pipe", "-vcodec", "png", "-"], binary=True)
                if r.stdout:
                    try:
                        strip.paste(Image.open(io.BytesIO(r.stdout)).convert("RGBA"), (x0, 0))
                    except Exception:
                        pass
    _save_png_atomic(strip, out)
    return out
