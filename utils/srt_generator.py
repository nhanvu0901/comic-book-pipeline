"""
SRT subtitle generator.
Creates scene-level SRT from script JSON + audio durations.
Also supports word-level SRT from Whisper output.
"""
import json
import os
from pathlib import Path


def format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_scene_level_srt(
    script: dict,
    audio_durations: dict[int, float],
    gap: float = 0.1,
) -> str:
    """
    Generate scene-level SRT from script and per-scene audio durations.

    Args:
        script: The full script JSON with "scenes" list
        audio_durations: {scene_id: duration_in_seconds}
        gap: Small gap between subtitles (seconds)

    Returns:
        SRT formatted string
    """
    entries = []
    current_time = 0.0

    for scene in script["scenes"]:
        sid = scene["scene_id"]
        narration = scene["narration"]
        duration = audio_durations.get(sid, scene.get("duration_seconds", 8))

        start = format_srt_time(current_time)
        end = format_srt_time(current_time + duration - gap)

        # Wrap long lines (max ~45 chars per line for readability)
        wrapped = _wrap_subtitle_text(narration, max_chars=45)

        entries.append(f"{sid}\n{start} --> {end}\n{wrapped}\n")
        current_time += duration

    return "\n".join(entries)


def generate_srt_from_script_only(script: dict) -> str:
    """
    Generate SRT using only the estimated durations in the script.
    Use this when you don't have actual audio files yet.
    """
    durations = {}
    for scene in script["scenes"]:
        sid = scene["scene_id"]
        # Estimate: ~3 words per second for narration
        word_count = len(scene["narration"].split())
        estimated_duration = max(3.0, word_count / 3.0)
        durations[sid] = estimated_duration
    
    return generate_scene_level_srt(script, durations)


def generate_srt_from_audio_files(script: dict, audio_dir: str) -> str:
    """
    Generate SRT by measuring actual audio file durations.
    Expects files named: scene_01.wav, scene_02.wav, etc.
    """
    from pydub import AudioSegment

    durations = {}
    for scene in script["scenes"]:
        sid = scene["scene_id"]
        audio_path = os.path.join(audio_dir, f"scene_{sid:02d}.wav")
        
        if os.path.exists(audio_path):
            audio = AudioSegment.from_file(audio_path)
            durations[sid] = len(audio) / 1000.0  # ms to seconds
        else:
            # Fallback to word-count estimate
            word_count = len(scene["narration"].split())
            durations[sid] = max(3.0, word_count / 3.0)
            print(f"  ⚠️  Audio not found for scene {sid}, using estimate: {durations[sid]:.1f}s")

    return generate_scene_level_srt(script, durations)


def parse_whisper_to_srt(whisper_json_path: str) -> str:
    """
    Convert Whisper JSON output (word-level timestamps) to SRT.
    This is the optional upgrade path when using Whisper on Colab.
    
    Expects Whisper output format with segments:
    {"segments": [{"start": 0.0, "end": 2.5, "text": "..."}, ...]}
    """
    with open(whisper_json_path) as f:
        data = json.load(f)

    entries = []
    for i, seg in enumerate(data.get("segments", []), 1):
        start = format_srt_time(seg["start"])
        end = format_srt_time(seg["end"])
        text = seg["text"].strip()
        wrapped = _wrap_subtitle_text(text, max_chars=45)
        entries.append(f"{i}\n{start} --> {end}\n{wrapped}\n")

    return "\n".join(entries)


def save_srt(srt_content: str, output_path: str):
    """Save SRT content to file."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    print(f"  📝 SRT saved: {output_path}")


def _wrap_subtitle_text(text: str, max_chars: int = 45) -> str:
    """Wrap text into lines of max_chars for subtitle readability."""
    words = text.split()
    lines = []
    current_line = []
    current_len = 0

    for word in words:
        if current_len + len(word) + 1 > max_chars and current_line:
            lines.append(" ".join(current_line))
            current_line = [word]
            current_len = len(word)
        else:
            current_line.append(word)
            current_len += len(word) + 1

    if current_line:
        lines.append(" ".join(current_line))

    return "\n".join(lines)
