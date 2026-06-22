"""
Resemble AI TTS (Chatterbox) via the synthesize endpoint.

Mirrors cartesia_tts.synthesize's return shape (wav_bytes + sample_rate +
word_timestamps) so Stage 4 can switch providers with no other changes.

Endpoint:  POST https://f.cluster.resemble.ai/synthesize
Auth:      Authorization: Bearer <key>
Response:  JSON {audio_content(b64 wav), audio_timestamps{graph_chars,graph_times}, ...}

Resemble emits per-CHARACTER (grapheme) timestamps; we fold them into word-level
timestamps in the same [{word, start, end}] shape Stage 4 expects.
"""
import base64
import io
import json
import re
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

from config import (
    RESEMBLE_API_KEY,
    RESEMBLE_SYNTH_URL,
    RESEMBLE_VOICE_MAP,
    RESEMBLE_VOICE_UUID,
)


@dataclass
class ResembleResult:
    wav_bytes: bytes
    sample_rate: int
    word_timestamps: list[dict]   # [{"word": str, "start": float, "end": float}]


_MAX_SYNTH_CHARS = 480   # Resemble /synthesize 504s on long input → chunk by sentence


def _split_chunks(text: str, max_chars: int) -> list[str]:
    """Pack whole sentences into chunks no longer than max_chars so each /synthesize
    call stays well under the timeout. Falls back to the raw text if unsplittable."""
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    cur = ""
    for s in sents:
        s = s.strip()
        if not s:
            continue
        if cur and len(cur) + 1 + len(s) > max_chars:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [text.strip()]


def _synth_chunk(text: str, voice_uuid: str, sample_rate: int, timeout: int) -> dict:
    """One /synthesize call with a single retry on 504/timeout. Returns the JSON dict."""
    body = {"voice_uuid": voice_uuid, "data": text,
            "sample_rate": sample_rate, "output_format": "wav"}
    last = ""
    for _attempt in range(2):
        req = urllib.request.Request(
            RESEMBLE_SYNTH_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {RESEMBLE_API_KEY}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            if d.get("success") and d.get("audio_content"):
                return d
            last = f"no audio: {str(d)[:200]}"
        except urllib.error.HTTPError as e:
            last = f"{e.code}: {e.read().decode()[:200]}"
            if e.code != 504:
                break
        except Exception as e:  # noqa: BLE001 — network flake → retry once
            last = str(e)
    raise RuntimeError(f"Resemble synthesize failed ({last})")


def synthesize(
    text: str,
    *,
    voice_id: str | None = None,
    sample_rate: int = 44100,
    timeout: int = 180,
    **_ignored,   # swallow Cartesia-only kwargs (model/speed/volume/emotion/language)
) -> ResembleResult:
    """Generate TTS audio + word timestamps via Resemble. Long text is chunked by
    sentence (the /synthesize endpoint 504s on long input); chunk WAVs are stitched and
    word timestamps offset by cumulative duration. Same result shape as
    cartesia_tts.synthesize so the Stage 4 dispatcher is provider-agnostic."""
    if not RESEMBLE_API_KEY:
        raise RuntimeError("RESEMBLE_API_KEY is empty — add it to .env")
    if not text or not text.strip():
        raise ValueError("synthesize() called with empty text")

    voice = voice_id or RESEMBLE_VOICE_UUID
    chunks = _split_chunks(text, _MAX_SYNTH_CHARS)
    pcm_parts: list[bytes] = []
    words: list[dict] = []
    offset = 0.0
    sr, sampwidth, channels = sample_rate, 2, 1
    for c in chunks:
        d = _synth_chunk(c, voice, sample_rate, timeout)
        wav = base64.b64decode(d["audio_content"])
        with wave.open(io.BytesIO(wav), "rb") as wf:
            sr, sampwidth, channels = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
            nframes = wf.getnframes()
            pcm_parts.append(wf.readframes(nframes))
            dur = nframes / float(sr or sample_rate)
        for w in _words_from_graphemes(d.get("audio_timestamps") or {}):
            words.append({"word": w["word"], "start": w["start"] + offset, "end": w["end"] + offset})
        offset += dur

    wav_bytes = _wrap_pcm_as_wav(b"".join(pcm_parts), sample_rate=sr,
                                 sampwidth=sampwidth, channels=channels)
    return ResembleResult(wav_bytes=wav_bytes, sample_rate=sr, word_timestamps=words)


def _wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int, sampwidth: int = 2, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _words_from_graphemes(ts: dict) -> list[dict]:
    """Fold per-character (grapheme) timestamps into word-level ones: a word runs
    from its first non-space char's start to its last char's end; spaces split words."""
    chars = ts.get("graph_chars") or []
    times = ts.get("graph_times") or []
    words: list[dict] = []
    cur, start, end = "", None, None
    for ch, tt in zip(chars, times):
        if not isinstance(tt, (list, tuple)) or len(tt) < 2:
            continue
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": float(start), "end": float(end)})
                cur, start = "", None
            continue
        if start is None:
            start = tt[0]
        end = tt[1]
        cur += ch
    if cur and start is not None:
        words.append({"word": cur, "start": float(start), "end": float(end)})
    return words


def _load_voice_map() -> dict:
    try:
        return json.loads(Path(RESEMBLE_VOICE_MAP).read_text())
    except Exception:
        return {}


def select_voice(narration: dict, comic_context: dict | None = None, *, log=print) -> tuple[str, str]:
    """Auto-pick the best Resemble voice for THIS story via the Claude SDK, reading the
    narration prose + comic context — NOT the panel speech-bubble text (that's noise).
    Falls back to the map's default_uuid on any failure. Returns (voice_uuid, voice_name)."""
    vm = _load_voice_map()
    voices = vm.get("voices") or []
    default_uuid = vm.get("default_uuid") or (voices[0]["uuid"] if voices else RESEMBLE_VOICE_UUID)
    if not voices:
        return default_uuid, "default"
    by_uuid = {v["uuid"]: v for v in voices}
    by_name = {v["name"].lower(): v for v in voices}

    def _fallback(why: str) -> tuple[str, str]:
        log(f"[voice-select] {why} → default {default_uuid}")
        return default_uuid, by_uuid.get(default_uuid, {}).get("name", "default")

    nara = narration or {}
    scenes = nara.get("scenes") or []
    story_text = " ".join(str(s.get("text", "")).strip() for s in scenes if s.get("text"))[:2500]
    cc = comic_context or {}
    summary = (cc.get("plot_summary") or (cc.get("summary") or {}).get("story_arc") or "")[:1500]
    chars = (cc.get("summary") or {}).get("characters") or cc.get("characters") or []
    char_lines = "; ".join(f"{c.get('name','?')} ({str(c.get('role',''))[:60]})" for c in chars[:6])

    catalog = "\n".join(
        f"- {v['name']} | uuid={v['uuid']} | {v.get('gender','?')} | vibe: {v.get('vibe','')}"
        f" | use_for: {v.get('use_for','')}"
        for v in voices
    )
    system = (
        "You pick ONE narrator voice for a YouTube Short that narrates a comic story. "
        "Match the story's dominant tone/genre to a voice's vibe.\n"
        "Priority rules — apply IN ORDER, stop at the first that fits:\n"
        "1. HORROR MONSTERS present — vampires, undead, demons, ghouls, werewolves, supernatural "
        "creatures of dread (but NOT cosmic beings like Galactus or gods, which are epic sci-fi, "
        "not horror) → the deep, ominous HORROR voice, EVEN IF the lead is female or the story is "
        "also tragic/emotional. Monster/horror presence OVERRIDES the tragic and female-lead rules below.\n"
        "2. Cosmic / sci-fi / world-ender epic (space, gods, Power Cosmic, planet-eaters) → the grand, "
        "intellectual voice.\n"
        "3. Heartfelt / emotional / tragic with NO monsters → a warm, emotive voice.\n"
        "4. LIGHT tone AND female lead AND no monsters → a female voice.\n"
        "5. Otherwise → the calm, neutral default.\n"
        'Return ONLY compact JSON: {"voice_uuid":"...","voice_name":"...","reason":"..."}.'
    )
    user = (
        f"TITLE: {nara.get('title','')}\nMODE: {nara.get('mode','')}\nHOOK: {nara.get('hook','')}\n"
        f"LEAD CHARACTERS: {char_lines}\n"
        f"PLOT SUMMARY: {summary}\n"
        f"NARRATION: {story_text}\n\n"
        f"AVAILABLE VOICES:\n{catalog}\n\nPick the single best-fit voice."
    )

    from stages._claude_sdk import sdk_available, sdk_complete
    if not sdk_available():
        return _fallback("SDK unavailable")
    out = sdk_complete(system, user, log=log)
    if not out:
        return _fallback("SDK returned nothing")
    m = re.search(r"\{.*\}", out, re.DOTALL)
    pick = {}
    if m:
        try:
            pick = json.loads(m.group(0))
        except Exception:
            pick = {}
    uuid = str(pick.get("voice_uuid", "")).strip()
    name = str(pick.get("voice_name", "")).strip()
    reason = str(pick.get("reason", "")).strip()
    chosen = by_uuid.get(uuid) or by_name.get(name.lower())
    if not chosen:
        return _fallback(f"invalid pick {uuid!r}/{name!r}")
    log(f"[voice-select] {chosen['name']} ({chosen['uuid']}) — {reason[:140]}")
    return chosen["uuid"], chosen["name"]
