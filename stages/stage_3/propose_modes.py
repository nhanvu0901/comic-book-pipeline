"""Phase 0 of Stage 3: ask the LLM to propose the 3 best narration modes."""
import json
import re
from typing import Callable

from config import PIPELINE_MODE, PipelineMode
from .modes import MODES_BY_KEY, describe_catalog
from .schema import ProposedMode
from ._llm import call_with_chain


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
    progress: Callable[[str], None] | None = None,
) -> list[ProposedMode]:
    """Ask the text LLM for the best-fit narration modes."""
    log = progress or (lambda _msg: None)

    user = _build_user_prompt(comic_context, story_pages, n=n)
    log(f"[stage3]   prompt built — {len(user)} chars, {len(story_pages)} story pages")

    chain = [model] if model else None
    content, mdl_used = call_with_chain(
        system=_SYSTEM,
        user=user,
        models=chain,
        max_tokens=1200,
        progress=progress,
        label="propose",
    )
    log(f"[stage3]   LLM ({mdl_used}) returned {len(content)} chars — parsing JSON…")
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
    log(f"[stage3]   parsed {len(out)} valid mode(s): {', '.join(p.mode for p in out)}")
    return out


def _build_user_prompt(comic_context: dict, story_pages: list[dict], n: int) -> str:
    ctx_lines = [
        f"Title: {comic_context.get('title', '?')}",
        f"Series: {comic_context.get('series', '?')} {comic_context.get('issues', '')}".strip(),
        f"Year: {comic_context.get('year', '?')}",
        f"Writer / Artist: {comic_context.get('writer', '?')} / {comic_context.get('artist', '?')}",
        f"Characters: {', '.join(comic_context.get('characters', [])) or '?'}",
    ]
    from stages.stage_1.tools.summarize_context import format_for_narration
    summary_block = format_for_narration(comic_context.get("summary") or {})
    if summary_block:
        ctx_lines.append("\n" + summary_block)
    else:
        plot = comic_context.get("plot_summary", "")
        if plot:
            ctx_lines.append(f"\nPlot summary (from wiki):\n{plot[:3000]}")

    page_lines = []
    for p in story_pages:
        pn = p.get("page_number")
        summ = (p.get("page_summary") or "").strip()
        if not summ:
            continue
        page_lines.append(f"- p{pn} ({p.get('issue_label','')}): {summ}")
    pages_block = "\n".join(page_lines) if page_lines else "(no page summaries — use plot summary only)"

    panel_walk_pref = ""
    if PIPELINE_MODE == PipelineMode.NARRATE_1_COMIC:
        panel_walk_pref = (
            "STRONGLY PREFER the `panel_walk` mode unless the story has a uniquely strong thematic / character / twist angle "
            "that justifies a different mode. The user's pipeline is set to narrate-one-comic, so panel-faithful retelling is the default.\n\n"
        )

    return (
        f"COMIC CONTEXT:\n" + "\n".join(ctx_lines) + "\n\n"
        f"STORY PAGE SUMMARIES:\n{pages_block}\n\n"
        f"MODE CATALOG:\n{_CATALOG}\n\n"
        f"TASK: Pick the {n} narration modes that would make the most compelling ≤58-second Short from THIS specific story. "
        f"{panel_walk_pref}"
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
