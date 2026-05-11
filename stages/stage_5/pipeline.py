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
from .schema import AssemblyResult, Shot
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

    shots = build_shots(narration)
    if not shots:
        raise RuntimeError("build_shots produced 0 shots — check narration.json fields")
    log(f"[stage5] planning {len(shots)} shots across {len(narration.get('scenes') or [])} scenes")

    shot_paths: list[Path] = []
    for s in shots:
        sp = shots_dir / f"shot_{s.shot_id:03d}.mp4"
        if sp.exists() and not force:
            log(f"[stage5] reusing {sp.name}")
        else:
            render_shot(s, sp, work_dir=shots_dir / "_panels", progress=log)
        shot_paths.append(sp)

    if silent_video_path.exists() and not force:
        log(f"[stage5] reusing {silent_video_path.name}")
    else:
        log(f"[stage5] concatenating {len(shot_paths)} shots → {silent_video_path.name}")
        _concat(shot_paths, silent_video_path)

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
    sub_filter = f"subtitles='{captions}'"
    if fonts_dir.exists():
        sub_filter += f":fontsdir='{fonts_dir}'"
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
