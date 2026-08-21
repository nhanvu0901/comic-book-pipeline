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
# The tags MiniMax Music 3 was trained on (the Space's own _LYRICS_RULES). Anything else is an
# invented tag the model has never seen, so it is rejected rather than silently rendered.
_ALLOWED_LYRIC_TAGS = ("intro", "verse", "pre-chorus", "chorus", "post-chorus",
                       "bridge", "instrumental", "solo", "outro")
_TAG_LIST = " ".join(f"[{tag}]" for tag in _ALLOWED_LYRIC_TAGS)
# A caption budget of 250-400 words plus lyrics, title and reasoning does not fit the 900 tokens
# this call used to allow: measured 2026-08-21, deepseek-v4-flash was truncated mid-JSON at 900
# and at 2000 (unparseable, so the model looked "empty" or "invalid"), and returned a complete
# 3176-character object at 3000. A truncated answer costs the render its whole score, so the cap
# is set where a full answer measurably fits.
_BRIEF_MAX_TOKENS = 3000
# Mirrors the Space's _CAPTION_CONTRACT / _LYRICS_RULES verbatim in intent: those labels are the
# exact style the checkpoint was captioned with, so free-prose briefs waste the caption budget.
_BRIEF_SYSTEM = """You are a music supervisor for a narrated comic video. Analyse the supplied
final narration, dramatic beats, genre preference and exact final-video duration. Write the
complete MiniMax Music 3 Studio state; do not write a generic reusable prompt.

The score is instrumental and must leave room for continuous TTS narration: sparse, with audible
space between phrases, resolving just before the requested duration. Dense beds measure as
inaudible under speech, so density is a bug, not a style.

WHAT THE MODEL ACTUALLY READS: only `global_meta`, `vocals` and `arrangement`, joined in that
order, form the caption — plus `lyrics`. `description` never reaches the model; it is a project
label. Every musical instruction must therefore live in those three caption fields, which
together should run roughly 250-400 words: concrete and musical, an energy arc and instrument
lifecycles, never a static equipment list or decorative adjectives.

The caption fields use the exact labeled style the checkpoint was trained on, one paragraph each:
- `global_meta`: "Basic Attributes: bpm is <number>. key is <letter>, and scale is
  <major|minor>. <Genre / Subgenre>." then "Global Emotional Progression: <how the emotion
  evolves from the opening through the final section>." then "Application Scenarios & Imagery:
  <two or three vivid listening scenarios>." then "Sonics & Production Profile: <soundstage,
  frequency balance, dynamics, production character>."
- `vocals`: open with "Instrumental, no vocals." then name the instrument or texture carrying the
  lead melodic role, and forbid singing, chanting, whispering and spoken words.
- `arrangement`: "Instrument Lifecycle Description (Primary/Secondary Layering): Primary: <core
  instruments present start to finish>. Secondary: <instruments that enter, exit or intensify,
  and in which sections>." then "Groove & Foundation Progression: <how groove and low end
  develop, or stay absent>." then "Embellishments, Textures & Spatial FX: <textures, transitional
  gestures, stereo and space>." State what enters, exits or changes for every section tag, and
  describe how the piece ends.

`lyrics` carry no words at all. Use ONLY these tags — """ + _TAG_LIST + """ — each ALWAYS ALONE
on its own line, structured to fit the duration, including at least one [instrumental]. Tempo,
instruments and dynamics never belong in `lyrics`.

Return strict JSON only:
{"genre":"<the selected genre preference, preserved verbatim>","minimax_state":{
"mode":"studio","description":"<short project label, not a prompt>","instrumental":true,
"title":"...","lyrics":"[intro]\\n[instrumental]\\n...","global_meta":"...","vocals":"...",
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


def _lyrics_ok(lyrics: str) -> bool:
    """An instrumental score's lyrics are tags and nothing else.

    The Space requires every tag alone on its own line, and the checkpoint only knows the tags in
    _ALLOWED_LYRIC_TAGS. Since this pipeline only ever asks for instrumental scores, any line that
    is not a known bare tag is a sung word or an invented section — both reject the brief.
    """
    lines = [line.strip() for line in lyrics.splitlines() if line.strip()]
    if not lines:
        return False
    for line in lines:
        match = re.fullmatch(r"\[([^\[\]]+)\]", line)
        if match is None or match.group(1).strip().lower() not in _ALLOWED_LYRIC_TAGS:
            return False
    return True


def _valid_state(value) -> dict | None:
    if not isinstance(value, dict) or value.get("instrumental") is not True:
        return None
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in _REQUIRED_STATE):
        return None
    if not _lyrics_ok(value["lyrics"]) or "[instrumental]" not in value["lyrics"].lower():
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
            candidate = _parse_object(
                _call_with_deadline(client, model, _BRIEF_SYSTEM, user, _BRIEF_MAX_TOKENS))
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
