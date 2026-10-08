"""Background TTS for the Q&A video-clip flow (ENABLE_VIDEO_CLIPS=1).

While Master is still in the review gate, the narration is synthesized ahead of time, chunk by
chunk, so (a) every beat can show its exact length — the length a picked clip has to fill — and
(b) Stage 4 later finds the audio already there and only synthesizes what changed.

It reproduces Stage 4's Chatterbox path, not an approximation of it:
  * NORMALISE  stages.stage_4.pipeline._normalize_for_tts over the whole narration text,
  * CHUNK      chatterbox_tts._chunks (one chunk per sentence, <= 320 chars) over that text,
  * ATEMPO     config.POST_ATEMPO (POST_ATEMPO_LONGFORM for longform modes), applied to each chunk
               before it is cached — the atempo is part of the cache key,
  * WORDS      chatterbox_tts._even_words spreads each chunk's words over its measured length.

ONE CACHE, projects/<p>/cache/tts/<sha256>.wav + .json, keyed by
sha256(text | voice | exaggeration | cfg_weight | seed | post_atempo [| temperature]) — see
get_cache_key. A chunk whose key is on disk is never synthesized again: not by the next pass, not
by Stage 4 (load_or_synthesize_cached). Chunks are seeded INDIVIDUALLY (the worker re-seeds before
each one), so a chunk's audio depends only on its key, never on which batch it travelled in.

ONE STATUS FILE, review/tts_status.json (stages.stage_4.tts_status) — written here, read by the
UI and the web routes.

It never calls ensure_reviewed(): the review gate is open on purpose, and that guard exists to
stop Stage 4 until it closes.

THE WORKER. synthesize_missing() starts ONE chatterbox worker for a whole batch of chunks — the
model loads once, not once per sentence — and reads its progress line by line, so each chunk is
cached and its beat's length published the moment it finishes. A priority bump or a narration
edit stops the batch (the chunks already done stay cached) and the runner re-plans.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import config
from . import tts_status
from .beat_timing import beat_rows, compute_beat_timings, narration_text, scene_word_ranges, set_keep_awake
from .chatterbox_tts import (
    CHATTERBOX_CFG_WEIGHT,
    CHATTERBOX_DEVICE,
    CHATTERBOX_EXAGGERATION,
    CHATTERBOX_TEMPERATURE,
    CHATTERBOX_VOICE_WAV,
    _chunks,
    _even_words,
)

logger = logging.getLogger(__name__)

# a chunk the worker failed to render this many times in one run is given up on (status.error says so)
_MAX_ATTEMPTS = 2
# no output from a running worker for this long = it is hung
_STALL_SECONDS = float(os.getenv("BACKGROUND_TTS_STALL_SECONDS", "1200"))


# ─── cache key / settings ─────────────────────────────────────────────────────────────────────

def get_cache_key(
    text: str,
    voice: str | None,
    exaggeration: float,
    cfg_weight: float,
    seed: int | None,
    post_atempo: float,
    temperature: float | None = None,
) -> str:
    """sha256 of everything that decides a chunk's audio:
    text | voice | exaggeration | cfg_weight | seed | post_atempo [| temperature]."""
    v = str(voice or "built-in").strip()
    payload = f"{text.strip()}|{v}|{exaggeration}|{cfg_weight}|{seed}|{post_atempo}"
    if temperature is not None:
        payload += f"|{temperature}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TTSSettings:
    """Every knob that shapes a chunk's audio; `key(text)` is the cache key."""
    voice: str
    exaggeration: float
    cfg_weight: float
    temperature: float
    seed: int | None
    post_atempo: float

    @classmethod
    def current(cls, *, voice_wav: str | None, post_atempo: float) -> "TTSSettings":
        return cls(
            voice=str(voice_wav or CHATTERBOX_VOICE_WAV or "built-in"),
            exaggeration=CHATTERBOX_EXAGGERATION,
            cfg_weight=CHATTERBOX_CFG_WEIGHT,
            temperature=CHATTERBOX_TEMPERATURE,
            # Only a flag-ON run is seeded (the flag-OFF Chatterbox read stays unseeded, as ever).
            seed=config.CHATTERBOX_SEED if config.ENABLE_VIDEO_CLIPS else None,
            post_atempo=float(post_atempo),
        )

    def key(self, text: str) -> str:
        return get_cache_key(text, self.voice, self.exaggeration, self.cfg_weight, self.seed,
                             self.post_atempo, self.temperature)


def resolve_post_atempo(narration: dict[str, Any]) -> float:
    """The atempo Stage 4 applies to this narration — one definition, shared with Stage 4."""
    from .pipeline import post_atempo_for_mode
    return post_atempo_for_mode(str(narration.get("mode") or ""))


def plan_chunks(scenes: Sequence[dict[str, Any]]) -> list[str]:
    """The chunks Stage 4's Chatterbox path synthesizes for these scenes: the whole narration
    normalised for TTS, then split into sentences. Identical to what synthesize_project builds."""
    from .pipeline import _normalize_for_tts
    return _chunks(_normalize_for_tts(narration_text(scenes)))


# ─── the chunk cache ──────────────────────────────────────────────────────────────────────────

class ChunkCache:
    """projects/<p>/cache/tts/<key>.wav (+ <key>.json {"duration", "words"}). A chunk counts as
    cached only when both files exist; the json is written last, atomically."""

    def __init__(self, cache_dir: Path, settings: TTSSettings):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings

    def wav_path(self, text: str) -> Path:
        return self.dir / f"{self.settings.key(text)}.wav"

    def _meta_path(self, text: str) -> Path:
        return self.dir / f"{self.settings.key(text)}.json"

    def meta(self, text: str) -> dict | None:
        if not self.wav_path(text).is_file():
            return None
        try:
            meta = json.loads(self._meta_path(text).read_text(encoding="utf-8"))
            float(meta["duration"])
            return meta
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def duration(self, text: str) -> float | None:
        m = self.meta(text)
        return None if m is None else float(m["duration"])

    def has(self, text: str) -> bool:
        return self.meta(text) is not None

    def store(self, text: str, wav_bytes: bytes, duration: float) -> None:
        wav, meta = self.wav_path(text), self._meta_path(text)
        tmp_w = wav.with_name(wav.name + ".tmp")
        tmp_w.write_bytes(wav_bytes)
        os.replace(tmp_w, wav)
        tmp_m = meta.with_name(meta.name + ".tmp")
        tmp_m.write_text(json.dumps({"duration": duration, "words": _even_words(text, 0.0, duration)},
                                    indent=2), encoding="utf-8")
        os.replace(tmp_m, meta)


def _ffmpeg_bin() -> str | None:
    return ((config.FFMPEG_BIN if os.path.isfile(config.FFMPEG_BIN) else None)
            or shutil.which(config.FFMPEG_BIN) or shutil.which("ffmpeg"))


def _wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def _apply_chunk_atempo(wav_bytes: bytes, atempo: float) -> tuple[bytes, float]:
    """ffmpeg atempo over one chunk's wav (pitch-preserving, pcm_s16le like Stage 4's
    _apply_atempo). Returns (wav bytes, duration)."""
    if abs(atempo - 1.0) < 1e-4:
        return wav_bytes, _wav_seconds(wav_bytes)
    ff = _ffmpeg_bin()
    if not ff:
        raise FileNotFoundError(f"ffmpeg not found (FFMPEG_BIN={config.FFMPEG_BIN}) — needed for atempo")
    with tempfile.TemporaryDirectory(prefix="atempo_chunk_") as tdir:
        in_p, out_p = Path(tdir) / "in.wav", Path(tdir) / "out.wav"
        in_p.write_bytes(wav_bytes)
        res = subprocess.run([ff, "-y", "-i", str(in_p), "-filter:a", f"atempo={atempo}",
                              "-c:a", "pcm_s16le", "-vn", str(out_p)], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg atempo failed: {(res.stderr or '')[-400:]}")
        out = out_p.read_bytes()
    return out, _wav_seconds(out)


# ─── the one worker job ───────────────────────────────────────────────────────────────────────

def _prompt_wav(voice: str) -> str | None:
    """The reference wav the worker clones — resolved like chatterbox_tts.synthesize does."""
    if voice and Path(voice).is_file():
        return str(voice)
    if CHATTERBOX_VOICE_WAV and Path(CHATTERBOX_VOICE_WAV).is_file():
        return str(CHATTERBOX_VOICE_WAV)
    return None


def synthesize_missing(
    texts: Sequence[str],
    cache: ChunkCache,
    *,
    on_chunk: Callable[[str, float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Synthesize every chunk in `texts` that isn't cached, with ONE worker process (one model
    load) for the whole batch, caching each chunk as it finishes. Returns the texts that are
    still not cached afterwards (failed chunks, or the ones left when should_stop() fired)."""
    from . import chatterbox_tts
    _log = log or (lambda _m: None)
    todo = [t for t in dict.fromkeys(texts) if t.strip() and not cache.has(t)]
    if not todo:
        return []
    venv_py = chatterbox_tts._venv_python()
    if not venv_py.exists():
        raise RuntimeError(
            f"Chatterbox venv missing at {venv_py.parent.parent} (set CHATTERBOX_VENV, or create it "
            f"with: python3 -m venv .venv-chatterbox && {venv_py} -m pip install chatterbox-tts)")

    st = cache.settings
    tmp = Path(tempfile.mkdtemp(prefix="chatterbox_batch_"))
    out_dir = tmp / "wav"
    chunk_specs = []
    for t in todo:
        spec: dict[str, Any] = {"text": t, "exaggeration": st.exaggeration, "cfg_weight": st.cfg_weight}
        if st.seed is not None:
            spec["seed"] = st.seed        # re-seeded before EACH chunk: audio depends on its key only
        chunk_specs.append(spec)
    job = tmp / "job.json"
    job.write_text(json.dumps({
        "chunks": chunk_specs,
        "out_dir": str(out_dir),
        "audio_prompt": _prompt_wav(st.voice),
        "temperature": st.temperature,
        "device": CHATTERBOX_DEVICE or None,
    }))
    _log(f"[bg-tts] one worker job for {len(todo)} chunk(s)")

    proc = subprocess.Popen([str(venv_py), str(chatterbox_tts._WORKER), str(job)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    lines: "queue.Queue[str | None]" = queue.Queue()

    def _pump() -> None:
        try:
            for ln in proc.stdout:                                  # type: ignore[union-attr]
                lines.put(ln)
        finally:
            lines.put(None)

    threading.Thread(target=_pump, daemon=True, name="bg-tts-worker-pump").start()
    tail: list[str] = []
    last_output = time.monotonic()
    stopped = False
    try:
        while True:
            if should_stop is not None and should_stop():
                stopped = True
                break
            try:
                ln = lines.get(timeout=0.5)
            except queue.Empty:
                if time.monotonic() - last_output > _STALL_SECONDS:
                    tail.append(f"(no output for {_STALL_SECONDS:.0f}s — worker killed)")
                    break
                continue
            if ln is None:
                break
            last_output = time.monotonic()
            ln = ln.strip()
            if not ln:
                continue
            if not ln.startswith("{"):
                tail.append(ln)                                     # worker stderr, kept for the error
                continue
            try:
                msg = json.loads(ln)
            except ValueError:
                continue
            if msg.get("ready"):
                _log(f"[bg-tts] model loaded on {msg.get('device')} @ {msg.get('sr')}Hz")
            elif msg.get("error"):
                _log(f"[bg-tts] chunk {msg.get('i')} failed: {str(msg['error'])[:140]}")
            elif "sec" in msg:
                i = int(msg["i"])
                if not 0 <= i < len(todo):
                    continue
                wav = out_dir / f"chunk_{i:05d}.wav"
                if not wav.is_file():
                    continue
                final_bytes, dur = _apply_chunk_atempo(wav.read_bytes(), st.post_atempo)
                cache.store(todo[i], final_bytes, dur)
                try:
                    wav.unlink()
                except OSError:
                    pass
                if on_chunk is not None:
                    on_chunk(todo[i], dur)
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    left = [t for t in todo if not cache.has(t)]
    if left and not stopped:
        _log(f"[bg-tts] {len(left)} chunk(s) not produced. Worker output: " + " | ".join(tail[-6:]))
    return left


# ─── the runner ───────────────────────────────────────────────────────────────────────────────

class BackgroundTTSRunner:
    """Keeps projects/<p>/cache/tts and review/tts_status.json in step with narration.json.
    One per project (see start_background_tts); safe to poke from the UI and web threads."""

    def __init__(self, project_name: str, voice_wav: str | None = None):
        self.project_name = project_name
        self.root = config.PROJECTS_ROOT / project_name
        self.cache_dir = self.root / "cache" / "tts"
        self.review_dir = self.root / "review"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)
        self.voice_wav = voice_wav
        self.priority_scenes: list[int] = []
        self._lock = threading.Lock()              # priority_scenes + job bookkeeping
        self._state_lock = threading.Lock()        # thread start/stop decision
        self._thread: threading.Thread | None = None
        self._dirty = threading.Event()            # narration changed: re-plan
        self._bumped = threading.Event()           # priority changed: maybe re-order the batch
        self._reorder = False                      # set by _should_stop when it ended a batch for a bump
        self._job_order: list[str] = []
        self._cache: ChunkCache | None = None
        self._attempts: dict[str, int] = {}

    # ── pokes from other threads ──
    def bump_priority_beat(self, beat_id: str) -> None:
        """Put the scene holding review beat `beat_id` first in the queue."""
        sid = self._scene_of_beat(str(beat_id))
        if sid is None:
            return
        with self._lock:
            if sid in self.priority_scenes:
                self.priority_scenes.remove(sid)
            self.priority_scenes.insert(0, sid)
        self._bumped.set()

    def request_resync(self) -> None:
        """narration.json changed: re-plan, synthesizing only chunks that aren't cached."""
        self._dirty.set()
        self.ensure_running()

    def ensure_running(self) -> bool:
        """Start the background thread unless one is already working. True when it was started."""
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._thread = threading.Thread(target=self._thread_main, daemon=True,
                                            name=f"bg-tts-{self.project_name}")
            self._thread.start()
            return True

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict[str, Any]:
        return tts_status.read_status(self.root)

    # ── internals ──
    def _load_narration(self) -> dict[str, Any] | None:
        try:
            doc = json.loads((self.root / "narration.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) and (doc.get("scenes") or []) else None

    def _scene_of_beat(self, beat_id: str) -> int | None:
        narration = self._load_narration()
        if narration is None:
            return None
        for row in beat_rows(narration):
            if row.beat_key == beat_id:
                return row.scene_id
        return None

    def _thread_main(self) -> None:
        self._attempts.clear()                                      # a fresh run retries what failed before
        try:
            while True:
                self.run_sync()
                with self._state_lock:
                    if not self._dirty.is_set():
                        self._thread = None
                        return
        except Exception as exc:                                    # never die silently
            logger.exception("background TTS for %s crashed", self.project_name)
            self._write_status({"completed": False, "running": False, "beat_durations": {},
                                "scene_durations": {}, "error": f"{type(exc).__name__}: {exc}"})
            with self._state_lock:
                self._thread = None

    def _write_status(self, status: dict[str, Any]) -> None:
        status["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            tts_status.write_status(self.root, status)
        except OSError as exc:
            logger.warning("could not write the TTS status for %s: %s", self.project_name, exc)

    def _publish(self, narration: dict, chunks: list[str], cache: ChunkCache, *,
                 running: bool, error: str | None = None) -> dict[str, Any]:
        timings = compute_beat_timings(narration, chunks, [cache.duration(c) for c in chunks])
        done = sum(1 for c in chunks if cache.has(c))
        status = {
            "completed": timings.complete,
            "running": running,
            "beat_durations": timings.durations,
            "beat_windows": {k: list(v) for k, v in timings.windows.items()},
            "scene_durations": {str(k): v for k, v in timings.scene_durations.items()},
            "chunks_total": len(chunks),
            "chunks_done": done,
            "narration_sha": hashlib.sha256(narration_text(narration["scenes"]).encode("utf-8")).hexdigest(),
            "post_atempo": cache.settings.post_atempo,
            "seed": cache.settings.seed,
            "error": error,
        }
        self._write_status(status)
        return status

    def _queue_order(self, narration: dict, chunks: list[str], cache: ChunkCache) -> list[str]:
        """Missing chunks, those overlapping a bumped scene first, then stream order."""
        pos, lo = [], 0
        for c in chunks:
            n = len(c.split())
            pos.append((lo, lo + n))
            lo += n
        ranges = scene_word_ranges(narration["scenes"], lo)
        with self._lock:
            prio = list(self.priority_scenes)
        missing = [(i, c) for i, c in enumerate(chunks)
                   if not cache.has(c) and self._attempts.get(c, 0) < _MAX_ATTEMPTS]
        ordered: list[str] = []
        for sid in prio:
            if sid not in ranges:
                continue
            s_lo, s_hi = ranges[sid]
            for i, c in missing:
                a, b = pos[i]
                if a < s_hi and b > s_lo and c not in ordered:
                    ordered.append(c)
        ordered.extend(c for _i, c in missing if c not in ordered)
        return ordered

    def _should_stop(self) -> bool:
        """Polled by the worker loop: stop the batch when it is stale or a bump needs a re-order."""
        if self._dirty.is_set():
            return True
        if not self._bumped.is_set():
            return False
        self._bumped.clear()
        cache = self._cache
        narration = self._load_narration()
        if cache is None or narration is None:
            return False
        want = self._queue_order(narration, plan_chunks(narration["scenes"]), cache)
        if not want:
            return False
        remaining = [t for t in self._job_order if not cache.has(t)]
        # the chunk in flight and the one after it are already "next": no restart for those
        if want[0] in remaining[:2]:
            return False
        self._reorder = True
        return True

    def run_sync(self, progress_cb: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Bring the cache and the status up to date with narration.json, synchronously.
        Repeats while the narration (or the priority) keeps changing underneath it."""
        say = progress_cb or (lambda _m: None)
        set_keep_awake(True)
        try:
            while True:
                self._dirty.clear()
                self._reorder = False
                narration = self._load_narration()
                if narration is None:
                    status = {"completed": False, "running": False, "beat_durations": {},
                              "scene_durations": {},
                              "error": f"narration.json missing or without scenes in {self.root}"}
                    self._write_status(status)
                    return status
                settings = TTSSettings.current(voice_wav=self.voice_wav,
                                               post_atempo=resolve_post_atempo(narration))
                cache = ChunkCache(self.cache_dir, settings)
                self._cache = cache
                chunks = plan_chunks(narration["scenes"])
                if not chunks:
                    return self._publish(narration, chunks, cache, running=False)
                order = self._queue_order(narration, chunks, cache)
                self._job_order = order
                self._publish(narration, chunks, cache, running=bool(order))

                error: str | None = None
                if order:
                    def _on_chunk(text: str, dur: float, _n=narration, _c=chunks, _k=cache) -> None:
                        self._publish(_n, _c, _k, running=True)
                        say(f"[bg-tts] cached {dur:.2f}s: {text[:60]}")
                    try:
                        left = synthesize_missing(order, cache, on_chunk=_on_chunk,
                                                  should_stop=self._should_stop, log=say)
                    except RuntimeError as exc:         # no venv / the worker could not start
                        left, error = [], str(exc)
                    if error is None and not (self._dirty.is_set() or self._reorder) and left:
                        for t in left:                  # the worker produced nothing for these
                            self._attempts[t] = self._attempts.get(t, 0) + 1
                        if any(self._attempts[t] < _MAX_ATTEMPTS for t in left):
                            continue                    # one more try, in a fresh job
                        error = (f"{len(left)} chunk(s) could not be synthesized "
                                 f"(first: {left[0][:60]!r})")
                    if error is None and (self._dirty.is_set() or self._reorder):
                        continue                        # edited narration / new priority: re-plan
                elif any(not cache.has(c) for c in chunks):
                    error = "some chunks were given up on after repeated failures"
                status = self._publish(narration, chunks, cache, running=False, error=error)
                if error or not self._dirty.is_set():
                    return status
        finally:
            set_keep_awake(False)


# ─── per-project registry ─────────────────────────────────────────────────────────────────────

_RUNNERS: dict[str, BackgroundTTSRunner] = {}
_RUNNERS_LOCK = threading.Lock()


def _runner_for(project_name: str, voice_wav: str | None = None) -> BackgroundTTSRunner:
    with _RUNNERS_LOCK:
        runner = _RUNNERS.get(project_name)
        if runner is None:
            runner = BackgroundTTSRunner(project_name, voice_wav=voice_wav)
            _RUNNERS[project_name] = runner
        return runner


def start_background_tts(project_name: str, voice_wav: str | None = None) -> BackgroundTTSRunner:
    """The project's runner (one per project), its daemon thread running."""
    runner = _runner_for(project_name, voice_wav)
    runner.ensure_running()
    return runner


def bump_priority_beat(project_name: str, beat_id: str) -> BackgroundTTSRunner:
    """Move the scene of review beat `beat_id` to the front of the project's queue."""
    runner = start_background_tts(project_name)
    runner.bump_priority_beat(beat_id)
    return runner


def request_resync(project_name: str) -> BackgroundTTSRunner:
    """narration.json changed — have the runner re-plan (and start it if it is idle)."""
    runner = _runner_for(project_name)
    runner.request_resync()
    return runner


# ─── Stage 4 side ─────────────────────────────────────────────────────────────────────────────

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
    """Stage 4 (ENABLE_VIDEO_CLIPS=1): the project's audio from the shared chunk cache,
    synthesizing only the chunks that are missing (in one worker job), concatenated into
    audio.wav with the word timeline Stage 4 builds from it."""
    if provider != "chatterbox":
        return None
    root = config.PROJECTS_ROOT / project_name
    cache = ChunkCache(root / "cache" / "tts",
                       TTSSettings.current(voice_wav=voice_wav, post_atempo=post_atempo))
    chunks = plan_chunks(scenes)
    if not chunks:
        return None
    left = synthesize_missing(chunks, cache, log=print)
    if left:
        raise RuntimeError(f"Chatterbox produced no audio for {len(left)} chunk(s) "
                           f"(first: {left[0][:60]!r}) — re-run Stage 4 to retry just those")

    all_words: list[dict[str, Any]] = []
    wav_paths: list[Path] = []
    t = 0.0
    for ch in chunks:
        meta = cache.meta(ch)
        assert meta is not None
        dur = float(meta["duration"])
        for w in meta.get("words") or []:
            all_words.append({"word": w["word"], "start": round(t + float(w["start"]), 4),
                              "end": round(t + float(w["end"]), 4)})
        wav_paths.append(cache.wav_path(ch))
        t += dur

    audio_path = root / "audio.wav"
    _concat_wavs(wav_paths, audio_path)
    return CachedAudioResult(audio_path=audio_path, duration_seconds=t, words=all_words)


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
