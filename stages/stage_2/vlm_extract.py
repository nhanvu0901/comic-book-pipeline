"""
VLM semantic enrichment of a single comic page with multi-model fallback.

Given the page image and Magi-detected panel bboxes, ask the VLM for:
  - page_type classification: "cover" | "story" | "skip"
  - text blocks (speech/narration/sfx/caption) attributed to panels + speakers
  - one-sentence description per panel + characters + emotion
  - page summary

Iterates through config.VLM_MODELS — on per-model rate-limit (429) it advances
immediately to the next provider; on transient errors it retries once on the
same model; on unparseable JSON it sharpens the prompt and retries once.
"""
import base64
import json
import re
import time
from pathlib import Path
from typing import Callable

from openai import OpenAI, RateLimitError

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, VLM_MODEL, VLM_MODELS


_SYSTEM_PROMPT = """You are a comic book page analyst. You receive one page image, a list of pre-detected panel bounding boxes, and optionally a STORY CONTEXT block listing the comic's named characters, setting, and key objects.

Use the STORY CONTEXT only to recognize and disambiguate entities by their canonical names (e.g. label "Ben Grimm" or "the Thing" instead of "Unknown character"). Do NOT use it to predict events, invent dialog, or assume a character is on a panel they aren't visibly in. Every text block must come verbatim from the panel itself; every character listed for a panel must be visually present.

STEP 1 — Classify the page into ONE of three types:

  • "cover"  — REQUIRES visible title text AND/OR issue-number text on the page itself
               (e.g. "WHAT IF...? DARK VENOM", "ISSUE #1", series logo, credits block).
               This is the primary signal. Without that text, the page is NOT a cover.
               Splash pages featuring a character in a striking pose, aftermath imagery,
               or iconic finale shot but WITHOUT title/issue-number text are page_type="story"
               even if they look "cover-like" in composition. Variant/back covers also count
               as cover only when title or credit text is visibly present.
               EXTRACT metadata for confirmed covers (characters visible, title text, etc.).

  • "story"  — an actual narrative page from inside the issue. Includes:
               - panels with action, dialogue, or plot progression
               - full-page splash panels WITHOUT title text (climax, aftermath, transformation reveals)
               - title-less iconic poses occurring INSIDE the story
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


_SHARP_JSON_SUFFIX = "\n\nRespond with ONLY valid JSON. No prose, no markdown."


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


def _is_rate_limited(exc: Exception) -> bool:
    """Detect both proper 429s and OpenRouter's 200-with-error-body rate limits."""
    if isinstance(exc, RateLimitError):
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    body = getattr(exc, "body", None) or getattr(exc, "response", None)
    if isinstance(body, dict):
        err = body.get("error") or {}
        if isinstance(err, dict) and (err.get("code") == 429 or str(err.get("code")) == "429"):
            return True
    msg = str(exc).lower()
    return "rate limit" in msg or "rate-limit" in msg or "quota" in msg or "429" in msg


def _detect_inline_rate_limit(content: str) -> bool:
    """Some OpenRouter providers return 200 OK with a rate-limit error JSON in body."""
    if not content:
        return False
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return False
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if code == 429 or str(code) == "429":
                return True
            msg = str(err.get("message", "")).lower()
            if "rate limit" in msg or "quota" in msg:
                return True
    return False


def _call_model(client: OpenAI, model: str, b64: str, user_text: str) -> str:
    resp = client.chat.completions.create(
        model=model,
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
    return (resp.choices[0].message.content or "").strip()


def extract_page(
    image_path: Path | str,
    panels: list[dict],
    models: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
    story_context: str = "",
) -> dict:
    """Call the VLM chain to enrich one page; falls back across models on rate-limits."""
    chain = list(models) if models else list(VLM_MODELS or [VLM_MODEL])
    log = progress or (lambda _msg: None)

    b64 = _encode_image(image_path)
    panels_desc = _format_panels_prompt(panels)
    context_block = f"STORY CONTEXT (canonical names + setting; do NOT use to predict events):\n{story_context.strip()}\n\n" if story_context.strip() else ""
    base_user_text = (
        f"{context_block}"
        f"{panels_desc}\n\n"
        f"Return JSON strictly in this shape:\n{_RESPONSE_SCHEMA_HINT}"
    )

    client = _client()
    total = len(chain)
    errors: list[str] = []

    for idx, model in enumerate(chain, start=1):
        log(f"[vlm] try {idx}/{total} model={model}")
        try:
            content = _call_model(client, model, b64, base_user_text)
        except Exception as exc:
            if _is_rate_limited(exc):
                log(f"[vlm] ✗ rate-limited on {model} — falling back")
                errors.append(f"{model}: rate_limited ({type(exc).__name__})")
                continue
            log(f"[vlm] ⚠ {model} transient error: {type(exc).__name__} — retrying once")
            time.sleep(2)
            try:
                content = _call_model(client, model, b64, base_user_text)
            except Exception as exc2:
                if _is_rate_limited(exc2):
                    log(f"[vlm] ✗ rate-limited on {model} (retry) — falling back")
                    errors.append(f"{model}: rate_limited_retry ({type(exc2).__name__})")
                else:
                    log(f"[vlm] ✗ {model} failed twice: {type(exc2).__name__}")
                    errors.append(f"{model}: {type(exc2).__name__}: {str(exc2)[:160]}")
                continue

        if _detect_inline_rate_limit(content):
            log(f"[vlm] ✗ rate-limited on {model} (inline error body) — falling back")
            errors.append(f"{model}: rate_limited_inline")
            continue

        parsed = _extract_json(content)
        if parsed is not None:
            log(f"[vlm] ✓ {model} returned valid JSON")
            parsed["_vlm_model_used"] = model
            return parsed

        log(f"[vlm] ⚠ {model} unparseable JSON — retrying with sharper prompt")
        try:
            content2 = _call_model(client, model, b64, base_user_text + _SHARP_JSON_SUFFIX)
        except Exception as exc:
            if _is_rate_limited(exc):
                log(f"[vlm] ✗ rate-limited on {model} (sharp retry) — falling back")
                errors.append(f"{model}: rate_limited_sharp")
            else:
                log(f"[vlm] ✗ {model} sharp retry error: {type(exc).__name__}")
                errors.append(f"{model}: sharp_retry {type(exc).__name__}: {str(exc)[:160]}")
            continue

        if _detect_inline_rate_limit(content2):
            log(f"[vlm] ✗ rate-limited on {model} (inline error body, sharp) — falling back")
            errors.append(f"{model}: rate_limited_inline_sharp")
            continue

        parsed2 = _extract_json(content2)
        if parsed2 is not None:
            log(f"[vlm] ✓ {model} returned valid JSON (sharp retry)")
            parsed2["_vlm_model_used"] = model
            return parsed2

        log(f"[vlm] ✗ {model} unparseable JSON twice — falling back")
        errors.append(f"{model}: unparseable_json: {content2[:120]}")

    log(f"[vlm] ✗ all {total} models exhausted — page marked vlm_failure")
    return {
        "page_type": "skip",
        "skip_reason": "vlm_failure",
        "error": " | ".join(errors),
        "panels": [],
        "text_blocks": [],
        "page_summary": "",
        "_vlm_model_used": "",
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
