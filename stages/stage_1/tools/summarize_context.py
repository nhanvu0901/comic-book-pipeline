"""Structured comic-context summarizer used to ground every downstream LLM/VLM call."""
import json
import re
from typing import Callable

from stages.stage_3._llm import call_with_chain


_SYSTEM = """You are a comic-context summarizer. Given a comic's metadata and the full wiki plot text, produce a STRUCTURED FACTUAL summary in JSON. This summary is injected into downstream prompts for:
- a vision LLM that analyzes comic panels — it needs the character roster, setting, and key objects so it can label entities by name, NOT story beats (which would bias its visual interpretation).
- a text LLM that writes narration — it needs the full story arc.

Rules:
- Be FACTUAL. Use only what's in the wiki plot text and known canon names. No invented motives, no embellished language, no spoilers withheld.
- Visual descriptions: derive from wiki text or reliable canon (e.g. "the Thing" has orange rocky skin). Leave empty if uncertain.
- key_objects = items, locations, OR named concepts the panel analyst should be able to recognize on a page (e.g. "Venom symbiote", "the Baxter Building", "Battleworld" as a backstory event).
- Include EVERY character with a role beyond a one-off mention. Capture dialogue aliases too.
- story_arc must include the ending. No spoiler-protection.

Return ONLY JSON. No markdown fences, no prose."""


_RESPONSE_SHAPE = """{
  "story_arc": "<2-3 paragraph factual story summary, 600-1200 chars total. Cover setup, complication, climax, ending.>",
  "characters": [
    {
      "name": "<canonical name>",
      "aliases": ["<alt names used in dialogue>"],
      "role": "<1-line factual role in this issue>",
      "visual": "<1-line physical description if known, empty string if uncertain>"
    }
  ],
  "setting": "<when + where, <=200 chars>",
  "key_objects": ["<entity or location>", "..."]
}"""


def summarize_context(comic_context: dict, *, progress: Callable[[str], None] | None = None) -> dict:
    """Call the LLM chain to produce a structured summary dict from the wiki plot text."""
    log = progress or (lambda _msg: None)
    plot = (comic_context.get("plot_summary") or "").strip()
    if not plot:
        log("[stage1] summarize: no plot_summary, skipping")
        return _empty_summary()

    user = (
        f"COMIC: {comic_context.get('title', '?')}\n"
        f"SERIES: {comic_context.get('series', '?')} {comic_context.get('issues', '')}\n"
        f"YEAR: {comic_context.get('year', '?')}\n"
        f"PUBLISHER: {comic_context.get('publisher', '?')}\n"
        f"WRITER / ARTIST: {comic_context.get('writer', '?')} / {comic_context.get('artist', '?')}\n"
        f"KNOWN CHARACTERS (from identification): {', '.join(comic_context.get('characters', [])) or '?'}\n\n"
        f"WIKI PLOT TEXT ({len(plot)} chars):\n{plot}\n\n"
        f"Return JSON in this exact shape:\n{_RESPONSE_SHAPE}"
    )

    log(f"[stage1] summarize: prompt {len(user)} chars, calling LLM chain")
    raw, model_used = call_with_chain(
        system=_SYSTEM,
        user=user,
        max_tokens=2000,
        progress=progress,
        label="summarize",
    )
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        log("[stage1] summarize: unparseable JSON, returning empty summary")
        return _empty_summary()

    summary = {
        "story_arc": str(parsed.get("story_arc", "")).strip(),
        "characters": [_clean_character(c) for c in (parsed.get("characters") or []) if isinstance(c, dict)],
        "setting": str(parsed.get("setting", "")).strip(),
        "key_objects": [str(k).strip() for k in (parsed.get("key_objects") or []) if str(k).strip()],
        "_summarizer_model": model_used,
    }
    log(f"[stage1] summarize: ok ({len(summary['characters'])} characters, {len(summary['key_objects'])} objects, model={model_used})")
    return summary


def enrich_with_summary(comic_context: dict, *, progress: Callable[[str], None] | None = None) -> dict:
    """Mutate comic_context in place by adding the structured `summary` field. Returns the same dict."""
    if comic_context.get("summary"):
        return comic_context
    comic_context["summary"] = summarize_context(comic_context, progress=progress)
    return comic_context


def format_for_vlm(summary: dict) -> str:
    """Compact character roster + setting + key objects, no story beats. Target <=500 chars."""
    if not summary or not isinstance(summary, dict):
        return ""
    lines: list[str] = []
    setting = (summary.get("setting") or "").strip()
    if setting:
        lines.append(f"Setting: {setting}")
    chars = summary.get("characters") or []
    if chars:
        lines.append("Characters:")
        for c in chars:
            name = c.get("name", "")
            aliases = ", ".join(c.get("aliases") or [])
            role = c.get("role", "")
            visual = c.get("visual", "")
            head = f"- {name}" + (f" ({aliases})" if aliases else "")
            tail = " — ".join(p for p in (role, visual) if p)
            lines.append(f"{head}: {tail}" if tail else head)
    objects = summary.get("key_objects") or []
    if objects:
        lines.append("Key objects: " + ", ".join(objects))
    return "\n".join(lines)


def format_for_narration(summary: dict) -> str:
    """Full structured dump including story arc, for Stage 4 prompts."""
    if not summary or not isinstance(summary, dict):
        return ""
    parts: list[str] = []
    arc = (summary.get("story_arc") or "").strip()
    if arc:
        parts.append(f"STORY ARC:\n{arc}")
    roster = format_for_vlm(summary)
    if roster:
        parts.append(roster)
    return "\n\n".join(parts)


def _clean_character(entry: dict) -> dict:
    aliases = entry.get("aliases") or []
    return {
        "name": str(entry.get("name", "")).strip(),
        "aliases": [str(a).strip() for a in aliases if str(a).strip()],
        "role": str(entry.get("role", "")).strip(),
        "visual": str(entry.get("visual", "")).strip(),
    }


def _empty_summary() -> dict:
    return {"story_arc": "", "characters": [], "setting": "", "key_objects": [], "_summarizer_model": ""}


def _extract_json(raw: str) -> dict | None:
    for pattern in (r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"):
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(raw[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None
