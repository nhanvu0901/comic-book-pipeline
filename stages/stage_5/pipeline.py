"""Stage 5 orchestrator: narration + audio + panels → final 9:16 MP4."""
import json
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Callable

from config import BG_MUSIC_PATH, PROJECTS_ROOT
from .audio import mix_audio
from .captions import build_ass
from .schema import AssemblyResult
from .shots import build_shots, render_shot


FPS = 30


def assemble_project(
    project_name: str,
    *,
    bg_music_path: str | None = None,
    enable_music: bool = True,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> AssemblyResult:
    """Build the final 1080x1920 H.264 MP4 from narration + audio + panels."""
    log = progress or (lambda m: print(m))
    _require_ffmpeg()

    root = PROJECTS_ROOT / project_name
    narration_path = root / "narration.json"
    audio_path = root / "audio.wav"
    words_path = root / "word_timestamps.json"
    for req in (narration_path, audio_path, words_path):
        if not req.exists():
            raise FileNotFoundError(f"missing {req.name}: {req}. Run earlier stages first.")

    narration = json.loads(narration_path.read_text())
    word_timestamps = json.loads(words_path.read_text())
    audio_duration = _wav_duration(audio_path)
    scene_timings_path = root / "scene_timings.json"
    scene_timings = (
        json.loads(scene_timings_path.read_text()) if scene_timings_path.exists() else []
    )

    shots_dir = root / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    captions_path = root / "captions.ass"
    silent_video_path = root / "video_silent.mp4"
    audio_mixed_path = root / "audio_mixed.wav"
    final_path = root / "final.mp4"

    if final_path.exists() and not force:
        log(f"[stage5] final.mp4 already exists ({final_path}); pass force=True to rebuild")
        duration = _probe_duration(final_path)
        return AssemblyResult(
            final_path=str(final_path),
            duration_seconds=round(duration, 3),
            shot_count=len(list(shots_dir.glob("shot_*.mp4"))),
            scene_count=len(narration.get("scenes") or []),
            caption_path=str(captions_path) if captions_path.exists() else "",
            silent_video_path=str(silent_video_path),
            audio_mixed_path=str(audio_mixed_path),
            shots_dir=str(shots_dir),
            bgm_used=None,
        )

    bgm = _resolve_bgm(bg_music_path, enable_music, log)

    # Load caption_chunks + preprocessed pages for caption-chunk shot strategy
    caption_chunks_path = root / "caption_chunks.json"
    caption_chunks = (
        json.loads(caption_chunks_path.read_text()) if caption_chunks_path.exists() else []
    )
    pages_by_number = _load_preprocessed_pages(root)

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
            render_shot(s, sp, work_dir=shots_dir / "_panels", progress=log, corner_logo=corner_logo)
        shot_paths.append(sp)

    # Debug log: record every shot's panel selection (page, bbox, image path,
    # upscale factor) so we can inspect "why did this shot pick that panel"
    # without re-running. Flags shots upscaled ≥3× (too-zoomed candidates).
    _write_shots_log(shots, caption_chunks, shots_dir, root / "shots.json", log)

    if silent_video_path.exists() and not force:
        log(f"[stage5] reusing {silent_video_path.name}")
    else:
        log(f"[stage5] assembling {len(shot_paths)} shots → {silent_video_path.name} "
            f"(xfade={ _xfade_label() })")
        _assemble_video(shots, shot_paths, silent_video_path)

    log(f"[stage5] generating captions.ass ({len(word_timestamps)} words)")
    ass_text = build_ass(word_timestamps, audio_duration)
    captions_path.write_text(ass_text)

    if audio_mixed_path.exists() and not force:
        log(f"[stage5] reusing {audio_mixed_path.name}")
    else:
        mix_audio(audio_path, bgm, audio_mixed_path, progress=log)

    log(f"[stage5] final encode → {final_path.name}")
    _final_encode(silent_video_path, audio_mixed_path, captions_path, final_path)

    duration = _probe_duration(final_path)
    log(f"[stage5] done: {final_path} ({duration:.2f}s)")

    return AssemblyResult(
        final_path=str(final_path),
        duration_seconds=round(duration, 3),
        shot_count=len(shots),
        scene_count=len(narration.get("scenes") or []),
        caption_path=str(captions_path),
        silent_video_path=str(silent_video_path),
        audio_mixed_path=str(audio_mixed_path),
        shots_dir=str(shots_dir),
        bgm_used=str(bgm) if bgm else None,
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


def _resolve_bgm(
    override: str | None, enable_music: bool, log: Callable[[str], None]
) -> Path | None:
    if not enable_music:
        log("[stage5] music disabled — narration-only mix")
        return None
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    env_path = BG_MUSIC_PATH
    if env_path:
        p = Path(env_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent.parent / p
        candidates.append(p)
    for c in candidates:
        if c and c.exists():
            log(f"[stage5] BGM: {c}")
            return c
    log("[stage5] no BGM file found — narration-only mix")
    return None


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
    from config import XFADE_DURATION, XFADE_TRANSITION
    return f"{XFADE_TRANSITION} {XFADE_DURATION}s" if float(XFADE_DURATION) > 0 else "off"


def _assemble_video(shots, shot_paths, out_path: Path) -> Path:
    """Scene-grouped assembly: hard-cut within a scene, dissolve between scenes.
    Falls back to a plain hard-cut concat when disabled, single-scene, or on error."""
    from config import XFADE_DURATION, XFADE_TRANSITION
    from .shots import FPS as _SHOTS_FPS
    x = float(XFADE_DURATION)
    groups = _group_shots_by_scene(shots, shot_paths)
    if x <= 0 or len(groups) < 2:
        return _concat(shot_paths, out_path)
    try:
        ff = _require_ffmpeg()
        tmp = out_path.parent / "_scene_clips"
        tmp.mkdir(parents=True, exist_ok=True)
        clips = [_concat(paths, tmp / f"scene_{i:03d}.mp4")
                 for i, (_sid, paths, _d) in enumerate(groups)]
        durs = [d for (_sid, _p, d) in groups]
        offs = _xfade_offsets(durs)

        inputs: list[str] = []
        for c in clips:
            inputs += ["-i", str(c)]
        # normalize + tail-pad every clip except the last (pad absorbs the overlap)
        chains = []
        last = len(clips) - 1
        for i in range(len(clips)):
            pad = "" if i == last else f",tpad=stop_mode=clone:stop_duration={x}"
            chains.append(f"[{i}:v]settb=AVTB,fps={FPS}{pad}[v{i}]")
        prev = "v0"
        for k in range(1, len(clips)):
            out = f"x{k}"
            chains.append(
                f"[{prev}][v{k}]xfade=transition={XFADE_TRANSITION}:"
                f"duration={x}:offset={offs[k-1]}[{out}]")
            prev = out
        filter_complex = ";".join(chains)
        cmd = [ff, "-y", *inputs,
               "-filter_complex", filter_complex,
               "-map", f"[{prev}]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-pix_fmt", "yuv420p", "-r", str(FPS),
               str(out_path)]
        _run(cmd)
        return out_path
    except Exception as exc:  # any ffmpeg/IO failure → never block a render
        print(f"[stage5] xfade assembly failed ({exc}); falling back to hard-cut concat")
        return _concat(shot_paths, out_path)


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


def _final_encode(
    silent_video: Path, audio_mixed: Path, captions: Path, out_path: Path
) -> Path:
    ff = _require_ffmpeg()
    fonts_dir = Path(__file__).resolve().parent.parent.parent / "fonts"
    # ffmpeg libavfilter strict parser rejects quoted paths in `subtitles=...` —
    # use explicit `filename=` key with backslash-escaped colons/special chars.
    sub_filter = f"subtitles=filename={_ffmpeg_escape(str(captions))}"
    if fonts_dir.exists():
        sub_filter += f":fontsdir={_ffmpeg_escape(str(fonts_dir))}"
    cmd = [
        ff, "-y",
        "-i", str(silent_video),
        "-i", str(audio_mixed),
        "-vf", sub_filter,
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "20",
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


def _ffmpeg_escape(path: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument.

    Inside -vf the chars `\\`, `'`, `:`, `[`, `]`, `,` and `;` are special and
    must be backslash-escaped. Spaces are fine. See ffmpeg-filters(1) "Escaping".
    """
    for ch in ("\\", "'", ":", "[", "]", ",", ";"):
        path = path.replace(ch, "\\" + ch)
    return path


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
