"""
Phase A of Stage 3: ask the LLM to propose the 3 best narration modes for
the given comic.
"""
import json
import re

from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from .modes import MODES_BY_KEY, describe_catalog
from .schema import ProposedMode


_SYSTEM = """You are PanelNarrator, a script writer for 60-second YouTube Shorts / TikTok narration of comic book stories. You write in a ComicsExplained-style third-person documentary voice.

Given a comic's context and its actual page content (panels, dialog, summary), you pick the 3 narration angles that would make the BEST short from this specific story — not generic picks. For each, you provide a candidate hook line (the opening sentence of the narration) that demonstrates the angle.

Return ONLY JSON. No prose, no markdown fences."""


_CATALOG = describe_catalog()


def propose_modes(
    comic_context: dict,
    story_pages: list[dict],
    *,
    model: str | None = None,
    n: int = 3,
) -> list[ProposedMode]:
    """
    Ask the text LLM for the best-fit narration modes.
    """
    client = _client()
    mdl = model or OPENROUTER_MODEL

    user = _build_user_prompt(comic_context, story_pages, n=n)

    resp = client.chat.completions.create(
        model=mdl,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    content = (resp.choices[0].message.content or "").strip()
    parsed = _extract_json(content)

    if not parsed or not isinstance(parsed.get("proposed_modes"), list):
        raise RuntimeError(f"LLM did not return proposed_modes JSON. Raw:\n{content[:500]}")

    out: list[ProposedMode] = []
    for p in parsed["proposed_modes"][:n]:
        key = str(p.get("mode", "")).strip()
        if key not in MODES_BY_KEY:
            continue
        out.append(ProposedMode(
            mode=key,
            hook=str(p.get("hook", "")).strip(),
            rationale=str(p.get("rationale", "")).strip(),
        ))
    if not out:
        raise RuntimeError(f"No valid modes in LLM response. Raw:\n{content[:500]}")
    return out


def _build_user_prompt(comic_context: dict, story_pages: list[dict], n: int) -> str:
    ctx_lines = [
        f"Title: {comic_context.get('title', '?')}",
        f"Series: {comic_context.get('series', '?')} {comic_context.get('issues', '')}".strip(),
        f"Year: {comic_context.get('year', '?')}",
        f"Writer / Artist: {comic_context.get('writer', '?')} / {comic_context.get('artist', '?')}",
        f"Characters: {', '.join(comic_context.get('characters', [])) or '?'}",
    ]
    plot = comic_context.get("plot_summary", "")
    if plot:
        ctx_lines.append(f"\nPlot summary (from wiki):\n{plot[:3000]}")

    # Compact per-page summaries only — keep token count reasonable
    page_lines = []
    for p in story_pages:
        pn = p.get("page_number")
        summ = (p.get("page_summary") or "").strip()
        if not summ:
            continue
        page_lines.append(f"- p{pn} ({p.get('issue_label','')}): {summ}")
    pages_block = "\n".join(page_lines) if page_lines else "(no page summaries — use plot summary only)"

    return (
        f"COMIC CONTEXT:\n" + "\n".join(ctx_lines) + "\n\n"
        f"STORY PAGE SUMMARIES:\n{pages_block}\n\n"
        f"MODE CATALOG:\n{_CATALOG}\n\n"
        f"TASK: Pick the {n} narration modes that would make the most compelling ≤58-second Short from THIS specific story. "
        f"For each, write a 1-sentence hook (the opening line of the narration, ≤20 words, punchy, drops us into the story) "
        f"and a 1-sentence rationale explaining why this mode fits this comic.\n\n"
        f"Return JSON in exactly this shape:\n"
        f"{{\n"
        f'  "proposed_modes": [\n'
        f'    {{"mode": "<key from catalog>", "hook": "<hook line>", "rationale": "<why>"}},\n'
        f'    {{"mode": "...", "hook": "...", "rationale": "..."}},\n'
        f'    {{"mode": "...", "hook": "...", "rationale": "..."}}\n'
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
    patterns = [r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"]
    for pat in patterns:
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
