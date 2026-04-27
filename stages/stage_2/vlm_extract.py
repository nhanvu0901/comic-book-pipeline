"""
VLM semantic enrichment of a single comic page.

Given the page image and YOLO-detected panel bboxes, ask the VLM for:
  - page_type classification: "cover" | "story" | "skip"
  - text blocks (speech/narration/sfx/caption) attributed to panels + speakers
  - one-sentence description per panel + characters + emotion
  - page summary

Only `cover` and `story` pages get full metadata extracted. `skip` pages
(promotions, recap/summary pages, ads, letter columns, "next issue" previews,
solicit/credits, blanks) return empty panels/text_blocks with a skip_reason.

Uses OpenRouter's OpenAI-compatible /v1/chat/completions endpoint with the
vision-capable model from config.VLM_MODEL (default google/gemma-4-26b-a4b-it:free).
"""
import base64
import json
import re
from pathlib import Path

from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, VLM_MODEL


_SYSTEM_PROMPT = """You are a comic book page analyst. You receive one page image and a list of pre-detected panel bounding boxes.

STEP 1 — Classify the page into ONE of three types:

  • "cover"  — front/back cover, issue cover, chapter cover, variant cover.
               Usually shows title, issue number, main character(s) in a splash pose.
               EXTRACT metadata for covers (characters visible, title text, etc.).

  • "story"  — an actual narrative page from inside the issue.
               Has panels with action, dialogue, or plot progression.
               EXTRACT full metadata.

  • "skip"   — NOT worth extracting. Any of:
               - promotional / advertisement page (house ads, creator credits, shop ads)
               - recap / "previously on" / summary page
               - "next issue" preview or teaser
               - letter column / reader mail
               - solicit / editorial / credits-only page
               - blank, filler, or end-of-story divider
               For skip pages: set page_type="skip", fill skip_reason with the specific
               category, and return EMPTY panels, text_blocks, and page_summary.
               DO NOT extract any metadata for skip pages.

STEP 2 — For cover + story pages ONLY, do this:

  2a. For EACH panel: write a one-sentence visual description, list characters present,
      and name the dominant emotion.
  2b. Extract EVERY visible text element (speech bubbles, narration/caption boxes,
      SFX text, cover title/subtitle/credits). For each: classify type, identify the
      speaker (null for narration/sfx/caption/title), and assign it to the panel whose
      bbox contains it (panel_index). Use -1 if the text is outside all panels.
  2c. Write a 2-3 sentence page_summary. For covers: describe what's visually depicted
      (e.g. "Cover: Spider-Man in classic red/blue swings past the Daily Bugle…").
      For story pages: describe the key story beats on this page.

Return ONLY valid JSON. No markdown fences, no preamble, no explanation."""


_RESPONSE_SCHEMA_HINT = """{
  "page_type": "cover" | "story" | "skip",
  "skip_reason": "" | "advertisement" | "recap" | "next_issue_preview" | "letter_column" | "solicit_credits" | "blank_filler",
  "panels": [
    {"index": 0, "description": "...", "characters": ["Character Name"], "dominant_emotion": "tense"}
  ],
  "text_blocks": [
    {"panel_index": 0, "type": "speech", "speaker": "Character Name", "text": "Exact dialog line."},
    {"panel_index": 0, "type": "sfx", "speaker": null, "text": "BOOM!"},
    {"panel_index": -1, "type": "title", "speaker": null, "text": "THE AMAZING SPIDER-MAN #121"}
  ],
  "page_summary": "2-3 sentences. For covers: describe visual. For story: describe story beats. For skip: empty string."
}

RULES:
  • If page_type="skip", panels and text_blocks MUST be empty arrays and page_summary MUST be "".
  • If page_type="cover" or "story", skip_reason MUST be "".
  • Always return the full JSON shape — do not omit fields."""


def _client() -> OpenAI:
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/comic-video-pipeline",
            "X-Title": "Comic Video Pipeline",
        },
    )


def _encode_image(path: Path | str) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("utf-8")


def _format_panels_prompt(panels: list[dict]) -> str:
    if not panels:
        return "No panels were detected by the layout detector. Treat the full page as one panel (index 0)."
    lines = [f"Detected {len(panels)} panels (top-left origin, reading order):"]
    for i, p in enumerate(panels):
        b = p["bbox"]
        lines.append(f"  Panel {i}: x={b['x']}, y={b['y']}, w={b['w']}, h={b['h']}")
    return "\n".join(lines)


def extract_page(
    image_path: Path | str,
    panels: list[dict],
    model: str | None = None,
    max_retries: int = 2,
) -> dict:
    """
    Call the VLM to enrich one page. Returns the parsed JSON dict or
    {"page_type": "skip", "skip_reason": "vlm_failure", "error": "..."} on failure.
    """
    mdl = model or VLM_MODEL
    b64 = _encode_image(image_path)
    panels_desc = _format_panels_prompt(panels)

    user_text = (
        f"{panels_desc}\n\n"
        f"Return JSON strictly in this shape:\n{_RESPONSE_SCHEMA_HINT}"
    )

    client = _client()
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=mdl,
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ],
                    },
                ],
            )
            content = (resp.choices[0].message.content or "").strip()
            parsed = _extract_json(content)
            if parsed is not None:
                return parsed
            last_err = f"unparseable_json: {content[:200]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"

    return {
        "page_type": "skip",
        "skip_reason": "vlm_failure",
        "error": last_err,
        "panels": [],
        "text_blocks": [],
        "page_summary": "",
    }


def _extract_json(raw: str) -> dict | None:
    """Try hard to pull a JSON object out of the VLM response."""
    patterns = [r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"]
    for p in patterns:
        m = re.search(p, raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(raw[i : j + 1])
        except json.JSONDecodeError:
            return None
    return None
