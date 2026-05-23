"""Shot list construction and per-shot ffmpeg Ken Burns rendering."""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image

from .schema import Shot


OUTPUT_W = 1080
OUTPUT_H = 1920
TARGET_ASPECT = OUTPUT_W / OUTPUT_H
FPS = 30
PADDING_PCT = 0.05
UPSCALE_DIM = 2160
MOTION_CYCLE = ("zoom_in", "pan_right", "zoom_out")

# Two strategies:
#   "scene"          — one continuous Ken-Burns per scene (ComicsUnlocked: 4-5 shots, 10-30s each)
#   "caption_chunk"  — one shot per caption chunk (TheComicCivilian: 35-40 shots, ~1.5s each,
#                      visual changes EVERY time the on-screen text changes)
SHOT_STRATEGY = "caption_chunk"

SHOTS_PER_SCENE = 1            # used when SHOT_STRATEGY == "scene"
SHOT_TARGET_SECONDS = 2.5
SHOT_MIN_SECONDS = 0.6         # caption-chunk mode: ~0.5-2s per shot
SHOT_MAX_SECONDS = 4.5
STATIC_MOTION_BELOW_SECONDS = 1.5
SILENCE_GAP_THRESHOLD = 0.2
SNAP_WINDOW_SECONDS = 0.5


def build_shots(
    narration: dict,
    *,
    scene_timings: list[dict] | None = None,
    word_timestamps: list[dict] | None = None,
    caption_chunks: list[dict] | None = None,
    pages_by_number: dict[int, dict] | None = None,
) -> list[Shot]:
    """Split each narration scene into shots.

    Strategy depends on SHOT_STRATEGY:
      • "scene"         — one shot per scene (one continuous Ken-Burns)
      • "caption_chunk" — one shot per caption chunk (visual changes WITH the text,
                           cycling through the page's panels for visual variety)
    """
    if SHOT_STRATEGY == "caption_chunk" and caption_chunks and pages_by_number is not None:
        return _build_shots_per_chunk(
            narration, caption_chunks, pages_by_number, scene_timings or [],
        )
    return _build_shots_per_scene(narration, scene_timings, word_timestamps)


def _build_shots_per_scene(
    narration: dict,
    scene_timings: list[dict] | None,
    word_timestamps: list[dict] | None,
) -> list[Shot]:
    """ComicsUnlocked-style: one continuous Ken-Burns shot per scene."""
    scenes = narration.get("scenes") or []
    timings_by_scene = {int(t.get("scene_id", 0) or 0): t for t in (scene_timings or [])}
    shots: list[Shot] = []
    shot_id = 0
    prev_scene_end = 0.0  # Each scene "owns" from the previous scene's end → its own end,
                          # so inter-scene silence (TTS gaps between sentences) is absorbed
                          # into the visual timeline. Without this, ffmpeg -shortest clips
                          # the audio tail.
    for s in scenes:
        scene_id = int(s.get("scene_id") or len(shots) + 1)
        # Prefer ACTUAL scene duration from Stage 4 word-alignment over Stage 3's
        # words-per-second estimate — the LLM estimate underpredicts when TTS uses
        # a slower emotion (e.g. "contemplative"), causing audio truncation under
        # ffmpeg's `-shortest` flag. scene_timings.json is the source of truth.
        timing = timings_by_scene.get(scene_id)
        if timing and float(timing.get("end", 0)) > float(timing.get("start", 0)):
            target = float(timing["end"]) - prev_scene_end
            prev_scene_end = float(timing["end"])
        else:
            target = float(s.get("target_seconds") or 0.0)
        bbox = s.get("panel_bbox") or {}
        source_image = str(s.get("source_image") or "")
        if not source_image or target <= 0.0:
            continue

        if SHOTS_PER_SCENE <= 1:
            # ONE continuous motion per scene — matches ComicsUnlocked's 10-30s
            # held-panel-with-Ken-Burns style. Motion cycles per-scene for variety,
            # not per-sub-shot (which created hard cuts mid-scene).
            durations = [target]
        else:
            durations = _plan_durations(
                scene_id=scene_id,
                target=target,
                scene_timing=timings_by_scene.get(scene_id),
                word_timestamps=word_timestamps,
            )
        for i, dur in enumerate(durations):
            if dur < STATIC_MOTION_BELOW_SECONDS:
                motion = "static"
            elif SHOTS_PER_SCENE <= 1:
                # Rotate motion based on scene_id so consecutive scenes differ.
                motion = MOTION_CYCLE[(scene_id - 1) % len(MOTION_CYCLE)]
            else:
                motion = MOTION_CYCLE[i % len(MOTION_CYCLE)]
            shots.append(Shot(
                shot_id=shot_id,
                scene_id=scene_id,
                duration_seconds=max(0.4, dur),
                panel_bbox={"x": int(bbox.get("x", 0)), "y": int(bbox.get("y", 0)),
                            "w": int(bbox.get("w", 0)), "h": int(bbox.get("h", 0))},
                source_image=source_image,
                motion=motion,
            ))
            shot_id += 1
    return shots


def _build_shots_per_chunk(
    narration: dict,
    caption_chunks: list[dict],
    pages_by_number: dict[int, dict],
    scene_timings: list[dict],
) -> list[Shot]:
    """TheComicCivilian-style: one shot per caption chunk, cycling through the
    scene's page's panels for visual variety. Shot duration = chunk duration
    (extended slightly to absorb inter-chunk silence)."""
    scenes = narration.get("scenes") or []
    scenes_by_id = {int(s.get("scene_id") or i): s for i, s in enumerate(scenes, start=1)}
    timings_by_scene = {int(t.get("scene_id", 0) or 0): t for t in (scene_timings or [])}

    # Map each chunk to its scene by time
    def find_scene_for_chunk(c):
        c_mid = (float(c.get("start", 0)) + float(c.get("end", 0))) / 2
        for st in scene_timings:
            if float(st.get("start", 0)) <= c_mid < float(st.get("end", 1e9)):
                return scenes_by_id.get(int(st.get("scene_id", 0)))
        # fallback: first scene
        return scenes_by_id.get(1) if scenes_by_id else None

    # Track per-page panel cycle index so consecutive chunks on the same page
    # get different panels (visual variety).
    page_panel_idx: dict[int, int] = {}

    shots: list[Shot] = []
    for i, chunk in enumerate(caption_chunks):
        c_start = float(chunk.get("start", 0))
        c_end = float(chunk.get("end", c_start + 1.0))
        scene = find_scene_for_chunk(chunk)
        if scene is None:
            continue
        # Extend shot end to next chunk start (or scene end / audio end) to absorb gap silence.
        if i + 1 < len(caption_chunks):
            next_start = float(caption_chunks[i + 1].get("start", c_end))
            c_end_extended = max(c_end, next_start)
        else:
            # last chunk: extend to the very end of the last scene's audio
            c_end_extended = max(c_end, max((float(st.get("end", 0)) for st in scene_timings), default=c_end))
        dur = max(0.4, c_end_extended - c_start)

        page_ref = int(scene.get("page_ref", 0) or 0)
        page = pages_by_number.get(page_ref) if pages_by_number else None
        panels_on_page = (page.get("panels") or []) if page else []

        # Pick a panel: cycle through the page's panels for variety
        if panels_on_page:
            idx = page_panel_idx.get(page_ref, 0) % len(panels_on_page)
            panel = panels_on_page[idx]
            bbox = panel.get("bbox") or {}
            source_image = str(page.get("source_image") or "") if page else str(scene.get("source_image") or "")
            page_panel_idx[page_ref] = idx + 1
        else:
            bbox = scene.get("panel_bbox") or {}
            source_image = str(scene.get("source_image") or "")

        motion = "static" if dur < STATIC_MOTION_BELOW_SECONDS else MOTION_CYCLE[i % len(MOTION_CYCLE)]
        shots.append(Shot(
            shot_id=i,
            scene_id=int(scene.get("scene_id") or 1),
            duration_seconds=dur,
            panel_bbox={"x": int(bbox.get("x", 0)), "y": int(bbox.get("y", 0)),
                        "w": int(bbox.get("w", 0)), "h": int(bbox.get("h", 0))},
            source_image=source_image,
            motion=motion,
        ))
    return shots


def _plan_durations(
    *,
    scene_id: int,
    target: float,
    scene_timing: dict | None,
    word_timestamps: list[dict] | None,
) -> list[float]:
    n_shots = max(1, min(4, round(target / SHOT_TARGET_SECONDS)))
    if n_shots == 1:
        return [target]

    even_step = target / n_shots
    rel_splits = [even_step * i for i in range(1, n_shots)]

    if scene_timing and word_timestamps:
        scene_start = float(scene_timing.get("start", 0.0))
        scene_end = float(scene_timing.get("end", scene_start + target))
        gaps_abs = _silence_gaps_in_window(word_timestamps, scene_start, scene_end)
        rel_splits = [_snap_split_to_gaps(rel, scene_start, gaps_abs) for rel in rel_splits]

    rel_splits = sorted(_clamp_splits(rel_splits, target))
    boundaries = [0.0] + rel_splits + [target]
    return [round(max(0.4, boundaries[i + 1] - boundaries[i]), 3) for i in range(n_shots)]


def _silence_gaps_in_window(
    word_timestamps: list[dict], scene_start: float, scene_end: float
) -> list[float]:
    gaps: list[float] = []
    prev_end = scene_start
    for w in word_timestamps:
        ws = float(w.get("start", 0.0))
        we = float(w.get("end", 0.0))
        if we < scene_start or ws > scene_end:
            continue
        if ws - prev_end >= SILENCE_GAP_THRESHOLD:
            gaps.append((prev_end + ws) / 2.0)
        prev_end = max(prev_end, we)
    return gaps


def _snap_split_to_gaps(rel_split: float, scene_start: float, gaps_abs: list[float]) -> float:
    if not gaps_abs:
        return rel_split
    abs_split = scene_start + rel_split
    best_gap = min(gaps_abs, key=lambda g: abs(g - abs_split))
    if abs(best_gap - abs_split) <= SNAP_WINDOW_SECONDS:
        return max(SHOT_MIN_SECONDS / 2, best_gap - scene_start)
    return rel_split


def _clamp_splits(splits: list[float], total: float) -> list[float]:
    if not splits:
        return splits
    sorted_splits = sorted(splits)
    fixed: list[float] = []
    prev = 0.0
    for s in sorted_splits:
        s = max(prev + SHOT_MIN_SECONDS, min(s, total - SHOT_MIN_SECONDS))
        fixed.append(s)
        prev = s
    return fixed


def render_shot(
    shot: Shot,
    out_path: Path,
    *,
    work_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Render one Ken Burns shot to MP4."""
    ff = _require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = work_dir or out_path.parent / "_panels"
    work_dir.mkdir(parents=True, exist_ok=True)

    panel_png = work_dir / f"panel_{shot.shot_id:03d}.png"
    _crop_panel(shot.source_image, shot.panel_bbox, panel_png)

    framed = _prepare_panel_frame(panel_png, panel_png.with_name(panel_png.stem + "_9x16.png"))

    duration = max(0.4, shot.duration_seconds)
    frames = max(1, int(round(duration * FPS)))

    filter_complex = f"[0:v]{_zoompan_expr(shot.motion, frames)}[v]"

    cmd = [
        ff, "-y",
        "-framerate", "1",
        "-loop", "1",
        "-t", "1",
        "-i", str(framed),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-an",
        str(out_path),
    ]
    if progress:
        progress(f"[stage5] shot {shot.shot_id:03d} (scene {shot.scene_id}, "
                 f"{shot.motion}, {duration:.2f}s)")
    _run(cmd)
    return out_path


def _zoompan_expr(motion: str, frames: int) -> str:
    s = f"{OUTPUT_W}x{OUTPUT_H}"
    fps = FPS
    if motion == "zoom_in":
        return (
            f"zoompan=z='min(1.10,zoom+{0.10 / max(1, frames):.6f})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "zoom_out":
        return (
            f"zoompan=z='if(eq(on,0),1.10,max(1.0,zoom-{0.10 / max(1, frames):.6f}))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "pan_right":
        return (
            f"zoompan=z='1.05':"
            f"x='iw/2-(iw/zoom/2)+(iw*0.05)*(on/{max(1, frames)})':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    return (
        f"zoompan=z='1.05':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={s}:fps={fps}"
    )


def _prepare_panel_frame(panel_png: Path, out_path: Path) -> Path:
    """Always cover-scale the panel to fill 1080×1920. Wide panels lose edge
    content — channel data (65/66 audited frames from 11 reference videos)
    shows this is the right trade-off vs blurred letterbox backgrounds."""
    with Image.open(panel_png) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        scale = max(OUTPUT_W / iw, OUTPUT_H / ih)
        new_w = max(OUTPUT_W, int(round(iw * scale)))
        new_h = max(OUTPUT_H, int(round(ih * scale)))
        scaled = im.resize((new_w, new_h), Image.LANCZOS)
        x0 = (new_w - OUTPUT_W) // 2
        y0 = (new_h - OUTPUT_H) // 2
        frame = scaled.crop((x0, y0, x0 + OUTPUT_W, y0 + OUTPUT_H))
    frame.save(out_path, "PNG")
    return out_path


def _crop_panel(source_image: str, bbox: dict[str, int], out_path: Path) -> Path:
    src = Path(source_image)
    if not src.exists():
        raise FileNotFoundError(f"source image missing: {src}")
    with Image.open(src) as im:
        iw, ih = im.size
        x = int(bbox.get("x", 0))
        y = int(bbox.get("y", 0))
        w = int(bbox.get("w", 0))
        h = int(bbox.get("h", 0))
        if w <= 0 or h <= 0:
            x, y, w, h = 0, 0, iw, ih
        pad_x = int(w * PADDING_PCT)
        pad_y = int(h * PADDING_PCT)
        left = max(0, x - pad_x)
        top = max(0, y - pad_y)
        right = min(iw, x + w + pad_x)
        bottom = min(ih, y + h + pad_y)
        cropped = im.convert("RGB").crop((left, top, right, bottom))
        cropped.save(out_path, "PNG")
    return out_path


def _require_ffmpeg() -> str:
    from config import FFMPEG_BIN
    if os.path.isabs(FFMPEG_BIN) and os.path.isfile(FFMPEG_BIN):
        return FFMPEG_BIN
    p = shutil.which(FFMPEG_BIN) or shutil.which("ffmpeg")
    if not p:
        raise FileNotFoundError(f"ffmpeg not found (FFMPEG_BIN={FFMPEG_BIN}). Check .env or PATH.")
    return p


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = (res.stderr or "")[-2000:]
        raise RuntimeError(
            f"ffmpeg failed (exit {res.returncode})\ncmd: {' '.join(cmd)}\nstderr:\n{tail}"
        )
