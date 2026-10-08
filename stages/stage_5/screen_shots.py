"""stages/stage_5/screen_shots.py
Shot builder, never-crash renderer and Stage-5 runner for mode "screen_qa" (a Q&A Short whose
pictures are video clips / stills / cards — no comic page anywhere).

Independent of comic panels: nothing here reads pages_by_number, _panel_pool or a source_image.
It is also independent of the comic *builder*: shots.py is not edited — this module composes the
unchanged pieces it needs (clips.py's manifest + fit + contract, shots.render_shot for a still,
pipeline's assembly / audio / encode) around its own beat plan (screen_beats.py).

BEAT KEYS — the review scheme ("intro" | "outro" | "<sid>" | "<sid>:<frag>", 0-based), so a clip
picked in /moments_review (review/clips/clips.json) and a still locked in the review screen
(review/locks.json → review/custom/) land on the right shot through the SAME resolvers the comic
path uses (clips.resolve_clip_assignments / shots._resolve_custom_images).

NEVER-CRASH CHAIN (render_screen_shot) — each level runs only when the one above it failed:
  1. the beat's clip                       (clips.render_clip_shot: trim → extend → speed → hold)
  2. its BACKUP clip  (clips.json entry "backup": {...})
  3. a still / custom image with Ken Burns (shots.render_shot on the beat's custom_image)
  4. a text card                           (utils.screen_card, repo font)  → then a plain-colour
     frame as the last resort, so a missing file, a bad codec, a dead network or a corrupt image
     can never stop a render. Only a missing ffmpeg can — that is the environment, not content.

CLIP SHOT CONTRACT — every shot (whatever level made it) is h264 yuv420p 1080x1920 30fps, one
video stream, exactly round(duration*30) frames, no audio, SAR unsignalled (verify_shot_contract
+ setsar=0), so pipeline._concat's stream copy can splice clip, still and card shots freely and
hard-cuts around clips stay clean.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import config
from . import shots as _sh
from .clips import (
    apply_clips_to_shots,
    parse_manifest,
    render_clip_shot,
    resolve_clip_assignments,
    shot_log_entry,
    verify_shot_contract,
)
from .pipeline import (
    _assemble_video,
    _build_outro_card,
    _final_encode,
    _pad_audio_tail,
    _probe_duration,
    _score_final_video,
    _write_title_file,
    mix_audio,
)
from .schema import AssemblyResult, Shot
from .screen_beats import ScreenWindow, plan_windows

# Motions the zoompan builder actually implements (a name outside this set renders STATIC — a
# frozen frame). Reuse the comic cycle: every entry moves.
SCREEN_MOTIONS = tuple(_sh.MOTION_CYCLE)

# Bump when a change here alters what a given shot sidecar signature would render to.
_RENDER_VERSION = 2


@dataclass
class ScreenShot(Shot):
    """A Shot plus what screen_qa needs: the beats it covers, a backup clip, and the level the
    never-crash chain ended on. A subclass (not new Shot fields) so the comic dataclass — and
    every shots.json it feeds — is untouched; copy.copy / dataclasses.replace keep the extras."""
    beat_keys: list = field(default_factory=list)
    query: str = ""
    backup_clip_path: str = ""
    backup_clip_in: float = 0.0
    backup_clip_out: float = 0.0
    backup_clip_crop: dict = field(default_factory=dict)
    backup_clip_id: str = ""
    backup_source_url: str = ""
    # Absolute second in the source video where a section file's t=0 sits (clips.json
    # "source_start", written when /moments_review downloads a section). -1 = unknown. Lets a
    # section that went missing be fetched again from the RIGHT moment instead of from 0:00.
    clip_source_start: float = -1.0
    backup_source_start: float = -1.0
    render_level: int = 0            # 1 clip · 2 backup · 3 still · 4 card · 0 not rendered yet
    level_notes: list = field(default_factory=list)   # why each skipped level failed


# ─── builder ────────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _project_root_of(project: str | None, project_root: Path | None) -> Path | None:
    if project_root is not None:
        return Path(project_root)
    if project:
        p = Path(str(project))
        return p if p.is_dir() else Path(config.PROJECTS_ROOT) / str(project)
    return None


def _backups_from_manifest(raw: Any, root: Path | None) -> dict[str, dict]:
    """{clip id / beat key: backup dict} for every manifest entry carrying a "backup" object —
    clips.parse_manifest ignores unknown keys, so this reads the raw JSON. A backup is
    {"file"|"source_url", "start", "end", "crop", "id"} like a manifest entry."""
    out: dict[str, dict] = {}
    items = raw.get("clips") if isinstance(raw, dict) else raw
    for it in items or []:
        if not isinstance(it, dict) or it.get("enabled", True) is False:
            continue
        bk = it.get("backup")
        if not isinstance(bk, dict) or not (bk.get("file") or bk.get("source_url")):
            continue
        f = str(bk.get("file") or "").strip()
        if f and root is not None and not Path(f).is_absolute():
            f = str(Path(root) / f)
        entry = {**bk, "file": f}
        eid = str(it.get("id") or (Path(str(it.get("file") or "")).stem) or "").strip()
        if eid:
            out[f"id:{eid}"] = entry
        if it.get("beat"):
            out[f"beat:{it['beat']}"] = entry
    return out


def _source_starts(raw: Any) -> dict[str, float]:
    """{clip id: source_start} for manifest entries that record where their section begins."""
    out: dict[str, float] = {}
    items = raw.get("clips") if isinstance(raw, dict) else raw
    for it in items or []:
        if isinstance(it, dict) and isinstance(it.get("source_start"), (int, float)):
            eid = str(it.get("id") or Path(str(it.get("file") or "")).stem or "").strip()
            if eid:
                out[eid] = float(it["source_start"])
    return out


def _stamp_backup(sh: ScreenShot, b: dict, offset: float) -> None:
    """Attach backup entry `b` to the shot; `offset` keeps a beat that spans several shots playing
    the backup continuously, as clips._stamp does for the primary."""
    sh.backup_clip_path = str(b.get("file") or "")
    start = float(b.get("start") or 0.0)
    sh.backup_clip_in = round(start + offset, 3)
    sh.backup_clip_out = float(b.get("end") or 0.0)
    sh.backup_clip_crop = dict(b.get("crop") or {})
    sh.backup_clip_id = str(b.get("id") or f"{sh.clip_id}-backup")
    sh.backup_source_url = str(b.get("source_url") or "")
    ss = b.get("source_start")
    sh.backup_source_start = float(ss) if isinstance(ss, (int, float)) else -1.0


def _requantize(shots: list[ScreenShot], fps: int = 30) -> None:
    """Re-snap durations to whole frames by cumulative rounding after later passes split shots
    (clips._split_in_time rounds to ms): the total — and so the audio alignment — is preserved."""
    cum = 0.0
    prev = 0
    for s in shots:
        cum += float(s.duration_seconds)
        f = int(round(cum * fps))
        frames = max(f - prev, int(round(_sh.FPS * 0.4)))
        prev += frames
        s.duration_seconds = frames / fps


def build_shots_for_screen_qa(
    narration: dict[str, Any],
    scene_timings: Any = None,
    word_timestamps: list[dict] | None = None,
    caption_chunks: Any = None,                  # accepted for build_shots parity; unused
    *,
    pages_by_number: dict | None = None,         # ignored: screen_qa has no comic pages
    cluster_to_name: dict | None = None,         # ignored
    project: str | None = None,
    project_root: Path | None = None,
    clips_manifest: dict | list | None = None,
    custom_images: dict[str, str] | None = None,
    screen_context: dict | None = None,
    audio_duration: float = 0.0,
    log: Callable[[str], None] = print,
) -> list[ScreenShot]:
    """One shot per beat window (see screen_beats.plan_windows) with the beat's clip, backup and
    custom image stamped on through the same resolvers the comic path uses.

    Contract with video-qa/p3-core (stages/screen_pipeline.py): `narration` is the Scene-schema
    dict with mode "screen_qa" and visual_beats {text, query}; `screen_context` is
    {question, items:[...]}; positional (narration, scene_timings, word_timestamps) is valid and
    {} / None for the timings is fine — durations then come from target_seconds.

    `custom_images` ({beat_key: abs path}) and `clips_manifest` override what the project folder
    holds (tests, dry runs); with project=None nothing is read from disk."""
    scenes = (narration or {}).get("scenes") or []
    if not scenes:
        return []
    root = _project_root_of(project, project_root)

    if screen_context is None and root is not None:
        screen_context = _load_json(root / "screen_context.json") or {}

    # ── the beats Master picked something for (also protects them from the short-window merge)
    raw_manifest: Any = clips_manifest
    if raw_manifest is None and root is not None:
        p = root / "review" / "clips" / "clips.json"
        raw_manifest = _load_json(p) if p.exists() else None
    if isinstance(raw_manifest, list):
        raw_manifest = {"clips": raw_manifest}
    entries = parse_manifest(raw_manifest, root or Path("."))[0] if raw_manifest else []

    if custom_images is not None:
        custom_map = {str(k): str(v) for k, v in custom_images.items()}
    elif root is not None:
        custom_map = _sh._resolve_custom_images(str(root), narration)
    else:
        custom_map = {}

    assignments = resolve_clip_assignments(entries, narration, claimed_beats=set(custom_map),
                                           log=log) if entries else []
    picked = set(custom_map) | {bk for bk, _ in assignments}

    windows = plan_windows(narration, scene_timings, word_timestamps, audio_duration=audio_duration,
                           screen_context=screen_context, protected=picked, fps=_sh.FPS)
    shots: list[ScreenShot] = []
    seen_intro = False
    for i, w in enumerate(windows):
        is_intro = bool(w.is_intro and not seen_intro)
        seen_intro = seen_intro or w.is_intro
        shots.append(ScreenShot(
            shot_id=i, scene_id=w.scene_id, duration_seconds=w.duration, panel_bbox={},
            source_image="", motion=SCREEN_MOTIONS[i % len(SCREEN_MOTIONS)],
            caption_text=w.text, is_intro=is_intro, beat_keys=list(w.keys), query=w.query))

    if custom_map:
        _sh._apply_custom_images_to_shots(shots, custom_map, narration)
    if assignments:
        apply_clips_to_shots(shots, assignments, narration, log=log)
        backups = _backups_from_manifest(raw_manifest, root)
        entry_start = {e.id: e.start for e in entries}
        src_start = _source_starts(raw_manifest)
        for sh in shots:
            if not isinstance(sh, ScreenShot) or not sh.clip_id:
                continue
            sh.clip_source_start = src_start.get(sh.clip_id, -1.0)
            b = backups.get(f"id:{sh.clip_id}") or next(
                (backups[f"beat:{k}"] for k in sh.beat_keys if f"beat:{k}" in backups), None)
            if b:
                _stamp_backup(sh, b, max(0.0, sh.clip_in - entry_start.get(sh.clip_id, sh.clip_in)))
    _requantize(shots)
    for i, sh in enumerate(shots):
        sh.shot_id = i
    return shots


# ─── network repair: a clip entry that only names a URL ─────────────────────────

def _fetch_section(url: str, start: float, seconds: float, root: Path,
                   log: Callable[[str], None]) -> Path:
    """P1's section download (yt-dlp *start-end with the CLIP_SPEED_MAX margin, keyframe-accurate)
    into review/clips/. The file's t=0 IS the source `start`."""
    from .. import clip_fetch
    return clip_fetch.fetch_clip_section(url, root / "review" / "clips", start=start,
                                         beat_duration=seconds, log=log)


# ─── the never-crash renderer ───────────────────────────────────────────────────

def _card_texts(shot: Shot, screen_context: dict | None) -> tuple[str, str, str]:
    """(headline, body, footer) for a shot's card — the research item named in the words
    (adaptation title + year), else the question; the spoken words; the channel."""
    ctx = screen_context or {}
    headline = str(ctx.get("question") or "").strip()
    text = (getattr(shot, "caption_text", "") or "").lower()
    for item in ctx.get("items") or []:
        ent = str(item.get("entity") or "").strip().lower()
        if ent and ent in text and item.get("adaptation_title"):
            yr = f" ({item['year']})" if item.get("year") else ""
            headline = f"{item['adaptation_title']}{yr}"
            break
    body = (getattr(shot, "caption_text", "") or "").strip() or f"Scene {shot.scene_id}"
    return headline, body, str(getattr(config, "CHANNEL_NAME", "") or "")


def _encode_still_frame(png: Path, out_path: Path, frames: int, corner_logo: Path | None) -> None:
    """A PNG held for `frames` frames, encoded to the shot contract (setsar=0 → same SPS as the
    clip/panel shots, so the concat stream copy accepts it)."""
    ff = _sh._require_ffmpeg()
    inputs = ["-loop", "1", "-framerate", str(_sh.FPS), "-i", str(png)]
    graph = "[0:v]format=yuv420p,setsar=0[v]"
    if corner_logo is not None and Path(corner_logo).is_file():
        inputs += ["-i", str(corner_logo)]
        graph = "[0:v][1:v]overlay=W-w-36:36,format=yuv420p,setsar=0[v]"
    cmd = [ff, "-y", *inputs, "-filter_complex", graph, "-map", "[v]",
           "-frames:v", str(frames), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(_sh.FPS), "-an", str(out_path)]
    _sh._run(cmd)
    verify_shot_contract(out_path, frames)


def _render_card(shot: Shot, out_path: Path, work_dir: Path, corner_logo: Path | None,
                 screen_context: dict | None, frames: int) -> None:
    from utils.screen_card import render_screen_card
    headline, body, footer = _card_texts(shot, screen_context)
    png = work_dir / f"card_{shot.shot_id:03d}.png"
    render_screen_card(png, width=_sh.OUTPUT_W, height=_sh.OUTPUT_H, headline=headline,
                       body=body, footer=footer)
    _encode_still_frame(png, out_path, frames, corner_logo)


def _render_blank(shot: Shot, out_path: Path, work_dir: Path, frames: int) -> None:
    """Last resort: a plain dark frame straight from ffmpeg's colour source (no Pillow, no font,
    no input file at all)."""
    ff = _sh._require_ffmpeg()
    cmd = [ff, "-y", "-f", "lavfi", "-i",
           f"color=c=0x0e1117:s={_sh.OUTPUT_W}x{_sh.OUTPUT_H}:r={_sh.FPS}",
           "-vf", "format=yuv420p,setsar=0", "-frames:v", str(frames), "-c:v", "libx264",
           "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(_sh.FPS),
           "-an", str(out_path)]
    _sh._run(cmd)
    verify_shot_contract(out_path, frames)


def _fail_note(level: int, exc: BaseException | str) -> str:
    return f"L{level}: {' '.join(str(exc).split())[:300]}"


def render_screen_shot(
    shot: ScreenShot,
    out_path: Path,
    *,
    work_dir: Path | None = None,
    corner_logo: Path | None = None,
    screen_context: dict | None = None,
    project_root: Path | None = None,
    fetch_missing: bool = True,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Render ONE screen_qa shot through the never-crash chain (module docstring). Sets
    shot.render_level (1-4) and shot.level_notes; shot.clip_fallback stays non-empty only when a
    clip was wanted and none rendered (that is what pipeline._assemble_video reads to decide
    whether the shot is a clip shot for its hard cuts)."""
    log = progress or (lambda _m: None)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(work_dir) if work_dir else out_path.parent / "_screen_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    shot.render_level = 0
    shot.level_notes = []
    frames = max(1, int(round(max(0.4, float(shot.duration_seconds)) * _sh.FPS)))
    wanted_clip = bool(shot.clip_path or shot.clip_source_url or shot.clip_fallback)
    root = Path(project_root) if project_root else None

    def _clip_candidate(path: str, c_in: float, c_out: float, crop: dict, cid: str,
                        url: str, src_start: float = -1.0) -> ScreenShot | None:
        """A shot copy pointing at one clip, fetching the section first when only a URL is known."""
        sh = copy.copy(shot)
        sh.clip_path, sh.clip_in, sh.clip_out = path, c_in, c_out
        sh.clip_crop, sh.clip_id, sh.clip_source_url = dict(crop or {}), cid, url
        if (not path or not Path(path).is_file()) and url and fetch_missing and root is not None:
            # in-point is relative to the section file when its source moment is known
            start = src_start + c_in if src_start >= 0 else c_in
            got = _fetch_section(url, start, float(shot.duration_seconds), root, log)
            sh.clip_path, sh.clip_in = str(got), 0.0
            sh.clip_out = 0.0
        if not sh.clip_path:
            return None
        if not Path(sh.clip_path).is_file():
            raise FileNotFoundError(f"clip file missing: {sh.clip_path}")
        return sh

    # ── Level 1 — the beat's own clip ───────────────────────────────────────────
    if wanted_clip:
        try:
            cand = _clip_candidate(shot.clip_path, shot.clip_in, shot.clip_out, shot.clip_crop,
                                   shot.clip_id, shot.clip_source_url, shot.clip_source_start)
            if cand is None:
                raise FileNotFoundError(shot.clip_fallback or "clip has no local file")
            render_clip_shot(cand, out_path, corner_logo=corner_logo, progress=log)
            shot.render_level, shot.clip_fallback = 1, ""
            shot.clip_path, shot.clip_in, shot.clip_out = cand.clip_path, cand.clip_in, cand.clip_out
            return out_path
        except Exception as exc:                                     # noqa: BLE001 — chain
            shot.level_notes.append(_fail_note(1, exc))
            log(f"[screen_qa] shot {shot.shot_id:03d}: clip failed ({exc}) — trying the backup clip")

    # ── Level 2 — the backup clip ───────────────────────────────────────────────
    if shot.backup_clip_path or shot.backup_source_url:
        try:
            cand = _clip_candidate(shot.backup_clip_path, shot.backup_clip_in, shot.backup_clip_out,
                                   shot.backup_clip_crop, shot.backup_clip_id or f"{shot.clip_id}-backup",
                                   shot.backup_source_url, shot.backup_source_start)
            if cand is None:
                raise FileNotFoundError("backup clip has no local file")
            render_clip_shot(cand, out_path, corner_logo=corner_logo, progress=log)
            shot.render_level, shot.clip_fallback = 2, ""
            shot.clip_path, shot.clip_in, shot.clip_out = cand.clip_path, cand.clip_in, cand.clip_out
            shot.clip_id, shot.clip_crop = cand.clip_id, cand.clip_crop
            shot.clip_source_url = cand.clip_source_url
            return out_path
        except Exception as exc:                                     # noqa: BLE001 — chain
            shot.level_notes.append(_fail_note(2, exc))
            log(f"[screen_qa] shot {shot.shot_id:03d}: backup clip failed ({exc}) — trying a still")
    if wanted_clip:
        shot.clip_fallback = " | ".join(shot.level_notes) or shot.clip_fallback or "clip did not render"

    # ── Level 3 — a still / custom image, Ken Burns (shots.render_shot, unchanged) ──────────
    still = getattr(shot, "custom_image", "") or ""
    if still:
        try:
            still_shot = copy.copy(shot)
            still_shot.clip_path = ""                  # render_shot takes its custom-image path
            _sh.render_shot(still_shot, out_path, work_dir=work_dir / "_stills",
                            progress=log, corner_logo=corner_logo)
            verify_shot_contract(out_path, frames)
            shot.render_level = 3
            return out_path
        except Exception as exc:                                     # noqa: BLE001 — chain
            shot.level_notes.append(_fail_note(3, exc))
            log(f"[screen_qa] shot {shot.shot_id:03d}: still failed ({exc}) — falling back to a card")

    # ── Level 4 — a text card; the plain frame is its own failsafe ──────────────
    try:
        _render_card(shot, out_path, work_dir, corner_logo, screen_context, frames)
    except Exception as exc:                                         # noqa: BLE001 — never crash
        shot.level_notes.append(_fail_note(4, exc))
        log(f"[screen_qa] shot {shot.shot_id:03d}: text card failed ({exc}) — plain frame")
        _render_blank(shot, out_path, work_dir, frames)
    shot.render_level = 4
    return out_path


# ─── shots.json + reuse signature ───────────────────────────────────────────────

def _file_sig(p: str) -> list:
    try:
        st = Path(p).stat()
        return [p, st.st_size, int(st.st_mtime)]
    except OSError:
        return [p, 0, 0]


def shot_signature(shot: ScreenShot, corner_logo: Path | None) -> str:
    """Everything that decides what a shot file contains, so a rerun reuses a shot only when it
    would render the same (a changed clip pick, still or duration re-renders it)."""
    doc = {
        "v": _RENDER_VERSION, "frames": int(round(shot.duration_seconds * _sh.FPS)),
        "motion": shot.motion, "text": shot.caption_text, "intro": shot.is_intro,
        "clip": [_file_sig(shot.clip_path) if shot.clip_path else shot.clip_source_url,
                 shot.clip_in, shot.clip_out, shot.clip_crop],
        "backup": [_file_sig(shot.backup_clip_path) if shot.backup_clip_path else shot.backup_source_url,
                   shot.backup_clip_in, shot.backup_clip_out],
        "still": _file_sig(shot.custom_image) if shot.custom_image else "",
        "logo": bool(corner_logo), "fit": [config.ENABLE_VIDEO_CLIPS, config.CLIP_SPEED_MIN,
                                           config.CLIP_SPEED_MAX, config.CLIP_MAX_HOLD],
        "frame": [_sh.OUTPUT_W, _sh.OUTPUT_H],
    }
    return hashlib.sha1(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def _write_screen_shots_log(shots: list[ScreenShot], out_path: Path,
                            log: Callable[[str], None]) -> None:
    """shots.json — a LIST like the comic Stage 5 writes (nothing reads it downstream)."""
    entries = []
    for s in shots:
        e: dict[str, Any] = {
            "shot_id": s.shot_id, "scene_id": s.scene_id, "beats": list(s.beat_keys),
            "caption_text": s.caption_text, "duration_seconds": round(s.duration_seconds, 3),
            "frames": int(round(s.duration_seconds * _sh.FPS)), "motion": s.motion,
            "render_level": s.render_level,
            "level_name": {1: "clip", 2: "backup_clip", 3: "still", 4: "card"}.get(s.render_level),
            "level_notes": list(s.level_notes) or None,
        }
        clip = shot_log_entry(s)
        if clip:
            e["clip"] = clip
        if s.custom_image:
            e["custom_image"] = s.custom_image
        if s.backup_clip_path or s.backup_source_url:
            e["backup"] = {"id": s.backup_clip_id, "file": s.backup_clip_path,
                           "in": s.backup_clip_in, "source_url": s.backup_source_url}
        entries.append(e)
    Path(out_path).write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    levels = [e["render_level"] for e in entries]
    log(f"[screen_qa] wrote shots.json ({len(entries)} shots: "
        f"{levels.count(1)} clip, {levels.count(2)} backup, {levels.count(3)} still, "
        f"{levels.count(4)} card)")


# ─── Stage 5 runner ─────────────────────────────────────────────────────────────

def _load_screen_context(root: Path) -> dict:
    return _load_json(root / "screen_context.json") or {}


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
    scene_timings: Any = None,
    word_timestamps: list[dict] | None = None,
) -> AssemblyResult:
    """Stage 5 for mode="screen_qa": the shots of build_shots_for_screen_qa → never-crash render →
    the comic Stage 5's own assembly (hard cuts around clips, dissolves elsewhere), narration mix,
    outro card, final encode and music score → projects/<p>/final.mp4 (+ video_silent.mp4,
    audio_mixed.wav, shots.json, title.txt), the same contract the comic path ships.
    Called by stage_5.pipeline.assemble_project, which has already passed the review gate and
    checked the TTS hash."""
    root = Path(project_root)
    _sh.set_output_frame("screen_qa")       # Shorts frame (a long-form render earlier in this
                                            # process may have left the module on 1920x1080)
    shots_dir = root / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    silent_video_path = root / "video_silent.mp4"
    audio_mixed_path = root / "audio_mixed.wav"
    final_path = root / "final.mp4"
    audio_path = Path(audio_path) if audio_path else root / "audio.wav"
    n_scenes = len(narration.get("scenes") or [])

    def _result(shots=None, final="", dur=0.0):
        return AssemblyResult(
            final_path=final, duration_seconds=round(dur, 3),
            shot_count=len(shots) if shots is not None else len(list(shots_dir.glob("shot_*.mp4"))),
            scene_count=n_scenes, caption_path="", silent_video_path=str(silent_video_path),
            audio_mixed_path=str(audio_mixed_path), shots_dir=str(shots_dir), shots=shots or [])

    if final_path.exists() and not force and not panels_only:
        log(f"[screen_qa] final.mp4 already exists ({final_path}); pass force=True to rebuild")
        return _result(final=str(final_path), dur=_probe_duration(final_path))

    screen_context = _load_screen_context(root)
    shots = build_shots_for_screen_qa(
        narration, scene_timings, word_timestamps, project=project_name, project_root=root,
        screen_context=screen_context, audio_duration=audio_duration, log=log)
    if not shots:
        raise RuntimeError("build_shots_for_screen_qa produced 0 shots — check narration.json scenes")
    total = sum(s.duration_seconds for s in shots)
    log(f"[screen_qa] planning {len(shots)} shots over {n_scenes} scene(s), "
        f"{total:.2f}s of video for {audio_duration:.2f}s of audio")

    if panels_only:
        _write_screen_shots_log(shots, root / "shots.json", log)
        return _result(shots)

    from config import CHANNEL_LOGO_PATH, ENABLE_CORNER_LOGO
    corner_logo = None
    if ENABLE_CORNER_LOGO:
        corner_logo = _sh._prepare_corner_logo(
            CHANNEL_LOGO_PATH, shots_dir / "_corner_logo.png",
            width=int(_sh.OUTPUT_W * 0.10), alpha=0.55)
        if corner_logo is None:
            log(f"[screen_qa] corner logo unavailable ({CHANNEL_LOGO_PATH}); skipping overlay")

    shot_paths: list[Path] = []
    for s in shots:
        sp = shots_dir / f"shot_{s.shot_id:03d}.mp4"
        meta = shots_dir / f"shot_{s.shot_id:03d}.json"
        sig = shot_signature(s, corner_logo)
        prev = _load_json(meta) if meta.exists() else None
        if (not force and sp.exists() and isinstance(prev, dict) and prev.get("sig") == sig):
            s.render_level = int(prev.get("level") or 0)
            s.clip_fallback = str(prev.get("clip_fallback") or "")
            s.level_notes = list(prev.get("notes") or [])
            log(f"[screen_qa] reusing {sp.name} (level {s.render_level})")
        else:
            render_screen_shot(s, sp, work_dir=shots_dir / "_work", corner_logo=corner_logo,
                               screen_context=screen_context, project_root=root, progress=log)
            # the signature taken BEFORE the render: a level-2 render rewrites the shot's clip
            # fields, and the next run starts again from the manifest's (primary) ones
            meta.write_text(json.dumps({"sig": sig, "level": s.render_level,
                                        "clip_fallback": s.clip_fallback, "notes": s.level_notes}),
                            encoding="utf-8")
        shot_paths.append(sp)
    _write_screen_shots_log(shots, root / "shots.json", log)

    from config import (CHANNEL_HANDLE, CHANNEL_NAME, ENABLE_OUTRO_CARD, OUTRO_CARD_SECONDS)
    outro_card, outro_dur = None, 0.0
    if ENABLE_OUTRO_CARD:
        outro_card = _build_outro_card(root / "_outro_card.mp4", duration=OUTRO_CARD_SECONDS,
                                       logo=CHANNEL_LOGO_PATH, channel_name=CHANNEL_NAME,
                                       handle=CHANNEL_HANDLE)
        outro_dur = OUTRO_CARD_SECONDS if outro_card is not None else 0.0

    if silent_video_path.exists() and not force:
        log(f"[screen_qa] reusing {silent_video_path.name}")
    else:
        log(f"[screen_qa] assembling {len(shot_paths)} shots → {silent_video_path.name}")
        _assemble_video(shots, shot_paths, silent_video_path, outro_card=outro_card,
                        outro_dur=outro_dur, project=project_name)

    if (audio_mixed_path.exists() and not force and audio_path.exists()
            and audio_mixed_path.stat().st_mtime >= audio_path.stat().st_mtime):
        log(f"[screen_qa] reusing {audio_mixed_path.name}")
    else:
        mix_audio(audio_path, audio_mixed_path, bg_music_path=None, progress=log)
        if outro_dur > 0:
            try:
                padded = audio_mixed_path.with_suffix(".pad.wav")
                _pad_audio_tail(audio_mixed_path, outro_dur, padded)
                padded.replace(audio_mixed_path)
            except Exception as exc:                                  # noqa: BLE001
                log(f"[screen_qa] audio pad failed ({exc}); shipping without the outro-card tail")

    log(f"[screen_qa] final encode → {final_path.name}")
    _final_encode(silent_video_path, audio_mixed_path, final_path)
    _score_final_video(project_name, root, silent_video_path, audio_path, final_path, log=log)
    duration = _probe_duration(final_path)
    log(f"[screen_qa] done: {final_path} ({duration:.2f}s)")
    _write_title_file(root, narration)
    return _result(shots, final=str(final_path), dur=duration)
