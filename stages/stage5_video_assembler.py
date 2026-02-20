"""
Stage 5: Video Assembler
Combines images (with Ken Burns effects), narration audio, background music,
and subtitles into a final 1920x1080 video.

Usage:
    python -m stages.stage5_video_assembler
    python -m stages.stage5_video_assembler --project "death_of_gwen_stacy"
    python -m stages.stage5_video_assembler --project "death_of_gwen_stacy" --bgm path/to/music.mp3
"""
import json
import sys
import os
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from moviepy.editor import (
    concatenate_videoclips,
    AudioFileClip,
    CompositeVideoClip,
    CompositeAudioClip,
    TextClip,
    ColorClip,
    afx,
)
from pydub import AudioSegment
import numpy as np

from config import (
    VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS, MAX_DURATION,
    BGM_VOLUME, AUDIO_FADE_IN, AUDIO_FADE_OUT,
    SUB_FONT_SIZE, SUB_FONT_COLOR, SUB_STROKE_COLOR, SUB_STROKE_WIDTH,
    SUB_MARGIN_BOTTOM, CROSSFADE_DURATION,
    GDRIVE_BASE, get_project_dirs,
)
from utils.kenburns import apply_kenburns
from utils.srt_generator import (
    generate_srt_from_audio_files,
    generate_srt_from_script_only,
    save_srt,
    format_srt_time,
)


def get_audio_duration(audio_path: str) -> float:
    """Get duration of an audio file in seconds."""
    audio = AudioSegment.from_file(audio_path)
    return len(audio) / 1000.0


def get_scene_durations(script: dict, audio_dir: str) -> dict[int, float]:
    """
    Get duration for each scene.
    Uses actual audio files if available, otherwise estimates from word count.
    """
    durations = {}
    for scene in script["scenes"]:
        sid = scene["scene_id"]
        # Try per-scene audio first
        for ext in [".wav", ".mp3", ".ogg"]:
            audio_path = os.path.join(audio_dir, f"scene_{sid:02d}{ext}")
            if os.path.exists(audio_path):
                durations[sid] = get_audio_duration(audio_path)
                break
        else:
            # Estimate from word count (~3 words/sec)
            words = len(scene["narration"].split())
            durations[sid] = max(3.0, words / 3.0)
    
    return durations


def concatenate_scene_audio(script: dict, audio_dir: str, output_path: str) -> str | None:
    """
    Concatenate per-scene audio files into one narration track.
    Returns path to concatenated file, or None if using pre-made narration.
    """
    # Check if full narration already exists
    for name in ["narration.mp3", "narration.wav"]:
        full_path = os.path.join(audio_dir, name)
        if os.path.exists(full_path):
            print(f"  📎 Using existing narration: {name}")
            return full_path

    # Concatenate per-scene files
    combined = AudioSegment.empty()
    found_any = False

    for scene in script["scenes"]:
        sid = scene["scene_id"]
        for ext in [".wav", ".mp3", ".ogg"]:
            audio_path = os.path.join(audio_dir, f"scene_{sid:02d}{ext}")
            if os.path.exists(audio_path):
                segment = AudioSegment.from_file(audio_path)
                combined += segment
                found_any = True
                break

    if not found_any:
        print("  ⚠️  No audio files found. Video will be created without narration.")
        return None

    combined.export(output_path, format="mp3")
    print(f"  🔗 Concatenated narration: {output_path}")
    return output_path


def build_subtitle_clips(script: dict, durations: dict[int, float]) -> list:
    """
    Create subtitle TextClips for each scene.
    Returns list of (clip, start_time) tuples.
    """
    sub_clips = []
    current_time = 0.0

    for scene in script["scenes"]:
        sid = scene["scene_id"]
        text = scene["narration"]
        duration = durations.get(sid, 5.0)

        # Wrap text for readability
        wrapped = _wrap_text(text, max_chars=50)

        try:
            txt_clip = (
                TextClip(
                    wrapped,
                    fontsize=SUB_FONT_SIZE,
                    color=SUB_FONT_COLOR,
                    stroke_color=SUB_STROKE_COLOR,
                    stroke_width=SUB_STROKE_WIDTH,
                    font="Arial-Bold",
                    method="caption",
                    size=(VIDEO_WIDTH - 200, None),
                    align="center",
                )
                .set_duration(duration - 0.2)
                .set_start(current_time + 0.1)
                .set_position(("center", VIDEO_HEIGHT - SUB_MARGIN_BOTTOM - 80))
            )
            sub_clips.append(txt_clip)
        except Exception as e:
            print(f"  ⚠️  Subtitle error for scene {sid}: {e}")

        current_time += duration

    return sub_clips


def assemble_video(
    project_name: str,
    bgm_path: str | None = None,
    include_subtitles: bool = True,
    preview: bool = False,
):
    """
    Assemble the final video from all components.

    Args:
        project_name: Name of the project folder in Google Drive
        bgm_path: Path to background music file (optional)
        include_subtitles: Whether to burn subtitles into video
        preview: If True, render at lower quality for speed
    """
    dirs = get_project_dirs(project_name)
    images_dir = str(dirs["images"])
    audio_dir = str(dirs["audio"])
    output_dir = str(dirs["output"])

    # Load script
    script_path = str(dirs["root"] / "script.json")
    if not os.path.exists(script_path):
        print(f"❌ Script not found: {script_path}")
        return
    
    with open(script_path) as f:
        script = json.load(f)

    print(f"\n🎬 Assembling video: {script.get('title', project_name)}")
    print(f"   Scenes: {len(script['scenes'])}")

    # ─── Step 1: Get scene durations ────────────────────────────────────────
    print("\n📏 Calculating scene durations...")
    durations = get_scene_durations(script, audio_dir)
    total_duration = sum(durations.values())
    print(f"   Total duration: {total_duration:.1f}s")

    if total_duration > MAX_DURATION:
        print(f"   ⚠️  Exceeds {MAX_DURATION}s limit. Scaling down durations.")
        scale = MAX_DURATION / total_duration
        durations = {k: v * scale for k, v in durations.items()}
        total_duration = sum(durations.values())

    # ─── Step 2: Build Ken Burns clips ──────────────────────────────────────
    print("\n🎞️  Generating Ken Burns clips...")
    scene_clips = []

    for scene in script["scenes"]:
        sid = scene["scene_id"]
        image_path = os.path.join(images_dir, f"scene_{sid:02d}.jpg")
        effect = scene.get("effect", "slow_zoom_in")
        duration = durations[sid]

        if not os.path.exists(image_path):
            print(f"   ⚠️  Missing image for scene {sid}, using black frame")
            clip = ColorClip(
                size=(VIDEO_WIDTH, VIDEO_HEIGHT),
                color=(0, 0, 0),
                duration=duration,
            )
        else:
            print(f"   Scene {sid}: {effect} ({duration:.1f}s)")
            clip = apply_kenburns(
                image_path=image_path,
                duration=duration,
                effect=effect,
                video_size=(VIDEO_WIDTH, VIDEO_HEIGHT),
                fps=VIDEO_FPS,
            )

        scene_clips.append(clip)

    # Concatenate all scene clips
    print("\n🔗 Concatenating scenes...")
    video = concatenate_videoclips(scene_clips, method="compose")
    video = video.set_fps(VIDEO_FPS)

    # ─── Step 3: Add narration audio ────────────────────────────────────────
    print("\n🎙️  Processing narration audio...")
    narration_path = concatenate_scene_audio(
        script, audio_dir, os.path.join(audio_dir, "narration_combined.mp3")
    )

    audio_tracks = []
    if narration_path:
        narration_clip = AudioFileClip(narration_path)
        # Trim to match video duration
        if narration_clip.duration > video.duration:
            narration_clip = narration_clip.subclip(0, video.duration)
        audio_tracks.append(narration_clip)
        print(f"   Narration: {narration_clip.duration:.1f}s")

    # ─── Step 4: Add background music ───────────────────────────────────────
    if bgm_path and os.path.exists(bgm_path):
        print(f"\n🎵 Adding background music: {bgm_path}")
        bgm_clip = AudioFileClip(bgm_path)

        # Loop BGM if shorter than video
        if bgm_clip.duration < video.duration:
            loops_needed = int(video.duration / bgm_clip.duration) + 1
            from moviepy.editor import concatenate_audioclips
            bgm_clip = concatenate_audioclips([bgm_clip] * loops_needed)

        # Trim to video length
        bgm_clip = bgm_clip.subclip(0, video.duration)

        # Apply volume and fades
        bgm_clip = bgm_clip.volumex(BGM_VOLUME)
        bgm_clip = afx.audio_fadein(bgm_clip, AUDIO_FADE_IN)
        bgm_clip = afx.audio_fadeout(bgm_clip, AUDIO_FADE_OUT)

        audio_tracks.append(bgm_clip)
        print(f"   BGM volume: {BGM_VOLUME}, fade in: {AUDIO_FADE_IN}s, fade out: {AUDIO_FADE_OUT}s")
    else:
        # Check for bgm.mp3 in project root
        default_bgm = str(dirs["root"] / "bgm.mp3")
        if os.path.exists(default_bgm):
            print(f"\n🎵 Found default BGM: bgm.mp3")
            return assemble_video(project_name, bgm_path=default_bgm, 
                                  include_subtitles=include_subtitles, preview=preview)

    # Combine audio tracks
    if audio_tracks:
        if len(audio_tracks) == 1:
            video = video.set_audio(audio_tracks[0])
        else:
            combined_audio = CompositeAudioClip(audio_tracks)
            video = video.set_audio(combined_audio)

    # ─── Step 5: Add subtitles ──────────────────────────────────────────────
    if include_subtitles:
        print("\n📝 Adding subtitles...")
        try:
            sub_clips = build_subtitle_clips(script, durations)
            if sub_clips:
                video = CompositeVideoClip([video] + sub_clips)
                print(f"   Added {len(sub_clips)} subtitle overlays")
        except Exception as e:
            print(f"   ⚠️  Subtitle overlay failed: {e}")
            print("   Continuing without burned-in subtitles.")

    # ─── Step 6: Generate SRT file ──────────────────────────────────────────
    print("\n📄 Generating SRT file...")
    srt_content = generate_srt_from_audio_files(script, audio_dir)
    if not any(
        os.path.exists(os.path.join(audio_dir, f"scene_{s['scene_id']:02d}.wav"))
        for s in script["scenes"]
    ):
        srt_content = generate_srt_from_script_only(script)
    
    srt_path = os.path.join(output_dir, "subtitles.srt")
    save_srt(srt_content, srt_path)

    # ─── Step 7: Export ─────────────────────────────────────────────────────
    output_path = os.path.join(output_dir, "final_video.mp4")
    print(f"\n🎬 Exporting video to: {output_path}")
    print(f"   Resolution: {VIDEO_WIDTH}x{VIDEO_HEIGHT} @ {VIDEO_FPS}fps")
    print(f"   Duration: {video.duration:.1f}s")
    print(f"   This may take a few minutes...\n")

    fps = 15 if preview else VIDEO_FPS

    video.write_videofile(
        output_path,
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        bitrate="5000k" if not preview else "2000k",
        preset="medium" if not preview else "ultrafast",
        threads=4,
        logger="bar",
    )

    # Cleanup
    video.close()
    for clip in scene_clips:
        clip.close()

    print(f"\n✅ Video assembled successfully!")
    print(f"   📹 Video: {output_path}")
    print(f"   📝 SRT:   {srt_path}")
    print(f"   📁 Output: {output_dir}")

    return output_path


def _wrap_text(text: str, max_chars: int = 50) -> str:
    """Wrap text for subtitle display."""
    words = text.split()
    lines, current = [], []
    length = 0
    for w in words:
        if length + len(w) + 1 > max_chars and current:
            lines.append(" ".join(current))
            current, length = [w], len(w)
        else:
            current.append(w)
            length += len(w) + 1
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stage 5: Video Assembler")
    parser.add_argument("--project", type=str, help="Project name (folder name in Google Drive)")
    parser.add_argument("--bgm", type=str, help="Path to background music file")
    parser.add_argument("--no-subs", action="store_true", help="Disable burned-in subtitles")
    parser.add_argument("--preview", action="store_true", help="Fast preview render (lower quality)")
    args = parser.parse_args()

    # If no project specified, list available ones
    if not args.project:
        print("Available projects:")
        if GDRIVE_BASE.exists():
            projects = sorted([
                d.name for d in GDRIVE_BASE.iterdir()
                if d.is_dir() and (d / "script.json").exists()
            ])
            if projects:
                for p in projects:
                    print(f"  - {p}")
                print(f"\nUsage: python -m stages.stage5_video_assembler --project \"{projects[0]}\"")
            else:
                print("  (none found)")
        else:
            print(f"  Projects path: {GDRIVE_BASE}")
        return

    print("=" * 70)
    print("STAGE 5: Video Assembler")
    print("=" * 70)

    assemble_video(
        project_name=args.project,
        bgm_path=args.bgm,
        include_subtitles=not args.no_subs,
        preview=args.preview,
    )


if __name__ == "__main__":
    main()
