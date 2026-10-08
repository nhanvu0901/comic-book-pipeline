"""Narration Writer for screen_qa Mode (cites Film/Show and Year, no comic issues)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import config

logger = logging.getLogger(__name__)

SCREEN_NARRATION_PROMPT = """You are an engaging voice-over script writer for YouTube Shorts explaining superhero lore.
Write a 35-50 second narration answering this question based exclusively on SCREEN CANON (Film/TV/Game).

Question: {question}
Citation Work: {citation}
Summary: {summary}
Beats to Cover:
{beats_text}

Rules:
1. Ground the explanation explicitly in {citation} (e.g. 'In {citation}...').
2. DO NOT cite comic book issue numbers, volume years, or comic pages.
3. Keep the narration punchy, cinematic, and fast-paced (~100-130 words total).
4. Each scene MUST have verbatim 'visual_beats' (each sentence split into 2-3 visual phrases).
5. The combined visual_beats MUST equal the scene text word for word.

Return ONLY valid JSON matching this schema:
{{
  "title": "Compelling Short Title",
  "mode": "screen_qa",
  "scenes": [
    {{
      "scene_id": 1,
      "text": "Full sentence text of this scene.",
      "visual_beats": ["First phrase of sentence", "second phrase of sentence."]
    }}
  ]
}}
"""


def _get_client():
    from openai import OpenAI
    api_key = config.OPENROUTER_API_KEY or os.environ.get("OPENROUTER_API_KEY", "")
    base_url = config.OPENROUTER_BASE_URL or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


def _call_screen_narration_llm(context_data: dict, client: Any = None) -> dict:
    llm_client = client or _get_client()

    items = context_data.get("items") or []
    beats_lines = [f"- {it.get('title')}: {it.get('description')}" for it in items]
    beats_text = "\n".join(beats_lines)

    prompt = SCREEN_NARRATION_PROMPT.format(
        question=context_data.get("question", ""),
        citation=context_data.get("citation", ""),
        summary=context_data.get("summary", ""),
        beats_text=beats_text,
    )

    model = getattr(config, "ROUTER_MODEL", "google/gemini-2.5-flash-lite")
    resp = llm_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


def write_screen_narration(
    project_name: str,
    *,
    client: Any = None,
    log: Callable[[str], None] = logger.info,
) -> Path:
    """Generate narration.json for a screen_qa project from screen_context.json."""
    proj_dir = Path(config.PROJECTS_ROOT) / project_name
    ctx_file = proj_dir / "screen_context.json"
    if not ctx_file.exists():
        raise FileNotFoundError(f"Missing {ctx_file} — run screen research first")

    context_data = json.loads(ctx_file.read_text("utf-8"))
    log(f"[screen-narration] generating script for: {context_data.get('citation')}")

    data = _call_screen_narration_llm(context_data, client=client)
    data["mode"] = "screen_qa"
    data["question"] = context_data.get("question", "")
    data["citation"] = context_data.get("citation", "")

    # Clean / ensure visual_beats formatting
    scenes = data.get("scenes") or []
    for s in scenes:
        if not s.get("visual_beats"):
            s["visual_beats"] = [s.get("text", "")]

    nar_file = proj_dir / "narration.json"
    nar_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    log(f"[screen-narration] wrote narration to {nar_file} ({len(scenes)} scene(s))")
    return nar_file
