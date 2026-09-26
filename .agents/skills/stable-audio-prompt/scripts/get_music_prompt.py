#!/usr/bin/env python3
"""
Inspect the latest project with a rendered video (final.mp4 or audio.wav),
extract duration, narrative style, mood, and music state, then construct
precision-engineered prompts for Stable Audio 3 on Hugging Face, including
the signature Galactus Dark Drift Phonk style and Minimal Dark Cinematic style.
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


def generate_stable_audio_prompts(info: dict) -> dict:
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
            duration = 71.0

    target_duration_sec = int(round(duration))
    topic = comic.get("title") or narration.get("title") or info.get("name")

    # 1. Galactus Signature Style: Dark Aggressive Drift Phonk (140 BPM)
    phonk_prompt = (
        "A dark aggressive drift phonk instrumental at 140 BPM. "
        "Heavy distorted 808 sub bass glides, menacing detuned cowbell lead melody, "
        "fast rolling trap hi-hats, punchy kick drums, and dark cosmic synth atmospheres. "
        "The track begins with an ominous space drone and eerie bells, building tension "
        "before dropping into a relentless, high-energy phonk beat with thundering bass. "
        "Epic, cinematic, triumphant, strictly instrumental, no vocals, high production quality, "
        "clean punchy mix, wide stereo image."
    )

    phonk_short_prompt = (
        "Dark cosmic drift phonk instrumental, 140 BPM, aggressive distorted 808 bass, "
        "punchy trap drums, fast hi-hats, eerie detuned cowbell riff, cinematic cosmic tension, "
        "epic climax, purely instrumental, no voices, polished studio production."
    )

    phonk_negative = (
        "vocals, voice, singing, spoken words, speech, humming, choir, "
        "muddy low-end, muffled mix, noisy distortion, low fidelity, out of tune"
    )

    # 2. Cinematic Score Style (Minimal Dark Cinematic - 68 BPM)
    cinematic_prompt = (
        "Minimal Dark Cinematic score, deep atmospheric cello, felt upright piano, "
        "warm sub-bass, ambient synth drones, subtle cinematic percussion, "
        "cold introspective tension, brooding psychological atmosphere, cavernous wide soundstage, "
        "heavy analog saturation, long dark reverbs, slow emotional buildup, film score soundtrack, "
        "68 BPM, C minor, dynamic range master, instrumental, no vocals, no singing, no speech"
    )

    cinematic_negative = (
        "vocals, singing, human voice, speech, spoken word, choir, vocal chops, acapella, "
        "talking, whispering, distortion, clipping, muffled, low quality, noise, harsh frequencies"
    )

    return {
        "project_name": info.get("name") or "latest_project",
        "topic": topic,
        "target_duration_seconds": target_duration_sec,
        "exact_duration_float": round(duration, 2),
        "phonk_prompt": phonk_prompt,
        "phonk_short_prompt": phonk_short_prompt,
        "phonk_negative": phonk_negative,
        "cinematic_prompt": cinematic_prompt,
        "cinematic_negative": cinematic_negative,
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
        project_info = get_remote_project_info()

    if not project_info or not project_info.get("name"):
        sys.stderr.write("Error: Could not find any project with final.mp4 or audio.wav.\n")
        sys.exit(1)

    result = generate_stable_audio_prompts(project_info)

    print("=" * 76)
    print(f"🎵 STABLE AUDIO 3 MUSIC PROMPTS FOR: {result['project_name']}")
    print(f"🎯 Target Duration: {result['target_duration_seconds']}s (exact: {result['exact_duration_float']}s)")
    print("=" * 76)
    print("\n🔥 [STYLE 1: GALACTUS SIGNATURE - DARK AGGRESSIVE DRIFT PHONK (140 BPM)]")
    print("👉 Positive Prompt (Full):")
    print(result["phonk_prompt"])
    print("\n👉 Positive Prompt (Short & Punchy):")
    print(result["phonk_short_prompt"])
    print("\n👉 Negative Prompt:")
    print(result["phonk_negative"])
    print("\n⚙️ Settings: Duration: " + str(result['target_duration_seconds']) + "s | Steps: 50 | CFG: 7.0 - 8.0")
    print("-" * 76)
    print("\n🎻 [STYLE 2: MINIMAL DARK CINEMATIC - BROODING CELLO & PIANO (68 BPM)]")
    print("👉 Positive Prompt:")
    print(result["cinematic_prompt"])
    print("\n👉 Negative Prompt:")
    print(result["cinematic_negative"])
    print("\n⚙️ Settings: Duration: " + str(result['target_duration_seconds']) + "s | Steps: 8-12 | CFG: 1.0 - 7.0")
    print("=" * 76)
    print(f"🔗 Hugging Face Space: {result['huggingface_space_url']}")
    print("=" * 76)


if __name__ == "__main__":
    main()
