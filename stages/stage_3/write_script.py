"""
Phase B of Stage 3: write the final narration script in the chosen mode,
given the preprocessed pages.

Output: a Narration with scenes, each tagged with (page_ref, panel_ref) so
Stage 5 knows which panel to zoom into during that scene.
"""
import json
import re

from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from .modes import MODES_BY_KEY
from .schema import Narration, Scene


# ≤58s at ~2.9 words/sec → ~168 words. We target 150-175 to leave TTS headroom.
_TARGET_WORDS_MIN = 150
_TARGET_WORDS_MAX = 175
_WORDS_PER_SEC = 2.9


_SYSTEM = """You are PanelNarrator, writing 60-second narration for YouTube Shorts / TikTok / Reels about comic book stories. Your voice is ComicsExplained-style: third-person, documentary, dramatic, informed.

RULES:
- Strict cap: ≤58 seconds spoken aloud. At 2.9 words/sec that is 150-175 words TOTAL.
- Scene 1 is the HOOK. It must land in under 2 seconds (5-8 words max) and drop the viewer into the story immediately. No "In today's video..." — no intros.
- Full spoilers OK — tell the actual ending.
- Every scene ties to ONE panel on ONE page (page_ref + panel_ref). Pick the most visually impactful panel for that beat.
- Break the script into 8-14 scenes. Each scene = 1-2 sentences. Short scenes for action, longer scenes for setup/landing.
- Land the ending with impact. No "what do you think in the comments" filler.
- Do NOT write stage directions, camera cues, or scene numbers inside the text. Just the spoken words.
- Do NOT use em-dashes, brackets, or text-only conventions — this will be spoken aloud.

Return ONLY JSON in the exact shape requested. No prose, no markdown fences."""


def write_script(
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    hook_hint: str = "",
    *,
    model: str | None = None,
) -> Narration:
    """
    Write the final Narration using the chosen mode.
    """
    if mode not in MODES_BY_KEY:
        raise ValueError(f"Unknown mode: {mode!r}. Valid: {sorted(MODES_BY_KEY)}")

    client = _client()
    mdl = model or OPENROUTER_MODEL

    user = _build_user_prompt(comic_context, story_pages, mode, hook_hint)

    resp = client.chat.completions.create(
        model=mdl,
        max_tokens=3000,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    content = (resp.choices[0].message.content or "").strip()
    parsed = _extract_json(content)

    if not parsed or not isinstance(parsed.get("scenes"), list):
        raise RuntimeError(f"LLM did not return a scenes array. Raw:\n{content[:500]}")

    scenes: list[Scene] = []
    total_words = 0
    for i, s in enumerate(parsed["scenes"], start=1):
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        wc = len(text.split())
        scenes.append(Scene(
            scene_id=i,
            text=text,
            page_ref=int(s.get("page_ref", 0) or 0),
            panel_ref=int(s.get("panel_ref", -1) if s.get("panel_ref") is not None else -1),
            word_count=wc,
            target_seconds=round(wc / _WORDS_PER_SEC, 2),
        ))
        total_words += wc

    est_duration = round(total_words / _WORDS_PER_SEC, 2)
    return Narration(
        mode=mode,
        title=str(parsed.get("title", comic_context.get("title", "")).strip()),
        hook=str(parsed.get("hook", scenes[0].text if scenes else "")).strip(),
        scenes=scenes,
        total_word_count=total_words,
        estimated_duration_seconds=est_duration,
        words_per_second=_WORDS_PER_SEC,
        source_project="",
        llm_model=mdl,
    )


def _build_user_prompt(
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    hook_hint: str,
) -> str:
    mode_info = MODES_BY_KEY[mode]
    ctx_lines = [
        f"Title: {comic_context.get('title', '?')}",
        f"Series: {comic_context.get('series', '?')} {comic_context.get('issues', '')}".strip(),
        f"Year: {comic_context.get('year', '?')}",
        f"Characters: {', '.join(comic_context.get('characters', [])) or '?'}",
    ]
    plot = comic_context.get("plot_summary", "")
    if plot:
        ctx_lines.append(f"\nPlot (from wiki):\n{plot[:3000]}")

    # Pass per-page detail: page_number, panels with index + description,
    # text blocks with speaker + text + type. This is what lets the LLM
    # cite the right (page_ref, panel_ref).
    page_blocks: list[str] = []
    for p in story_pages:
        pn = p.get("page_number")
        issue = p.get("issue_label", "")
        summary = p.get("page_summary", "")
        panels = p.get("panels") or []
        texts = p.get("text_blocks") or []
        block = [f"[page {pn}{' ' + issue if issue else ''}] {summary}"]
        for pan in panels:
            desc = pan.get("description", "")
            chars = ", ".join(pan.get("characters", []) or [])
            emo = pan.get("dominant_emotion", "")
            block.append(f"  panel {pan.get('index')}: {desc} [chars: {chars or '?'}] [emotion: {emo or '?'}]")
            panel_idx = pan.get("index")
            for tb in texts:
                if int(tb.get("panel_index", -99)) == panel_idx:
                    spk = tb.get("speaker") or "—"
                    ttype = tb.get("type", "speech")
                    block.append(f"    {ttype} [{spk}]: \"{tb.get('text','')}\"")
        page_blocks.append("\n".join(block))

    pages_section = "\n\n".join(page_blocks) if page_blocks else "(no preprocessed pages — use plot summary only)"

    hook_line = f"\nSTARTING HOOK SUGGESTION (you can refine or replace): {hook_hint}" if hook_hint else ""

    return (
        f"COMIC CONTEXT:\n" + "\n".join(ctx_lines) + "\n\n"
        f"STORY PAGES (with per-panel text and descriptions):\n{pages_section}\n\n"
        f"CHOSEN NARRATION MODE: {mode} — {mode_info.description}"
        f"{hook_line}\n\n"
        f"WRITE the final narration now.\n"
        f"- {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} words total (strict upper bound).\n"
        f"- 8-14 scenes. Each scene 1-2 sentences.\n"
        f"- Every scene MUST reference a real page_ref (page_number from above) and panel_ref (panel index on that page). "
        f"Use panel_ref=-1 only if no single panel fits (rare).\n"
        f"- Scene 1 = hook (5-8 words).\n\n"
        f"Return JSON in this exact shape:\n"
        f"{{\n"
        f'  "title": "<short punchy title for this Short>",\n'
        f'  "hook": "<the opening hook line>",\n'
        f'  "scenes": [\n'
        f'    {{"text": "...", "page_ref": 3, "panel_ref": 0}},\n'
        f'    {{"text": "...", "page_ref": 3, "panel_ref": 2}},\n'
        f"    ...\n"
        f"  ]\n"
        f"}}"
    )


def _client() -> OpenAI:
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/comic-video-pipeline",
            "X-Title": "Comic Video Pipeline",
        },
    )


def _extract_json(raw: str) -> dict | None:
    for pat in [r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"]:
        m = re.search(pat, raw, re.DOTALL)
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
    if i != -1 and j != -1:
        try:
            return json.loads(raw[i : j + 1])
        except json.JSONDecodeError:
            return None
    return None
