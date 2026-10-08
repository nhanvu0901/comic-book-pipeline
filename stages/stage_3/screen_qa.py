"""Stage 3 Screen Narration Writer for the "screen_qa" mode.

Produces a ~50-65s Q&A script grounded in SCREEN CANON (movies, TV shows, animation),
citing adaptation title + release year (e.g. 'Avengers: Endgame (2019)') instead of comic issue numbers.

Interface Contract:
- narration.json uses existing Scene schema (stages/stage_3/schema.py) with mode 'screen_qa';
- each visual_beat is a dict {text, query}, where query is a clip-search hint
  (compatible with stages/stage_5/shots.py _vb_text);
- reads screen_context.json = {question, items:[{entity, event, adaptation_title, year, summary, visual_query, source_urls}]}.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from config import CREATIVE_LLM_MODELS, get_project_dirs
from utils.atomic_json import write_json_atomic
from ._llm import call_with_chain
from .schema import Narration, Scene

_WORDS_PER_SEC = 3.4

_SCREEN_QA_SYSTEM = """You are QAWriter for a screen-media YouTube Short. The video answers ONE question \
grounded in real movies, TV series, or animated adaptations (the ITEMS below).

HARD RULES:
1. CITE THE ADAPTATION TITLE AND YEAR naturally in the spoken lines (e.g. "...in Avengers: Endgame (2019)...", \
"...in Spider-Man 2 (2004)..."). NEVER cite comic issue numbers or '#'.
2. PUNCHY B2 SPOKEN ENGLISH:
   - Plain words, active verbs, concise sentences.
   - Tell what happened and why it answers the question.
   - Avoid generic fluff ("It is fascinating to see", "Let's dive in", "Watch till the end").
3. VISUAL BEATS (every scene):
   - Split each scene's text into 2-4 sequential verbatim clause fragments.
   - For each fragment, provide a specific clip-search query ("query") to find matching video footage.
   - Return visual_beats as an array of objects: [{"text": "<verbatim fragment>", "query": "<clip search hint>"}].
4. STRUCTURE:
   - "title": 4-8 words statement.
   - "hook": Opening line (8-22 words) framing the question and promising the answer.
   - "scenes": 2-4 scenes covering the key items in order.
   - "outro": Closing takeaway line (6-16 words) summarizing what the answer proves.

Return STRICT JSON only, matching:
{
  "title": "...",
  "hook": "...",
  "outro": "...",
  "scenes": [
    {
      "text": "...",
      "visual_beats": [
        {"text": "...", "query": "..."}
      ]
    }
  ]
}
"""


def _normalize_visual_beats(raw_beats: list[Any], default_query: str = "") -> list[dict[str, str]]:
    """Ensure visual_beats is a list of dicts with 'text' and 'query' keys."""
    normalized: list[dict[str, str]] = []
    for b in raw_beats:
        if isinstance(b, dict):
            txt = str(b.get("text", "")).strip()
            qry = str(b.get("query", "")).strip() or default_query
            if txt:
                normalized.append({"text": txt, "query": qry})
        elif isinstance(b, str) and b.strip():
            normalized.append({"text": b.strip(), "query": default_query})
    return normalized


def _extract_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _call_llm_chain(
    system: str,
    user: str,
    models: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """Dispatch LLM call through call_with_chain."""
    chain = models if models else list(CREATIVE_LLM_MODELS)
    return call_with_chain(
        system=system,
        user=user,
        models=chain,
        max_tokens=2400,
        progress=progress,
        label="screen_write",
    )


def _build_deterministic_fallback(
    question: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Grounded fallback script when LLM is unavailable or fails validation."""
    title = f"How {question.rstrip('?')} Was Answered"[:60]
    first_item = items[0] if items else {}
    title_cited = f"{first_item.get('adaptation_title', 'the film')} ({first_item.get('year', '')})".strip()
    hook = f"The answer to {question.rstrip('?').lower()} begins in {title_cited}."

    scenes = []
    for it in items:
        entity = it.get("entity", "")
        event = it.get("event", "")
        adapt = it.get("adaptation_title", "")
        yr = it.get("year", "")
        summary = it.get("summary", "")
        v_query = it.get("visual_query") or f"{adapt} {entity} {event}".strip()

        cite_str = f"In {adapt} ({yr}), " if adapt and yr else ""
        text = f"{cite_str}{entity} {event}. {summary}".strip()
        vbs = [
            {"text": f"{cite_str}{entity} {event}.".strip(), "query": v_query},
            {"text": summary, "query": v_query},
        ]
        scenes.append({
            "text": text,
            "visual_beats": [b for b in vbs if b["text"]],
        })

    outro = f"Together, these screen events reveal how {question.rstrip('?').lower()}."
    return {
        "title": title,
        "hook": hook,
        "outro": outro,
        "scenes": scenes,
    }


def write_screen_qa(
    project_name: str,
    *,
    hook_hint: str = "",
    progress: Callable[[str], None] | None = None,
    model: str | None = None,
) -> Narration:
    """Orchestrates screen narration writing from screen_context.json."""
    log = progress or (lambda _m: None)
    root = get_project_dirs(project_name)["root"]
    context_path = root / "screen_context.json"
    if not context_path.exists():
        raise FileNotFoundError(
            f"[screen_qa] missing {context_path} — run Stage 1 screen research first."
        )

    context = json.loads(context_path.read_text())
    question = str(context.get("question", "")).strip()
    items = context.get("items") or []
    if not items:
        raise RuntimeError(f"[screen_qa] {context_path} has no items.")

    log(f"[screen_qa] writing screen Q&A narration for {len(items)} item(s)...")

    user_prompt = (
        f"QUESTION: {question}\n\n"
        f"HOOK HINT: {hook_hint or 'none'}\n\n"
        f"SCREEN CANON ITEMS:\n{json.dumps(items, indent=2, ensure_ascii=False)}"
    )

    parsed: dict[str, Any] | None = None
    llm_model_used = ""
    try:
        models = [model] if model else None
        raw_text, llm_model_used = _call_llm_chain(_SCREEN_QA_SYSTEM, user_prompt, models=models, progress=progress)
        parsed = _extract_json(raw_text)
    except Exception as exc:
        log(f"[screen_qa] LLM call failed ({exc}); using grounded fallback")

    if not parsed or not isinstance(parsed.get("scenes"), list) or not parsed.get("scenes"):
        log("[screen_qa] writer returned invalid scenes; building deterministic fallback")
        parsed = _build_deterministic_fallback(question, items)

    title = str(parsed.get("title", "")).strip() or f"Answer: {question}"[:50]
    hook = str(parsed.get("hook", "")).strip()
    raw_scenes = parsed.get("scenes", [])

    scenes: list[Scene] = []
    for i, s_data in enumerate(raw_scenes, start=1):
        txt = str(s_data.get("text", "")).strip()
        item_ref = items[min(i - 1, len(items) - 1)] if items else {}
        fallback_query = item_ref.get("visual_query") or f"{item_ref.get('adaptation_title', '')} {item_ref.get('entity', '')}"

        vbs = _normalize_visual_beats(s_data.get("visual_beats", []), default_query=fallback_query)
        if not vbs:
            vbs = [{"text": txt, "query": fallback_query}]

        wc = len(txt.split())
        target_sec = round(wc / _WORDS_PER_SEC, 2)
        scene = Scene(
            scene_id=i,
            text=txt,
            page_ref=0,
            panel_ref=-1,
            word_count=wc,
            target_seconds=target_sec,
            visual_beats=vbs,
            is_intro=(i == 1),
            is_outro=(i == len(raw_scenes)),
        )
        scenes.append(scene)

    if not hook and scenes:
        hook = scenes[0].text[:120]

    total_words = sum(s.word_count for s in scenes)
    est_dur = round(sum(s.target_seconds for s in scenes), 1)

    narration = Narration(
        mode="screen_qa",
        title=title,
        hook=hook,
        banner_title=title,
        scenes=scenes,
        total_word_count=total_words,
        estimated_duration_seconds=est_dur,
        words_per_second=_WORDS_PER_SEC,
        source_project=project_name,
        llm_model=llm_model_used,
    )
    log(f"[screen_qa] written {len(scenes)} scenes (~{est_dur}s, {total_words} words)")
    return narration


def save_screen_narration(
    narration: Narration,
    project_name: str,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Save narration.json to project root conforming to the schema."""
    log = progress or (lambda _m: None)
    root = get_project_dirs(project_name)["root"]
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / "narration.json"

    data = narration.to_dict()
    write_json_atomic(out_path, data)
    log(f"[screen_qa] saved narration → {out_path}")
    return out_path
