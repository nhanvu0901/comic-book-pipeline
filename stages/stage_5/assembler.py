"""
ffmpeg invocations for Stage 5.

Two-pass approach:
  1. For each VisualPlan, render a silent clip_NNN.mp4 — static image with
     a 9:16 crop + gentle zoom (Ken Burns) over the scene duration.
  2. Concat all clips, overlay pre-rendered caption PNGs, mix in TTS audio,
     output final.mp4 (1080×1920 H.264 30fps).

Uses subprocess.run — no ffmpeg-python dep.
"""
import shutil
import subprocess
from pathlib import Path

from .captions import RenderedCaption
from .scene_planner import OUTPUT_H, OUTPUT_W, VisualPlan


FPS = 30


def require_ffmpeg() -> str:
    """Return absolute ffmpeg path or raise FileNotFoundError."""
    p = shutil.which("ffmpeg")
    if not p:
        raise FileNotFoundError(
            "ffmpeg not found on PATH. Install via `brew install ffmpeg`."
        )
    return p


def render_scene_clip(plan: VisualPlan, out_path: Path, *, fps: int = FPS) -> Path:
    """
    Render a single silent clip for one scene.

    Filter chain: crop (9:16 around panel) → scale to 1080×1920 → zoompan
    for a gentle zoom-in over the scene's duration.
    """
    ff = require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration_frames = max(1, int(round(plan.duration * fps)))

    # zoompan sampled region shrinks from 1/ZOOM_START to 1/ZOOM_END of the
    # scaled 1080×1920 input, centered — producing a slow zoom-in.
    zoom_step = (plan.zoom_end - plan.zoom_start) / max(1, duration_frames - 1)
    vf = (
        f"crop={plan.crop_w}:{plan.crop_h}:{plan.crop_x}:{plan.crop_y},"
        f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,"
        f"zoompan=z='{plan.zoom_start}+{zoom_step}*on':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={duration_frames}:s={OUTPUT_W}x{OUTPUT_H}:fps={fps}"
    )

    cmd = [
        ff,
        "-y",
        "-loop", "1",
        "-t", f"{plan.duration:.3f}",
        "-i", plan.image_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-an",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def concat_clips(clip_paths: list[Path], out_path: Path) -> Path:
    """
    Concatenate silent clips losslessly using the concat demuxer.
    """
    ff = require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    list_file = out_path.parent / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{Path(p).resolve()}'" for p in clip_paths) + "\n"
    )

    cmd = [
        ff, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def finalize(
    silent_video: Path,
    audio_wav: Path,
    captions: list[RenderedCaption],
    out_path: Path,
    *,
    caption_bottom_offset: int = 260,
    fps: int = FPS,
) -> Path:
    """
    Overlay captions onto the silent video and mix in the TTS audio.

    Captions are positioned centered horizontally at `caption_bottom_offset`
    pixels above the bottom of the frame (standard MrBeast-style lower-third).
    """
    ff = require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    inputs: list[str] = ["-i", str(silent_video), "-i", str(audio_wav)]
    for c in captions:
        inputs += ["-i", c.image_path]

    # Build the overlay chain
    filter_parts: list[str] = []
    prev_label = "[0:v]"
    for i, cap in enumerate(captions):
        in_label = f"[{2 + i}:v]"
        out_label = f"[vc{i}]" if i < len(captions) - 1 else "[vout]"
        y_expr = f"H-h-{caption_bottom_offset}"
        enable = f"enable='between(t,{cap.start:.3f},{cap.end:.3f})'"
        filter_parts.append(
            f"{prev_label}{in_label}overlay=x=(W-w)/2:y={y_expr}:{enable}{out_label}"
        )
        prev_label = out_label

    if not captions:
        # no captions — just map video through
        filter_parts.append("[0:v]copy[vout]")

    filter_complex = ";".join(filter_parts)

    cmd = [
        ff, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = res.stderr[-2000:] if res.stderr else "(no stderr)"
        raise RuntimeError(
            f"ffmpeg failed (exit {res.returncode})\ncmd: {' '.join(cmd)}\nstderr tail:\n{tail}"
        )
