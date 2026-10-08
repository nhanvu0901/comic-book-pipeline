"""Background TTS runner and audio cache for Q&A pipeline."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import config
from .beat_timing import BeatWindow, calculate_beat_durations, set_keep_awake
from .chatterbox_tts import (
    CHATTERBOX_CFG_WEIGHT,
    CHATTERBOX_EXAGGERATION,
    CHATTERBOX_VOICE_WAV,
    ChatterboxResult,
    _chunks,
    _even_words,
)
from .schema import TTSResult


def get_cache_key(
    text: str,
    voice: str | None,
    exaggeration: float,
    cfg_weight: float,
    seed: int | None,
    post_atempo: float,
) -> str:
    """Unique sha256 cache key per TTS chunk:
    sha256(text|voice|exaggeration|cfg_weight|seed|post_atempo)
    """
    v = str(voice or "built-in").strip()
    payload = f"{text.strip()}|{v}|{exaggeration}|{cfg_weight}|{seed}|{post_atempo}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _apply_chunk_atempo(wav_bytes: bytes, atempo: float) -> tuple[bytes, float]:
    """Apply ffmpeg atempo filter to wav bytes in memory."""
    if abs(atempo - 1.0) < 1e-4:
        # No speed change
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            dur = wf.getnframes() / float(wf.getframerate())
        return wav_bytes, dur

    ff = shutil.which("ffmpeg")
    if not ff:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            dur = wf.getnframes() / float(wf.getframerate())
        return wav_bytes, dur

    with tempfile.TemporaryDirectory(prefix="atempo_chunk_") as tdir:
        in_p = Path(tdir) / "in.wav"
        out_p = Path(tdir) / "out.wav"
        in_p.write_bytes(wav_bytes)

        cmd = [
            ff, "-y", "-i", str(in_p),
            "-filter:a", f"atempo={atempo}",
            "-vn", str(out_p),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        res_bytes = out_p.read_bytes()
        with wave.open(str(out_p), "rb") as wf:
            dur = wf.getnframes() / float(wf.getframerate())
        return res_bytes, dur


def _synthesize_chunk_raw(
    text: str,
    voice_wav: str | None = None,
    exaggeration: float = 0.5,
    cfg_weight: float = 0.5,
    seed: int | None = None,
    post_atempo: float = 1.15,
) -> tuple[bytes, float, int]:
    """Synthesize one chunk using chatterbox_tts and return (wav_bytes, duration, sr)."""
    from . import chatterbox_tts
    res = chatterbox_tts.synthesize(
        text,
        voice_id=voice_wav,
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        seed=seed,
    )
    final_bytes, dur = _apply_chunk_atempo(res.wav_bytes, post_atempo)
    return final_bytes, dur, 24000


class BackgroundTTSRunner:
    """Background TTS runner that operates progressively on narration.json
    without requiring ensure_reviewed."""

    def __init__(self, project_name: str, voice_wav: str | None = None):
        self.project_name = project_name
        self.root = config.PROJECTS_ROOT / project_name
        self.cache_dir = self.root / "cache" / "tts"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.voice_wav = voice_wav or CHATTERBOX_VOICE_WAV or "built-in"
        self.exaggeration = CHATTERBOX_EXAGGERATION
        self.cfg_weight = CHATTERBOX_CFG_WEIGHT
        self.seed = config.CHATTERBOX_SEED or 42
        self.post_atempo = float(os.getenv("POST_ATEMPO", str(config.POST_ATEMPO)))
        self.priority_scenes: list[int] = []

    def bump_priority_beat(self, beat_id: str) -> None:
        """Bump the scene containing beat_id to the front of the queue."""
        narration_path = self.root / "narration.json"
        if not narration_path.exists():
            return
        try:
            narration = json.loads(narration_path.read_text())
            for sc in narration.get("scenes", []):
                sid = int(sc.get("scene_id") or 1)
                for b in sc.get("visual_beats", []):
                    if str(b.get("beat_id")) == beat_id:
                        if sid in self.priority_scenes:
                            self.priority_scenes.remove(sid)
                        self.priority_scenes.insert(0, sid)
                        return
        except Exception:
            pass

    def get_status(self) -> dict[str, Any]:
        status_file = self.cache_dir / "status.json"
        if status_file.exists():
            try:
                return json.loads(status_file.read_text())
            except Exception:
                pass
        return {"completed": False, "beat_durations": {}, "scene_durations": {}}

    def _write_status(self, status: dict[str, Any]) -> None:
        (self.cache_dir / "status.json").write_text(json.dumps(status, indent=2))

    def run_sync(self, progress_cb: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Execute background TTS run synchronously with keep-awake."""
        narration_path = self.root / "narration.json"
        if not narration_path.exists():
            return {"error": f"narration.json not found in {self.root}"}

        narration = json.loads(narration_path.read_text())
        scenes = narration.get("scenes") or []
        if not scenes:
            return {"error": "No scenes in narration"}

        set_keep_awake(True)
        try:
            status = self.get_status()
            beat_durations = status.get("beat_durations", {})
            scene_durations = status.get("scene_durations", {})

            # Order scenes by priority then sequential
            ordered_scenes: list[dict[str, Any]] = []
            seen_sids = set()
            for sid in self.priority_scenes:
                for sc in scenes:
                    if int(sc.get("scene_id") or 1) == sid and sid not in seen_sids:
                        ordered_scenes.append(sc)
                        seen_sids.add(sid)

            for sc in scenes:
                sid = int(sc.get("scene_id") or 1)
                if sid not in seen_sids:
                    ordered_scenes.append(sc)
                    seen_sids.add(sid)

            sentence_timings: dict[int, dict[str, float]] = {}
            running_t = 0.0

            for sc in ordered_scenes:
                sid = int(sc.get("scene_id") or 1)
                text = str(sc.get("text", "")).strip()
                if not text:
                    continue

                chunks = _chunks(text)
                scene_wav_dur = 0.0

                for ch in chunks:
                    key = get_cache_key(
                        text=ch,
                        voice=self.voice_wav,
                        exaggeration=self.exaggeration,
                        cfg_weight=self.cfg_weight,
                        seed=self.seed,
                        post_atempo=self.post_atempo,
                    )
                    wav_file = self.cache_dir / f"{key}.wav"
                    meta_file = self.cache_dir / f"{key}.json"

                    if wav_file.exists() and meta_file.exists():
                        try:
                            meta = json.loads(meta_file.read_text())
                            ch_dur = float(meta["duration"])
                        except Exception:
                            meta = None
                    else:
                        meta = None

                    if meta is None:
                        wav_bytes, ch_dur, sr = _synthesize_chunk_raw(
                            ch,
                            voice_wav=self.voice_wav,
                            exaggeration=self.exaggeration,
                            cfg_weight=self.cfg_weight,
                            seed=self.seed,
                            post_atempo=self.post_atempo,
                        )
                        wav_file.write_bytes(wav_bytes)
                        words = _even_words(ch, 0.0, ch_dur)
                        meta_file.write_text(json.dumps({"duration": ch_dur, "words": words}, indent=2))

                    scene_wav_dur += ch_dur

                scene_durations[str(sid)] = round(scene_wav_dur, 4)
                sentence_timings[sid] = {
                    "duration": scene_wav_dur,
                    "start": running_t,
                    "end": running_t + scene_wav_dur,
                }
                running_t += scene_wav_dur

                # Update beat durations progressively for this scene
                windows = calculate_beat_durations([sc], sentence_timings)
                for w in windows:
                    beat_durations[w.beat_id] = round(w.duration, 4)

                status["beat_durations"] = beat_durations
                status["scene_durations"] = scene_durations
                self._write_status(status)
                if progress_cb:
                    progress_cb(f"[bg-tts] scene {sid} ready ({scene_wav_dur:.2f}s)")

            # Final pass: recalculate all beat windows across full project
            full_windows = calculate_beat_durations(scenes, sentence_timings)
            for w in full_windows:
                beat_durations[w.beat_id] = round(w.duration, 4)

            status["completed"] = True
            status["beat_durations"] = beat_durations
            status["scene_durations"] = scene_durations
            self._write_status(status)
            return status

        finally:
            set_keep_awake(False)


@dataclass
class CachedAudioResult:
    audio_path: Path
    duration_seconds: float
    words: list[dict[str, Any]]


def load_or_synthesize_cached(
    project_name: str,
    scenes: list[dict[str, Any]],
    post_atempo: float = 1.30,
    voice_wav: str | None = None,
    provider: str = "chatterbox",
) -> CachedAudioResult | None:
    """Used by Stage 4: loads all cached chunks for the project, synthesizing
    only missing chunks, and combines them into audio.wav."""
    if provider != "chatterbox":
        return None

    root = config.PROJECTS_ROOT / project_name
    cache_dir = root / "cache" / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)

    v = voice_wav or CHATTERBOX_VOICE_WAV or "built-in"
    ex = CHATTERBOX_EXAGGERATION
    cfg = CHATTERBOX_CFG_WEIGHT
    seed = config.CHATTERBOX_SEED or 42

    all_words: list[dict[str, Any]] = []
    chunk_wav_paths: list[Path] = []
    running_t = 0.0

    for sc in scenes:
        text = str(sc.get("text", "")).strip()
        if not text:
            continue
        chunks = _chunks(text)
        for ch in chunks:
            key = get_cache_key(
                text=ch,
                voice=v,
                exaggeration=ex,
                cfg_weight=cfg,
                seed=seed,
                post_atempo=post_atempo,
            )
            wav_file = cache_dir / f"{key}.wav"
            meta_file = cache_dir / f"{key}.json"

            if not (wav_file.exists() and meta_file.exists()):
                # Synthesize missing chunk
                wav_bytes, ch_dur, sr = _synthesize_chunk_raw(
                    ch,
                    voice_wav=v,
                    exaggeration=ex,
                    cfg_weight=cfg,
                    seed=seed,
                    post_atempo=post_atempo,
                )
                wav_file.write_bytes(wav_bytes)
                ch_words = _even_words(ch, 0.0, ch_dur)
                meta_file.write_text(json.dumps({"duration": ch_dur, "words": ch_words}, indent=2))
            else:
                meta = json.loads(meta_file.read_text())
                ch_dur = float(meta["duration"])
                ch_words = meta.get("words", [])

            chunk_wav_paths.append(wav_file)
            for w in ch_words:
                all_words.append({
                    "word": w["word"],
                    "start": round(running_t + float(w["start"]), 4),
                    "end": round(running_t + float(w["end"]), 4),
                })
            running_t += ch_dur

    if not chunk_wav_paths:
        return None

    # Concatenate chunk wavs into audio.wav
    audio_path = root / "audio.wav"
    _concat_wavs(chunk_wav_paths, audio_path)

    return CachedAudioResult(
        audio_path=audio_path,
        duration_seconds=running_t,
        words=all_words,
    )


def _concat_wavs(wav_paths: list[Path], out_path: Path) -> None:
    """Concatenate a list of mono 16-bit WAV files into out_path."""
    if not wav_paths:
        return
    if len(wav_paths) == 1:
        shutil.copyfile(str(wav_paths[0]), str(out_path))
        return

    frames = bytearray()
    nchannels = sampwidth = framerate = None

    for p in wav_paths:
        with wave.open(str(p), "rb") as wf:
            if nchannels is None:
                nchannels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
            frames.extend(wf.readframes(wf.getnframes()))

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(nchannels or 1)
        wf.setsampwidth(sampwidth or 2)
        wf.setframerate(framerate or 24000)
        wf.writeframes(bytes(frames))
