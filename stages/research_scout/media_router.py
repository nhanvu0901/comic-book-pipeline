"""LLM-based Media Source Router using Gemini 2.5 Flash Lite."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from pydantic import ValidationError

import config
from .router_schema import QuestionRouteResponse

logger = logging.getLogger(__name__)

DEFAULT_ROUTER_MODEL = "google/gemini-2.5-flash-lite"
_MAX_ANSWER_TOKENS = 2500

_SCHEMA_PROMPT = json.dumps(QuestionRouteResponse.model_json_schema(), indent=2)

SYSTEM_PROMPT = f"""You are a strict, type-safe Media Router for a superhero video production pipeline.
Given a question and its web search results (URLs, titles, snippets, domain indicators), you must classify whether the event predominantly lives in SCREEN media (film, tv_animation, game), in COMICS, or in BOTH (mixed).

Visual Source Rule:
- primary_medium in ["film", "tv_animation", "game"] -> visual_source = "youtube"
- primary_medium == "comic" -> visual_source = "comic"
- primary_medium == "mixed" -> visual_source = "both"

For events that exist prominently in both comics and screen adaptations, identify the specific instances or adaptations in your item list and specify adaptation_title.

Comic source reference (comic_series / comic_issue / comic_year):
- Whenever an item is based on, adapts, or originates from a comic, fill comic_series with that comic's series (or event/mini-series) title and comic_year with the year it started, even when the search results only mention the screen work.
- Fill comic_issue only if you are sure of the exact issue number; otherwise leave it null. Never guess an issue number.
- Leave all three null ONLY when the item has no comic counterpart at all (a story that exists only on screen).

Keep the answer short: at most 6 items, at most 3 evidence_urls per item, and a reason of one short sentence.

You must output strictly valid JSON matching this schema:
{_SCHEMA_PROMPT}
Do not include any explanation outside the JSON object.
"""


def _get_default_client():
    from openai import OpenAI
    api_key = config.OPENROUTER_API_KEY or os.environ.get("OPENROUTER_API_KEY", "")
    base_url = config.OPENROUTER_BASE_URL or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


def route_media_source(
    question: str,
    search_results: list[dict],
    *,
    model: str | None = None,
    max_retries: int = 2,
    client: Any = None,
) -> QuestionRouteResponse:
    """Route a question and its search evidence to determine primary medium and visual source.

    Retries up to max_retries on malformed or schema-invalid JSON responses.
    """
    selected_model = model or getattr(config, "ROUTER_MODEL", DEFAULT_ROUTER_MODEL)
    llm_client = client or _get_default_client()

    evidence_lines = []
    for i, w in enumerate(search_results[:10], 1):
        u = w.get("url", "")
        t = w.get("title", "")
        snippets = w.get("snippets", [])
        if isinstance(snippets, list):
            s = " ".join(snippets)
        else:
            s = str(snippets)
        evidence_lines.append(f"[{i}] URL: {u}\nTitle: {t}\nSnippet: {s[:250]}\n")
    search_context = "\n".join(evidence_lines)

    user_prompt = (
        f"Question: {question}\n\n"
        f"Search Results:\n{search_context}\n\n"
        "Perform routing classification for this question and its event items."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        resp = llm_client.chat.completions.create(
            model=selected_model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=_MAX_ANSWER_TOKENS,
        )
        content = resp.choices[0].message.content or ""
        try:
            parsed = QuestionRouteResponse.model_validate_json(content)
            return parsed
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("Router attempt %d validation failed: %s", attempt, exc)
            if attempt < max_retries:
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": f"Schema validation error: {exc}. Return ONLY valid JSON matching the requested schema.",
                })

    raise last_error or RuntimeError("Media routing failed after retries")
