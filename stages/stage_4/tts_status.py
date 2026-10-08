"""The ONE place the background-TTS status lives.

    projects/<project>/review/tts_status.json

Written only by stages.stage_4.background_tts.BackgroundTTSRunner; read by the review gate
(per-beat duration chips), the /moments_review web routes (the beat duration a pick downloads
for) and anything else that needs to know how long a beat is in the cached audio. The runner
used to write cache/tts/status.json while every reader looked in review/ — the readers saw
nothing — and then wrote BOTH. Don't open the file by path anywhere else; call these.

Shape (every key optional for a reader — use .get):

    {"completed": bool,                    # every chunk of the current narration is cached
     "beat_durations": {beat_key: seconds},   # review beat keys: "intro" | "outro" | "<sid>" | "<sid>:<frag>"
     "beat_windows":   {beat_key: [start, end]},   # absolute, only once the whole timeline is known
     "scene_durations": {scene_id: seconds},
     "chunks_total": int, "chunks_done": int,
     "narration_sha": str,                 # sha of the scene texts the numbers belong to
     "error": str | null}
"""
from __future__ import annotations

import json
from pathlib import Path

STATUS_REL = Path("review") / "tts_status.json"


def empty_status() -> dict:
    return {"completed": False, "beat_durations": {}, "scene_durations": {}}


def status_path(project_root: Path) -> Path:
    return Path(project_root) / STATUS_REL


def read_status(project_root: Path) -> dict:
    """The project's TTS status, or an empty one (never raises: a half-written / missing file just
    means 'nothing known yet')."""
    try:
        data = json.loads(status_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_status()
    return data if isinstance(data, dict) else empty_status()


def write_status(project_root: Path, status: dict) -> None:
    from utils.atomic_json import write_json_atomic
    write_json_atomic(status_path(project_root), status)


def beat_duration(project_root: Path, beat_key: str) -> float | None:
    """Seconds the cached audio gives `beat_key`, or None while it isn't known (TTS hasn't
    reached it yet)."""
    try:
        v = float((read_status(project_root).get("beat_durations") or {})[str(beat_key)])
    except (KeyError, TypeError, ValueError):
        return None
    return v if v > 0 else None
