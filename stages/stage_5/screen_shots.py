"""Independent Shot Builder and 4-Tier Fallback Renderer for screen_qa Mode.

Does NOT modify or rely on stages/stage_5/shots.py:render_shot.
Fallback chain: Primary Clip -> Backup Clip -> Still Image -> Text Card (Never Crashes).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import config
from stages.stage_5.schema import Shot
from stages.stage_5.clips import render_clip_shot
from utils.text_card import generate_text_card_video

logger = logging.getLogger(__name__)
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def build_screen_shots(project_name: str, *, log: Callable[[str], None] = logger.info) -> list[Shot]:
    """Build shot list for a screen_qa project based on narration and clips manifest."""
    proj_dir = Path(config.PROJECTS_ROOT) / project_name
    nar_file = proj_dir / "narration.json"
    if not nar_file.exists():
        raise FileNotFoundError(f"Missing {nar_file} — run screen narration first")

    nar_data = json.loads(nar_file.read_text("utf-8"))
    scenes = nar_data.get("scenes") or []

    # Read clips manifest if exists
    clips_manifest_file = proj_dir / "review" / "clips" / "clips.json"
    clips_by_beat: dict[str, dict] = {}
    if clips_manifest_file.exists():
        try:
            m_data = json.loads(clips_manifest_file.read_text("utf-8"))
            for c in m_data.get("clips") or []:
                if c.get("beat"):
                    clips_by_beat[str(c["beat"])] = c
        except Exception as exc:
            log(f"[screen-shots] error reading clips.json: {exc}")

    # Read audio duration / beat timing if available
    timing_file = proj_dir / "review" / "tts_status.json"
    beat_durations: dict[str, float] = {}
    if timing_file.exists():
        try:
            t_data = json.loads(timing_file.read_text("utf-8"))
            beat_durations = t_data.get("beat_durations") or {}
        except Exception:
            pass

    shots: list[Shot] = []
    shot_id = 0
    wps = float(nar_data.get("words_per_second") or 3.4)

    for s_idx, scene in enumerate(scenes):
        sid = int(scene.get("scene_id") or (s_idx + 1))
        vbeats = scene.get("visual_beats") or [scene.get("text", "")]
        for f_idx, vb_text in enumerate(vbeats):
            bk = f"{sid}:{f_idx}" if len(vbeats) > 1 else str(sid)
            words_count = max(1, len(vb_text.split()))
            dur = beat_durations.get(bk, words_count / wps)
            dur = max(0.5, dur)

            clip_entry = clips_by_beat.get(bk) or {}
            clip_file = clip_entry.get("file", "")
            clip_abs = str((proj_dir / clip_file).resolve()) if clip_file and not Path(clip_file).is_absolute() else clip_file
            clip_start = float(clip_entry.get("start") or 0.0)

            shot = Shot(
                shot_id=shot_id,
                scene_id=sid,
                duration_seconds=dur,
                panel_bbox={"x": 0, "y": 0, "w": 1080, "h": 1920},
                source_image="",
                motion="zoom_in",
                caption_text=vb_text,
                clip_path=clip_abs,
                clip_in=clip_start,
                clip_out=clip_start + dur,
            )
            shots.append(shot)
            shot_id += 1

    log(f"[screen-shots] built {len(shots)} screen shots for '{project_name}'")
    return shots


def _render_still_shot(img_path: Path, duration: float, out_path: Path) -> Path:
    """Render a still image with subtle Ken Burns or letterbox into 1080x1920 30fps."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
        "fps=30"
    )
    cmd = [
        FFMPEG, "-y",
        "-loop", "1",
        "-i", str(img_path),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def render_screen_shot(
    shot: Shot,
    out_path: Path,
    *,
    backup_clip_path: Path | None = None,
    log: Callable[[str], None] = logger.info,
) -> Path:
    """Render a screen shot through the 4-tier fallback chain without crashing:
    1. Primary Clip
    2. Backup Clip
    3. Still / Custom Image
    4. Text Card
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Primary Clip
    if shot.clip_path and Path(shot.clip_path).is_file():
        try:
            render_clip_shot(shot, out_path)
            setattr(shot, "screen_fallback_level", "clip")
            return out_path
        except Exception as exc:
            log(f"[screen-shots] primary clip failed: {exc} — trying backup")

    # 2. Backup Clip
    if backup_clip_path and Path(backup_clip_path).is_file():
        try:
            orig_path = shot.clip_path
            shot.clip_path = str(backup_clip_path)
            render_clip_shot(shot, out_path)
            setattr(shot, "screen_fallback_level", "backup_clip")
            return out_path
        except Exception as exc:
            shot.clip_path = orig_path
            log(f"[screen-shots] backup clip failed: {exc} — trying still image")

    # 3. Still / Custom Image
    still_cand = getattr(shot, "custom_image", "") or getattr(shot, "source_image", "")
    if still_cand and Path(still_cand).is_file():
        try:
            _render_still_shot(Path(still_cand), shot.duration_seconds, out_path)
            setattr(shot, "screen_fallback_level", "still_image")
            return out_path
        except Exception as exc:
            log(f"[screen-shots] still image render failed: {exc} — falling back to text card")

    # 4. Text Card (Guaranteed never to crash)
    try:
        title = "SCREEN CANON"
        subtitle = shot.caption_text or "Story Lore"
        generate_text_card_video(title, subtitle, shot.duration_seconds, out_path)
        setattr(shot, "screen_fallback_level", "text_card")
        return out_path
    except Exception as exc:
        log(f"[screen-shots] text card render error: {exc} — emergency lavfi black frame")
        # Extreme emergency fallback: pure black frame
        cmd = [
            FFMPEG, "-y", "-f", "lavfi",
            "-i", f"color=c=black:s=1080x1920:r=30:d={shot.duration_seconds:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            str(out_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        setattr(shot, "screen_fallback_level", "emergency_black")
        return out_path


def assemble_screen_video(
    shot_list: list[Shot],
    shot_paths: list[Path],
    out_video_path: Path,
    *,
    log: Callable[[str], None] = logger.info,
) -> Path:
    """Assemble screen shots into a single silent video using Hard Cuts."""
    out_video_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_video_path.parent / "_screen_concat.txt"

    lines = [f"file '{p.resolve()}'" for p in shot_paths]
    list_file.write_text("\n".join(lines), "utf-8")

    cmd = [
        FFMPEG, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c:v", "copy",
        "-an",
        str(out_video_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        log(f"[screen-shots] assembled {len(shot_paths)} shot(s) to {out_video_path}")
    finally:
        if list_file.exists():
            list_file.unlink()

    return out_video_path
