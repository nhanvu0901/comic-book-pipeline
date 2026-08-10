"""Stage 5 orchestrator: narration + audio + panels → final 9:16 MP4."""
import json
import math
import os
import random
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT
from ..review_gate import ensure_reviewed
from ..stage_4.pipeline import verify_narration_hash
from .audio import mix_audio
from .panel_sheet import build_panel_sheet
from .schema import AssemblyResult
from .shots import (build_shots, render_shot, set_output_frame, widen_panels_to_tiers,
                    LONGFORM_MODES)
from .verify_frames import VERIFY_FRAMES, verify_frames


FPS = 30


def assemble_project(
    project_name: str,
    *,
    force: bool = False,
    skip_review: bool = False,
    panels_only: bool = False,
    progress: Callable[[str], None] | None = None,
) -> AssemblyResult:
    """Build the final H.264 MP4 from narration + audio + panels.

    1080x1920 for the Short modes; 1920x1080 for long-form (see shots.set_output_frame)."""
    log = progress or (lambda m: print(m))
    ensure_reviewed(project_name, skip_review, log=log)
    _require_ffmpeg()

    root = PROJECTS_ROOT / project_name
    narration_path = root / "narration.json"
    audio_path = root / "audio.wav"
    words_path = root / "word_timestamps.json"
    for req in (narration_path, audio_path, words_path):
        if not req.exists():
            raise FileNotFoundError(f"missing {req.name}: {req}. Run earlier stages first.")

    narration = json.loads(narration_path.read_text())
    verify_narration_hash(root / "narration.tts.sha256", narration.get("scenes") or [], log=log,
                          error_hint="Re-run Stage 4 with --force before Stage 5.")
    word_timestamps = json.loads(words_path.read_text())
    audio_duration = _wav_duration(audio_path)
    scene_timings_path = root / "scene_timings.json"
    scene_timings = (
        json.loads(scene_timings_path.read_text()) if scene_timings_path.exists() else []
    )

    shots_dir = root / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    silent_video_path = root / "video_silent.mp4"
    audio_mixed_path = root / "audio_mixed.wav"
    final_path = root / "final.mp4"

    if final_path.exists() and not force and not panels_only:
        log(f"[stage5] final.mp4 already exists ({final_path}); pass force=True to rebuild")
        duration = _probe_duration(final_path)
        return AssemblyResult(
            final_path=str(final_path),
            duration_seconds=round(duration, 3),
            shot_count=len(list(shots_dir.glob("shot_*.mp4"))),
            scene_count=len(narration.get("scenes") or []),
            caption_path="",  # ponytail: captions dropped 2026-07-10, captioning moved to CapCut
            silent_video_path=str(silent_video_path),
            audio_mixed_path=str(audio_mixed_path),
            shots_dir=str(shots_dir),
        )

    # Load caption_chunks + preprocessed pages for caption-chunk shot strategy
    caption_chunks_path = root / "caption_chunks.json"
    caption_chunks = (
        json.loads(caption_chunks_path.read_text()) if caption_chunks_path.exists() else []
    )
    pages_by_number = _load_preprocessed_pages(root)

    # Long-form renders LANDSCAPE and crops per TIER, not per panel. Both are no-ops for every
    # Short mode (set_output_frame returns the 1080x1920 default), so recap / micro_moment /
    # explore_answer come out byte-identical to before this branch existed.
    mode = str(narration.get("mode") or "")
    fw, fh = set_output_frame(mode)
    if mode in LONGFORM_MODES:
        pages_by_number = widen_panels_to_tiers(pages_by_number)
        log(f"[stage5] long-form: {fw}x{fh} frame, panel crops widened to page tiers")

    # v5 Phase 2: load Magi cluster → character name mapping for hybrid scoring
    cluster_to_name_path = root / "cluster_to_name.json"
    cluster_to_name: dict[int, str] = {}
    if cluster_to_name_path.exists():
        try:
            raw = json.loads(cluster_to_name_path.read_text())
            cluster_to_name = {int(k): str(v) for k, v in raw.items()}
            log(f"[stage5] loaded cluster_to_name: {len(cluster_to_name)} named clusters")
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            log(f"[stage5] cluster_to_name.json unreadable: {exc}")

    shots = build_shots(
        narration,
        scene_timings=scene_timings,
        word_timestamps=word_timestamps,
        caption_chunks=caption_chunks,
        pages_by_number=pages_by_number,
        cluster_to_name=cluster_to_name,
        project=project_name,
    )
    if not shots:
        raise RuntimeError("build_shots produced 0 shots — check narration.json fields")

    # BUG #122 Fix C: guarantee the silent video is at least as long as the
    # audio. ffmpeg's `-shortest` in _final_encode trims to the shorter input —
    # if the summed shot durations fall short of the TTS audio (here ~0.25s),
    # the last word ("Venom.") gets clipped. Extend the final shot to cover the
    # audio plus a small tail so nothing is cut.
    total_shot_dur = sum(s.duration_seconds for s in shots)
    if total_shot_dur < audio_duration:
        pad = (audio_duration - total_shot_dur) + 0.20
        shots[-1].duration_seconds += pad
        log(f"[stage5] extended last shot +{pad:.2f}s so video ≥ audio "
            f"({audio_duration:.2f}s) — prevents -shortest clipping the last word")

    silence_aligned = "silence-aligned" if scene_timings and word_timestamps else "even-split"
    log(f"[stage5] planning {len(shots)} shots across {len(narration.get('scenes') or [])} scenes ({silence_aligned} cuts)")

    # RULE (Master): a contact sheet of every panel the render will use, built from
    # the shot list BEFORE any ffmpeg render starts, for all 3 narration modes — a
    # wrong/blurry panel pick should be caught here, not after a full encode.
    panel_sheet_path = root / "panel_sheet.jpg"
    try:
        build_panel_sheet(shots, panel_sheet_path)
        log(f"[stage5] panel sheet: {panel_sheet_path}")
    except Exception as exc:
        log(f"[stage5] panel sheet build failed ({exc}); continuing without it")

    if panels_only:
        _write_shots_log(shots, caption_chunks, shots_dir, root / "shots.json", log)
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

    # Item 8: corner channel logo, baked into each shot (the outro card is built
    # separately and is NOT a shot, so it never gets a double logo).
    from config import ENABLE_CORNER_LOGO, CHANNEL_LOGO_PATH
    from .shots import _prepare_corner_logo, OUTPUT_W
    corner_logo = None
    if ENABLE_CORNER_LOGO:
        corner_logo = _prepare_corner_logo(
            CHANNEL_LOGO_PATH, shots_dir / "_corner_logo.png",
            width=int(OUTPUT_W * 0.10), alpha=0.55)
        if corner_logo is None:
            log(f"[stage5] corner logo unavailable ({CHANNEL_LOGO_PATH}); skipping overlay")

    shot_paths: list[Path] = []
    for s in shots:
        sp = shots_dir / f"shot_{s.shot_id:03d}.mp4"
        if sp.exists() and not force:
            log(f"[stage5] reusing {sp.name}")
        else:
            render_shot(s, sp, work_dir=shots_dir / "_panels", progress=log,
                        corner_logo=corner_logo)
        shot_paths.append(sp)

    # Debug log: record every shot's panel selection (page, bbox, image path,
    # upscale factor) so we can inspect "why did this shot pick that panel"
    # without re-running. Flags shots upscaled ≥3× (too-zoomed candidates).
    _write_shots_log(shots, caption_chunks, shots_dir, root / "shots.json", log)

    from config import (ENABLE_OUTRO_CARD, OUTRO_CARD_SECONDS, CHANNEL_NAME,
                        CHANNEL_HANDLE, CHANNEL_LOGO_PATH)
    outro_card = None
    outro_dur = 0.0
    if ENABLE_OUTRO_CARD:
        card_path = root / "_outro_card.mp4"
        outro_card = _build_outro_card(
            card_path, duration=OUTRO_CARD_SECONDS,
            logo=CHANNEL_LOGO_PATH, channel_name=CHANNEL_NAME, handle=CHANNEL_HANDLE)
        outro_dur = OUTRO_CARD_SECONDS if outro_card is not None else 0.0

    if silent_video_path.exists() and not force:
        log(f"[stage5] reusing {silent_video_path.name}")
    else:
        log(f"[stage5] assembling {len(shot_paths)} shots → {silent_video_path.name} "
            f"(xfade={ _xfade_label() }, outro_card={'on' if outro_card else 'off'})")
        _assemble_video(shots, shot_paths, silent_video_path,
                        outro_card=outro_card, outro_dur=outro_dur, project=project_name)

    if audio_mixed_path.exists() and not force:
        log(f"[stage5] reusing {audio_mixed_path.name}")
    else:
        mix_audio(audio_path, audio_mixed_path, progress=log)
        if outro_dur > 0:
            try:
                _pad_audio_tail(audio_mixed_path, outro_dur, audio_mixed_path.with_suffix(".pad.wav"))
                audio_mixed_path.with_suffix(".pad.wav").replace(audio_mixed_path)
                log(f"[stage5] padded audio +{outro_dur:.2f}s so -shortest keeps the outro card")
            except Exception as exc:
                log(f"[stage5] audio pad failed ({exc}); shipping without the outro-card tail")

    log(f"[stage5] final encode → {final_path.name}")
    _final_encode(silent_video_path, audio_mixed_path, final_path)

    duration = _probe_duration(final_path)
    log(f"[stage5] done: {final_path} ({duration:.2f}s)")
    _write_title_file(root, narration)

    # Stage 5.5: spot-check the rendered frames against the narration (see
    # verify_frames.py). A checker bug must never fail an otherwise-good render.
    if VERIFY_FRAMES:
        try:
            verify_frames(project_name, log=log)
        except Exception as exc:
            log(f"[stage5] frame verification failed ({exc}); continuing (final.mp4 unaffected)")

    return AssemblyResult(
        final_path=str(final_path),
        duration_seconds=round(duration, 3),
        shot_count=len(shots),
        scene_count=len(narration.get("scenes") or []),
        caption_path="",  # ponytail: captions dropped 2026-07-10, captioning moved to CapCut
        silent_video_path=str(silent_video_path),
        audio_mixed_path=str(audio_mixed_path),
        shots_dir=str(shots_dir),
        shots=shots,
    )


def _write_shots_log(shots, caption_chunks, shots_dir, out_path, log):
    """Write shots.json — a per-shot debug record of which panel was chosen.

    Each entry: shot_id, scene_id, caption text, page, panel bbox, the rendered
    panel image path, the native crop size, the cover-scale upscale factor, and
    an upscale_warning flag (≥3× = panel too small → blown up → likely the
    'too zoomed, can't tell what it is' bug). Purely a debug artifact; nothing
    downstream reads it."""
    import re
    from PIL import Image
    from .shots import OUTPUT_W, OUTPUT_H

    def _page_of(src: str) -> int | None:
        m = re.search(r"page[_-]?(\d+)", Path(src).name)
        return int(m.group(1)) if m else None

    entries = []
    for s in shots:
        native_png = shots_dir / "_panels" / f"panel_{s.shot_id:03d}.png"
        framed_png = native_png.with_name(native_png.stem + "_9x16.png")
        nw = nh = scale = None
        if native_png.exists():
            try:
                with Image.open(native_png) as im:
                    nw, nh = im.size
                scale = round(max(OUTPUT_W / nw, OUTPUT_H / nh), 2)
            except Exception:
                pass
        # Fix C: shot count no longer equals caption-chunk count (clause-anchoring),
        # so caption_chunks[shot_id] mislabels shots. Use the text the shot stored.
        text = s.caption_text
        if not text and 0 <= s.shot_id < len(caption_chunks):
            text = str(caption_chunks[s.shot_id].get("text", ""))
        entries.append({
            "shot_id": s.shot_id,
            "scene_id": s.scene_id,
            "caption_text": text,
            "duration_seconds": round(s.duration_seconds, 2),
            "motion": s.motion,
            "page": _page_of(s.source_image),
            "source_image": s.source_image,
            "panel_bbox": s.panel_bbox,
            "panel_png": str(framed_png),
            "panel_native_size": [nw, nh] if nw else None,
            "scale_factor": scale,
            "upscale_warning": bool(scale and scale >= 3.0),
        })
    out_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    n_warn = sum(1 for e in entries if e["upscale_warning"])
    log(f"[stage5] wrote shots.json ({len(entries)} shots, {n_warn} with upscale_warning ≥3×)")


def _load_preprocessed_pages(project_root: Path) -> dict[int, dict]:
    """Load all preprocessed page JSONs keyed by page_number — used to find candidate
    panels per page for the caption-chunk shot strategy."""
    prep = project_root / "preprocessed"
    if not prep.exists():
        return {}
    out: dict[int, dict] = {}
    for p in sorted(prep.glob("page_*.json")):
        try:
            page = json.loads(p.read_text())
            pn = int(page.get("page_number", 0) or 0)
            if pn:
                out[pn] = page
        except Exception:
            continue
    return out


def _write_title_file(root: Path, narration: dict) -> None:
    """Export Stage 3's banner_title to <project>/title.txt — Stage 5 no longer
    burns it into the video (Master writes titles in CapCut instead)."""
    title = str(narration.get("banner_title", "")).strip()
    if title:
        (root / "title.txt").write_text(title + "\n")


def _group_shots_by_scene(shots, shot_paths):
    """Group parallel (shot, path) lists by scene_id, order preserved.
    Returns [(scene_id, [paths], total_scene_dur)]."""
    groups: list[list] = []
    for s, p in zip(shots, shot_paths):
        if groups and groups[-1][0] == s.scene_id:
            groups[-1][1].append(p)
            groups[-1][2] += float(s.duration_seconds)
        else:
            groups.append([s.scene_id, [p], float(s.duration_seconds)])
    return [(g[0], g[1], g[2]) for g in groups]


def _xfade_offsets(scene_durs: list[float]) -> list[float]:
    """Cumulative xfade offsets [d0, d0+d1, ...] (len = M-1). With each non-final
    scene clip tail-padded by the xfade duration and the final clip unpadded, the
    chain's net duration == sum(scene_durs) — preserving scene_timings sync."""
    offs, acc = [], 0.0
    for d in scene_durs[:-1]:
        acc += d
        offs.append(round(acc, 3))
    return offs


def _xfade_label() -> str:
    from config import XFADE_DURATION, XFADE_TRANSITION, XFADE_SOFT_EDGES
    transition = str(XFADE_TRANSITION).strip().lower()
    if transition == "cut":
        return f"cut+soft-edges {XFADE_DURATION}s" if XFADE_SOFT_EDGES else "cut"
    return f"{XFADE_TRANSITION} {XFADE_DURATION}s" if float(XFADE_DURATION) > 0 else "off"


def _assemble_video(shots, shot_paths, out_path: Path,
                    outro_card: Path | None = None, outro_dur: float = 0.0,
                    project: str = "") -> Path:
    """Scene-grouped assembly — see _assemble_groups for the actual transition
    logic (xfade dissolve / hard-cut / soft-edges / flash accents), unchanged.

    `project` (non-empty) additionally rolls a deterministic per-boundary coin
    flip (TRANSITION_WHIP_PROB, see _pick_whip_boundaries) for a whip-blur wipe
    bridge at SOME scene boundaries, replacing whatever transition would have
    landed there. Empty `project` (e.g. call sites/tests that don't pass one)
    skips the whip step entirely — no stable seed possible, so no behavior change.
    """
    groups = _group_shots_by_scene(shots, shot_paths)
    whip = _pick_whip_boundaries(project, shots) if project else {}
    if not whip:
        return _assemble_groups(shots, groups, out_path,
                                outro_card=outro_card, outro_dur=outro_dur)
    return _assemble_with_whips(shots, groups, whip, out_path, outro_card, outro_dur)


def _assemble_groups(shots, groups, out_path: Path, *,
                     outro_card: Path | None = None, outro_dur: float = 0.0,
                     is_first_run: bool = True) -> Path:
    """Core scene-transition assembly for one contiguous run of scene groups (the
    whole video when there are no whip-transition splits — see _assemble_with_whips,
    which calls this once per run and splices the runs together with bridge clips).

    XFADE_TRANSITION == "cut" (default): hard-cut every scene boundary — optionally
    softened at just the two outer edges (XFADE_SOFT_EDGES) and/or spiced with a
    white flash-frame accent at action-classified cuts (FLASH_ACCENTS). Any other
    XFADE_TRANSITION value is the legacy opt-in: dissolve EVERY scene boundary.
    Falls back to a plain hard-cut concat on single-scene input or any ffmpeg/IO error.

    `is_first_run=False` means this run does NOT start at the true beginning of the
    video (a whip bridge already handles the join into it) — the two-outer-edges-only
    soft-edge dissolve must not also fire at this run's own start in that case.
    """
    from config import (XFADE_DURATION, XFADE_TRANSITION, XFADE_SOFT_EDGES,
                        XFADE_ROTATE, FLASH_ACCENTS, FLASH_ACCENTS_MAX)
    x = float(XFADE_DURATION)
    transition = str(XFADE_TRANSITION).strip().lower()
    # Curated transition rotation ("more animation between scenes"). Empty → uniform.
    rotate = [t.strip().lower() for t in str(XFADE_ROTATE).split(",") if t.strip()] or None

    if transition != "cut" and x > 0 and len(groups) >= 2:
        try:
            tmp = out_path.parent / "_scene_clips"
            tmp.mkdir(parents=True, exist_ok=True)
            clips = [_concat(paths, tmp / f"scene_{i:03d}.mp4")
                     for i, (_sid, paths, _d) in enumerate(groups)]
            durs = [d for (_sid, _p, d) in groups]
            per_boundary = _rotate_boundaries(
                shots, groups, rotate, has_outro=outro_card is not None)
            if outro_card is not None:
                clips.append(outro_card)
                durs.append(float(outro_dur))
            return _xfade_chain(clips, durs, out_path, x, transition, rotate=per_boundary)
        except Exception as exc:  # any ffmpeg/IO failure → never block a render
            print(f"[stage5] xfade assembly failed ({exc}); falling back to hard-cut concat")

    # Hard-cut path. Flash accents first (pure bookkeeping — no ffmpeg call unless
    # at least one boundary qualifies), then the soft-edges variant if requested.
    flash_clip = None
    flash_boundaries: set[int] = set()
    soft_edges = (bool(XFADE_SOFT_EDGES) and x > 0 and len(groups) >= 2
                  and (is_first_run or outro_card is not None))
    if FLASH_ACCENTS and len(groups) >= 2:
        try:
            flash_clip = _build_flash_clip(out_path.parent / "_flash_accent.mp4")
            scene_action = _scene_action_flags(shots)
            # the intro→scene1 edge is a dissolve when soft_edges is on, not a hard
            # cut — a flash has nothing to land on there.
            exclude = {0} if (soft_edges and is_first_run) else set()
            flash_boundaries = _pick_flash_boundaries(
                groups, scene_action, exclude=exclude, cap=int(FLASH_ACCENTS_MAX))
        except Exception as exc:
            print(f"[stage5] flash-accent setup failed ({exc}); shipping without flashes")
            flash_clip = None

    if soft_edges:
        try:
            return _hard_cut_soft_edges(groups, outro_card, outro_dur, out_path, x,
                                        flash_clip=flash_clip, flash_boundaries=flash_boundaries,
                                        soft_intro=is_first_run)
        except Exception as exc:
            print(f"[stage5] soft-edge assembly failed ({exc}); falling back to plain hard-cut concat")

    paths = _interleave_flashes(groups, flash_boundaries, flash_clip)
    if outro_card is not None:
        return _concat(paths + [outro_card], out_path)
    return _concat(paths, out_path)


def _rotate_boundaries(shots, groups, rotate: list[str] | None,
                       *, has_outro: bool) -> list[str] | None:
    """Per-boundary transition list for the dissolve path ("more animation between
    scenes"), or None (uniform `transition`) when rotation is off.

    Rotation fires ONLY at REAL story boundaries. Recap groups are real narration
    scenes already, so every boundary rotates. The Q&A locked builder gives every
    shot a UNIQUE scene_id (each is its own group so the assembler dissolves between
    them) — there, `Shot.beat_id` carries the real answer-item scene, and a boundary
    between two groups of the SAME beat keeps a plain dissolve (a slide between two
    panels of one answer reads as "next item", which it isn't). The final boundary
    into the outro card is always a dissolve: a dark end-card sliding in sideways
    reads as glitch, and whether it got one depended on group-count parity."""
    if not rotate:
        return None
    sid_beat: dict = {}
    for s in shots:
        sid_beat.setdefault(getattr(s, "scene_id", None), getattr(s, "beat_id", None))
    beats = [sid_beat.get(sid) for (sid, _p, _d) in groups]
    out: list[str] = []
    r = 0
    for i in range(len(groups) - 1):
        b1, b2 = beats[i], beats[i + 1]
        if b1 is not None and b1 == b2:
            out.append("dissolve")               # intra-beat sub-shot boundary
        else:
            out.append(rotate[r % len(rotate)])  # real scene/beat change
            r += 1
    if has_outro:
        out.append("dissolve")                   # end card always fades in
    return out


def _xfade_chain(clips: list[Path], durs: list[float], out_path: Path,
                 x: float, transition: str, rotate: list[str] | None = None) -> Path:
    """Chain `clips` with an xfade of `x` seconds at every boundary.
    Each non-last clip is tail-padded by `x` (the pad absorbs the overlap) so the
    net duration == sum(durs) — preserving scene_timings / audio sync.

    `transition` is the default used at every boundary. `rotate`, when given, is the
    RESOLVED per-boundary transition list from _rotate_boundaries (len == #boundaries;
    boundary k uses rotate[k-1]); None → uniform `transition` everywhere (also the
    case for the soft-edge intro/outro dissolves, which never rotate).

    Preset is `veryfast` (not `medium`): this clip is re-encoded again by
    _final_encode (slow/crf18), so its preset has no effect on final quality — only
    on how long this intermediate pass takes."""
    ff = _require_ffmpeg()
    offs = _xfade_offsets(durs)
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    chains = []
    last = len(clips) - 1
    for i in range(len(clips)):
        pad = "" if i == last else f",tpad=stop_mode=clone:stop_duration={x}"
        chains.append(f"[{i}:v]settb=AVTB,fps={FPS}{pad}[v{i}]")
    prev = "v0"
    for k in range(1, len(clips)):
        out = f"x{k}"
        trans = rotate[(k - 1) % len(rotate)] if rotate else transition
        chains.append(
            f"[{prev}][v{k}]xfade=transition={trans}:"
            f"duration={x}:offset={offs[k-1]}[{out}]")
        prev = out
    filter_complex = ";".join(chains)
    cmd = [ff, "-y", *inputs,
           "-filter_complex", filter_complex,
           "-map", f"[{prev}]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           str(out_path)]
    _run(cmd)
    return out_path


def _hard_cut_soft_edges(groups, outro_card, outro_dur, out_path: Path, x: float, *,
                         flash_clip: Path | None = None,
                         flash_boundaries: set[int] | None = None,
                         soft_intro: bool = True) -> Path:
    """Hard-cut every inter-scene boundary except the two outer edges (intro→scene1,
    last-story→outro card), which get a small `x`-second dissolve. A flat hard cut on
    those two specific joins read as an abrupt slap in review; every other cut in
    between stays a hard cut (unaffected).

    `soft_intro=False` (a whip-transition run that doesn't start at the true video
    start) drops the intro-edge dissolve — groups[0] joins the body as a plain hard
    cut — while keeping the outro-edge dissolve when outro_card is given."""
    flash_boundaries = flash_boundaries or set()
    tmp = out_path.parent / "_scene_clips"
    tmp.mkdir(parents=True, exist_ok=True)
    if soft_intro:
        intro_paths, intro_dur = groups[0][1], groups[0][2]
        body_groups = groups[1:]
        # flash_boundaries indexes the FULL groups list (boundary i sits between
        # groups[i] and groups[i+1]); shift into body_groups' own 0-based indexing.
        body_flash = {b - 1 for b in flash_boundaries if b >= 1}
    else:
        intro_paths, intro_dur = None, 0.0
        body_groups = groups
        body_flash = set(flash_boundaries)
    body_paths = _interleave_flashes(body_groups, body_flash, flash_clip)
    body_dur = sum(d for _sid, _p, d in body_groups)
    body_clip = _concat(body_paths, tmp / "body.mp4")
    if soft_intro:
        intro_clip = _concat(list(intro_paths), tmp / "intro.mp4")
        clips, durs = [intro_clip, body_clip], [intro_dur, body_dur]
    else:
        clips, durs = [body_clip], [body_dur]
    if outro_card is not None:
        clips.append(outro_card)
        durs.append(float(outro_dur))
    return _xfade_chain(clips, durs, out_path, x, "dissolve")


def _group_shots_only(shots) -> list[list]:
    """Same grouping key as _group_shots_by_scene (consecutive equal scene_id) but
    keeps the Shot objects instead of rendered paths — used by the whip-transition
    picker/splicer to read boundary-adjacent shot durations. _group_shots_by_scene's
    tested signature/return shape is left alone; this just mirrors its grouping."""
    groups: list[list] = []
    for s in shots:
        if groups and groups[-1][0] == s.scene_id:
            groups[-1][1].append(s)
        else:
            groups.append([s.scene_id, [s]])
    return groups


def _pick_whip_boundaries(project: str, shots) -> dict[int, float]:
    """Deterministically choose which SCENE boundaries (index i = the join between
    the i-th and (i+1)-th scene groups) get the whip-blur wipe instead of the normal
    transition. One coin flip per boundary, seeded by f"{project}:{scene_a}:{scene_b}"
    so re-renders of the same project pick the same boundaries. Never picks a
    boundary whose adjacent shot (the one that would be trimmed) is under 0.6s.
    Returns {boundary_index: whip_seconds}."""
    from config import TRANSITION_WHIP_PROB, TRANSITION_WHIP_SECONDS
    prob = float(TRANSITION_WHIP_PROB)
    if prob <= 0:
        return {}
    groups = _group_shots_only(shots)
    out: dict[int, float] = {}
    for i in range(len(groups) - 1):
        scene_a, shots_a = groups[i]
        scene_b, shots_b = groups[i + 1]
        rng = random.Random(f"{project}:{scene_a}:{scene_b}")
        if rng.random() >= prob:
            continue
        if float(shots_a[-1].duration_seconds) < 0.6 or float(shots_b[0].duration_seconds) < 0.6:
            continue
        out[i] = float(TRANSITION_WHIP_SECONDS)
    return out


def _whip_borrowed_durations(shots, whip: dict[int, float]) -> list[float]:
    """Per-shot durations after each chosen whip boundary borrows half its bridge
    length from the last shot of the scene before it and the first shot of the scene
    after — pure arithmetic (no ffmpeg). sum(result) + sum(whip.values()) ==
    sum(original durations): the bridge clips exactly replace what was borrowed, so
    total video duration (and audio sync) never changes."""
    durs = [float(s.duration_seconds) for s in shots]
    if not whip:
        return durs
    groups = _group_shots_only(shots)
    starts, idx = [], 0
    for _sid, gshots in groups:
        starts.append(idx)
        idx += len(gshots)
    for bi, whip_secs in whip.items():
        half = whip_secs / 2.0
        durs[starts[bi + 1] - 1] -= half
        durs[starts[bi + 1]] -= half
    return durs


def _trim_clip_tail(src: Path, out_path: Path, new_duration: float) -> Path:
    """Re-encode `src` shortened to `new_duration`s (frame-accurate — a stream-copy
    `-c copy -t` would snap to the nearest keyframe, off by up to a GOP)."""
    ff = _require_ffmpeg()
    cmd = [ff, "-y", "-i", str(src), "-t", f"{max(0.04, new_duration):.4f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", str(out_path)]
    _run(cmd)
    return out_path


def _trim_clip_head(src: Path, out_path: Path, skip_seconds: float) -> Path:
    """Re-encode `src` with its first `skip_seconds` removed (accurate seek via
    re-encode, not a stream-copy `-ss`/keyframe snap)."""
    ff = _require_ffmpeg()
    cmd = [ff, "-y", "-i", str(src), "-ss", f"{max(0.0, skip_seconds):.4f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", str(out_path)]
    _run(cmd)
    return out_path


def _build_whip_bridge(prev_clip: Path, next_clip: Path, out_path: Path,
                       seconds: float) -> Path:
    """Comicz-style vertical whip-blur wipe: the last frame of `prev_clip` slides up
    and blurs away with a brief white flash (fast, easing IN — accelerating), then
    the first frame of `next_clip` slides up into place from below while the blur
    eases back to 0 (easing OUT — decelerating). Built as a PNG-frame sequence via
    PIL — a single ffmpeg boxblur/crop filter instance can't vary its radius/offset
    per frame, but each frame here needs a different blur+offset — then encoded to
    match the pipeline's h.264/yuv420p/30fps output so it splices into the concat
    chain cleanly."""
    from PIL import Image
    from .shots import OUTPUT_W, OUTPUT_H   # at call time: set_output_frame may have rebound them
    ff = _require_ffmpeg()
    tmp = out_path.parent / f"_whip_frames_{out_path.stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    last_png, first_png = tmp / "a.png", tmp / "b.png"
    _run([ff, "-y", "-sseof", "-0.08", "-i", str(prev_clip), "-frames:v", "1", str(last_png)])
    _run([ff, "-y", "-i", str(next_clip), "-frames:v", "1", str(first_png)])
    img_a = Image.open(last_png).convert("RGB").resize((OUTPUT_W, OUTPUT_H))
    img_b = Image.open(first_png).convert("RGB").resize((OUTPUT_W, OUTPUT_H))

    n_frames = max(2, math.ceil(seconds * FPS) + 1)  # +1 safety margin — ffmpeg's
    half = max(1, n_frames // 2)                      # trailing -t trims to `seconds`
    max_shift = int(OUTPUT_H * 0.5)
    max_blur = 22.0

    def _vertical_smear(img: "Image.Image", blur: float) -> "Image.Image":
        if blur <= 0.3:
            return img
        small_h = max(4, int(OUTPUT_H / (1 + blur)))
        return img.resize((OUTPUT_W, small_h)).resize((OUTPUT_W, OUTPUT_H), Image.BILINEAR)

    def _shift_up(img: "Image.Image", px: int) -> "Image.Image":
        canvas = Image.new("RGB", (OUTPUT_W, OUTPUT_H))
        canvas.paste(img, (0, -px))
        if px > 0:
            edge = img.crop((0, OUTPUT_H - 1, OUTPUT_W, OUTPUT_H)).resize((OUTPUT_W, px))
            canvas.paste(edge, (0, OUTPUT_H - px))
        return canvas

    def _shift_from_below(img: "Image.Image", px: int) -> "Image.Image":
        canvas = Image.new("RGB", (OUTPUT_W, OUTPUT_H))
        canvas.paste(img, (0, px))
        if px > 0:
            edge = img.crop((0, 0, OUTPUT_W, 1)).resize((OUTPUT_W, px))
            canvas.paste(edge, (0, 0))
        return canvas

    def _flash(img: "Image.Image", alpha: float) -> "Image.Image":
        if alpha <= 0:
            return img
        white = Image.new("RGB", img.size, (255, 255, 255))
        return Image.blend(img, white, min(1.0, alpha))

    frames = []
    for i in range(half):
        t = (i + 1) / half
        ease = t * t                                  # accelerating (nhanh dần)
        frame = _shift_up(img_a, int(ease * max_shift))
        frame = _vertical_smear(frame, ease * max_blur)
        frames.append(_flash(frame, 0.35 * ease))
    for i in range(n_frames - half):
        t = (i + 1) / (n_frames - half)
        ease = 1.0 - (1.0 - t) ** 2                    # decelerating (chậm dần)
        px = int((1.0 - ease) * max_shift)
        frame = _shift_from_below(img_b, px)
        frame = _vertical_smear(frame, (1.0 - ease) * max_blur)
        frames.append(_flash(frame, 0.35 * (1.0 - ease)))

    for idx, fr in enumerate(frames):
        fr.save(tmp / f"f_{idx:03d}.png")
    cmd = [ff, "-y", "-framerate", str(FPS), "-i", str(tmp / "f_%03d.png"),
           "-t", f"{seconds:.4f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", str(out_path)]
    _run(cmd)
    shutil.rmtree(tmp, ignore_errors=True)
    return out_path


def _assemble_with_whips(shots, groups, whip: dict[int, float], out_path: Path,
                         outro_card: Path | None, outro_dur: float) -> Path:
    """Splits the scene-clip timeline at the chosen whip boundaries into independent
    runs, assembles each run with the UNCHANGED _assemble_groups (same xfade/hard-cut/
    soft-edges/flash logic as without whips), then splices the runs back together with
    a whip bridge clip at each split point instead of whatever transition would have
    landed there. Concat is associative for same-codec clips, so this produces the
    same output as a single _assemble_groups call except at the whip boundaries."""
    tmp = out_path.parent / "_whip_bridges"
    tmp.mkdir(parents=True, exist_ok=True)
    shot_groups = _group_shots_only(shots)
    adjusted_durs = _whip_borrowed_durations(shots, whip)

    groups = [[sid, list(paths), 0.0] for sid, paths, _dur in groups]
    idx = 0
    for gi, (_sid, gshots) in enumerate(shot_groups):
        n = len(gshots)
        groups[gi][2] = sum(adjusted_durs[idx: idx + n])
        idx += n

    bridges: dict[int, Path] = {}
    for bi, whip_secs in whip.items():
        half = whip_secs / 2.0
        last_shot, first_shot = shot_groups[bi][1][-1], shot_groups[bi + 1][1][0]
        last_path, first_path = groups[bi][1][-1], groups[bi + 1][1][0]
        bridges[bi] = _build_whip_bridge(
            last_path, first_path, tmp / f"bridge_{bi:03d}.mp4", whip_secs)
        groups[bi][1][-1] = _trim_clip_tail(
            last_path, tmp / f"trim_tail_{bi:03d}.mp4",
            float(last_shot.duration_seconds) - half)
        groups[bi + 1][1][0] = _trim_clip_head(
            first_path, tmp / f"trim_head_{bi:03d}.mp4", half)

    runs: list[list] = [[]]
    run_boundaries: list[int] = []
    for i, g in enumerate(groups):
        runs[-1].append(tuple(g))
        if i in whip:
            run_boundaries.append(i)
            runs.append([])

    run_clips: list[Path] = []
    n_runs = len(runs)
    for ri, run_groups in enumerate(runs):
        run_out = tmp / f"run_{ri:03d}.mp4"
        is_last = ri == n_runs - 1
        _assemble_groups(
            shots, run_groups, run_out,
            outro_card=outro_card if is_last else None,
            outro_dur=outro_dur if is_last else 0.0,
            is_first_run=(ri == 0),
        )
        run_clips.append(run_out)

    final_list: list[Path] = []
    for ri, clip in enumerate(run_clips):
        final_list.append(clip)
        if ri < len(run_boundaries):
            final_list.append(bridges[run_boundaries[ri]])
    return _concat(final_list, out_path)


def _scene_action_flags(shots) -> dict[int, bool]:
    """scene_id -> True if any shot's spoken caption in that scene reads as an
    action/impact beat (reuses shots.py's impact-verb check — a fight scene is a
    fight scene whether we're choosing camera motion or choosing a flash cut)."""
    from .shots import _is_action_text
    flags: dict[int, bool] = {}
    for s in shots:
        sid = s.scene_id
        flags[sid] = flags.get(sid, False) or _is_action_text(getattr(s, "caption_text", ""))
    return flags


def _pick_flash_boundaries(groups, scene_action: dict[int, bool],
                           exclude: set[int], cap: int) -> set[int]:
    """Boundary i (the cut INTO groups[i+1]) qualifies when the incoming scene is
    action-classified. Capped, preferring the LATEST qualifying boundaries — fights
    cluster toward the climax, and a flash near the top of a video reads as noise."""
    candidates = [i for i in range(len(groups) - 1)
                  if i not in exclude and scene_action.get(groups[i + 1][0], False)]
    return set(sorted(candidates, reverse=True)[:max(0, cap)])


def _interleave_flashes(groups, flash_boundaries: set[int],
                        flash_clip: Path | None) -> list[Path]:
    """Flatten scene groups into an ordered clip list, splicing `flash_clip` at the
    given boundary indices (between groups[i] and groups[i+1])."""
    paths: list[Path] = []
    last = len(groups) - 1
    for i, (_sid, ps, _d) in enumerate(groups):
        paths.extend(ps)
        if flash_clip is not None and i in flash_boundaries and i < last:
            paths.append(flash_clip)
    return paths


def _build_flash_clip(out_path: Path) -> Path:
    """A single white frame at output resolution — spliced at a cut as a flash
    accent. Encoded to match shot output params (h.264/yuv420p/same fps) so the
    concat demuxer's stream-copy doesn't choke on a mismatched stream."""
    if out_path.exists():
        return out_path
    ff = _require_ffmpeg()
    from .shots import OUTPUT_W, OUTPUT_H
    cmd = [ff, "-y", "-f", "lavfi", "-i", f"color=c=white:s={OUTPUT_W}x{OUTPUT_H}:r={FPS}",
           "-frames:v", "1", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", str(out_path)]
    _run(cmd)
    return out_path


def _concat(shot_paths: list[Path], out_path: Path) -> Path:
    ff = _require_ffmpeg()
    list_file = out_path.parent / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{Path(p).resolve()}'" for p in shot_paths) + "\n"
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


def _ass_drawtext_escape(text: str) -> str:
    """Escape text for ffmpeg drawtext (colon, backslash, single quote)."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")


def _build_outro_card(out_path: Path, *, duration: float, logo: str | None,
                      channel_name: str, handle: str) -> Path | None:
    """Render a ~`duration`s silent outro card: dark background, centered logo,
    channel name in Anton, and a SUBSCRIBE subline. Returns None on failure."""
    try:
        ff = _require_ffmpeg()
        font = Path(__file__).resolve().parent.parent.parent / "fonts" / "Anton-Regular.ttf"
        W, H = 1080, 1920
        name = _ass_drawtext_escape(channel_name.upper())
        sub = _ass_drawtext_escape(f"SUBSCRIBE FOR MORE  {handle}")
        # base dark canvas
        inputs = ["-f", "lavfi", "-i", f"color=c=0x0A0A0A:s={W}x{H}:d={duration}:r={FPS}"]
        filters = []
        base = "0:v"
        if logo and Path(logo).exists():
            inputs += ["-i", str(logo)]
            filters.append(f"[1:v]scale=360:-1[lg]")
            filters.append(f"[{base}][lg]overlay=(W-w)/2:(H-h)/2-200[bg]")
            base = "bg"
        fontfile = str(font).replace("\\", "/")
        filters.append(
            f"[{base}]drawtext=fontfile='{fontfile}':text='{name}':"
            f"fontcolor=white:fontsize=96:x=(w-text_w)/2:y=h/2+120[t1]")
        filters.append(
            f"[t1]drawtext=fontfile='{fontfile}':text='{sub}':"
            f"fontcolor=0xCC2222:fontsize=44:x=(w-text_w)/2:y=h/2+260[v]")
        cmd = [ff, "-y", *inputs, "-filter_complex", ";".join(filters),
               "-map", "[v]", "-t", f"{duration}",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", str(out_path)]
        _run(cmd)
        return out_path
    except Exception as exc:
        print(f"[stage5] outro card build failed ({exc}); shipping without it")
        return None


def _pad_audio_tail(audio_path: Path, extra_seconds: float, out_path: Path) -> Path:
    """Append `extra_seconds` of silence to a WAV so the video's outro-card tail
    is not trimmed by `-shortest` at final encode."""
    ff = _require_ffmpeg()
    cmd = [ff, "-y", "-i", str(audio_path),
           "-af", f"apad=pad_dur={extra_seconds}",
           str(out_path)]
    _run(cmd)
    return out_path


def _final_encode(
    silent_video: Path, audio_mixed: Path, out_path: Path
) -> Path:
    # No caption burn-in (Master 2026-07-10: captioning moved to CapCut, out of pipeline).
    ff = _require_ffmpeg()
    cmd = [
        ff, "-y",
        "-i", str(silent_video),
        "-i", str(audio_mixed),
        "-c:v", "libx264",
        # crf 18: intermediate must out-quality final (double-encode chain)
        "-preset", "slow",
        "-crf", "18",
        "-profile:v", "high",
        "-level", "4.1",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def _probe_duration(path: Path) -> float:
    ff = shutil.which("ffprobe")
    if not ff:
        return 0.0
    res = subprocess.run(
        [ff, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float((res.stdout or "0").strip())
    except ValueError:
        return 0.0


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
