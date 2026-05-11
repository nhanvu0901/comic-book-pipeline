"""
Stage 4 orchestrator: load narration.json → Cartesia TTS → align → persist.
"""
import io
import json
import wave
from pathlib import Path

from config import (
    CARTESIA_MODEL,
    CARTESIA_VOICE_ID,
    PROJECTS_ROOT,
    get_project_dirs,
)
from .cartesia_tts import synthesize
from .chunker import align_scenes_to_words, build_caption_chunks, words_from_dicts
from .schema import TTSResult


def synthesize_project(
    project_name: str,
    *,
    speed: float = 1.0,
    volume: float = 1.0,
    emotion: str = "neutral",
    voice_id: str | None = None,
    model: str | None = None,
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
        full_text = " ".join(str(s.get("text", "")).strip() for s in scenes if s.get("text"))
        print(f"[stage4] synthesizing {len(full_text)} chars via Cartesia "
              f"({model or CARTESIA_MODEL}, voice={voice_id or CARTESIA_VOICE_ID}, "
              f"speed={speed}, volume={volume}, emotion={emotion})")
        result = synthesize(full_text, voice_id=voice_id, model=model,
                            speed=speed, volume=volume, emotion=emotion)
        audio_path.write_bytes(result.wav_bytes)
        words = result.word_timestamps
        words_path.write_text(json.dumps(words, indent=2, ensure_ascii=False))
        duration = _wav_duration(audio_path)
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
