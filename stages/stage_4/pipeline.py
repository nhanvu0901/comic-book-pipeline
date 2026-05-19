"""
Stage 4 orchestrator: load narration.json → Cartesia TTS → align → persist.
"""
import io
import json
import subprocess
import wave
from pathlib import Path

from config import (
    CARTESIA_MODEL,
    CARTESIA_VOICE_ID,
    FFMPEG_BIN,
    PROJECTS_ROOT,
    get_project_dirs,
)
from .cartesia_tts import synthesize
from .chunker import align_scenes_to_words, build_caption_chunks, words_from_dicts
from .schema import TTSResult


def synthesize_project(
    project_name: str,
    *,
    speed: float = 1.0,  # Cartesia speed param caps near 1.2; let atempo post-process do the tempo work
    volume: float = 1.0,
    emotion: str = "confident",  # even-paced documentary narrator (vs contemplative's dramatic pauses)
    voice_id: str | None = None,
    model: str | None = None,
    post_atempo: float = 1.2,  # ffmpeg atempo post-process — slight tempo boost without pitch shift.
                                # 1.2 → ~3.14 wps (user-tuned; 1.3 felt slightly rushed)
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
        raw_text = " ".join(str(s.get("text", "")).strip() for s in scenes if s.get("text"))
        full_text = _normalize_for_tts(raw_text)
        if full_text != raw_text:
            print(f"[stage4] normalized {len(raw_text) - len(full_text)} char(s) for TTS "
                  f"(em-dashes etc. → commas to avoid dramatic pauses)")
        print(f"[stage4] synthesizing {len(full_text)} chars via Cartesia "
              f"({model or CARTESIA_MODEL}, voice={voice_id or CARTESIA_VOICE_ID}, "
              f"speed={speed}, volume={volume}, emotion={emotion})")
        result = synthesize(full_text, voice_id=voice_id, model=model,
                            speed=speed, volume=volume, emotion=emotion)
        audio_path.write_bytes(result.wav_bytes)
        words = result.word_timestamps
        duration = _wav_duration(audio_path)
        print(f"[stage4] cartesia output: {duration:.2f}s, {len(words)} words "
              f"({len(words)/duration:.2f} wps)")

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

    return TTSResult(
        audio_path=str(audio_path),
        audio_duration_seconds=round(duration, 3),
        voice_id=voice_id or CARTESIA_VOICE_ID,
        model=model or CARTESIA_MODEL,
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
