"""Screen Canon Research — Stage 1 mode `screen_qa`.

Conducts canonical research from SCREEN wikis (Fandom screen subdomains such as
marvelcinematicuniverse.fandom.com, dcau.fandom.com, dcextendeduniverse.fandom.com,
and Wikipedia film/animation articles) to produce screen_context.json.

Interface Contract:
screen_context.json = {
    "question": str,
    "items": [
        {
            "entity": str,
            "event": str,
            "adaptation_title": str,
            "year": int | str,
            "summary": str,
            "visual_query": str,
            "source_urls": list[str]
        }
    ]
}

Does NOT call stages.stage_1.answer_research.build_contexts (which requires Batcave reader URLs).
"""
from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import config
from config import get_project_dirs
from stages.research_scout.youcom import YouComClient
from utils.atomic_json import write_json_atomic

_MIN_ITEMS = 1
_SCREEN_DOMAINS = [
    "marvelcinematicuniverse.fandom.com",
    "dcau.fandom.com",
    "dcextendeduniverse.fandom.com",
    "arrow.fandom.com",
    "starwars.fandom.com",
    "disney.fandom.com",
    "memory-alpha.fandom.com",
    "en.wikipedia.org",
]

_SCREEN_RESEARCH_SYSTEM = """You are a screen-canon media research analyst specializing in films, TV series, \
and animation (e.g. MCU, DCEU, DCAU, Sony Spider-Verse, Star Wars). You are given a QUESTION \
and raw You.com Web Search evidence.

Your job is to identify real, verifiable SCREEN adaptation facts/events that answer the question.
Follow these rules strictly:
1. Ground facts in specific screen adaptations: films, TV episodes, or animated movies/series.
2. For each answer item, provide:
   - entity: the character, team, or artifact (e.g. "Scott Lang / Ant-Man", "Tony Stark", "Doctor Strange").
   - event: what specific screen action or development occurs to answer the question.
   - adaptation_title: official title of the movie or series (e.g. "Avengers: Endgame", "Spider-Man: Into the Spider-Verse").
   - year: release year as integer (e.g. 2019).
   - summary: 1-2 plain sentences describing what happens on screen.
   - visual_query: specific search hint for finding the corresponding video clip (e.g. "Avengers Endgame Scott Lang quantum realm time heist").
   - source_urls: list of source URLs from the provided evidence confirming this moment.
3. Order items logically or in dramatic progression (e.g. setup -> escalation -> climactic answer).
4. Return STRICT JSON only, matching this schema:
{
  "question": "...",
  "items": [
    {
      "entity": "...",
      "event": "...",
      "adaptation_title": "...",
      "year": 2019,
      "summary": "...",
      "visual_query": "...",
      "source_urls": ["..."]
    }
  ]
}
"""


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Safely parse JSON from raw LLM output, extracting from code blocks if present."""
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


def _clean_screen_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure all items conform to the screen_context contract."""
    cleaned: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        entity = str(it.get("entity", "")).strip()
        event = str(it.get("event", "")).strip()
        adaptation_title = str(it.get("adaptation_title", "")).strip()
        year_raw = it.get("year", "")
        year: int | str
        try:
            year = int(str(year_raw).strip())
        except (ValueError, TypeError):
            year = str(year_raw).strip()

        summary = str(it.get("summary", "")).strip()
        visual_query = str(it.get("visual_query", "")).strip()
        if not visual_query:
            visual_query = f"{adaptation_title} {entity} {event}".strip()

        urls_raw = it.get("source_urls", [])
        if isinstance(urls_raw, list):
            source_urls = [str(u).strip() for u in urls_raw if u]
        elif isinstance(urls_raw, str) and urls_raw:
            source_urls = [urls_raw.strip()]
        else:
            source_urls = []

        cleaned.append({
            "entity": entity,
            "event": event,
            "adaptation_title": adaptation_title,
            "year": year,
            "summary": summary,
            "visual_query": visual_query,
            "source_urls": source_urls,
        })
    return cleaned


def _call_openrouter(system_prompt: str, user_prompt: str) -> str:
    """Call OpenRouter LLM with json_object format."""
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("screen_research: OPENROUTER_API_KEY is not set")

    body = {
        "model": config.SCOUT_EVIDENCE_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        config.OPENROUTER_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
            choices = payload.get("choices") or []
            if not choices:
                raise RuntimeError(f"screen_research: Empty choices from LLM: {payload}")
            return str(choices[0].get("message", {}).get("content", ""))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise RuntimeError(f"screen_research: OpenRouter call failed: {exc}") from exc


def research_screen_canon(
    question: str,
    *,
    max_items: int = 5,
    hint: str = "",
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Web-research a screen media question and structure results into screen_context format."""
    question = (question or "").strip()
    if not question:
        raise ValueError("research_screen_canon: empty question")

    log(f"[screen-research] researching: {question!r} (max {max_items} items) ...")

    query = f"{question} {hint}".strip()
    # Emphasize screen wikis in search if not specified
    client = YouComClient()
    search_res = client.search(query, profile=None)
    evidence_payload = search_res.payload if search_res.ok else {}

    user_prompt = (
        f"QUESTION: {question}\n\n"
        f"HINT: {hint or 'none'}\n\n"
        f"Max items: {max_items}\n\n"
        f"RAW WEB SEARCH EVIDENCE:\n{json.dumps(evidence_payload, ensure_ascii=False)}"
    )

    raw_llm = _call_openrouter(_SCREEN_RESEARCH_SYSTEM, user_prompt)
    parsed = _extract_json(raw_llm)
    if not parsed or not isinstance(parsed.get("items"), list):
        raise RuntimeError(f"screen_research: Failed to parse valid items JSON from LLM: {raw_llm[:300]}")

    items = _clean_screen_items(parsed.get("items", []))[:max_items]
    if len(items) < _MIN_ITEMS:
        raise RuntimeError(f"screen_research: Expected at least {_MIN_ITEMS} items, got {len(items)}")

    log(f"[screen-research] ✓ found {len(items)} screen item(s)")
    return {
        "question": question,
        "items": items,
    }


def save_screen_context(
    project_name: str,
    context: dict[str, Any],
    log: Callable[[str], None] = print,
) -> Path:
    """Save screen_context.json to the project directory."""
    root = get_project_dirs(project_name)["root"]
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / "screen_context.json"

    data = {
        "question": context.get("question", ""),
        "items": _clean_screen_items(context.get("items", [])),
    }

    write_json_atomic(out_path, data)
    log(f"[screen-research] saved {out_path} ({len(data['items'])} items)")
    return out_path


def research_screen(
    question: str,
    project_name: str,
    *,
    max_items: int = 5,
    hint: str = "",
    log: Callable[[str], None] = print,
) -> Path:
    """Full research step: scout screen canon and persist screen_context.json."""
    data = research_screen_canon(question, max_items=max_items, hint=hint, log=log)
    return save_screen_context(project_name, data, log=log)
