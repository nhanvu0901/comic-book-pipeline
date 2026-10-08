"""Screen Media Lore Research for screen_qa Mode (MCU, DC Film, Animation, Games)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import config

logger = logging.getLogger(__name__)

RESEARCH_PROMPT_TEMPLATE = """You are an expert researcher on superhero screen media (MCU, Sony Spider-Verse, DCEU, DCAU, Batman Beyond, etc.).
Given a question about screen lore, identify the primary film/show/game that answers it, cite it accurately with Year, and outline 2 to 4 key story beats/moments.

Question: {question}

Return ONLY valid JSON matching this schema:
{{
  "work_title": "Title of film or show (e.g. 'Avengers: Endgame')",
  "release_year": "YYYY (e.g. '2019')",
  "citation": "Full citation e.g. 'Avengers: Endgame (2019)'",
  "summary": "Concise 1-2 sentence direct answer to the question",
  "items": [
    {{
      "title": "Short title of moment/beat",
      "description": "What happens in this beat",
      "drawable_moment": "Visually descriptive scene suitable for clip searching",
      "clip_query": "Exact YouTube search query to find this video clip"
    }}
  ]
}}
"""


def _get_client():
    from openai import OpenAI
    api_key = config.OPENROUTER_API_KEY or os.environ.get("OPENROUTER_API_KEY", "")
    base_url = config.OPENROUTER_BASE_URL or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


def _call_screen_research_llm(question: str, client: Any = None) -> dict:
    llm_client = client or _get_client()
    prompt = RESEARCH_PROMPT_TEMPLATE.format(question=question)
    model = getattr(config, "ROUTER_MODEL", "google/gemini-2.5-flash-lite")
    resp = llm_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


def research_screen(
    question: str,
    *,
    max_items: int = 4,
    client: Any = None,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Research a screen media lore question."""
    log(f"[screen-research] researching lore for: {question!r}")
    data = _call_screen_research_llm(question, client=client)
    items = data.get("items") or []
    if len(items) > max_items:
        data["items"] = items[:max_items]
    return data


def build_screen_context(
    question: str,
    research: dict,
    project_name: str,
    *,
    log: Callable[[str], None] = logger.info,
) -> Path:
    """Save screen_context.json into the project directory."""
    proj_dir = Path(config.PROJECTS_ROOT) / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)

    context_payload = {
        "mode": "screen_qa",
        "question": question,
        "work_title": research.get("work_title", ""),
        "release_year": research.get("release_year", ""),
        "citation": research.get("citation", ""),
        "summary": research.get("summary", ""),
        "items": research.get("items", []),
    }

    ctx_file = proj_dir / "screen_context.json"
    ctx_file.write_text(json.dumps(context_payload, indent=2, ensure_ascii=False), "utf-8")
    log(f"[screen-research] wrote {ctx_file}")
    return ctx_file
