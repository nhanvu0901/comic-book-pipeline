"""LLM-directed MiniMax score orchestration for the completed Stage 5 video."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from config import (HF_MUSIC_GUIDANCE, HF_MUSIC_HEADROOM, HF_MUSIC_SPACE, HF_MUSIC_STEPS,
                    MUSIC_BRIEF_MODELS, MUSIC_GENRE, PROJECTS_ROOT)


_REQUIRED_STATE = ("description", "lyrics", "global_meta", "vocals", "arrangement")
_BRIEF_SYSTEM = """You are a music supervisor for a narrated comic video. Analyse the supplied
final narration, dramatic beats, genre preference and exact final-video duration. Write the
complete MiniMax Music 3 Studio state; do not write a generic reusable prompt.

The score is instrumental and must leave room for continuous TTS narration. It must contain
section tags in `lyrics` even though it has no singing. `global_meta` must specify coherent
genre, tempo, key and emotional progression. `arrangement` must describe instrument entries,
exits and the ending near the requested duration. `vocals` must explicitly forbid voices,
singing, chanting and spoken words.

Return strict JSON only:
{"genre":"<the selected genre preference, preserved verbatim>","minimax_state":{
"mode":"studio","description":"...","instrumental":true,"title":"...",
"lyrics":"[intro]\\n[instrumental]\\n...","global_meta":"...","vocals":"...",
"arrangement":"..."},"reasoning":"<why this score follows this narration and beat map>"}"""


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value) -> str:
    data = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _script_of(narration: dict) -> str:
    return "\n".join(str(scene.get("text") or "").strip()
                     for scene in narration.get("scenes") or [] if scene.get("text"))


def _parse_object(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _valid_state(value) -> dict | None:
    if not isinstance(value, dict) or value.get("instrumental") is not True:
        return None
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in _REQUIRED_STATE):
        return None
    if "[" not in value["lyrics"] or "instrumental" not in value["lyrics"].lower():
        return None
    if not all(word in value["vocals"].lower() for word in ("no", "vocal")):
        return None
    return {"mode": "studio", "instrumental": True,
            "title": str(value.get("title") or "Comic score").strip(),
            **{key: value[key].strip() for key in _REQUIRED_STATE}}


def brief_identity(doc: dict) -> str:
    """Identity includes every source and remote setting that can alter a score."""
    return _sha256({
        "source_identity": doc.get("source_identity"),
        "state": doc.get("minimax_state"), "space": HF_MUSIC_SPACE,
        "steps": HF_MUSIC_STEPS, "guidance": HF_MUSIC_GUIDANCE, "headroom": HF_MUSIC_HEADROOM,
    })


def _source_identity(*, genre: str, duration_seconds: float,
                     narration_sha256: str, beats_sha256: str) -> str:
    return _sha256({
        "genre": genre, "duration_seconds": round(float(duration_seconds), 3),
        "narration_sha256": narration_sha256, "beats_sha256": beats_sha256,
        "space": HF_MUSIC_SPACE, "steps": HF_MUSIC_STEPS,
        "guidance": HF_MUSIC_GUIDANCE, "headroom": HF_MUSIC_HEADROOM,
    })


def build_brief(project: str, duration_seconds: float, *, log=print) -> dict | None:
    """Ask the LLM to write a validated MiniMax Studio state for the final MP4 duration."""
    root = PROJECTS_ROOT / project
    narration_path = root / "narration.json"
    narration = _load_json(narration_path)
    script = _script_of(narration)
    if not script or duration_seconds <= 0:
        log("[music] final narration or video duration unavailable — narration-only")
        return None
    existing = _load_json(root / "music.json")
    genre = str(existing.get("genre") or MUSIC_GENRE).strip()
    beats = narration.get("beats") or []
    narration_sha256 = _sha256(narration_path.read_bytes())
    beats_sha256 = _sha256(beats)
    source_identity = _source_identity(
        genre=genre, duration_seconds=duration_seconds,
        narration_sha256=narration_sha256, beats_sha256=beats_sha256,
    )
    if (existing.get("source_identity") == source_identity
            and _valid_state(existing.get("minimax_state"))
            and existing.get("identity")):
        log("[music] reusing matching MiniMax brief")
        return existing
    user = (
        f"MODE: {narration.get('mode') or ''}\nTITLE: {narration.get('title') or ''}\n"
        f"GENRE PREFERENCE (preserve verbatim): {genre or 'none; choose from the story'}\n"
        f"EXACT FINAL VIDEO DURATION: {duration_seconds:.2f} seconds\n\n"
        f"FINAL NARRATION:\n{script}\n\nDRAMATIC BEATS:\n{_canonical(beats)}"
    )
    try:
        from .stage_3._llm import _call_with_deadline, _client
        client = _client()
    except Exception as exc:
        log(f"[music] LLM unavailable ({exc}) — narration-only")
        return None
    parsed = None
    used_model = None
    for model in MUSIC_BRIEF_MODELS:
        try:
            candidate = _parse_object(_call_with_deadline(client, model, _BRIEF_SYSTEM, user, 900))
        except Exception as exc:
            log(f"[music] {model} failed: {exc}")
            continue
        if candidate and _valid_state(candidate.get("minimax_state")):
            parsed, used_model = candidate, model
            break
        log(f"[music] {model} returned an invalid MiniMax brief")
    if parsed is None:
        log("[music] no usable MiniMax brief — narration-only")
        return None
    state = _valid_state(parsed["minimax_state"])
    doc = {
        "genre": genre or str(parsed.get("genre") or "").strip(),
        "duration_seconds": round(float(duration_seconds), 3),
        "narration_sha256": narration_sha256,
        "beats_sha256": beats_sha256,
        "model": used_model,
        "minimax_state": state,
        "reasoning": str(parsed.get("reasoning") or "").strip(),
    }
    doc["source_identity"] = _source_identity(
        genre=doc["genre"], duration_seconds=duration_seconds,
        narration_sha256=narration_sha256, beats_sha256=beats_sha256,
    )
    doc["identity"] = brief_identity(doc)
    (root / "music.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[music] MiniMax brief via {used_model}: {doc['genre']} for {duration_seconds:.2f}s")
    return doc


def generate_bed(project: str, brief: dict, *, log=print) -> Path | None:
    """Generate `bgm.mp3` only for this exact brief; stale music is never retained."""
    root = PROJECTS_ROOT / project
    out = root / "bgm.mp3"
    stamp = root / "bgm.identity.sha256"
    identity = str(brief.get("identity") or "")
    state = _valid_state(brief.get("minimax_state"))
    duration = float(brief.get("duration_seconds") or 0.0)
    if not identity or state is None or duration <= 0:
        out.unlink(missing_ok=True)
        stamp.unlink(missing_ok=True)
        log("[music] invalid MiniMax brief — narration-only")
        return None
    if out.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == identity:
        log("[music] reusing bgm.mp3 (matching final-video brief)")
        return out
    out.unlink(missing_ok=True)
    stamp.unlink(missing_ok=True)
    from .minimax_music import generate_music
    generated = generate_music(state, duration, out, log=log)
    if generated is None or not generated.is_file():
        out.unlink(missing_ok=True)
        log("[music] score unavailable — narration-only")
        return None
    stamp.write_text(identity, encoding="utf-8")
    return generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a MiniMax score for a completed video.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--duration", required=True, type=float)
    args = parser.parse_args(argv)
    brief = build_brief(args.project, args.duration)
    return 0 if brief and generate_bed(args.project, brief) else 1


if __name__ == "__main__":
    raise SystemExit(main())
