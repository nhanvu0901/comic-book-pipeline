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
- Give 1 beat for a short atomic sentence (<12 words); 2-4 beats (5-11 words each, ~2-3.5s per beat) for a longer multi-moment sentence. Split where the VISUAL would naturally change so no single panel is held static for more than ~3.5 seconds.

You are given several numbered sentences. Return STRICT JSON ONLY mapping each id to its \
beats: {"1":["...","..."],"2":["..."], ...}"""


def _word_tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _verbatim_ok(text: str, beats: list[str]) -> bool:
    """Beats are valid only if their concatenated word tokens equal the sentence's,
    in order — guarantees Stage 5's word-position bucketing stays aligned."""
    return bool(beats) and _word_tokens(" ".join(beats)) == _word_tokens(text)


_HOOK_MIN_WORDS_TO_SPLIT = 12   # below this the hook is one drawable moment
_HOOK_FRAG_MIN_WORDS = 4        # never leave a fragment too short to hold a panel
_HOOK_FRAG_MAX_WORDS = 10       # target 4-10 words (~2-3.5s) per visual beat
# Split points, in order of preference: a hard clause break beats a bare conjunction.
_HOOK_SPLIT_RE = re.compile(
    r"(?<=[,;:])\s+|\s+—\s+|\s+-\s+|(?<!\band)(?<!\bbut)(?<!\bso)(?<!\byet)(?<!\bor)(?<!\bright)(?<!\bjust)(?<!\beven)(?<!\bonly)\s+(?=(?:(?:right|just|even|only|shortly|immediately)\s+)?(?:and|but|then|until|because|so|yet|while|before|after|when|where|who|which|once|without|instead)\s)",
    re.IGNORECASE)

_DANGLING_WORDS = frozenset(
    "a an the of in on at to for from with by into onto over under as and or but nor so yet than "
    "that which who whom whose his her their its our my your this these those "
    "after before when where while until because since once without although though unless if whether".split()
)
_SECONDARY_SPLIT_WORDS = frozenset(
    "to into from with by through across under over against inside around during as that for in on at "
    "after before when where who which while until because so yet".split()
)
_SPLIT_MODIFIERS = frozenset("right just even only shortly immediately and but so yet or".split())


def _subsplit_long_fragment(frag: str) -> list[str]:
    words = frag.split()
    if len(words) <= _HOOK_FRAG_MAX_WORDS or len(words) < 2 * _HOOK_FRAG_MIN_WORDS:
        return [frag]

    min_idx = _HOOK_FRAG_MIN_WORDS
    max_idx = len(words) - _HOOK_FRAG_MIN_WORDS
    mid = len(words) // 2

    cands: list[int] = []
    for i in range(min_idx, max_idx + 1):
        w_curr = words[i].lower().strip(",;:.!?—–-\"'")
        w_prev = words[i - 1].lower().strip(",;:.!?—–-\"'")
        if w_curr in _SECONDARY_SPLIT_WORDS and w_prev not in _DANGLING_WORDS:
            split_idx = i
            if w_prev in _SPLIT_MODIFIERS and i - 1 >= min_idx:
                w_prev_prev = words[i - 2].lower().strip(",;:.!?—–-\"'")
                if w_prev_prev not in _DANGLING_WORDS and w_prev_prev not in _SPLIT_MODIFIERS:
                    split_idx = i - 1
            cands.append(split_idx)

    if cands:
        idx = min(cands, key=lambda idx_cand: abs(idx_cand - mid))
    else:
        idx = mid
        for off in (0, 1, -1, 2, -2):
            i = mid + off
            if min_idx <= i <= max_idx:
                w_prev = words[i - 1].lower().strip(",;:.!?—–-\"'")
                if w_prev not in _DANGLING_WORDS and w_prev not in _SPLIT_MODIFIERS:
                    idx = i
                    break

    return _subsplit_long_fragment(" ".join(words[:idx])) + _subsplit_long_fragment(" ".join(words[idx:]))


def _apply_anti_dangling(merged: list[str]) -> list[str]:
    for i in range(len(merged) - 1):
        w_left = merged[i].split()
        w_right = merged[i + 1].split()
        while (
            len(w_left) > _HOOK_FRAG_MIN_WORDS
            and w_left[-1][-1] not in ",;:.!?—–-"
            and (
                w_left[-1].lower().strip("\"'") in _DANGLING_WORDS
                or w_left[-1].lower().strip("\"'") in _SPLIT_MODIFIERS
            )
        ):
            w_right.insert(0, w_left.pop())
        merged[i] = " ".join(w_left)
        merged[i + 1] = " ".join(w_right)
    return merged


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
    i = 0
    while i < len(parts):
        p = parts[i]
        p_words = p.split()
        if len(p_words) < _HOOK_FRAG_MIN_WORDS:
            first_w = p_words[0].lower().strip(",;:.!?—–-\"'")
            if i + 1 < len(parts) and first_w in (_DANGLING_WORDS | _SECONDARY_SPLIT_WORDS):
                parts[i + 1] = f"{p} {parts[i + 1]}"
            elif merged:
                merged[-1] = f"{merged[-1]} {p}"
            elif i + 1 < len(parts):
                parts[i + 1] = f"{p} {parts[i + 1]}"
            else:
                merged.append(p)
        else:
            merged.append(p)
        i += 1

    subsplit: list[str] = []
    for m in merged:
        subsplit.extend(_subsplit_long_fragment(m))
    merged = subsplit

    if len(merged) < 2:
        words = text.split()
        mid = len(words) // 2
        if min(mid, len(words) - mid) < _HOOK_FRAG_MIN_WORDS:
            return []
        merged = [" ".join(words[:mid]), " ".join(words[mid:])]

    merged = _apply_anti_dangling(merged)

    # Post-anti-dangling size guard: if moving dangling words caused any fragment
    # to exceed _HOOK_FRAG_MAX_WORDS, subsplit again and re-apply anti-dangling.
    if any(len(m.split()) > _HOOK_FRAG_MAX_WORDS for m in merged):
        subsplit = []
        for m in merged:
            subsplit.extend(_subsplit_long_fragment(m))
        merged = _apply_anti_dangling(subsplit)

    if len(merged) < 2:
        return []
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
