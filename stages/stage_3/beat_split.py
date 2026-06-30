"""LLM visual-beat splitter (end of Stage 3) — replaces the spaCy clause splitter.

Splits each BODY scene's sentence into VERBATIM visual beats so Stage 5 can show a
distinct panel per beat (the panel changes at each new visual moment instead of being
held static for the whole sentence). One batched call to a cheap/free OpenRouter model
(BEAT_SPLIT_MODELS), with per-scene verbatim validation. NEVER raises: any failure or
non-verbatim output for a scene falls back to a single beat = the whole sentence
(today's 1-panel behavior, no regression).

Beats are written onto each scene dict as `scene["visual_beats"]`. They MUST be verbatim
(their concatenated word tokens equal the sentence's, in order) so Stage 5's
word-position bucketing of caption chunks stays aligned — see
stages/stage_5/shots.py:_split_members_by_clause.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from config import ENABLE_LLM_BEAT_SPLIT, BEAT_SPLIT_MODELS

_MIN_WORDS_TO_SPLIT = 6   # shorter sentences are atomic → 1 beat, no LLM call needed

_SYSTEM = """You split comic-recap narration sentences into VISUAL BEATS — each beat is \
one visual moment a camera would show (a new action, a new subject, a scene shift).

HARD RULES:
- Use the sentence's EXACT words, VERBATIM, in order. The beats joined back together MUST \
reproduce the sentence word-for-word (you may only drop a comma or dash at a split point). \
NEVER reword, add, drop, or reorder words.
- Give 1 beat for a short atomic sentence; 2-4 beats for a longer multi-moment sentence. \
Split where the VISUAL would naturally change.

You are given several numbered sentences. Return STRICT JSON ONLY mapping each id to its \
beats: {"1":["...","..."],"2":["..."], ...}"""


def _word_tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _verbatim_ok(text: str, beats: list[str]) -> bool:
    """Beats are valid only if their concatenated word tokens equal the sentence's,
    in order — guarantees Stage 5's word-position bucketing stays aligned."""
    return bool(beats) and _word_tokens(" ".join(beats)) == _word_tokens(text)


def _extract_obj(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def split_visual_beats(scenes: list[dict], *, progress: Callable[[str], None] | None = None) -> None:
    """Set scene["visual_beats"] (verbatim fragments) on each BODY scene, in place.
    Every scene gets a value: a real split when the LLM returns verbatim beats, else
    [whole sentence]. Intro/outro and short scenes always get [text]. Never raises."""
    log = progress or (lambda _m: None)
    # default EVERY scene to a single beat first → guaranteed valid even on total failure
    for s in scenes:
        s["visual_beats"] = [str(s.get("text", "")).strip()]
    if not ENABLE_LLM_BEAT_SPLIT:
        return
    # candidates = body scenes long enough to be worth splitting
    cands = [(i, s) for i, s in enumerate(scenes)
             if not s.get("is_intro") and not s.get("is_outro")
             and len(str(s.get("text", "")).split()) >= _MIN_WORDS_TO_SPLIT]
    if not cands:
        return

    numbered = {str(n + 1): s for n, (_, s) in enumerate(cands)}
    user = "Split these sentences into visual beats:\n" + "\n".join(
        f'{k}. "{str(s.get("text","")).strip()}"' for k, s in numbered.items()
    ) + '\n\nReturn STRICT JSON {"1":[...],...} only.'

    try:
        from ._llm import _client, _call_with_deadline
        client = _client()
    except Exception as exc:
        log(f"[stage4] beat-split: LLM client unavailable — keeping 1 beat/scene: {exc}")
        return

    obj = None
    for model in BEAT_SPLIT_MODELS:
        try:
            raw = _call_with_deadline(client, model, _SYSTEM, user, 1500)
        except Exception as exc:
            log(f"[stage4]   beat-split {model} failed: {exc}")
            continue
        obj = _extract_obj(raw)
        if obj:
            log(f"[stage4]   beat-split via {model}")
            break
        log(f"[stage4]   beat-split {model}: unparseable → next")
    if not obj:
        log("[stage4] beat-split: no usable output — keeping 1 beat/scene")
        return

    applied = 0
    for k, s in numbered.items():
        raw_beats = obj.get(k)
        if not isinstance(raw_beats, list):
            continue
        beats = [str(x).strip() for x in raw_beats if str(x).strip()]
        if len(beats) > 1 and _verbatim_ok(str(s.get("text", "")), beats):
            s["visual_beats"] = beats
            applied += 1
        # else: keep the [text] default (non-verbatim / single → no split)
    log(f"[stage4] beat-split: {applied}/{len(cands)} scenes → multi-beat panels")
