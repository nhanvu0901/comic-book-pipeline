#!/usr/bin/env python3
"""
Inspect the latest project with a rendered video (final.mp4 or audio.wav),
extract duration, narrative style, mood, and music state, then construct
a precision-engineered prompt for Stable Audio 3 on Hugging Face.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LOCAL_PROJECTS = REPO_ROOT / "projects"


def find_latest_project_local() -> Path | None:
    if not LOCAL_PROJECTS.exists():
        return None
    candidates = []
    for p in LOCAL_PROJECTS.iterdir():
        if not p.is_dir():
            continue
        final_mp4 = p / "final.mp4"
        audio_wav = p / "audio.wav"
        if final_mp4.exists():
            candidates.append((final_mp4.stat().st_mtime, p))
        elif audio_wav.exists():
            candidates.append((audio_wav.stat().st_mtime, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def get_remote_project_info() -> dict | None:
    """Fetch info from remote Windows winbox-lan if local is empty."""
    # Find latest directory containing final.mp4 or audio.wav
    find_cmd = [
        "ssh", "winbox-lan",
        "powershell -NoProfile -Command \""
        "$item = Get-ChildItem -Path D:\\code\\comic-book-pipeline\\projects -Filter final.mp4 -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1;"
        "if (-not $item) { $item = Get-ChildItem -Path D:\\code\\comic-book-pipeline\\projects -Filter audio.wav -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1; }"
        "if ($item) { Write-Output $item.DirectoryName }\""
    ]
    try:
        res = subprocess.run(find_cmd, capture_output=True, text=True, errors="replace", timeout=12)
        dir_path = res.stdout.strip().splitlines()[-1].strip() if res.stdout.strip() else ""
        if not dir_path or "DirectoryName" in dir_path:
            return None
        
        project_name = dir_path.split("\\")[-1]
        out = {"name": project_name}

        for fname in ["music.json", "comic_context.json", "narration.json"]:
            read_cmd = [
                "ssh", "winbox-lan",
                f'powershell -NoProfile -Command "if (Test-Path \'{dir_path}\\{fname}\') {{ Get-Content \'{dir_path}\\{fname}\' -Raw }}"'
            ]
            r = subprocess.run(read_cmd, capture_output=True, text=True, errors="replace", timeout=10)
            content = r.stdout.strip()
            if content.startswith("{"):
                try:
                    out[fname] = json.loads(content)
                except Exception:
                    pass
        return out
    except Exception as e:
        sys.stderr.write(f"Warning: remote probe failed: {e}\n")
    return None


def generate_stable_audio_prompt(info: dict) -> dict:
    music = info.get("music.json") or {}
    comic = info.get("comic_context.json") or {}
    narration = info.get("narration.json") or {}

    duration = music.get("duration_seconds")
    if not duration:
        scenes = narration.get("scenes", [])
        if scenes:
            wps = float(narration.get("words_per_second") or 2.88)
            word_count = sum(len(s.get("text", "").split()) for s in scenes)
            duration = round(word_count / wps, 2)
        else:
            duration = 75.0

    genre = music.get("genre") or "minimal dark cinematic"
    minimax = music.get("minimax_state") or {}
    meta = minimax.get("global_meta") or ""
    arrangement = minimax.get("arrangement") or ""
    
    # Extract BPM and Key if present
    bpm = 68
    key = "C minor"
    if "bpm is" in meta.lower():
        try:
            bpm_part = meta.lower().split("bpm is")[1].split(".")[0].strip()
            bpm = int("".join(filter(str.isdigit, bpm_part)))
        except Exception:
            pass
    if "key is" in meta.lower():
        try:
            key_part = meta.split("key is")[1].split(".")[0].strip()
            key = key_part.rstrip(",")
            if "scale is minor" in meta.lower() and "minor" not in key.lower():
                key += " minor"
        except Exception:
            pass

    # Extract primary instruments
    instruments = []
    text_blob = (meta + " " + arrangement).lower()
    if "cello" in text_blob:
        instruments.append("solo cello with deep mournful tone")
    if "piano" in text_blob:
        instruments.append("felt upright piano in low register")
    if "synth" in text_blob:
        instruments.append("dark evolving analog synth pads")
    if "sub-bass" in text_blob:
        instruments.append("deep cinematic sub-bass")
    if "808" in text_blob or "kick" in text_blob:
        instruments.append("sparse slow 808 heartbeat pulse")
    if not instruments:
        instruments = [
            "deep atmospheric cello",
            "felt upright piano",
            "warm sub-bass",
            "ambient synth drones",
            "subtle cinematic percussion"
        ]

    # Mood & Atmospheric tags
    moods = [
        "cold introspective tension",
        "brooding psychological atmosphere",
        "cavernous wide soundstage",
        "heavy analog saturation",
        "long dark reverbs",
        "slow emotional buildup",
        "film score soundtrack"
    ]

    instruments_str = ", ".join(instruments)
    moods_str = ", ".join(moods)

    # Core prompt construction following Stable Audio 3 official prompting standards
    positive_prompt = (
        f"{genre.title()} score, {instruments_str}, {moods_str}, "
        f"{bpm} BPM, {key}, dynamic range master, "
        f"instrumental, no vocals, no singing, no speech"
    )

    negative_prompt = (
        "vocals, singing, human voice, speech, spoken word, choir, vocal chops, acapella, "
        "talking, whispering, distortion, clipping, muffled, low quality, noise, harsh frequencies"
    )

    target_duration_sec = int(round(duration))

    return {
        "project_name": info.get("name") or "latest_project",
        "topic": comic.get("title") or narration.get("title") or info.get("name"),
        "target_duration_seconds": target_duration_sec,
        "exact_duration_float": round(duration, 2),
        "bpm": bpm,
        "key": key,
        "genre": genre,
        "positive_prompt": positive_prompt,
        "negative_prompt": negative_prompt,
        "recommended_steps": 8,
        "recommended_cfg": 1.0,
        "huggingface_space_url": "https://huggingface.co/spaces/stabilityai/stable-audio-3"
    }


def main():
    parser = argparse.ArgumentParser(description="Generate Stable Audio 3 prompts from latest project.")
    parser.add_argument("--project", default=None, help="Project name (optional, defaults to latest)")
    args = parser.parse_args()

    project_info = None

    if args.project:
        local_p = LOCAL_PROJECTS / args.project
        if local_p.exists():
            project_info = {"name": args.project}
            for fname in ["music.json", "comic_context.json", "narration.json"]:
                fpath = local_p / fname
                if fpath.exists():
                    try:
                        project_info[fname] = json.load(open(fpath, encoding="utf-8"))
                    except Exception:
                        pass

    if not project_info:
        local_latest = find_latest_project_local()
        if local_latest:
            project_info = {"name": local_latest.name}
            for fname in ["music.json", "comic_context.json", "narration.json"]:
                fpath = local_latest / fname
                if fpath.exists():
                    try:
                        project_info[fname] = json.load(open(fpath, encoding="utf-8"))
                    except Exception:
                        pass

    if not project_info:
        # Check remote Windows machine
        project_info = get_remote_project_info()

    if not project_info or not project_info.get("name"):
        sys.stderr.write("Error: Could not find any project with final.mp4 or audio.wav.\n")
        sys.exit(1)

    result = generate_stable_audio_prompt(project_info)

    print("=" * 76)
    print(f"🎵 STABLE AUDIO 3 MUSIC PROMPT FOR: {result['project_name']}")
    print(f"🎯 Target Duration: {result['target_duration_seconds']}s (exact: {result['exact_duration_float']}s)")
    print(f"🎹 Tempo & Key: {result['bpm']} BPM · {result['key']}")
    print(f"🎼 Genre: {result['genre']}")
    print("=" * 76)
    print("\n👉 [POSITIVE PROMPT] (Copy & paste to Prompt field in Hugging Face):")
    print(result["positive_prompt"])
    print("\n👉 [NEGATIVE PROMPT] (Copy & paste to Negative Prompt field in Hugging Face):")
    print(result["negative_prompt"])
    print("\n⚙️ [RECOMMENDED HUGGING FACE SETTINGS]:")
    print(f"• Duration: {result['target_duration_seconds']} seconds (drag slider to {result['target_duration_seconds']}s)")
    print(f"• Steps: {result['recommended_steps']} (or default 8)")
    print(f"• CFG Scale: {result['recommended_cfg']} (or up to 7.0 for strict prompt adherence)")
    print(f"• Hugging Face Space: {result['huggingface_space_url']}")
    print("=" * 76)


if __name__ == "__main__":
    main()
