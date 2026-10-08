"""Independent Orchestrator CLI for screen_qa Mode.

Processes a screen lore question end-to-end to projects/<p>/final.mp4:
1. Screen research (screen_context.json)
2. Clip search & section download (review/clips/)
3. Screen narration (narration.json citing Title and Year)
4. Stage 4 TTS (audio.wav)
5. Screen shot builder & 4-tier fallback renderer
6. Assembly to final.mp4
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import config
from stages.stage_1.screen_research import research_screen, build_screen_context
from stages.stage_3.screen_narration import write_screen_narration
from stages.stage_4.pipeline import synthesize_project
from stages.stage_5.screen_shots import build_screen_shots, render_screen_shot, assemble_screen_video
from stages.clip_fetch import youtube_search, fetch_clip_section

logger = logging.getLogger(__name__)
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def _fetch_screen_clips(project_name: str, *, log: Callable[[str], None] = logger.info) -> int:
    """Fetch video clip sections for beats described in screen_context.json."""
    proj_dir = Path(config.PROJECTS_ROOT) / project_name
    ctx_file = proj_dir / "screen_context.json"
    if not ctx_file.exists():
        return 0

    ctx_data = json.loads(ctx_file.read_text("utf-8"))
    items = ctx_data.get("items") or []
    clips_dir = proj_dir / "review" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    clips_manifest: list[dict] = []
    count = 0

    for idx, it in enumerate(items, 1):
        beat_key = str(idx)
        query = it.get("clip_query") or f"{it.get('drawable_moment')} clip"
        log(f"[screen-pipeline] searching clips for beat {beat_key}: {query!r}")

        hits = []
        try:
            hits = youtube_search(query)
        except Exception as exc:
            log(f"[screen-pipeline] YouTube search failed: {exc}")

        if not hits:
            continue

        best_hit = hits[0]
        vid = best_hit.get("id") or "clip"
        out_clip_file = clips_dir / f"{vid}.mp4"

        # Attempt downloading a 15-second section
        fetched = False
        if not out_clip_file.exists():
            try:
                fetch_clip_section(
                    video_id=vid,
                    start_sec=10.0,
                    beat_dur=4.0,
                    out_path=out_clip_file,
                )
                fetched = True
            except Exception as exc:
                log(f"[screen-pipeline] clip download failed for {vid}: {exc}")
        else:
            fetched = True

        if fetched and out_clip_file.exists():
            clips_manifest.append({
                "id": vid,
                "file": f"review/clips/{vid}.mp4",
                "beat": beat_key,
                "start": 0.0,
                "end": 4.0,
                "source_url": f"https://www.youtube.com/watch?v={vid}",
                "enabled": True,
            })
            count += 1

    manifest_file = clips_dir / "clips.json"
    manifest_file.write_text(json.dumps({"clips": clips_manifest}, indent=2), "utf-8")
    log(f"[screen-pipeline] recorded {count} clip(s) into {manifest_file}")
    return count


def _merge_audio_video(video_silent: Path, audio_wav: Path, out_final: Path) -> Path:
    """Merge silent video and narration audio into final.mp4."""
    out_final.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y",
        "-i", str(video_silent),
        "-i", str(audio_wav),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_final),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_final


def run_screen_pipeline(
    question: str,
    project_name: str,
    *,
    atempo: float = 1.30,
    skip_research: bool = False,
    skip_tts: bool = False,
    log: Callable[[str], None] = logger.info,
) -> Path:
    """Run full screen_qa pipeline from question to final.mp4."""
    proj_dir = Path(config.PROJECTS_ROOT) / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)

    # 1. Research
    ctx_file = proj_dir / "screen_context.json"
    if not skip_research or not ctx_file.exists():
        log(f"[screen-pipeline] 1. Researching screen lore...")
        research_data = research_screen(question, log=log)
        build_screen_context(question, research_data, project_name, log=log)

    # 2. Fetch clips
    log(f"[screen-pipeline] 2. Searching and fetching clips...")
    _fetch_screen_clips(project_name, log=log)

    # 3. Narrate
    nar_file = proj_dir / "narration.json"
    if not nar_file.exists():
        log(f"[screen-pipeline] 3. Writing screen narration...")
        write_screen_narration(project_name, log=log)

    # 4. TTS
    audio_wav = proj_dir / "audio.wav"
    if not skip_tts or not audio_wav.exists():
        log(f"[screen-pipeline] 4. Synthesizing narration audio...")
        try:
            synthesize_project(project_name, post_atempo=atempo, force=True, skip_review=True)
        except Exception as exc:
            log(f"[screen-pipeline] TTS error: {exc}")

    # 5. Build & render shots
    log(f"[screen-pipeline] 5. Building and rendering screen shots...")
    shots = build_screen_shots(project_name, log=log)
    shots_dir = proj_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    shot_paths: list[Path] = []
    for s in shots:
        out_p = shots_dir / f"shot_{s.shot_id:03d}.mp4"
        render_screen_shot(s, out_p, log=log)
        shot_paths.append(out_p)

    # 6. Assemble
    log(f"[screen-pipeline] 6. Assembling final video...")
    video_silent = proj_dir / "video_silent.mp4"
    assemble_screen_video(shots, shot_paths, video_silent, log=log)

    out_final = proj_dir / "final.mp4"
    if audio_wav.exists():
        _merge_audio_video(video_silent, audio_wav, out_final)
    else:
        shutil.copyfile(video_silent, out_final)

    log(f"[screen-pipeline] Completed! Output video: {out_final}")
    return out_final


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m stages.screen_pipeline",
        description="screen_qa mode: question -> film/show video Short end-to-end.",
    )
    parser.add_argument("--question", default="", help="The question about screen lore to answer.")
    parser.add_argument("--project", required=True, help="Project name under projects/.")
    parser.add_argument("--atempo", type=float, default=1.30, help="TTS tempo (default 1.30).")
    parser.add_argument("--skip-research", action="store_true", help="Reuse existing screen_context.json.")
    parser.add_argument("--skip-tts", action="store_true", help="Reuse existing audio.wav.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_screen_pipeline(
        args.question,
        args.project,
        atempo=args.atempo,
        skip_research=args.skip_research,
        skip_tts=args.skip_tts,
        log=print,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
