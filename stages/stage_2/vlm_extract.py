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

from config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, VLM_MODEL, VLM_MODELS, VLM_MODELS_BATCH,
)


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


# ─── Multi-page batch extraction (Approach B + A) ─────────────────────────────


_BATCH_SYSTEM_PROMPT = """You are a comic book reader analyst. You receive N comic pages in reading order, plus per-page panel bounding boxes and optionally a STORY CONTEXT block, a PRIOR PAGE block, or a RUNNING NARRATIVE STATE from earlier pages.

CRITICAL READING-FLOW RULE — this is why you are batched, not asked per-page:
You are simulating how a human reader experiences these pages back-to-back. Each panel description must read as a continuation of the panel before it (within and across pages). Do NOT describe panels as isolated facts. Use pronouns, named characters, and connective phrasing exactly as a human narrator would — "He then…", "Across the room…", "The next page opens with…". When a character was introduced earlier, later panels should reference them by name or pronoun, not redescribe them.

PRIOR PAGE OVERLAP RULE — when a PRIOR PAGE block is present in the user message:
  • The first image in the batch is the prior page (already analyzed). It is included so you can see it visually for continuity, AND its structured data is provided as text.
  • DO NOT output a `pages` entry for the prior page. Your `pages` array should contain entries ONLY for the new pages listed under "New page 0", "New page 1", etc.
  • Use the prior page's last panel as the launch point for new page 0's first panel. Maintain character names and the immediate visual/narrative thread.

DO NOT invent events, dialog, or characters. Every fact must be derivable from the panel image. STORY CONTEXT, PRIOR PAGE, and RUNNING STATE are name-disambiguation aids only — not predictive prompts.

PER-PAGE STEPS (apply to each page independently for classification, but write descriptions with continuity):

  STEP 1 — Classify each page into "cover" | "story" | "skip" (same rules as single-page mode: cover requires visible title/issue text; skip = ad/recap/blank).

  STEP 2 — For "cover" + "story" pages:
    2a. Per panel: one-sentence description, character list, dominant emotion.
        Descriptions chain — panel N+1 continues panel N's thread.
    2b. Extract every text element (speech, narration, sfx, caption, title) into text_blocks, assigned to panel_index.
    2c. A 2-3 sentence page_summary that recaps what happened on THIS page in plain prose.

OUTPUT: a single JSON object containing a `pages` array (one entry per input page, in order) and a `running_state` string (~150-250 chars) summarizing the story state AFTER reading this batch — for feeding into the next batch.

Return ONLY JSON. No prose, no markdown fences."""


_BATCH_RESPONSE_SCHEMA = """{
  "pages": [
    {
      "page_index": 0,
      "page_type": "cover" | "story" | "skip",
      "skip_reason": "" | "advertisement" | "recap" | "next_issue_preview" | "letter_column" | "solicit_credits" | "blank_filler",
      "panels": [
        {"index": 0, "description": "...", "characters": ["..."], "dominant_emotion": "..."}
      ],
      "text_blocks": [
        {"panel_index": 0, "type": "speech", "speaker": "...", "text": "..."}
      ],
      "page_summary": "2-3 sentences."
    }
  ],
  "running_state": "Short prose: where are we in the story now, who is on stage, what tension is unresolved."
}

RULES:
  • `pages` length MUST equal the number of input images.
  • `page_index` is 0-based within the batch (NOT the global page number).
  • For "skip" pages: panels=[], text_blocks=[], page_summary="".
  • Always return full JSON shape — do not omit fields."""


def _format_prior_page_block(prior_page: dict) -> str:
    """Render an already-extracted page's data as text for the next batch's prompt.

    The matching image is sent as the FIRST image in the batch — VLM can see it
    visually AND has its structured data here. VLM is instructed NOT to re-output
    this page; if it does, the caller drops the entry."""
    label = prior_page.get("issue_label", "")
    pn = prior_page.get("page_number", "?")
    summary = (prior_page.get("page_summary") or "").strip()
    panels = prior_page.get("panels") or []
    text_blocks = prior_page.get("text_blocks") or []

    lines: list[str] = [
        f"PRIOR PAGE (page {pn}{' ' + label if label else ''}) — already analyzed, included here AS CONTEXT ONLY:",
        f"  page_summary: {summary}" if summary else "  page_summary: (none)",
    ]
    if panels:
        lines.append("  panels:")
        for p in panels:
            idx = p.get("index", "?")
            desc = (p.get("description") or "").strip()
            chars = ", ".join(p.get("characters") or []) or "?"
            emo = (p.get("dominant_emotion") or "").strip() or "?"
            lines.append(f"    panel {idx} [chars: {chars}] [emo: {emo}]: {desc}")
    if text_blocks:
        lines.append("  text_blocks:")
        for tb in text_blocks:
            spk = tb.get("speaker") or "—"
            ttype = tb.get("type", "speech")
            txt = (tb.get("text") or "").strip()
            lines.append(f"    panel {tb.get('panel_index', '?')} [{ttype}, {spk}]: \"{txt}\"")
    lines.append(
        "\nUse this prior page as the immediate context — pick up the narrative thread from its last panel. "
        "The image of this prior page IS included visually as the first image in the batch (for reference), "
        "but DO NOT output a `pages` entry for it. Output entries ONLY for the new pages below."
    )
    return "\n".join(lines)


def _format_batch_user_text(
    panels_per_page: list[list[dict]],
    story_context: str = "",
    running_state: str = "",
    prior_page: dict | None = None,
) -> str:
    context_block = ""
    if story_context.strip():
        context_block += (
            f"STORY CONTEXT (canonical names + setting; do NOT use to predict events):\n"
            f"{story_context.strip()}\n\n"
        )
    if prior_page is not None:
        context_block += _format_prior_page_block(prior_page) + "\n\n"
    elif running_state.strip():
        # Fallback: when no overlap (first batch), still use running_state if present.
        context_block += (
            f"RUNNING NARRATIVE STATE (from prior pages — continue from here, do NOT contradict):\n"
            f"{running_state.strip()}\n\n"
        )

    pages_block_lines: list[str] = [
        f"You will analyze {len(panels_per_page)} NEW page(s) in reading order.",
        "Panel bboxes are top-left origin, in pixels:",
    ]
    for pidx, panels in enumerate(panels_per_page):
        pages_block_lines.append(f"\nNew page {pidx} ({len(panels)} panel(s)):")
        if not panels:
            pages_block_lines.append(
                "  (No panels detected. Treat the page as one panel index 0 — likely splash/cover.)"
            )
        for i, p in enumerate(panels):
            b = p["bbox"]
            pages_block_lines.append(
                f"  Panel {i}: x={b['x']}, y={b['y']}, w={b['w']}, h={b['h']}"
            )

    return (
        f"{context_block}"
        + "\n".join(pages_block_lines)
        + f"\n\nReturn JSON strictly in this shape:\n{_BATCH_RESPONSE_SCHEMA}"
    )


_BATCH_TIMEOUT_S = 90  # fail fast — free-tier providers can queue requests for hours otherwise


def _call_model_batch(
    client: OpenAI, model: str, b64_images: list[str], user_text: str,
) -> str:
    content: list[dict] = [{"type": "text", "text": user_text}]
    for b64 in b64_images:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    resp = client.with_options(timeout=_BATCH_TIMEOUT_S).chat.completions.create(
        model=model,
        max_tokens=3500,
        messages=[
            {"role": "system", "content": _BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def extract_pages_batch(
    image_paths: list[Path],
    panels_per_page: list[list[dict]],
    *,
    models: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
    story_context: str = "",
    running_state: str = "",
    prior_page: dict | None = None,
    prior_image_path: Path | None = None,
) -> tuple[list[dict] | None, str, str]:
    """Multi-image VLM call with optional prior-page overlap (first-wins lock-in).

    Args:
      image_paths: NEW pages to extract — one entry per page-to-analyze.
      panels_per_page: Magi-detected panels for each NEW page.
      prior_page: Already-extracted dict of the page immediately preceding this batch.
        Its data is injected as text context; its image is sent as the first image
        in the batch (visual context). VLM is told NOT to output an entry for it.
        If the VLM outputs N+1 entries anyway, we drop the first one.
      prior_image_path: path to the image for prior_page (required if prior_page is set).

    Returns (fresh_pages_list, new_running_state, model_used). pages_list is None
    on total failure — caller should fall back to per-page extract_page().
    """
    n = len(image_paths)
    if n != len(panels_per_page):
        raise ValueError(f"image_paths ({n}) and panels_per_page ({len(panels_per_page)}) must align")
    if n == 0:
        return [], running_state, ""
    if prior_page is not None and prior_image_path is None:
        raise ValueError("prior_page passed without prior_image_path")

    chain = list(models) if models else list(VLM_MODELS_BATCH)
    log = progress or (lambda _msg: None)

    # Build image list: [prior_image (optional), fresh_images...]
    all_image_paths: list[Path] = []
    if prior_page is not None and prior_image_path is not None:
        all_image_paths.append(prior_image_path)
    all_image_paths.extend(image_paths)
    b64_images = [_encode_image(p) for p in all_image_paths]

    user_text = _format_batch_user_text(
        panels_per_page, story_context, running_state, prior_page=prior_page,
    )

    client = _client()
    errors: list[str] = []
    has_prior = prior_page is not None

    for idx, model in enumerate(chain, start=1):
        nice_label = f"{n} new" + (f" +1 prior" if has_prior else "")
        log(f"[vlm-batch] try {idx}/{len(chain)} model={model} ({nice_label})")
        try:
            content = _call_model_batch(client, model, b64_images, user_text)
        except Exception as exc:
            if _is_rate_limited(exc):
                log(f"[vlm-batch] ✗ rate-limited on {model} — falling back")
                errors.append(f"{model}: rate_limited")
                continue
            log(f"[vlm-batch] ⚠ {model} transient error: {type(exc).__name__} — retrying once")
            time.sleep(2)
            try:
                content = _call_model_batch(client, model, b64_images, user_text)
            except Exception as exc2:
                log(f"[vlm-batch] ✗ {model} failed twice: {type(exc2).__name__}")
                errors.append(f"{model}: {type(exc2).__name__}: {str(exc2)[:160]}")
                continue

        if _detect_inline_rate_limit(content):
            log(f"[vlm-batch] ✗ rate-limited on {model} (inline) — falling back")
            errors.append(f"{model}: rate_limited_inline")
            continue

        parsed = _extract_json(content)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("pages"), list):
            log(f"[vlm-batch] ⚠ {model} unparseable / missing 'pages' — retrying with sharper prompt")
            try:
                content2 = _call_model_batch(client, model, b64_images, user_text + _SHARP_JSON_SUFFIX)
            except Exception as exc:
                errors.append(f"{model}: sharp_retry {type(exc).__name__}")
                continue
            parsed = _extract_json(content2)

        if not isinstance(parsed, dict) or not isinstance(parsed.get("pages"), list):
            log(f"[vlm-batch] ✗ {model} no 'pages' array twice — falling back")
            errors.append(f"{model}: no_pages_array")
            continue

        pages_out = parsed["pages"]

        # If VLM ignored "do not output prior page" and emitted N+1 entries, drop the first.
        # Heuristic: when has_prior and len == n+1, assume entry 0 is the prior page.
        if has_prior and len(pages_out) == n + 1:
            log(f"[vlm-batch]   VLM also emitted prior page entry — dropping it (first-wins lock-in)")
            pages_out = pages_out[1:]

        if len(pages_out) != n:
            log(f"[vlm-batch] ⚠ {model} returned {len(pages_out)} pages, expected {n} — accepting partial")
            while len(pages_out) < n:
                pages_out.append({"page_type": "skip", "skip_reason": "vlm_failure",
                                  "panels": [], "text_blocks": [], "page_summary": ""})
            pages_out = pages_out[:n]

        for p in pages_out:
            p["_vlm_model_used"] = model

        new_state = str(parsed.get("running_state") or running_state).strip()
        log(f"[vlm-batch] ✓ {model} returned {len(pages_out)} fresh page(s); new_state={len(new_state)} chars")
        return pages_out, new_state, model

    log(f"[vlm-batch] ✗ all {len(chain)} multi-image models exhausted: {' | '.join(errors)}")
    return None, running_state, ""


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
