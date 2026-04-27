"""
Clone a voice on Cartesia from a local audio clip.

POST https://api.cartesia.ai/voices/clone  (multipart/form-data)

The cloned voice is persisted to your Cartesia voice library and the returned
`id` can be passed straight to `voice.id` in the TTS endpoint.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


_CLONE_URL = "https://api.cartesia.ai/voices/clone"
_CARTESIA_VERSION = "2024-11-13"


def clone_voice(
    clip_path: str | Path,
    *,
    name: str,
    api_key: str,
    description: str = "",
    language: str = "en",
    mode: str = "similarity",  # "similarity" (closer match) or "stability" (more robust)
    enhance: bool = False,
    timeout: int = 300,
) -> dict:
    clip_path = Path(clip_path)
    if not clip_path.is_file():
        raise FileNotFoundError(clip_path)

    headers = {
        "X-API-Key": api_key,
        "Cartesia-Version": _CARTESIA_VERSION,
    }
    data = {
        "name": name,
        "description": description,
        "language": language,
        "mode": mode,
        "enhance": "true" if enhance else "false",
    }
    with clip_path.open("rb") as fh:
        files = {"clip": (clip_path.name, fh, "audio/wav")}
        r = requests.post(_CLONE_URL, headers=headers, data=data,
                          files=files, timeout=timeout)

    if r.status_code != 200:
        raise RuntimeError(f"Cartesia clone failed {r.status_code}: {r.text[:600]}")
    return r.json()


def _main() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    p = argparse.ArgumentParser(description="Clone a voice on Cartesia.")
    p.add_argument("clip", help="Path to a clean voice sample (wav/mp3, 5–60s).")
    p.add_argument("--name", required=True, help="Name to save in your voice library.")
    p.add_argument("--description", default="", help="Optional description.")
    p.add_argument("--language", default="en")
    p.add_argument("--mode", default="similarity", choices=["similarity", "stability"])
    p.add_argument("--enhance", action="store_true",
                   help="Let Cartesia denoise/clean the clip before cloning.")
    args = p.parse_args()

    api_key = os.environ.get("CARTESIA_KEY") or os.environ.get("CARTESIA_API_KEY")
    if not api_key:
        print("CARTESIA_KEY missing — set it in .env", file=sys.stderr)
        return 1

    result = clone_voice(
        args.clip,
        name=args.name,
        description=args.description,
        language=args.language,
        mode=args.mode,
        enhance=args.enhance,
        api_key=api_key,
    )
    print(json.dumps(result, indent=2))
    voice_id = result.get("id")
    if voice_id:
        print(f"\nSaved to voice library. voice_id = {voice_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
