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


_HOOK_MIN_WORDS_TO_SPLIT = 13   # below this the hook is one drawable moment
_HOOK_FRAG_MIN_WORDS = 5        # never leave a fragment too short to hold a panel
# Split points, in order of preference: a hard clause break beats a bare conjunction.
_HOOK_SPLIT_RE = re.compile(
    r"(?<=[,;:])\s+|\s+—\s+|\s+-\s+|\s+(?=(?:and|but|then|until|because|so|yet|while)\s)",
    re.IGNORECASE)


def split_hook_fragments(hook: str) -> list[str]:
    """Split the spoken HOOK into drawable fragments, verbatim, WITHOUT an LLM call.

    Why this exists: all three modes emitted the intro as one scene with `visual_beats: []`,
    and stage_5's bookend branch turns a fragment-less scene into exactly ONE unit — so a
    26-word hook sat ~7 seconds on a single frozen drawing, across the 3-second gate where
    the channel measurably loses viewers. Fragments let it cut.

    No LLM: a hook is one or two sentences, the split points are punctuation and a short
    conjunction list, and every LLM touch is a chance to reword the line Master approved.
    Returns [] when the hook is too short to be worth cutting (caller keeps the old
    single-panel behaviour), and never returns a split that is not verbatim."""
    text = " ".join(str(hook or "").split())
    if len(text.split()) < _HOOK_MIN_WORDS_TO_SPLIT:
        return []
    parts = [p.strip() for p in _HOOK_SPLIT_RE.split(text) if p and p.strip()]
    merged: list[str] = []
    for p in parts:
        # Fold a runt into its neighbour rather than shipping a 2-word shot.
        if merged and len(p.split()) < _HOOK_FRAG_MIN_WORDS:
            merged[-1] = f"{merged[-1]} {p}"
        elif merged and len(merged[-1].split()) < _HOOK_FRAG_MIN_WORDS:
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    if len(merged) < 2:
        # No comma, dash or conjunction anywhere — common in a punchy hook ("Batman falls
        # from the moon without a single gadget left to save him"). Left whole it is one
        # frozen panel for the entire opening, which is the shot the 3-second gate judges.
        # Cut at the word nearest the midpoint: verbatim by construction, since the split
        # only ever falls BETWEEN words.
        words = text.split()
        mid = len(words) // 2
        if min(mid, len(words) - mid) < _HOOK_FRAG_MIN_WORDS:
            return []
        merged = [" ".join(words[:mid]), " ".join(words[mid:])]
    if not _verbatim_ok(text, merged):
        return []
    return merged


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
