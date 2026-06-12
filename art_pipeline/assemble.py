"""A6: plan-driven art assembler (spec 2026-06-11 §A6).

Comic Stage 5's build_shots embeds its own motion cycle and panel-picking
heuristics — correct for comics, wrong for the art visual plan. This module
plans Shots directly from visual_plan.json and reuses the comic render bricks
READ-ONLY: render_shot, build_ass, mix_audio, _concat, _final_encode.

Motion contract (the user's "zoom with intent"):
  painting_region → real zoom (alternation already in plan)
  painting_full   → static (intro) / subtle zoom_out
  related         → pan_right drift, never zoom
No shot holds a static frame longer than ART_MAX_STATIC_SEC; scenes at or over
ART_SHOT_SPLIT_SEC get a second shot (mirrors comic SCENE_SECOND_PANEL_MIN_DUR)."""
import json
from pathlib import Path
from typing import Callable

from stages.stage_5.audio import mix_audio
from stages.stage_5.captions import build_ass
from stages.stage_5.pipeline import (
    FPS, _concat, _final_encode, _load_preprocessed_pages, _probe_duration,
    _require_ffmpeg, _resolve_bgm, _wav_duration,
)
from stages.stage_5.schema import AssemblyResult, Shot
from stages.stage_5.shots import render_shot

from .config import ART_MAX_STATIC_SEC, ART_SHOT_SPLIT_SEC, get_art_project_path
from .visual_plan import derive_trivial_plan


def _full_bbox(page: dict) -> dict:
    dims = page.get("image_dimensions") or {}
    return {"x": 0, "y": 0,
            "w": int(dims.get("width", 0)), "h": int(dims.get("height", 0))}


def _region_bbox(page: dict, panel_ref: int) -> dict:
    for pn in page.get("panels") or []:
        if int(pn.get("index", -1)) == panel_ref:
            b = pn.get("bbox") or {}
            return {"x": int(b.get("x", 0)), "y": int(b.get("y", 0)),
                    "w": int(b.get("w", 0)), "h": int(b.get("h", 0))}
    return _full_bbox(page)


# Aspect bounds are RELATIVE to the output frame: tuned on 9:16 as [0.4, 2.5],
# i.e. [0.711x, 4.444x] the frame aspect (0.5625). Computing from the live
# frame keeps the same perceived geometry when video.py overrides the output
# to 16:9 for long-form.
_REL_MIN_ASPECT = 0.4 / (1080 / 1920)   # 0.7111…
_REL_MAX_ASPECT = 2.5 / (1080 / 1920)   # 4.4444…


def _aspect_bounds() -> tuple[float, float]:
    # intentional lazy import — must read OUTPUT_W/H live AFTER video.py's
    # runtime override, not module-load-time values
    import stages.stage_5.shots as shots
    frame = shots.OUTPUT_W / shots.OUTPUT_H
    return _REL_MIN_ASPECT * frame, _REL_MAX_ASPECT * frame


def _expand_extreme_bbox(bbox: dict, page: dict) -> dict:
    """A region far wider/taller than the output frame renders as a thin sliver
    over blur (measured: 3920x262 'gas lamp string' intro). Grow the short side
    around the region's center until aspect is within bounds, clamped to the
    image — the zoom still lands on the region, with readable surroundings.
    Bounds scale with the live OUTPUT_W/OUTPUT_H so 16:9 long-form uses the
    correct geometry instead of the 9:16 Shorts constants."""
    x, y = int(bbox.get("x", 0)), int(bbox.get("y", 0))
    w, h = int(bbox.get("w", 0)), int(bbox.get("h", 0))
    if w <= 0 or h <= 0:
        return bbox
    min_aspect, max_aspect = _aspect_bounds()
    aspect = w / h
    if min_aspect <= aspect <= max_aspect:
        return bbox
    dims = page.get("image_dimensions") or {}
    pw, ph = int(dims.get("width", 0)), int(dims.get("height", 0))
    if aspect > max_aspect:
        # too wide → grow height symmetrically around the center y
        new_h = int(round(w / max_aspect))
        y = int(round(y + h / 2 - new_h / 2))
        h = new_h
        if ph > 0:
            y = max(0, min(y, ph - h))
            if h > ph:
                y, h = 0, ph   # hit both edges — best we can do
    else:
        # too tall → grow width symmetrically around the center x
        new_w = int(round(h * min_aspect))
        x = int(round(x + w / 2 - new_w / 2))
        w = new_w
        if pw > 0:
            x = max(0, min(x, pw - w))
            if w > pw:
                x, w = 0, pw
    return {"x": x, "y": y, "w": w, "h": h}


def _fmt_srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(word_timestamps: list[dict], *, max_words: int = 7) -> str:
    """Plain .srt from word timestamps (~7-word cues). Long-form videos ship
    subtitles as CC instead of burned-in karaoke (genre + 16:9 decision,
    spec 2026-06-12 §A6).

    Raw word timestamps on disk use key ``"word"`` (confirmed from art_projects/).
    The fallback to ``"text"`` is kept for forward-compatibility."""
    if not word_timestamps:
        return ""
    blocks: list[str] = []
    n = 0   # manual counter — skipped empty cues must not leave numbering gaps
    for i in range(0, len(word_timestamps), max_words):
        chunk = word_timestamps[i:i + max_words]
        text = " ".join(str(w.get("word", w.get("text", ""))) for w in chunk).strip()
        if not text:
            continue   # all-empty chunk → no blank cue
        n += 1
        start = float(chunk[0]["start"]); end = float(chunk[-1]["end"])
        blocks.append(f"{n}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n{text}")
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def _scene_durations(scenes: list[dict], timings: list[dict],
                     audio_duration: float) -> dict[int, float]:
    """Visual duration per scene. A scene's shot stays on screen from its OWN
    start until the NEXT scene's start — the inter-scene silence belongs to the
    scene before it. Using end-start instead drops every gap, so each later
    shot appears EARLIER than its audio (measured: 6.75s cumulative drift over
    13 scenes). The last scene runs to the end of the audio."""
    by_id = {int(t["scene_id"]): (float(t["start"]), float(t["end"]))
             for t in timings or []}
    if by_id and set(by_id) >= {s["scene_id"] for s in scenes}:
        out: dict[int, float] = {}
        for i, s in enumerate(scenes):
            start, end = by_id[s["scene_id"]]
            if i + 1 < len(scenes):
                nxt_start = by_id[scenes[i + 1]["scene_id"]][0]
                out[s["scene_id"]] = max(0.4, nxt_start - start)
            else:
                out[s["scene_id"]] = max(0.4, (audio_duration - start)
                                         if audio_duration > start else end - start)
        return out
    even = max(0.4, audio_duration / max(1, len(scenes)))
    return {s["scene_id"]: even for s in scenes}


def plan_shots(narration: dict, plan: list[dict], pages_by_number: dict,
               scene_timings: list[dict], *, audio_duration: float) -> list[Shot]:
    if not pages_by_number:
        raise RuntimeError("plan_shots: no preprocessed pages — run regions/hunt first")
    scenes = narration.get("scenes") or []
    plan_by_id = {d["scene_id"]: d for d in plan}
    durations = _scene_durations(scenes, scene_timings, audio_duration)

    used_regions: set = {(s.get("page_ref"), d["panel_ref"])
                         for s in scenes
                         for d in [plan_by_id.get(s["scene_id"], {})]
                         if d.get("kind") == "painting_region"
                         and isinstance(d.get("panel_ref"), int)
                         and d["panel_ref"] >= 0}

    def _unused_region(page_ref: int):
        page = pages_by_number.get(page_ref)
        if not page or page.get("preprocessing_method") == "web-related":
            # related page has only its full frame — fall back to first painting page
            painting = [n for n, p in sorted(pages_by_number.items())
                        if p.get("preprocessing_method") != "web-related"]
            if not painting:
                return None
            page_ref, page = painting[0], pages_by_number[painting[0]]
        for pn in page.get("panels") or []:
            key = (page_ref, int(pn["index"]))
            if key not in used_regions:
                used_regions.add(key)
                raw = {"x": int(pn["bbox"]["x"]), "y": int(pn["bbox"]["y"]),
                       "w": int(pn["bbox"]["w"]), "h": int(pn["bbox"]["h"])}
                return page_ref, _expand_extreme_bbox(raw, page)
        return None

    shots: list[Shot] = []
    shot_id = 0
    for s in scenes:
        d = plan_by_id.get(s["scene_id"]) or {"kind": "painting_full",
                                               "panel_ref": -1, "motion": "zoom_out"}
        page_ref = int(d.get("page_ref") or s.get("page_ref") or 1)
        page = pages_by_number.get(page_ref) or next(iter(pages_by_number.values()))
        dur = durations[s["scene_id"]]
        motion = d.get("motion") or "zoom_out"
        if d["kind"] == "painting_region":
            bbox = _expand_extreme_bbox(_region_bbox(page, int(d["panel_ref"])), page)
        else:
            bbox = _full_bbox(page)
        if motion == "static" and dur > ART_MAX_STATIC_SEC:
            motion = "zoom_out"   # never hold a dead frame past the cap

        parts: list = []   # list of (bbox, motion, dur, page_ref)
        if dur >= ART_SHOT_SPLIT_SEC:
            second = _unused_region(page_ref)
            if second:
                sec_page_ref, sec_bbox = second
                # Oppose the primary shot's motion so the cut reads as a
                # deliberate counter-move, not a continuation.
                sec_motion = "zoom_out" if motion == "zoom_in" else "zoom_in"
                parts = [(bbox, motion, dur * 0.6, page_ref),
                         (sec_bbox, sec_motion, dur * 0.4, sec_page_ref)]
        if not parts:
            parts = [(bbox, motion, dur, page_ref)]

        for b, m, dsec, pref in parts:
            src = (pages_by_number.get(pref) or page).get("source_image", "")
            shots.append(Shot(shot_id=shot_id, scene_id=s["scene_id"],
                              duration_seconds=round(dsec, 3), panel_bbox=b,
                              source_image=src, motion=m, text_bboxes=[]))
            shot_id += 1

    # ── Anti-repeat pass — hunt fallback exhaustion can leave several
    # consecutive scenes as painting_full of the SAME page (seen on
    # circus-sideshow: scenes 11-13 → 3 identical full frames ≈15.6s). When a
    # shot repeats the previous one's (source_image, bbox), re-aim it at a
    # region of that page: unused regions first, then round-robin reuse — a
    # repeated region still beats a frozen full frame. Also guards legacy
    # derive_trivial_plan output. Runs BEFORE the audio pad below.
    src_to_page: dict[str, tuple[int, dict]] = {}
    for pn, p in sorted(pages_by_number.items()):
        src_to_page.setdefault(str(p.get("source_image") or ""), (pn, p))
    rr_counters: dict[int, int] = {}

    def _replacement_region(page_ref: int, page: dict) -> dict | None:
        panels = page.get("panels") or []
        if not panels:
            return None   # nothing to re-aim at — caller flips motion only
        for pn in panels:
            key = (page_ref, int(pn["index"]))
            if key not in used_regions:
                used_regions.add(key)
                return pn.get("bbox") or {}
        i = rr_counters.get(page_ref, 0)   # exhausted → round-robin reuse
        rr_counters[page_ref] = i + 1
        return panels[i % len(panels)].get("bbox") or {}

    for prev, cur in zip(shots, shots[1:]):
        if (cur.source_image, cur.panel_bbox) != (prev.source_image, prev.panel_bbox):
            continue
        entry = src_to_page.get(cur.source_image)
        if entry:
            rep_page_ref, rep_page = entry
            b = _replacement_region(rep_page_ref, rep_page)
            if b is not None:
                raw = {"x": int(b.get("x", 0)), "y": int(b.get("y", 0)),
                       "w": int(b.get("w", 0)), "h": int(b.get("h", 0))}
                cur.panel_bbox = _expand_extreme_bbox(raw, rep_page)
        # Oppose the previous shot's motion (pan_right/static before → zoom_in).
        cur.motion = "zoom_out" if prev.motion == "zoom_in" else "zoom_in"

    total = sum(sh.duration_seconds for sh in shots)
    if shots and total < audio_duration:
        shots[-1].duration_seconds += (audio_duration - total) + 0.20
    return shots


def assemble_art_video(
    project_name: str,
    *,
    bg_music_path: str | None = None,
    enable_music: bool = True,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> AssemblyResult:
    """Assemble the final art video from narration + audio + visual plan.

    Shot reuse with force=False assumes visual_plan.json has not changed
    between runs; after editing the plan, rerun with force=True."""
    log = progress or print
    _require_ffmpeg()
    root = get_art_project_path(project_name)
    narration = json.loads((root / "narration.json").read_text())
    for req in ("audio.wav", "word_timestamps.json"):
        if not (root / req).exists():
            raise FileNotFoundError(f"missing {req}: run tts first.")
    word_timestamps = json.loads((root / "word_timestamps.json").read_text())
    audio_duration = _wav_duration(root / "audio.wav")
    timings_path = root / "scene_timings.json"
    scene_timings = json.loads(timings_path.read_text()) if timings_path.exists() else []
    pages_by_number = _load_preprocessed_pages(root)
    plan = (json.loads((root / "visual_plan.json").read_text())
            if (root / "visual_plan.json").exists()
            else derive_trivial_plan(narration))

    final_path = root / "final.mp4"
    shots_dir = root / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    if final_path.exists() and not force:
        log(f"[assemble] final.mp4 exists ({final_path}); force=True to rebuild")
        return AssemblyResult(final_path=str(final_path),
                              duration_seconds=round(_probe_duration(final_path), 3),
                              shot_count=len(list(shots_dir.glob("shot_*.mp4"))),
                              scene_count=len(narration.get("scenes") or []),
                              caption_path=str(root / "captions.ass"),
                              silent_video_path=str(root / "video_silent.mp4"),
                              audio_mixed_path=str(root / "audio_mixed.wav"),
                              shots_dir=str(shots_dir))

    shots = plan_shots(narration, plan, pages_by_number, scene_timings,
                       audio_duration=audio_duration)
    if not shots:
        raise RuntimeError("plan_shots produced 0 shots — check narration/visual_plan")
    zooms = sum(1 for s in shots if s.motion in ("zoom_in", "zoom_out"))
    log(f"[assemble] {len(shots)} shots / {len(narration.get('scenes') or [])} scenes "
        f"({zooms} zoom, {len(shots) - zooms} drift/static)")

    shot_paths: list[Path] = []
    for sh in shots:
        sp = shots_dir / f"shot_{sh.shot_id:03d}.mp4"
        if sp.exists() and not force:
            log(f"[assemble] reusing {sp.name}")
        else:
            render_shot(sh, sp, work_dir=shots_dir / "_panels", progress=log)
        shot_paths.append(sp)

    (root / "shots.json").write_text(json.dumps(
        [{**sh.to_dict(), "kind": next((d["kind"] for d in plan
                                        if d["scene_id"] == sh.scene_id), "")}
         for sh in shots], indent=2, ensure_ascii=False))

    silent = root / "video_silent.mp4"
    log(f"[assemble] concatenating {len(shot_paths)} shots")
    _concat(shot_paths, silent)
    captions = root / "captions.ass"
    longform = (root / "chapters.json").exists()
    if longform:
        # Long-form ships CC subtitles, not burned-in karaoke: write a
        # header-only .ass (renders nothing) + subtitles.srt for upload.
        from stages.stage_5.captions import ASS_HEADER
        captions.write_text(ASS_HEADER)
        (root / "subtitles.srt").write_text(build_srt(word_timestamps))
        log("[assemble] long-form: wrote subtitles.srt (no burned-in captions)")
    else:
        captions.write_text(build_ass(word_timestamps, audio_duration))
    mixed = root / "audio_mixed.wav"
    bgm = _resolve_bgm(bg_music_path, enable_music, log)
    mix_audio(root / "audio.wav", bgm, mixed, progress=log)
    log(f"[assemble] final encode → {final_path.name}")
    _final_encode(silent, mixed, captions, final_path)
    duration = _probe_duration(final_path)
    log(f"[assemble] done: {final_path} ({duration:.2f}s)")
    return AssemblyResult(final_path=str(final_path),
                          duration_seconds=round(duration, 3),
                          shot_count=len(shots),
                          scene_count=len(narration.get("scenes") or []),
                          caption_path=str(captions),
                          silent_video_path=str(silent),
                          audio_mixed_path=str(mixed),
                          shots_dir=str(shots_dir),
                          bgm_used=str(bgm) if bgm else None, shots=shots)
