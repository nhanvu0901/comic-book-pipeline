"""
Stage 4 orchestrator: load narration.json → Cartesia TTS → align → persist.
"""
import json
import subprocess
import wave
from pathlib import Path

from config import (
    CARTESIA_MODEL,
    CARTESIA_VOICE_ID,
    FFMPEG_BIN,
    PROJECTS_ROOT,
    RESEMBLE_VOICE_UUID,
    TTS_PROVIDER,
)
from .chunker import align_scenes_to_words, build_caption_chunks, words_from_dicts
from .schema import TTSResult


# ── Emotional delivery (sonic-3 SSML) ──────────────────────────────────────
# A flat single emotion ("confident") makes the narrator sound like an even-paced
# documentary. Instead we pick a warmer BASE emotion per narration mode, then add
# a dynamic per-scene arc via inline <emotion> SSML tags. PROBE-VERIFIED: SSML
# tags do NOT leak into Cartesia word_timestamps and the spoken words are
# unchanged, so scene_timings / caption_chunks alignment stays identical.
# All values below are members of cartesia_tts.VALID_EMOTIONS.
_MODE_BASE_EMOTION = {
    "tragedy": "melancholic",
    "twist_reveal": "mysterious",
    "fun_fact": "curious",
    "feat": "confident",
    "power_ranking": "confident",
    "hot_take": "confident",
}
_DEFAULT_BASE_EMOTION = "contemplative"  # warm storyteller rest-tone (vs flat "confident")

# keyword (lowercased, punctuation-stripped) → Cartesia emotion. A scene whose
# text hits a cue gets that dramatic emotion; a scene with no hit inherits the
# base "rest" emotion, giving a natural rise/fall instead of a monotone.
_SCENE_EMOTION_CUES = {
    "sad": ("grief", "grieving", "mourned", "mourning", "wept", "weeping", "tears",
            "tearful", "sorrow", "heartbreak", "heartbroken", "broken", "loss", "mourns"),
    "scared": ("fear", "feared", "terror", "terrified", "horror", "horrified", "panic",
               "panicked", "fled", "flees", "fleeing", "trapped", "haunted", "nightmare",
               "nightmares", "dread", "monstrous", "monster"),
    "angry": ("rage", "raging", "fury", "furious", "wrath", "wrathful", "snarl", "snarled",
              "screams", "screamed", "outraged", "vengeance"),
    "triumphant": ("triumph", "triumphant", "victory", "victorious", "wins", "won", "prevailed"),
    "determined": ("vow", "vowed", "vows", "swore", "swears", "resolved", "determined",
                   "refuses", "refused", "fought", "unleashed", "drove"),
    "surprised": ("shocked", "stunned", "reveals", "revealed", "realizes", "realized",
                  "twist", "gasped", "astonished"),
}


def _base_emotion_for(narration: dict) -> str:
    mode = str(narration.get("mode", "")).strip().lower()
    return _MODE_BASE_EMOTION.get(mode, _DEFAULT_BASE_EMOTION)


def _scene_emotion(scene: dict, base: str) -> str:
    """Cartesia emotion for one scene: intro hook → curious; thematic outro →
    wistful (factual 'comic is' credit → base); dramatic keyword cue → that
    emotion; otherwise the base rest-tone."""
    text = str(scene.get("text", "")).lower()
    if scene.get("is_intro"):
        return "curious"
    if scene.get("is_outro"):
        return base if "comic is" in text else "wistful"
    words = {w.strip(",.!?:;\"'—-").lower() for w in text.split()}
    for emo, cues in _SCENE_EMOTION_CUES.items():
        if words & set(cues):
            return emo
    return base


def _build_emotional_transcript(scenes: list[dict], base_emotion: str, log) -> str:
    """Assemble the TTS transcript with per-scene <emotion> SSML tags + a short
    <break> before the outro. Each scene's text is normalized FIRST so tags are
    never mangled. A tag is emitted only when the emotion CHANGES (limits the
    experimental mid-generation shifts)."""
    parts: list[str] = []
    active: str | None = None
    shifts = 0
    for s in scenes:
        txt = _normalize_for_tts(str(s.get("text", "")).strip())
        if not txt:
            continue
        emo = _scene_emotion(s, base_emotion)
        seg = ""
        if s.get("is_outro"):
            seg += '<break time="300ms"/> '  # a beat of silence before the closing line
        if emo != active:
            seg += f'<emotion value="{emo}"/> '
            active = emo
            shifts += 1
        parts.append(seg + txt)
    log(f"[stage4] emotional transcript: base={base_emotion}, {shifts} emotion shift(s)")
    return " ".join(parts)


def synthesize_project(
    project_name: str,
    *,
    speed: float = 1.0,  # Cartesia speed param caps near 1.2; let atempo post-process do the tempo work
    volume: float = 1.0,
    emotion: str | None = None,  # BASE emotion; None → derived from narration mode (see _base_emotion_for)
    flat: bool = False,          # True → old single-emotion behavior, no per-scene SSML tags
    voice_id: str | None = None,
    model: str | None = None,
    post_atempo: float = 1.1,  # ffmpeg atempo — pitch-preserving tempo boost.
                                # 1.1 → calmer, more intelligible pace (user-chosen over
                                # the 1.3 channel benchmark). ~3.2 wps. Keep narration near
                                # ~225 words so a 1.1-paced video still lands under ~72s.
    force: bool = False,
) -> TTSResult:
    """Load narration.json, synthesize audio + timings via Cartesia, save all artifacts."""
    root = PROJECTS_ROOT / project_name
    narration_path = root / "narration.json"
    if not narration_path.exists():
        raise FileNotFoundError(f"narration.json missing: {narration_path}. Run Stage 3 first.")

    narration = json.loads(narration_path.read_text())
    scenes = narration.get("scenes") or []
    if not scenes:
        raise ValueError("narration.json has no scenes")
    selected_voice = voice_id  # may be overridden by Resemble auto-select below

    audio_path = root / "audio.wav"
    words_path = root / "word_timestamps.json"
    scenes_path = root / "scene_timings.json"
    captions_path = root / "caption_chunks.json"

    if audio_path.exists() and words_path.exists() and not force:
        print(f"[stage4] reusing existing audio.wav + word_timestamps.json "
              f"(pass --force to regenerate)")
        words = json.loads(words_path.read_text())
        duration = _wav_duration(audio_path)
    else:
        base_emotion = (emotion or _base_emotion_for(narration)).strip().lower()
        if TTS_PROVIDER == "resemble":
            from .resemble_tts import synthesize as _synthesize, select_voice
            # Resemble has no Cartesia <emotion> SSML — speak plain normalized text.
            full_text = _normalize_for_tts(
                " ".join(str(s.get("text", "")).strip() for s in scenes if s.get("text")))
            if selected_voice is None:   # auto-pick via Claude SDK (reads narration + context, NOT bubbles)
                cc = {}
                cc_path = root / "comic_context.json"
                if cc_path.exists():
                    try:
                        cc = json.loads(cc_path.read_text())
                    except Exception:
                        cc = {}
                selected_voice, chosen_name = select_voice(narration, cc, log=print)
                print(f"[stage4] auto-selected Resemble voice: {chosen_name} ({selected_voice})")
            print(f"[stage4] synthesizing {len(full_text)} chars via Resemble "
                  f"(voice={selected_voice or RESEMBLE_VOICE_UUID})")
            result = _synthesize(full_text, voice_id=selected_voice, log=print)
        else:
            from .cartesia_tts import synthesize as _synthesize
            if flat:
                full_text = _normalize_for_tts(
                    " ".join(str(s.get("text", "")).strip() for s in scenes if s.get("text")))
            else:
                full_text = _build_emotional_transcript(scenes, base_emotion, print)
            print(f"[stage4] synthesizing {len(full_text)} chars via Cartesia "
                  f"({model or CARTESIA_MODEL}, voice={voice_id or CARTESIA_VOICE_ID}, "
                  f"speed={speed}, volume={volume}, base_emotion={base_emotion}, flat={flat})")
            result = _synthesize(full_text, voice_id=voice_id, model=model,
                                 speed=speed, volume=volume, emotion=base_emotion)
        audio_path.write_bytes(result.wav_bytes)
        words = result.word_timestamps
        duration = _wav_duration(audio_path)
        print(f"[stage4] {TTS_PROVIDER} output: {duration:.2f}s, {len(words)} words "
              f"({len(words)/max(duration, 0.01):.2f} wps)")

        if post_atempo and post_atempo != 1.0:
            print(f"[stage4] post-process: ffmpeg atempo={post_atempo} (preserves pitch)")
            _apply_atempo(audio_path, post_atempo)
            words = [
                {"word": w["word"],
                 "start": w["start"] / post_atempo,
                 "end": w["end"] / post_atempo}
                for w in words
            ]
            duration = _wav_duration(audio_path)
            print(f"[stage4] post-process done: {duration:.2f}s, "
                  f"{len(words)/duration:.2f} wps")

        words_path.write_text(json.dumps(words, indent=2, ensure_ascii=False))
        print(f"[stage4] saved audio: {audio_path} ({duration:.2f}s, {len(words)} words)")

    scene_timings = align_scenes_to_words(scenes, words)
    caption_chunks = build_caption_chunks(scenes, words)

    scenes_path.write_text(
        json.dumps([s.to_dict() for s in scene_timings], indent=2, ensure_ascii=False)
    )
    captions_path.write_text(
        json.dumps([c.to_dict() for c in caption_chunks], indent=2, ensure_ascii=False)
    )
    print(f"[stage4] saved scene_timings ({len(scene_timings)}) and caption_chunks ({len(caption_chunks)})")

    used_voice = (selected_voice or RESEMBLE_VOICE_UUID) if TTS_PROVIDER == "resemble" \
        else (voice_id or CARTESIA_VOICE_ID)
    used_model = "resemble/chatterbox" if TTS_PROVIDER == "resemble" else (model or CARTESIA_MODEL)
    return TTSResult(
        audio_path=str(audio_path),
        audio_duration_seconds=round(duration, 3),
        voice_id=used_voice,
        model=used_model,
        speed=speed,
        word_timestamps=words_from_dicts(words),
        scene_timings=scene_timings,
        caption_chunks=caption_chunks,
    )


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


_TTS_REPLACEMENTS = {
    # Cartesia interprets em-dash as a dramatic ~1s pause. Use comma for a short
    # natural pause instead — keeps pacing consistent.
    "—": ", ",
    "–": ", ",
    "…": ", ",   # ellipsis also drags
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", "­": "",  # NBSP, soft hyphen
}


def _normalize_for_tts(text: str) -> str:
    """Strip chars that Cartesia interprets as long pauses or rendering glitches."""
    for ch, rep in _TTS_REPLACEMENTS.items():
        text = text.replace(ch, rep)
    # Collapse repeated commas / spaces (em-dash → comma can create ", ," sequences)
    import re
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _resolve_ffmpeg() -> str:
    import os
    import shutil
    if os.path.isabs(FFMPEG_BIN) and os.path.isfile(FFMPEG_BIN):
        return FFMPEG_BIN
    p = shutil.which(FFMPEG_BIN) or shutil.which("ffmpeg")
    if not p:
        raise FileNotFoundError(f"ffmpeg not found (FFMPEG_BIN={FFMPEG_BIN}). Check .env or PATH.")
    return p


def _apply_atempo(audio_path: Path, factor: float) -> None:
    """Apply ffmpeg atempo in-place. Preserves pitch (unlike asetrate). factor must
    be in [0.5, 2.0]; ffmpeg chains multiple atempo if needed but we keep single-stage."""
    ff = _resolve_ffmpeg()
    tmp = audio_path.with_suffix(".sped.wav")
    res = subprocess.run(
        [ff, "-y", "-i", str(audio_path),
         "-filter:a", f"atempo={factor}",
         "-c:a", "pcm_s16le",
         str(tmp)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg atempo failed: {res.stderr[-500:]}")
    tmp.replace(audio_path)
