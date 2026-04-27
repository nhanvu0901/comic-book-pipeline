"""
Stage 5 orchestrator: load Stage-1..4 outputs → Ken Burns clips → concat →
overlay captions + audio → final.mp4.
"""
import json
import shutil
from pathlib import Path

from config import GDRIVE_BASE, get_project_dirs
from .assembler import concat_clips, finalize, render_scene_clip, require_ffmpeg
from .captions import render_caption_pngs
from .scene_planner import load_preprocessed_pages, plan_scenes


def assemble_project(
    project_name: str,
    *,
    keep_intermediates: bool = False,
) -> Path:
    """
    Full Stage 5 pipeline. Returns the path to final.mp4.
    """
    require_ffmpeg()

    root = GDRIVE_BASE / project_name
    audio = root / "audio.wav"
    scenes_file = root / "scene_timings.json"
    caps_file = root / "caption_chunks.json"

    for req in (audio, scenes_file, caps_file):
        if not req.exists():
            raise FileNotFoundError(f"missing {req.name} for project {project_name!r}. "
                                    f"Run Stage 4 first.")

    pages = load_preprocessed_pages(root)
    if not pages:
        raise RuntimeError(f"No preprocessed pages found for {project_name}. Run Stage 2.")

    scenes = json.loads(scenes_file.read_text())
    chunks = json.loads(caps_file.read_text())
    plans = plan_scenes(scenes, pages)
    if not plans:
        raise RuntimeError("scene_planner produced no VisualPlans — check that "
                           "scene_timings.page_ref values match preprocessed page_number.")

    work = root / "_stage5"
    work.mkdir(parents=True, exist_ok=True)
    clips_dir = work / "clips"
    captions_dir = work / "captions"

    # 1. per-scene Ken Burns clips
    print(f"[stage5] rendering {len(plans)} scene clips…")
    clip_paths: list[Path] = []
    for p in plans:
        clip = clips_dir / f"clip_{p.scene_id:03d}.mp4"
        render_scene_clip(p, clip)
        clip_paths.append(clip)
        print(f"[stage5]   scene {p.scene_id:02d}: {p.duration:.2f}s → {clip.name}")

    # 2. concat
    silent_video = work / "silent.mp4"
    print(f"[stage5] concatenating clips → {silent_video.name}")
    concat_clips(clip_paths, silent_video)

    # 3. caption PNGs
    print(f"[stage5] rendering {len(chunks)} caption PNGs…")
    captions = render_caption_pngs(chunks, captions_dir)

    # 4. final compose (video + audio + overlay)
    final_path = root / "final.mp4"
    print(f"[stage5] composing final → {final_path.name}")
    finalize(silent_video, audio, captions, final_path)

    if not keep_intermediates:
        shutil.rmtree(work, ignore_errors=True)

    print(f"[stage5] done: {final_path}")
    return final_path
