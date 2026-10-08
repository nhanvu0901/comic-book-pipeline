"""stages/stage_5/screen_shots.py
Shot builder, 4-level fallback renderer, and pipeline runner for Screen Q&A (mode='screen_qa').
Operates independently of comic panels (no pages_by_number / _panel_pool).
Never-crash fallback chain:
  Level 1: Primary Clip (from manifest)
  Level 2: Backup Candidate Clip
  Level 3: HD Still / Custom Image (Ken Burns)
  Level 4: Text Card (utils/text_card.py) -> NEVER CRASHES
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageFilter

import config
from utils.text_card import render_text_card
from . import shots as _sh
from .clips import (
    parse_manifest,
    render_clip_shot,
    verify_shot_contract,
    shot_log_entry,
)
from .pipeline import (
    _assemble_video,
    _concat,
    _final_encode,
    _probe_duration,
    _score_final_video,
    _write_title_file,
    mix_audio,
)
from .schema import AssemblyResult, Shot
from stages.stage_4.beat_timing import BeatWindow, calculate_beat_durations


def _find_font() -> Path:
    """Locate Anton font or system fallback."""
    root_font = Path(__file__).resolve().parent.parent.parent / "fonts" / "Anton-Regular.ttf"
    if root_font.is_file():
        return root_font
    return Path("fonts/Anton-Regular.ttf")


def build_shots_for_screen_qa(
    narration: dict[str, Any],
    *,
    scene_timings: list[dict[str, Any]] | None = None,
    word_timestamps: list[dict[str, Any]] | None = None,
    caption_chunks: list[dict[str, Any]] | None = None,
    pages_by_number: dict | None = None,  # Ignored: no comic panels in screen_qa
    cluster_to_name: dict | None = None,  # Ignored
    project: str | None = None,
    clips_manifest: dict | list | None = None,
    custom_images: dict | None = None,
    screen_context: dict | None = None,
) -> list[Shot]:
    """Build a list of Shot objects for screen_qa without relying on comic panels.
    Each visual beat (or scene) produces one shot covering its beat window.
    """
    scenes = narration.get("scenes") or []
    if not scenes:
        return []

    project_root = config.PROJECTS_ROOT / project if project else None

    # Load screen_context if available and not passed
    if screen_context is None and project_root and (project_root / "screen_context.json").exists():
        try:
            screen_context = json.loads((project_root / "screen_context.json").read_text())
        except Exception:
            screen_context = {}

    # Build sentence_timings for calculate_beat_durations
    sentence_timings: dict[int, dict[str, float]] = {}
    if scene_timings:
        for st in scene_timings:
            sid = int(st.get("scene_id", 1))
            st_start = float(st.get("start", 0.0))
            st_end = float(st.get("end", st_start + 4.0))
            sentence_timings[sid] = {
                "start": st_start,
                "end": st_end,
                "duration": round(st_end - st_start, 4),
            }
    elif project_root and (project_root / "cache" / "tts" / "status.json").exists():
        try:
            status = json.loads((project_root / "cache" / "tts" / "status.json").read_text())
            scene_durs = status.get("scene_durations") or {}
            running_t = 0.0
            for sc in scenes:
                sid = int(sc.get("scene_id", 1))
                dur = float(scene_durs.get(str(sid), sc.get("target_seconds", 4.0)))
                sentence_timings[sid] = {
                    "start": running_t,
                    "end": running_t + dur,
                    "duration": dur,
                }
                running_t += dur
        except Exception:
            sentence_timings = {}

    if not sentence_timings:
        # Fallback to target_seconds or word count estimation
        running_t = 0.0
        for sc in scenes:
            sid = int(sc.get("scene_id", 1))
            dur = float(sc.get("target_seconds") or max(2.0, len(str(sc.get("text", "")).split()) * 0.35))
            sentence_timings[sid] = {
                "start": running_t,
                "end": running_t + dur,
                "duration": dur,
            }
            running_t += dur

    # Calculate exact beat timing windows
    beat_windows = calculate_beat_durations(scenes, sentence_timings, min_duration=0.4)

    # Load clips manifest
    clip_entries = []
    if clips_manifest is not None:
        if isinstance(clips_manifest, dict):
            clip_entries, _ = parse_manifest(clips_manifest, project_root or Path("."))
        elif isinstance(clips_manifest, list):
            clip_entries, _ = parse_manifest({"clips": clips_manifest}, project_root or Path("."))
    elif project_root and (project_root / "review" / "clips" / "clips.json").exists():
        try:
            raw_m = json.loads((project_root / "review" / "clips" / "clips.json").read_text())
            clip_entries, _ = parse_manifest(raw_m, project_root)
        except Exception:
            clip_entries = []

    # Map clip entries by beat
    clips_by_beat: dict[str, Any] = {}
    for ce in clip_entries:
        if ce.beat:
            clips_by_beat[str(ce.beat)] = ce

    # Load custom images if available
    if custom_images is None and project_root and (project_root / "review" / "custom" / "custom_images.json").exists():
        try:
            custom_images = json.loads((project_root / "review" / "custom" / "custom_images.json").read_text())
        except Exception:
            custom_images = {}
    custom_images = custom_images or {}

    # Motion sequence to give varied motion to non-clip / still shots
    motions = ["push_in", "pan_left", "push_top", "pan_right", "push_bottom"]

    shots: list[Shot] = []
    for s_idx, bw in enumerate(beat_windows, start=1):
        motion = motions[(s_idx - 1) % len(motions)]
        is_intro = (s_idx == 1 and bool(scenes[0].get("is_intro", False)))

        shot = Shot(
            shot_id=s_idx,
            scene_id=bw.scene_id,
            duration_seconds=bw.duration,
            panel_bbox={"x": 0, "y": 0, "w": _sh.OUTPUT_W, "h": _sh.OUTPUT_H},
            source_image="",
            motion=motion,
            caption_text=bw.text,
            is_intro=is_intro,
            beat_id=bw.scene_id,
        )

        # Match custom still
        if bw.beat_id in custom_images:
            c_val = custom_images[bw.beat_id]
            shot.custom_image = str(c_val if isinstance(c_val, str) else c_val.get("file", ""))

        # Match clip
        ce = clips_by_beat.get(bw.beat_id)
        if not ce and ":" in bw.beat_id:
            # Fallback to scene-level clip
            ce = clips_by_beat.get(bw.beat_id.split(":")[0])

        if ce:
            shot.clip_path = ce.file
            shot.clip_in = ce.start
            shot.clip_out = ce.end
            shot.clip_crop = ce.crop
            shot.clip_id = ce.id
            shot.clip_source_url = ce.source_url

            # Check for backup candidate on clip entry
            raw_backup = getattr(ce, "backup_candidate", None) or getattr(ce, "backup_file", None)
            if isinstance(raw_backup, dict):
                shot.backup_clip_path = str(raw_backup.get("file", ""))
                shot.backup_clip_in = float(raw_backup.get("start", 0.0))
                shot.backup_clip_out = float(raw_backup.get("end", 0.0))
                shot.backup_clip_crop = raw_backup.get("crop", {})
                shot.backup_clip_id = str(raw_backup.get("id", f"{ce.id}_backup"))
            elif isinstance(raw_backup, str) and raw_backup:
                shot.backup_clip_path = raw_backup
                shot.backup_clip_in = 0.0
                shot.backup_clip_out = 0.0
                shot.backup_clip_id = f"{ce.id}_backup"

        shots.append(shot)

    return shots


def _prepare_still_frame(image_path: Path, out_path: Path) -> Path:
    """Contain+blur a still image to 1080x1920."""
    W, H = _sh.OUTPUT_W, _sh.OUTPUT_H
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        bg = im.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(radius=25))
        scale = min(W / iw, H / ih)
        fw, fh = max(2, int(round(iw * scale))), max(2, int(round(ih * scale)))
        fg = im.resize((fw, fh), Image.LANCZOS)
        bg.paste(fg, ((W - fw) // 2, (H - fh) // 2))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        bg.save(out_path)
    return out_path


def _render_still_ken_burns(
    image_path: Path,
    out_path: Path,
    duration: float,
    motion: str = "push_in",
    *,
    corner_logo: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Render a still image with Ken Burns animation matching shot contract."""
    ff = _sh._require_ffmpeg()
    work_dir = out_path.parent / "_still_frames"
    work_dir.mkdir(parents=True, exist_ok=True)
    framed = work_dir / f"framed_{out_path.stem}.png"
    _prepare_still_frame(image_path, framed)

    dur = max(0.4, duration)
    frames = max(1, int(round(dur * _sh.FPS)))
    factor = _sh.PRE_UPSCALE_FACTOR_FULL
    pre = f"scale={_sh.OUTPUT_W * factor}:{_sh.OUTPUT_H * factor}:flags=bicubic,"
    zp = _sh._zoompan_expr(motion, frames, action=False)

    inputs = ["-framerate", "1", "-loop", "1", "-t", "1", "-i", str(framed)]
    segs = [f"[0:v]{pre}{zp}[vz]"]
    prev = "vz"
    if corner_logo is not None and corner_logo.is_file():
        inputs += ["-i", str(corner_logo)]
        segs.append(f"[{prev}][1:v]overlay=W-w-36:36[vl]")
        prev = "vl"
    filter_complex = ";".join(segs)

    cmd = [
        ff, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{prev}]",
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", str(_sh.FPS),
        "-an",
        str(out_path),
    ]
    if progress:
        progress(f"[screen_qa] still ken_burns {out_path.name} ({dur:.2f}s, {motion})")
    _sh._run(cmd)
    verify_shot_contract(out_path, frames)
    return out_path


def _render_card_shot(
    shot: Shot,
    out_path: Path,
    *,
    work_dir: Path | None = None,
    corner_logo: Path | None = None,
    screen_context: dict | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Level 4: Render graphic text card via utils/text_card.py -> NEVER CRASHES."""
    ff = _sh._require_ffmpeg()
    work_dir = work_dir or out_path.parent / "_cards"
    work_dir.mkdir(parents=True, exist_ok=True)
    card_png = work_dir / f"card_{shot.shot_id:03d}.png"

    # Context title / question
    header = "Q&A BREAKDOWN"
    if screen_context and screen_context.get("question"):
        header = str(screen_context["question"])
    elif getattr(shot, "caption_text", ""):
        # short title
        words = shot.caption_text.split()
        if len(words) > 6:
            header = " ".join(words[:6]) + "..."
        else:
            header = shot.caption_text

    text_body = shot.caption_text or f"Scene {shot.scene_id} Beat {getattr(shot, 'beat_id', 1)}"
    font_path = _find_font()
    lines = [
        (header, "#58a6ff", 40, 0.35),
        (text_body, "#ffffff", 48, 0.50),
        ("COMIC BOOK PIPELINE", "#8b949e", 32, 0.65),
    ]

    render_text_card(
        card_png,
        width=_sh.OUTPUT_W,
        height=_sh.OUTPUT_H,
        background="#0e1117",
        lines=lines,
        font_path=font_path,
        logo_path=corner_logo,
        logo_width=240,
        logo_center_y=350,
    )

    dur = max(0.4, float(shot.duration_seconds))
    frames = max(1, int(round(dur * _sh.FPS)))

    cmd = [
        ff, "-y",
        "-loop", "1",
        "-framerate", str(_sh.FPS),
        "-t", f"{dur:.3f}",
        "-i", str(card_png),
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", str(_sh.FPS),
        "-an",
        str(out_path),
    ]
    if progress:
        progress(f"[screen_qa] text card fallback {out_path.name} ({dur:.2f}s)")
    _sh._run(cmd)
    verify_shot_contract(out_path, frames)
    return out_path


def render_screen_shot(
    shot: Shot,
    out_path: Path,
    *,
    work_dir: Path | None = None,
    corner_logo: Path | None = None,
    screen_context: dict | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Render one screen_qa shot through the 4-level fallback chain:
    Level 1: Primary clip ->
    Level 2: Backup candidate clip ->
    Level 3: HD Still / Custom image (Ken Burns) ->
    Level 4: Text Card (utils/text_card.py) -> NEVER CRASHES.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shot.screen_render_level = 0

    # ── Level 1: Primary Clip ──────────────────────────────────────────────
    if getattr(shot, "clip_path", "") and Path(shot.clip_path).is_file():
        try:
            res = render_clip_shot(shot, out_path, corner_logo=corner_logo, progress=progress)
            shot.screen_render_level = 1
            shot.clip_fallback = ""
            return res
        except Exception as exc:
            shot.clip_fallback = f"Level 1 failed ({type(exc).__name__}: {exc})"
            if progress:
                progress(f"[screen_qa] ⚠ shot {shot.shot_id:03d}: primary clip failed ({exc}) — trying backup candidate")
    elif getattr(shot, "clip_path", ""):
        shot.clip_fallback = f"Level 1 file not found: {shot.clip_path}"

    # ── Level 2: Backup Candidate Clip ─────────────────────────────────────
    backup_path = getattr(shot, "backup_clip_path", "")
    if backup_path and Path(backup_path).is_file():
        backup_shot = copy.copy(shot)
        backup_shot.clip_path = backup_path
        backup_shot.clip_in = getattr(shot, "backup_clip_in", 0.0)
        backup_shot.clip_out = getattr(shot, "backup_clip_out", 0.0)
        backup_shot.clip_crop = getattr(shot, "backup_clip_crop", {})
        backup_shot.clip_id = getattr(shot, "backup_clip_id", f"{shot.clip_id}_backup")
        try:
            res = render_clip_shot(backup_shot, out_path, corner_logo=corner_logo, progress=progress)
            shot.screen_render_level = 2
            shot.clip_fallback = "fallback_to_backup_candidate"
            return res
        except Exception as exc:
            prev_fb = shot.clip_fallback or ""
            shot.clip_fallback = f"{prev_fb} | Level 2 failed ({type(exc).__name__}: {exc})"
            if progress:
                progress(f"[screen_qa] ⚠ shot {shot.shot_id:03d}: backup clip failed ({exc}) — trying HD still")
    elif backup_path:
        shot.clip_fallback = f"{shot.clip_fallback or ''} | Level 2 file not found: {backup_path}"

    # ── Level 3: HD Still / Custom Image (Ken Burns) ────────────────────────
    still_path = getattr(shot, "custom_image", "") or getattr(shot, "still_image", "")
    if still_path and Path(still_path).is_file():
        try:
            res = _render_still_ken_burns(
                Path(still_path),
                out_path,
                shot.duration_seconds,
                motion=shot.motion or "push_in",
                corner_logo=corner_logo,
                progress=progress,
            )
            shot.screen_render_level = 3
            shot.clip_fallback = "fallback_to_hd_still"
            return res
        except Exception as exc:
            prev_fb = shot.clip_fallback or ""
            shot.clip_fallback = f"{prev_fb} | Level 3 failed ({type(exc).__name__}: {exc})"
            if progress:
                progress(f"[screen_qa] ⚠ shot {shot.shot_id:03d}: HD still failed ({exc}) — falling back to text card")
    elif still_path:
        shot.clip_fallback = f"{shot.clip_fallback or ''} | Level 3 still not found: {still_path}"

    # ── Level 4: Text Card (utils/text_card.py) -> NEVER CRASHES ────────────
    try:
        res = _render_card_shot(
            shot,
            out_path,
            work_dir=work_dir,
            corner_logo=corner_logo,
            screen_context=screen_context,
            progress=progress,
        )
        shot.screen_render_level = 4
        shot.clip_fallback = "fallback_to_text_card"
        return res
    except Exception as exc:
        # Ultimate Pillow failsafe: minimal plain solid color MP4 with 1 text
        if progress:
            progress(f"[screen_qa] ⚠ Level 4 card exception ({exc}) — rendering emergency blank card")
        emergency_png = (work_dir or out_path.parent) / f"emerg_{shot.shot_id:03d}.png"
        img = Image.new("RGB", (_sh.OUTPUT_W, _sh.OUTPUT_H), (15, 17, 21))
        emergency_png.parent.mkdir(parents=True, exist_ok=True)
        img.save(emergency_png)
        dur = max(0.4, float(shot.duration_seconds))
        frames = max(1, int(round(dur * _sh.FPS)))
        ff = _sh._require_ffmpeg()
        cmd = [
            ff, "-y", "-loop", "1", "-framerate", str(_sh.FPS), "-t", f"{dur:.3f}",
            "-i", str(emergency_png), "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(_sh.FPS), "-an",
            str(out_path),
        ]
        _sh._run(cmd)
        verify_shot_contract(out_path, frames)
        shot.screen_render_level = 4
        shot.clip_fallback = "fallback_to_emergency_card"
        return out_path


def _write_screen_shots_log(shots: list[Shot], shots_dir: Path, out_path: Path, log: Callable[[str], None]) -> None:
    """Write shots.json for screen_qa shots reporting render level and clip/still details."""
    entries = []
    for s in shots:
        entry: dict[str, Any] = {
            "shot_id": s.shot_id,
            "scene_id": s.scene_id,
            "duration": round(s.duration_seconds, 3),
            "caption_text": s.caption_text,
            "render_level": getattr(s, "screen_render_level", 0),
            "fallback_reason": getattr(s, "clip_fallback", None),
        }
        c_entry = shot_log_entry(s)
        if c_entry:
            entry["clip"] = c_entry
        if getattr(s, "custom_image", ""):
            entry["custom_image"] = s.custom_image
        entries.append(entry)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"shots": entries}, indent=2, ensure_ascii=False))
    log(f"[screen_qa] wrote {len(entries)} shot entries to {out_path.name}")


def run_screen_qa_pipeline(
    project_name: str,
    project_root: Path,
    narration: dict[str, Any],
    *,
    force: bool = False,
    panels_only: bool = False,
    log: Callable[[str], None] = print,
    audio_path: Path | None = None,
    audio_duration: float = 0.0,
    scene_timings: list[dict[str, Any]] | None = None,
    word_timestamps: list[dict[str, Any]] | None = None,
) -> AssemblyResult:
    """Complete Stage 5 assembly pipeline for mode='screen_qa'.
    Produces projects/<p>/final.mp4 adhering to the standard contract.
    """
    root = Path(project_root)
    shots_dir = root / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    silent_video_path = root / "video_silent.mp4"
    audio_mixed_path = root / "audio_mixed.wav"
    final_path = root / "final.mp4"
    audio_path = audio_path or root / "audio.wav"

    if final_path.exists() and not force and not panels_only:
        log(f"[screen_qa] final.mp4 already exists ({final_path}); pass force=True to rebuild")
        duration = _probe_duration(final_path)
        return AssemblyResult(
            final_path=str(final_path),
            duration_seconds=round(duration, 3),
            shot_count=len(list(shots_dir.glob("shot_*.mp4"))),
            scene_count=len(narration.get("scenes") or []),
            caption_path="",
            silent_video_path=str(silent_video_path),
            audio_mixed_path=str(audio_mixed_path),
            shots_dir=str(shots_dir),
        )

    # Build shots
    shots = build_shots_for_screen_qa(
        narration=narration,
        scene_timings=scene_timings,
        word_timestamps=word_timestamps,
        project=project_name,
    )
    if not shots:
        raise RuntimeError("build_shots_for_screen_qa produced 0 shots")

    # Audio padding guard: ensure video length covers audio duration
    if audio_duration > 0:
        total_shot_dur = sum(s.duration_seconds for s in shots)
        if total_shot_dur < audio_duration:
            pad = (audio_duration - total_shot_dur) + 0.20
            shots[-1].duration_seconds += pad
            log(f"[screen_qa] extended last shot +{pad:.2f}s so video >= audio ({audio_duration:.2f}s)")

    if panels_only:
        _write_screen_shots_log(shots, shots_dir, root / "shots.json", log)
        return AssemblyResult(
            final_path="",
            duration_seconds=0.0,
            shot_count=len(shots),
            scene_count=len(narration.get("scenes") or []),
            caption_path="",
            silent_video_path=str(silent_video_path),
            audio_mixed_path=str(audio_mixed_path),
            shots_dir=str(shots_dir),
            shots=shots,
        )

    # Corner logo
    from config import ENABLE_CORNER_LOGO, CHANNEL_LOGO_PATH
    corner_logo = None
    if ENABLE_CORNER_LOGO:
        corner_logo = _sh._prepare_corner_logo(
            CHANNEL_LOGO_PATH, shots_dir / "_corner_logo.png",
            width=int(_sh.OUTPUT_W * 0.10), alpha=0.55
        )

    # Screen context
    screen_context = None
    ctx_path = root / "screen_context.json"
    if ctx_path.exists():
        try:
            screen_context = json.loads(ctx_path.read_text())
        except Exception:
            pass

    # Render each shot
    shot_paths: list[Path] = []
    for s in shots:
        sp = shots_dir / f"shot_{s.shot_id:03d}.mp4"
        if sp.exists() and not force:
            log(f"[screen_qa] reusing {sp.name}")
        else:
            render_screen_shot(
                s, sp,
                work_dir=shots_dir / "_work",
                corner_logo=corner_logo,
                screen_context=screen_context,
                progress=log,
            )
        shot_paths.append(sp)

    # Write debug shots.json
    _write_screen_shots_log(shots, shots_dir, root / "shots.json", log)

    # Assemble silent video: hard cuts stream copy around clip shots
    if silent_video_path.exists() and not force:
        log(f"[screen_qa] reusing {silent_video_path.name}")
    else:
        _assemble_video(shots, shot_paths, silent_video_path, project=project_name)

    # Audio mix
    if audio_mixed_path.exists() and not force and audio_path.exists() and audio_mixed_path.stat().st_mtime >= audio_path.stat().st_mtime:
        log(f"[screen_qa] reusing {audio_mixed_path.name}")
    elif audio_path.exists():
        mix_audio(audio_path, audio_mixed_path, bg_music_path=None, progress=log)
    else:
        # Emergency dummy audio if no audio.wav found
        ff = _sh._require_ffmpeg()
        total_dur = _probe_duration(silent_video_path)
        cmd = [
            ff, "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
            "-t", f"{total_dur:.3f}",
            str(audio_mixed_path),
        ]
        _sh._run(cmd)

    # Final encode to projects/<p>/final.mp4
    log(f"[screen_qa] final encode -> {final_path.name}")
    _final_encode(silent_video_path, audio_mixed_path, final_path)

    duration = _probe_duration(final_path)
    log(f"[screen_qa] done: {final_path} ({duration:.2f}s)")
    _write_title_file(root, narration)

    return AssemblyResult(
        final_path=str(final_path),
        duration_seconds=round(duration, 3),
        shot_count=len(shots),
        scene_count=len(narration.get("scenes") or []),
        caption_path="",
        silent_video_path=str(silent_video_path),
        audio_mixed_path=str(audio_mixed_path),
        shots_dir=str(shots_dir),
        shots=shots,
    )
