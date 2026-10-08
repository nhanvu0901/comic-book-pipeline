"""Screen Canon Research — Stage 1 for mode `screen_qa`.

Answers a question about films / TV / animated adaptations from WEB EVIDENCE (You.com web
search) and structures it into screen_context.json. Nothing comic-related runs here: no
Batcave, no reader URLs, and stages.stage_1.answer_research (build_contexts / research_answer)
is never called.

Grounding rules (the comic Q&A research is held to the same bar):
  * no web evidence  -> hard error. The model is never allowed to answer from memory.
  * every item must cite at least one URL that really was in the search results; URLs the
    model made up are stripped, and an item left with none is dropped.
  * year must be a plausible release year, entity/title must be non-empty, duplicates collapse.

Interface contract (shared with video-qa/p3-visual and stages/stage_3/screen_qa.py):
screen_context.json = {
    "question": str,
    "answer_summary": str,           # OPTIONAL, one plain sentence; absent when not researched
    "items": [
        {
            "entity": str,
            "event": str,
            "adaptation_title": str,
            "year": int,
            "summary": str,
            "visual_query": str,
            "source_urls": list[str]
        }
    ]
}
"""
from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import config
from config import get_project_dirs
from stages.research_scout.youcom import YouComClient
from utils.atomic_json import write_json_atomic

_MIN_ITEMS = 1

# Evidence handed to the LLM: the raw You.com payload is ~130 KB (thumbnails, favicons, full
# page markdown). Only url/title/description/snippets and a bounded slice of the page body
# are useful, and a smaller prompt keeps the model on the evidence.
_EVIDENCE_MAX_CHARS = 24_000
_EVIDENCE_CONTENT_CHARS = 3_000
_EVIDENCE_SNIPPETS = 4
_EVIDENCE_SNIPPET_CHARS = 400

_YEAR_FIRST_SCREEN_ERA = 1888       # earliest moving pictures
_YEAR_LOOKAHEAD = 2                 # announced titles

_SCREEN_RESEARCH_SYSTEM = """You are a screen-canon research analyst for films, TV series and \
animated adaptations. You are given a QUESTION and WEB EVIDENCE: a JSON list of pages, each with \
url, title, description, snippets and content.

Your job: find the specific on-screen moments that answer the question.

HARD RULES:
1. USE ONLY THE EVIDENCE. Never add a fact from memory. If the evidence does not support a \
moment, leave it out - fewer verified items beat more guessed ones.
2. One item = one concrete on-screen moment (what a viewer could be shown), from a named film, \
episode or series.
3. Item fields:
   - entity: the character, team or object the moment is about.
   - event: what happens on screen that answers the question (a short verb phrase).
   - adaptation_title: the official title of the film / series / episode, as the evidence names it.
   - year: release year of that title (integer).
   - summary: 1-2 plain sentences saying what happens on screen and why it answers the question.
   - visual_query: a short phrase to find that scene as a video clip: title + character + what \
is on screen.
   - source_urls: URLs copied EXACTLY from the evidence pages that state this moment.
4. Order the items so the answer builds: setup first, the payoff / most surprising item LAST. \
They will be narrated in exactly this order.
5. answer_summary: ONE plain sentence that answers the question, from the evidence only. For a \
why/how question it is the causal answer; for a list question it is what the items have in common.
6. Return STRICT JSON only, no markdown fences, matching:
{
  "question": "...",
  "answer_summary": "...",
  "items": [
    {"entity": "...", "event": "...", "adaptation_title": "...", "year": 0000,
     "summary": "...", "visual_query": "...", "source_urls": ["..."]}
  ]
}
"""


# ─── evidence ───────────────────────────────────────────────────────────────


def _norm_url(url: str) -> str:
    """Comparison key for a URL: scheme/host case, fragment and trailing slash don't matter."""
    parts = urllib.parse.urlsplit(str(url).strip())
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _compact_evidence(payload: Any) -> list[dict[str, Any]]:
    """Reduce a You.com search payload to a bounded list of {url,title,description,snippets,content}.

    Tolerant of any malformed shape (returns [] rather than raising): the caller treats an empty
    list as "no evidence"."""
    if not isinstance(payload, Mapping):
        return []
    results = payload.get("results")
    entries: list[Any] = []
    if isinstance(results, Mapping):
        for group in results.values():
            if isinstance(group, list):
                entries.extend(group)
    elif isinstance(results, list):
        entries = list(results)

    evidence: list[dict[str, Any]] = []
    used = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        item: dict[str, Any] = {"url": url.strip()}
        for key in ("title", "description"):
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                item[key] = val.strip()
        snippets = [str(x).strip()[:_EVIDENCE_SNIPPET_CHARS]
                    for x in (entry.get("snippets") or []) if str(x).strip()]
        if snippets:
            item["snippets"] = snippets[:_EVIDENCE_SNIPPETS]
        contents = entry.get("contents")
        body = contents.get("markdown") if isinstance(contents, Mapping) else contents
        if isinstance(body, str) and body.strip():
            item["content"] = body.strip()[:_EVIDENCE_CONTENT_CHARS]
        size = len(json.dumps(item, ensure_ascii=False))
        if used + size > _EVIDENCE_MAX_CHARS:
            item.pop("content", None)
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > _EVIDENCE_MAX_CHARS:
                break
        evidence.append(item)
        used += size
    return evidence


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


def _parse_year(raw: Any) -> int | None:
    """A plausible release year from an int or a string like '2019' / '2019-04-26', else None."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        year = raw
    else:
        m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", str(raw or ""))
        if not m:
            return None
        year = int(m.group(1))
    if _YEAR_FIRST_SCREEN_ERA <= year <= date.today().year + _YEAR_LOOKAHEAD:
        return year
    return None


def _clean_screen_items(
    items: list[dict[str, Any]],
    allowed_urls: Iterable[str] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Normalise items to the screen_context contract and drop the ones that can't be trusted.

    `allowed_urls` (the URLs that were really in the search results) turns on URL grounding:
    URLs outside it are stripped and an item left with none is dropped. None = no URL check.
    """
    say = log or (lambda _m: None)
    canonical = ({_norm_url(u): u for u in allowed_urls} if allowed_urls is not None else None)
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        entity = str(it.get("entity", "")).strip()
        event = str(it.get("event", "")).strip()
        adaptation_title = str(it.get("adaptation_title", "")).strip()
        if not entity or not adaptation_title:
            say(f"[screen-research] dropped item without entity/title: {entity!r} / {adaptation_title!r}")
            continue
        year = _parse_year(it.get("year"))
        if year is None:
            say(f"[screen-research] dropped {entity!r}: implausible year {it.get('year')!r}")
            continue

        urls_raw = it.get("source_urls", [])
        if isinstance(urls_raw, list):
            source_urls = [str(u).strip() for u in urls_raw if u and str(u).strip()]
        elif isinstance(urls_raw, str) and urls_raw.strip():
            source_urls = [urls_raw.strip()]
        else:
            source_urls = []
        if canonical is not None:
            source_urls = [canonical[_norm_url(u)] for u in source_urls if _norm_url(u) in canonical]
            if not source_urls:
                say(f"[screen-research] dropped {entity!r}: no source URL found in the search evidence")
                continue

        key = (entity.casefold(), event.casefold())
        if key in seen:
            continue
        seen.add(key)

        visual_query = str(it.get("visual_query", "")).strip()
        if not visual_query:
            visual_query = f"{adaptation_title} {entity} {event}".strip()
        cleaned.append({
            "entity": entity,
            "event": event,
            "adaptation_title": adaptation_title,
            "year": year,
            "summary": str(it.get("summary", "")).strip(),
            "visual_query": visual_query,
            "source_urls": source_urls,
        })
    return cleaned


def _call_llm(system_prompt: str, user_prompt: str, log: Callable[[str], None] = print) -> str:
    """Structure the evidence with the project's LLM chain (honours config.FREE_MODEL).

    Same chain helper Stage 3 uses, so a rate-limited / empty model falls over to the next one
    instead of failing the whole research on a single flaky provider."""
    from stages.stage_3._llm import call_with_chain

    def _usable(raw: str) -> bool:
        parsed = _extract_json(raw)
        return isinstance(parsed, dict) and isinstance(parsed.get("items"), list)

    chain: list[str] = []
    for m in [config.SCOUT_EVIDENCE_MODEL, *config.CREATIVE_LLM_MODELS]:
        if m and m not in chain:
            chain.append(m)
    raw, _model = call_with_chain(
        system=system_prompt, user=user_prompt, models=chain, max_tokens=3000,
        progress=log, label="screen_research", validator=_usable,
    )
    return raw


def research_screen_canon(
    question: str,
    *,
    max_items: int = 5,
    hint: str = "",
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Web-research a screen-media question and structure the result as screen_context."""
    question = (question or "").strip()
    if not question:
        raise ValueError("research_screen_canon: empty question")

    log(f"[screen-research] researching: {question!r} (max {max_items} items) ...")

    search = YouComClient().search(f"{question} {hint}".strip(), profile=None)
    evidence = _compact_evidence(search.payload) if search.ok else []
    if not evidence:
        raise RuntimeError(
            f"screen_research: no web evidence for {question!r} "
            f"({search.error or 'search returned no results'}); refusing to answer from model memory"
        )
    log(f"[screen-research] {len(evidence)} evidence page(s)")

    user_prompt = (
        f"QUESTION: {question}\n\n"
        f"HINT: {hint or 'none'}\n\n"
        f"Max items: {max_items}\n\n"
        f"WEB EVIDENCE:\n{json.dumps(evidence, ensure_ascii=False)}"
    )
    raw_llm = _call_llm(_SCREEN_RESEARCH_SYSTEM, user_prompt, log=log)
    parsed = _extract_json(raw_llm)
    if not parsed or not isinstance(parsed.get("items"), list):
        raise RuntimeError(f"screen_research: Failed to parse valid items JSON from LLM: {raw_llm[:300]}")

    items = _clean_screen_items(
        parsed["items"], allowed_urls=[e["url"] for e in evidence], log=log)[:max_items]
    if len(items) < _MIN_ITEMS:
        raise RuntimeError(
            f"screen_research: Expected at least {_MIN_ITEMS} verified item(s), got {len(items)}")

    log(f"[screen-research] ✓ found {len(items)} screen item(s)")
    out: dict[str, Any] = {"question": question, "items": items}
    summary = str(parsed.get("answer_summary") or "").strip()
    if summary:
        out["answer_summary"] = summary
    return out


def save_screen_context(
    project_name: str,
    context: dict[str, Any],
    log: Callable[[str], None] = print,
) -> Path:
    """Save screen_context.json to the project directory."""
    root = get_project_dirs(project_name)["root"]
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / "screen_context.json"

    data: dict[str, Any] = {"question": context.get("question", "")}
    summary = str(context.get("answer_summary") or "").strip()
    if summary:
        data["answer_summary"] = summary
    data["items"] = _clean_screen_items(context.get("items", []))

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
