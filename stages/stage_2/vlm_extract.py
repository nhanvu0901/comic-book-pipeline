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
import io
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

Use the STORY CONTEXT only to recognize and disambiguate entities by their canonical names. HARD RULE — CHARACTER NAMING: only use a name that appears in the STORY CONTEXT roster. If a visible figure is not clearly one of the roster characters, label them GENERICALLY — "a man", "a woman", "a figure", "a hero", "soldiers", "a crowd" — and NEVER guess a famous Marvel/DC character from visual resemblance. Do NOT write "the Thing", "Reed Richards", "Sue Storm", "Loki", "the Fantastic Four" (or any name) unless it is in the roster. Recognizing a character by how they LOOK and naming them when they are not in the roster is a HALLUCINATION that corrupts the panel match — a green muscular figure is "a hulking figure" (or the roster's Hulk if listed), NOT a guessed cameo. Do NOT predict events, invent dialog, or assume a character is on a panel they aren't visibly in. Every text block must come verbatim from the panel itself; every character listed for a panel must be visually present.

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
               AD CHECK (do this FIRST): mentally transcribe the page's visible text,
               THEN classify. The page is an ADVERTISEMENT if ANY of these hold:
                 · review quotes with press attribution ("— IGN", "Entertainment Weekly")
                 · sales language: "ON SALE", "IN STORES", "AVAILABLE NOW", "DISCOVER",
                   price, ISBN, barcode
                 · catalog of OTHER books: cover thumbnails in a grid, "VOLUMES 1-9"
                 · publisher URLs, social handles, QR codes
                 · poster-style image with logo + tagline, NO sequential panels, NO
                   gutters, NO speech balloons spoken BY characters IN a scene
               House ads often reuse the SAME artwork and characters as the story —
               artwork similarity is NOT evidence of "story".
               For skip pages: set page_type="skip", fill skip_reason with the specific
               category, and return EMPTY panels, text_blocks, and page_summary.
               DO NOT extract any metadata for skip pages.

STEP 2 — For cover + story pages ONLY, do this:

  2a. Each panel is OUTLINED with a magenta box and its index NUMBER is drawn in the
      box's top-left corner ON the image. Your `index` for a panel MUST equal that drawn
      number, and the description must cover ONLY the region inside that numbered box.
      Do NOT renumber, merge, split, or re-order the panels — follow the boxes exactly.
      For EACH panel: write a SELF-CONTAINED 1-2 sentence visual description that
      NAMES the character(s) (no bare pronouns), leads with the SPECIFIC ACTION (a
      concrete verb — raises, opens, crushes, fires, bites, grabs — not a mood like
      "expresses frustration"), and NAMES the defining OBJECT/PROP (sonic gun,
      canister, specimen cage, the machine). Make it distinctive enough to tell this
      panel apart from its neighbors. SCENE-GROUND IT: situate the moment in the page's
      overall action in ≤1 clause so it carries story meaning (e.g. "As the suit begins
      replacing his flesh, Tony raises his glove" not "Tony raises his hand"), while
      keeping the panel DISTINCT (do not just repeat the page summary on every panel).
      Then list characters present and dominant emotion.
      Good: "Reed Richards raises a sonic gun at the symbiote-covered Thing."
      Bad:  "He expresses frustration." / "The Venomized Thing looks menacing."
      DESCRIBE ONLY WHAT IS VISIBLE — NEVER infer a MENTAL / INTERNAL event from an
      image: no telepathy, mind-reading, "merging minds", sharing or seeing someone's
      memories, dreams, or visions. If characters touch or one reaches for another,
      describe the PHYSICAL action only.
      Bad:  "Silver Surfer merges with the dying refugee's mind and sees her memories."
      Good: "Silver Surfer reaches toward the falling refugee; his hand passes through her."
  2b. Extract EVERY visible text element (speech bubbles, narration/caption boxes,
      SFX text, cover title/subtitle/credits). For each: classify type, identify the
      speaker (null for narration/sfx/caption/title), and assign it to the panel whose
      bbox contains it (panel_index). Use -1 if the text is outside all panels.
      READING ORDER — these are WESTERN / American comics: read panels AND the text
      bubbles inside each panel LEFT-to-RIGHT, then TOP-to-BOTTOM. NEVER use manga
      right-to-left order. List each panel's text_blocks in that sequence.
  2c. Write a 2-3 sentence page_summary. For covers: describe what's visually depicted
      (e.g. "Cover: Spider-Man in classic red/blue swings past the Daily Bugle…").
      For story pages: recap the MAIN story action on this page in plain prose. Do NOT
      transcribe in-world jargon, invented place-names, or social-class labels the
      reader cannot place — summarize the gist instead (e.g. "Peter explains the city's
      oppression under the villain" rather than quoting "City Prime / pleb-people").

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


def _encode_image_with_panels(path: Path | str, panels: list[dict]) -> str:
    """Set-of-marks overlay: draw each Magi panel's NUMBERED box onto the page before
    sending it to the VLM, so the VLM's per-panel `index` is anchored to Magi's exact
    regions. Without this the VLM numbers panels by its OWN independent reading of the
    page, and pipeline._panel_field then pairs description[i] with the WRONG bbox[i]
    (the 'reduced to ash' panel got a 'Ghost Rider turns away' description, etc.).
    Returns base64 JPEG. Falls back to the plain image on any drawing error."""
    if not panels:
        return _encode_image(path)
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(path).convert("RGB")
        draw = ImageDraw.Draw(img)
        W, _H = img.size
        lw = max(3, W // 350)            # outline width scales with page size
        fsize = max(28, W // 30)         # badge number large enough to read
        font = None
        for cand in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf"):
            try:
                font = ImageFont.truetype(cand, fsize)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        for i, p in enumerate(panels):
            b = p.get("bbox") or {}
            x, y = int(b.get("x", 0)), int(b.get("y", 0))
            w, h = int(b.get("w", 0)), int(b.get("h", 0))
            if w <= 0 or h <= 0:
                continue
            draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 255), width=lw)
            label = str(i)
            tb = draw.textbbox((0, 0), label, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            pad = max(4, fsize // 6)
            draw.rectangle([x, y, x + tw + 2 * pad, y + th + 2 * pad], fill=(255, 0, 255))
            draw.text((x + pad - tb[0], y + pad - tb[1]), label,
                      fill=(255, 255, 255), font=font)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return _encode_image(path)


def _format_panels_prompt(panels: list[dict]) -> str:
    if not panels:
        return "No panels were detected by the layout detector. Treat the full page as one panel (index 0)."
    lines = [
        f"Detected {len(panels)} panels. Each is OUTLINED with a magenta box and its "
        f"index NUMBER is drawn in the top-left corner of that box ON the image. Your "
        f"per-panel `index` MUST equal the number drawn on the box, and your description "
        f"must describe the exact region INSIDE that numbered box — do NOT renumber or "
        f"re-segment the page yourself. Coordinates (top-left origin) for reference:",
    ]
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
    create_kwargs = dict(
        model=model,
        max_tokens=4000,        # A3: avoid truncating a dense single page
        temperature=0,          # A1: deterministic decoding
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
    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_object"}, **create_kwargs)  # A2
    except Exception:
        resp = client.chat.completions.create(**create_kwargs)
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

    b64 = _encode_image_with_panels(image_path, panels)
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
You are simulating how a human reader experiences these pages back-to-back. Carry
the story thread across pages in the `page_summary` and `running_state` fields
(those may use pronouns and "He then…" / "Across the room…" connective phrasing).

PANEL NUMBERING — READ THIS FIRST: every panel is OUTLINED with a magenta box and its
index NUMBER is drawn in the box's top-left corner ON each page image. The `index` you
return for a panel MUST equal the number drawn on its box, and its description must
cover ONLY the region inside that numbered box. Do NOT renumber, merge, split, or
re-order panels by your own reading — follow the drawn boxes exactly. A page with K
drawn boxes must return exactly K panels with indices 0..K-1.

BUT the per-panel `description` field is DIFFERENT — it is a SELF-CONTAINED,
DISTINCTIVE caption used later to MATCH this exact panel against a narration line,
so it must stand on its own:
  • NAME the character(s) explicitly every time — never a bare pronoun ("Reed
    Richards", not "He"). The description must be understandable without the panel
    before it.
  • Lead with the SPECIFIC ACTION of THIS panel — a concrete verb (raises, opens,
    crushes, fires, bites, grabs, recoils, points, lunges, kneels), NOT a mood word
    ("expresses frustration", "looks determined" are too vague and look identical
    across panels).
  • NAME the distinctive OBJECT / PROP in play — sonic gun, canister, specimen
    cage, the machine, severed arm, sewer grate. These props are how a line is
    matched to its panel, so always include the one that defines the moment.
  • Make it TELL-APART-ABLE: if this panel resembles its neighbor, state what is
    different (who reacts, what changed, the new action). Two adjacent panels must
    never get near-identical descriptions.
DO NOT invent — only describe what is visibly happening in THIS panel's image.
  • NEVER infer a MENTAL / INTERNAL event from an image: no telepathy, mind-reading,
    "merging minds", sharing or seeing someone's memories, dreams, or visions. If
    characters touch or one reaches for another, describe the PHYSICAL action only.
    Bad: "Surfer merges with the refugee's mind and sees her memories."
    Good: "Silver Surfer reaches toward the falling refugee; his hand passes through her."

PRIOR PAGE OVERLAP RULE — when a PRIOR PAGE block is present in the user message:
  • The first image in the batch is the prior page (already analyzed). It is included so you can see it visually for continuity, AND its structured data is provided as text.
  • DO NOT output a `pages` entry for the prior page. Your `pages` array should contain entries ONLY for the new pages listed under "New page 0", "New page 1", etc.
  • Use the prior page's last panel as the launch point for new page 0's first panel. Maintain character names and the immediate visual/narrative thread.

DO NOT invent events, dialog, or characters. Every fact must be derivable from the panel image. STORY CONTEXT, PRIOR PAGE, and RUNNING STATE are name-disambiguation aids only — not predictive prompts.

HARD RULE — CHARACTER NAMING (most common corruption): only use a character name that appears in the STORY CONTEXT roster. If a visible figure is not clearly one of the roster characters, label them GENERICALLY — "a man", "a woman", "a figure", "a hero", "soldiers", "a crowd". NEVER guess a famous Marvel/DC name from visual resemblance: do NOT write "the Thing", "Reed Richards", "Sue Storm", "Loki", "the Fantastic Four" (or any off-roster name) just because a figure LOOKS like them. A green muscular figure is "a hulking figure" (or the roster's Hulk only if listed), a rocky figure is "a rocky man", a person on a rooftop is "a man" — never a guessed cameo. Recognizing-and-naming an off-roster character is a HALLUCINATION that makes the panel un-matchable to the narration.

PER-PAGE STEPS (apply to each page independently for classification, but write descriptions with continuity):

  STEP 1 — Classify each page into "cover" | "story" | "skip" (same rules as single-page mode: cover requires visible title/issue text; skip = ad/recap/blank).
    AD CHECK first: mentally transcribe the page's visible text BEFORE classifying.
    Review quotes with press attribution ("— IGN", "Entertainment Weekly"), sales
    language ("ON SALE", "IN STORES", "AVAILABLE NOW", "DISCOVER"), volume/catalog
    listings, publisher URLs/social handles, prices/barcodes → page_type="skip",
    skip_reason="advertisement".
    CLASSIFY EACH PAGE ON ITS OWN CONTENT — NEVER use story continuity from the
    prior page to justify "story": house ads reuse the same artwork and characters
    as the story. A trailing page after one containing "THE END" is back-matter
    (skip) unless it unmistakably continues the story with sequential panels.

  STEP 2 — For "cover" + "story" pages:
    2a. Per panel: a SELF-CONTAINED 1-2 sentence description (see the READING-FLOW
        rule above) — named character(s) + the specific ACTION + the defining
        OBJECT/PROP, distinctive enough to tell this panel apart from its neighbors.
        SCENE-GROUND IT: situate this panel's moment in the page's overall action so
        the description carries story meaning, NOT just a snapshot — e.g. "As the suit
        begins replacing his flesh, Tony raises his glove to Pepper" rather than "Tony
        raises his hand." Add the context in ≤1 clause; keep the panel still DISTINCT
        from its neighbors (do NOT just repeat the page summary on every panel).
        Then the character list and dominant emotion.
        Examples (good): "Reed Richards raises a sonic gun at the symbiote-covered
        Thing." / "Ben Grimm pulls the cloth off a glass specimen cage holding the
        Venom symbiote." / "The Venomized Thing crushes the sonic gun in his fist."
        Examples (BAD, too vague): "He expresses frustration." / "The Venomized
        Thing looks menacing." / "Reed reacts with concern."
    2b. Extract every text element (speech, narration, sfx, caption, title) into text_blocks, assigned to panel_index.
        READING ORDER — these are WESTERN / American comics: read panels AND the text
        bubbles inside each panel LEFT-to-RIGHT, then TOP-to-BOTTOM. NEVER use manga
        right-to-left order. List each panel's text_blocks in that left-to-right,
        top-to-bottom sequence so the dialog flows in the correct order.
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
        {"index": 0, "description": "<named character + specific action + defining object, self-contained>", "characters": ["..."], "dominant_emotion": "..."}
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
    # Dialog is nested under each panel now (no page-level text_blocks); flatten it back
    # with its panel index for this context render. Backward-compat for old cached pages.
    text_blocks = []
    if prior_page.get("text_blocks") is not None:
        text_blocks = prior_page.get("text_blocks") or []
    else:
        for p in panels:
            for tb in (p.get("dialog") or []):
                text_blocks.append({**tb, "panel_index": p.get("index")})

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
    # The cumulative story-so-far summary (running_state) is ALWAYS included when present
    # — it carries the whole arc up to here, not just the last page. The prior page adds
    # immediate detail on top. (Previously running_state was only a first-batch fallback,
    # so from page 2 on the VLM saw only the last page and lost the cumulative thread.)
    if running_state.strip():
        context_block += (
            f"STORY SO FAR (cumulative summary of all prior pages — continue from here, do NOT contradict):\n"
            f"{running_state.strip()}\n\n"
        )
    if prior_page is not None:
        context_block += _format_prior_page_block(prior_page) + "\n\n"

    pages_block_lines: list[str] = [
        f"You will analyze {len(panels_per_page)} NEW page(s) in reading order.",
        "Each panel is OUTLINED with a magenta box and its index NUMBER is drawn in the "
        "top-left corner of that box ON the image. Your per-panel `index` MUST equal the "
        "number drawn on the box, and the description must cover the exact region INSIDE "
        "that numbered box — do NOT renumber or re-segment the page yourself. "
        "Coordinates (top-left origin, pixels) for reference:",
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
    create_kwargs = dict(
        model=model,
        max_tokens=8000,        # A3: 3 detailed pages can exceed 3500 tokens → truncated JSON
        temperature=0,          # A1: deterministic decoding → far fewer malformed-JSON failures
        messages=[
            {"role": "system", "content": _BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )
    cli = client.with_options(timeout=_BATCH_TIMEOUT_S)
    try:
        # A2: force a valid JSON object (kills most "no 'pages' array" failures).
        resp = cli.chat.completions.create(
            response_format={"type": "json_object"}, **create_kwargs)
    except Exception:
        # Model/route doesn't support JSON mode → retry without it (plain decode).
        resp = cli.chat.completions.create(**create_kwargs)
    return (resp.choices[0].message.content or "").strip()


_EMPTY_DESC_RETRIES = 3
_EMPTY_DESC_NUDGE = (
    "\n\nIMPORTANT: a previous pass left one or more panels with an EMPTY description. "
    "EVERY cover/story panel MUST have a non-empty, specific description — describe even "
    "SFX, transition, establishing, or figure-less panels by exactly what is visibly "
    "drawn (objects, setting, motion lines, colors). Never return a blank description."
)


def _count_empty_desc(pages: list[dict]) -> int:
    """Panels on cover/story pages whose description is blank — the matcher-invisible ones."""
    n = 0
    for pg in pages or []:
        if str(pg.get("page_type", "")).lower() not in ("cover", "story"):
            continue
        for p in pg.get("panels") or []:
            if not str(p.get("description", "")).strip():
                n += 1
    return n


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
    """Batch VLM extraction with an EMPTY-DESCRIPTION retry: if the result comes back
    with blank panel descriptions (matcher-invisible panels), re-run up to
    _EMPTY_DESC_RETRIES times with EXPONENTIAL backoff (1s, 2s, 4s). A nudge is injected
    via story_context on each retry so the (temperature=0) call isn't a deterministic
    repeat. Returns the last result even if still imperfect after 3 tries."""
    log = progress or (lambda _msg: None)
    last: tuple[list[dict] | None, str, str] = (None, running_state, "")
    for attempt in range(_EMPTY_DESC_RETRIES):
        sc = story_context + (_EMPTY_DESC_NUDGE if attempt > 0 else "")
        last = _extract_pages_batch_once(
            image_paths, panels_per_page, models=models, progress=progress,
            story_context=sc, running_state=running_state,
            prior_page=prior_page, prior_image_path=prior_image_path,
        )
        pages = last[0]
        if pages is None:
            return last                      # total failure → caller falls back per-page
        n_empty = _count_empty_desc(pages)
        if n_empty == 0:
            return last                      # clean
        if attempt < _EMPTY_DESC_RETRIES - 1:
            wait = min(2.0 ** attempt, 30.0)
            log(f"[vlm-batch] {n_empty} empty description(s) — retry "
                f"{attempt + 2}/{_EMPTY_DESC_RETRIES} after {wait:.0f}s")
            time.sleep(wait)
    log(f"[vlm-batch] still {_count_empty_desc(last[0])} empty desc after "
        f"{_EMPTY_DESC_RETRIES} tries — keeping best result (pipeline fills the rest)")
    return last


def _extract_pages_batch_once(
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

    # Build image list: [prior_image (optional), fresh_images...]. Each page gets the
    # set-of-marks overlay (numbered Magi panel boxes) so the VLM's `index` is anchored
    # to Magi's regions. The prior page uses its own stored panels for the overlay.
    all_image_paths: list[Path] = []
    panels_for_overlay: list[list[dict]] = []
    if prior_page is not None and prior_image_path is not None:
        all_image_paths.append(prior_image_path)
        panels_for_overlay.append(prior_page.get("panels") or [])
    all_image_paths.extend(image_paths)
    panels_for_overlay.extend(panels_per_page)
    b64_images = [_encode_image_with_panels(p, pn)
                  for p, pn in zip(all_image_paths, panels_for_overlay)]

    user_text = _format_batch_user_text(
        panels_per_page, story_context, running_state, prior_page=prior_page,
    )

    client = _client()
    errors: list[str] = []
    has_prior = prior_page is not None

    for idx, model in enumerate(chain, start=1):
        # Exponential backoff as we fall through the model list (scales with position
        # in the chain): model 1 fires immediately, then 2s, 4s, 8s… (capped) so a
        # rate-limited provider gets time to recover before we hit the next one.
        if idx > 1:
            wait = min(2.0 ** (idx - 1), 30.0)
            log(f"[vlm-batch] backoff {wait:.0f}s before model {idx}/{len(chain)}")
            time.sleep(wait)
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
            # Count mismatch = the pages[] array no longer lines up 1:1 with the
            # input images. The VLM almost always drops a blank/back-matter page
            # MID-batch (e.g. a near-empty page), which shifts every later entry
            # onto the WRONG image — a credits page then inherits the prior page's
            # story description and is mislabeled "story". Padding the tail does
            # NOT fix a mid-batch shift, so we must NOT trust positional alignment.
            # Abandon this model; if the whole chain mismatches, the caller falls
            # back to per-page extract_page() (verified to classify each page
            # correctly, including credits → skip/solicit_credits).
            log(f"[vlm-batch] ✗ {model} returned {len(pages_out)} pages, expected {n} "
                f"— misaligned, NOT padding (falling back)")
            errors.append(f"{model}: count_mismatch_{len(pages_out)}_vs_{n}")
            continue

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
