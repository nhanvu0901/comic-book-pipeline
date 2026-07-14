"""
MONEY SHOT funnel — Phan 1: derive the ONE canonical "money shot" visual target for
a Q&A answer_context (character/object/event + a full visual query sentence), then
score existing panels for lexical hits on that target via their OCR/dialog/sfx text.

Two independent pieces (funnel wiring / panel selection is a separate task, Phan 2):
  - derive_money_target(): one small LLM call over a free OpenRouter chain (NOT the
    shared FREE_MODEL/SDK-gated stages.stage_3._llm.call_with_chain — this funnel
    step deliberately stays independent of that global switch) that picks the
    character/object/event that should headline the funnel, plus a natural-language
    query_text for downstream embedding search.
  - ocr_money_hits(): pure lexical scoring, no network — which panels' OCR/dialog/sfx
    text literally mention the money_object/money_character.
"""
from __future__ import annotations

import re
from typing import Callable

from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from stages.stage_2.cluster_namer import _parse_vlm_response
from stages.stage_2.vlm_extract import _detect_inline_rate_limit, _is_rate_limited

# ponytail: hard-pinned 2-model free chain per spec — deliberately NOT config.LLM_MODELS
# / call_with_chain (those are FREE_MODEL/SDK-gated); this step always goes straight to
# OpenRouter, same client/retry pattern as stages/stage_2/cluster_namer.py.
MONEY_MODEL_CHAIN: list[str] = ["google/gemma-4-31b-it:free", "openai/gpt-oss-120b:free"]

_SYSTEM_PROMPT = (
    "You are a trailer editor picking the ONE 'money shot' for a Short's hook — the "
    "single most visually striking, filmable image that answers the question below. "
    "Read the question and its researched answer items, then name the ONE character "
    "who should headline it, the ONE object/power/location that makes it iconic (or "
    "null if nothing fits), and describe the ONE concrete event. Ground everything in "
    "the given items — never invent a character or event not implied by them."
)

_JSON_SPEC = (
    'Return ONLY this JSON, no prose: {"money_character": <string or null>, '
    '"money_object": <string or null - a power/costume/weapon/location, or null>, '
    '"money_event": <string, required - the one concrete moment>, '
    '"query_text": <string, required - one full sentence describing the moment '
    'visually, for an image search>}'
)


def _client() -> OpenAI:
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/comic-video-pipeline",
            "X-Title": "Comic Video Pipeline",
        },
    )


def _build_user_prompt(answer_context: dict) -> str:
    question = answer_context.get("question", "")
    items = answer_context.get("items") or []
    lines = [f"QUESTION: {question}", "", "ITEMS:"]
    for it in items:
        lines.append(
            f"- entity={it.get('entity', '')!r} "
            f"how_or_why={it.get('how_or_why', '')!r} "
            f"drawable_moment={it.get('drawable_moment', '')!r}"
        )
    lines.append("")
    lines.append(_JSON_SPEC)
    return "\n".join(lines)


def _norm_nullable(value) -> str | None:
    """Coerce a free model's occasional string "null"/"none" into real None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a", "unknown"):
        return None
    return text


def _valid_target(parsed: dict) -> bool:
    """money_event + query_text are required; money_character/money_object are
    nullable by design (an answer's money shot may have no distinct object)."""
    if not isinstance(parsed, dict):
        return False
    return bool(str(parsed.get("money_event") or "").strip()) and \
        bool(str(parsed.get("query_text") or "").strip())


def derive_money_target(answer_context: dict, *, log: Callable[[str], None] = print) -> dict | None:
    """Ask a free OpenRouter model for the funnel's ONE money-shot target.

    Tries MONEY_MODEL_CHAIN in order (google/gemma-4-31b-it:free, then
    openai/gpt-oss-120b:free). Returns {"money_character": str|None,
    "money_object": str|None, "money_event": str, "query_text": str}, or None if
    both models fail / return unparseable JSON — the caller (funnel) then simply
    skips money-shot scoring for this project."""
    if not OPENROUTER_API_KEY:
        log("[money-shot] no OPENROUTER_API_KEY — skipping money_target")
        return None

    user_prompt = _build_user_prompt(answer_context)
    client = _client()

    for model in MONEY_MODEL_CHAIN:
        try:
            resp = client.with_options(timeout=60, max_retries=0).chat.completions.create(
                model=model,
                max_tokens=400,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            reason = "rate_limited" if _is_rate_limited(exc) else type(exc).__name__
            log(f"[money-shot] {model} failed ({reason}) — falling back")
            continue

        if not content or _detect_inline_rate_limit(content):
            log(f"[money-shot] {model} returned empty/rate-limited body — falling back")
            continue

        parsed = _parse_vlm_response(content)
        if _valid_target(parsed):
            log(f"[money-shot] {model} -> {parsed.get('money_event', '')!r}")
            return {
                "money_character": _norm_nullable(parsed.get("money_character")),
                "money_object": _norm_nullable(parsed.get("money_object")),
                "money_event": str(parsed["money_event"]).strip(),
                "query_text": str(parsed["query_text"]).strip(),
            }
        log(f"[money-shot] {model} returned unparseable/incomplete JSON — falling back")

    log("[money-shot] all models failed — money_target skipped")
    return None


# ─── ocr_money_hits: pure lexical scoring, no network ───────────────────────────

_OBJECT_SCORE = 2.0
_CHARACTER_SCORE = 1.0


def _term_pattern(term: str | None) -> re.Pattern | None:
    term = (term or "").strip()
    if not term:
        return None
    return re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)


def _panel_text_corpus(panel: dict) -> str:
    """Join a panel's OCR/dialog/sfx text channel (panels[].dialog entries built by
    Stage 2 — see stages/stage_2/pipeline.py). Prefers the `.ocr` ground truth over
    the (possibly VLM-paraphrased) `.text` field, same preference the dialog-truth
    gate uses elsewhere in Stage 2."""
    parts = []
    for entry in panel.get("dialog") or []:
        text = str(entry.get("ocr") or entry.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def ocr_money_hits(pages: list[dict], target: dict) -> dict[tuple[int, int], float]:
    """Score every panel's OCR/dialog/sfx text for money_object/money_character hits.

    Pure keyword matching (case-insensitive, word-boundary) — no network, no LLM.
    object match = +2.0, character match = +1.0, accumulated when both hit. Returns
    only panels with a nonzero score, keyed by (page_number, panel_index)."""
    object_re = _term_pattern((target or {}).get("money_object"))
    char_re = _term_pattern((target or {}).get("money_character"))
    if object_re is None and char_re is None:
        return {}

    hits: dict[tuple[int, int], float] = {}
    for page in pages:
        page_number = page.get("page_number")
        for panel in page.get("panels") or []:
            corpus = _panel_text_corpus(panel)
            if not corpus:
                continue
            score = 0.0
            if object_re is not None and object_re.search(corpus):
                score += _OBJECT_SCORE
            if char_re is not None and char_re.search(corpus):
                score += _CHARACTER_SCORE
            if score > 0:
                hits[(page_number, panel.get("index"))] = score
    return hits
