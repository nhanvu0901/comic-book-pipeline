"""Shot list construction and per-shot ffmpeg Ken Burns rendering."""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter

from .schema import Shot


OUTPUT_W = 1080
OUTPUT_H = 1920
TARGET_ASPECT = OUTPUT_W / OUTPUT_H
FPS = 30
PADDING_PCT = 0.05
UPSCALE_DIM = 2160
ASPECT_THRESHOLD = 0.7

MOTION_CYCLE = ("zoom_in", "pan_right", "zoom_out")


def build_shots(narration: dict) -> list[Shot]:
    """Split each narration scene into 1-3 visually-distinct shots."""
    scenes = narration.get("scenes") or []
    shots: list[Shot] = []
    shot_id = 0
    for s in scenes:
        scene_id = int(s.get("scene_id") or len(shots) + 1)
        target = float(s.get("target_seconds") or 0.0)
        bbox = s.get("panel_bbox") or {}
        source_image = str(s.get("source_image") or "")
        if not source_image or target <= 0.0:
            continue

        if target < 2.0:
            n_shots = 1
        elif target < 4.0:
            n_shots = 2
        else:
            n_shots = 3

        per = round(target / n_shots, 3)
        for i in range(n_shots):
            dur = per if i < n_shots - 1 else round(target - per * (n_shots - 1), 3)
            shots.append(Shot(
                shot_id=shot_id,
                scene_id=scene_id,
                duration_seconds=max(0.4, dur),
                panel_bbox={"x": int(bbox.get("x", 0)), "y": int(bbox.get("y", 0)),
                            "w": int(bbox.get("w", 0)), "h": int(bbox.get("h", 0))},
                source_image=source_image,
                motion=MOTION_CYCLE[i % len(MOTION_CYCLE)],
            ))
            shot_id += 1
    return shots


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
        "-loop", "1",
        "-t", f"{duration:.3f}",
        "-i", str(framed),
        "-filter_complex", filter_complex,
        "-map", "[v]",
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
    """Pre-render the cropped panel as a 1080x1920 frame with cover-scale or blur-fill background."""
    with Image.open(panel_png) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        aspect = iw / max(1, ih)
        if aspect < ASPECT_THRESHOLD:
            scale = max(OUTPUT_W / iw, OUTPUT_H / ih)
            new_w = max(OUTPUT_W, int(round(iw * scale)))
            new_h = max(OUTPUT_H, int(round(ih * scale)))
            scaled = im.resize((new_w, new_h), Image.LANCZOS)
            x0 = (new_w - OUTPUT_W) // 2
            y0 = (new_h - OUTPUT_H) // 2
            frame = scaled.crop((x0, y0, x0 + OUTPUT_W, y0 + OUTPUT_H))
        else:
            bg_scale = max(OUTPUT_W / iw, OUTPUT_H / ih) * 1.2
            bg_w = int(round(iw * bg_scale))
            bg_h = int(round(ih * bg_scale))
            bg = im.resize((bg_w, bg_h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(radius=20))
            x0 = (bg_w - OUTPUT_W) // 2
            y0 = (bg_h - OUTPUT_H) // 2
            frame = bg.crop((x0, y0, x0 + OUTPUT_W, y0 + OUTPUT_H))
            fg_h = OUTPUT_H
            fg_w = max(1, int(round(iw * fg_h / max(1, ih))))
            if fg_w > OUTPUT_W:
                fg_w = OUTPUT_W
                fg_h = max(1, int(round(ih * fg_w / max(1, iw))))
            fg = im.resize((fg_w, fg_h), Image.LANCZOS)
            paste_x = (OUTPUT_W - fg_w) // 2
            paste_y = (OUTPUT_H - fg_h) // 2
            frame.paste(fg, (paste_x, paste_y))
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
