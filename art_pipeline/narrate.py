"""A4b: grounded art narration in 3 modes → narration.json (comic Stage 3 schema,
so Stage 4 TTS + Stage 5 video consume it unchanged).

Reused read-only from the comic pipeline: call_with_chain (LLM fallback chain),
semantic_sim (embedding region grounding), Narration/Scene dataclasses.
Prompts, validators, and word budgets are art-specific (spec §6 / risk table)."""
import json
import re

from config import CREATIVE_LLM_MODELS
from stages._embedding import semantic_sim
from stages.stage_3._llm import call_with_chain
from stages.stage_3.schema import Narration, Scene

from .config import (
    ART_MIN_SCENES, ART_MODES_BY_KEY, ART_SCENE_MAX_WORDS, ART_TARGET_WORDS_MAX,
    ART_TARGET_WORDS_MIN, ART_WORDS_PER_SEC, _CONNECTIVES, get_art_project_path,
)

# ── Prompt facts cap ─────────────────────────────────────────────────────────
_PROMPT_FACTS_MAX_CHARS = 24_000  # wiki extracts are lead-first, so a prefix keeps the key facts


def cap_facts(text: str, max_chars: int = _PROMPT_FACTS_MAX_CHARS) -> str:
    """Cap grounded facts for the PROMPT only (art_context.json on disk stays full).
    MediaWiki extracts put the lead/summary first, so a prefix cut keeps the
    densest facts and drops deep-bio tail sections."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[... facts truncated for prompt length ...]"


_SYSTEM = """You are a museum-educator scriptwriter for ~75-second vertical videos
about artworks. Neutral, educational, precise — never sensational, never invented.

Hard rules:
1. EVERY factual claim must come from the GROUNDED FACTS block. No outside facts,
   no speculation, no legends presented as fact.
2. One idea per scene. Scene length 5-{scene_max} words. Total body {wmin}-{wmax} words.
3. Each scene carries page_ref and panel_ref pointing at the REGION CATALOG entry
   it should be shown over. Use panel_ref -1 if no specific region fits.
4. Scene 1 is the hook (is_intro=true): a surprising verified fact, 10-26 words.
5. Last scene (is_outro=true) names the artwork and that it is in The Met.
6. Educational register: explain terms in-line, no hype words (insane, epic).
Respond with ONLY valid JSON."""

_MODE_BLOCKS = {
    "painting_deep_dive": """Structure (one artwork): hook → when/why it was made →
3-5 region reveals walking the eye across the painting (each scene's panel_ref =
the region being described) → what it means or changed → outro.""",
    "themed_listicle": """Structure (several artworks around the theme): hook naming
the theme → 1-2 scenes per artwork, each naming the work and landing ONE verified
fact, page_ref = that artwork's page → outro tying the theme together.""",
    "artist_journey": """Structure (one artist, several works in time order): hook
about the artist → scenes walk the works chronologically as biography beats,
page_ref = the work on screen when each beat lands → outro on the artist's legacy.""",
}
assert set(_MODE_BLOCKS) == set(ART_MODES_BY_KEY), "art modes and prompt blocks out of sync"


def mode_prompt_block(mode_key: str) -> str:
    return _MODE_BLOCKS[mode_key]  # KeyError on unknown mode — intentional


def region_catalog(pages: list[dict]) -> str:
    lines = []
    for p in pages:
        lines.append(f"page {p['page_number']} = {p.get('issue_label') or 'artwork'}: "
                     f"{p.get('page_summary') or ''}")
        for pn in p.get("panels") or []:
            lines.append(f"  panel {pn['index']}: {pn.get('description') or ''}")
    return "\n".join(lines)


def _starts_with_connective(text: str) -> str | None:
    t = text.lstrip().lower()
    for c in sorted(_CONNECTIVES, key=len, reverse=True):
        cl = c.lower()
        if t.startswith(cl) and (len(t) == len(cl) or t[len(cl)] in " ,.;:!?"):
            return c
    return None


def _extract_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _to_int(val, *, what: str, scene: int, default: int = 0) -> int:
    """Coerce an LLM-supplied ref field to int. A non-coercible value ("one",
    None, [...]) becomes a contextual ValueError so write_narration's retry
    loop catches it and feeds the message back to the model — instead of a
    raw TypeError/ValueError escaping the loop."""
    try:
        return int(val)
    except (TypeError, ValueError):
        raise ValueError(f"narration: scene {scene} non-integer {what}: {val!r}")


def ground_panel_ref(scene_text: str, page: dict) -> int:
    """Embedding-pick the best region for a scene the LLM left unassigned (-1)."""
    panels = page.get("panels") or []
    if not panels:
        return -1
    best_i, best_s = -1, 0.35  # below this similarity, keep whole-page (-1)
    for pn in panels:
        s = semantic_sim(scene_text, pn.get("description") or "")
        if s > best_s:
            best_i, best_s = int(pn["index"]), s
    return best_i


def build_narration_from_raw(
    raw: str, pages: list[dict], ctx: dict, mode_key: str,
    project_name: str, model_used: str, *, log=print,
) -> dict:
    """Parse + validate LLM output, embed-ground -1 panel refs, emit Narration dict.
    Raises ValueError with a specific message on any contract violation."""
    data = _extract_json(raw)
    if data is None:
        raise ValueError("narration: unparseable JSON")
    scenes_raw = data.get("scenes") or []
    if len(scenes_raw) < ART_MIN_SCENES:
        raise ValueError(f"narration: only {len(scenes_raw)} scenes (need >= {ART_MIN_SCENES})")

    by_number = {p["page_number"]: p for p in pages}
    scenes: list[Scene] = []
    total_words = 0
    for i, s in enumerate(scenes_raw, start=1):
        text = str(s.get("text") or "").strip()
        if not text:
            raise ValueError(f"narration: scene {i} empty text")
        wc = len(text.split())
        if wc > int(ART_SCENE_MAX_WORDS * 1.5):
            raise ValueError(f"narration: scene {i} too long ({wc} words)")
        pref = _to_int(s.get("page_ref") or 0, what="page_ref", scene=i)
        if pref not in by_number:
            raise ValueError(f"narration: scene {i} bad page_ref {pref}")
        page = by_number[pref]
        panel_ref = _to_int(s.get("panel_ref", -1), what="panel_ref", scene=i, default=-1)
        n_panels = len(page.get("panels") or [])
        if panel_ref >= n_panels:
            panel_ref = -1
        if panel_ref == -1:
            panel_ref = ground_panel_ref(text, page)
        total_words += wc
        scenes.append(Scene(
            scene_id=i, text=text, page_ref=pref, panel_ref=panel_ref,
            word_count=wc, target_seconds=round(wc / ART_WORDS_PER_SEC, 2),
            connective=_starts_with_connective(text),
            beat_id=i, is_intro=bool(s.get("is_intro")), is_outro=bool(s.get("is_outro")),
        ))

    body = sum(sc.word_count for sc in scenes if not sc.is_intro)
    if not (ART_TARGET_WORDS_MIN * 0.7 <= body <= ART_TARGET_WORDS_MAX * 1.3):
        log(f"[narrate] ⚠ body {body} words outside soft band "
            f"[{ART_TARGET_WORDS_MIN}-{ART_TARGET_WORDS_MAX}] — accepted, tune later")

    narration = Narration(
        mode=mode_key,
        title=str(data.get("title") or ctx.get("title") or ""),
        hook=str(data.get("hook") or scenes[0].text),
        scenes=scenes,
        total_word_count=total_words,
        estimated_duration_seconds=round(total_words / ART_WORDS_PER_SEC, 1),
        words_per_second=ART_WORDS_PER_SEC,
        source_project=project_name,
        llm_model=model_used,
    )
    return narration.to_dict()


def write_narration(project_name: str, mode_key: str | None = None, *, log=print) -> dict:
    root = get_art_project_path(project_name)
    ctx = json.loads((root / "art_context.json").read_text())
    mode_key = mode_key or ctx.get("mode") or "painting_deep_dive"
    if mode_key not in ART_MODES_BY_KEY:
        raise KeyError(f"unknown art mode: {mode_key}")

    pages = []
    prep = root / "preprocessed"
    for p in sorted(prep.glob("page_*.json")):
        pages.append(json.loads(p.read_text()))
    if not pages:
        raise FileNotFoundError(f"no preprocessed pages in {prep}. Run regions first.")

    system = _SYSTEM.format(scene_max=ART_SCENE_MAX_WORDS,
                            wmin=ART_TARGET_WORDS_MIN, wmax=ART_TARGET_WORDS_MAX)
    user = (
        f"MODE: {mode_key}\n{mode_prompt_block(mode_key)}\n\n"
        f"GROUNDED FACTS (the ONLY allowed source of claims):\n{cap_facts(ctx['plot_summary'])}\n\n"
        f"REGION CATALOG (page_ref/panel_ref targets):\n{region_catalog(pages)}\n\n"
        'Return STRICT JSON only:\n'
        '{"title": "<video title>", "hook": "<scene-1 text>",\n'
        ' "scenes": [{"text": "...", "page_ref": 1, "panel_ref": 0,\n'
        '             "is_intro": false, "is_outro": false}]}'
    )

    last_err: Exception | None = None
    for attempt in (1, 2):
        raw, model_used = call_with_chain(
            system=system, user=user, models=CREATIVE_LLM_MODELS,
            max_tokens=3000, progress=log, label=f"art-narrate#{attempt}",
            validator=lambda c: _extract_json(c) is not None,
        )
        try:
            n = build_narration_from_raw(raw, pages, ctx, mode_key,
                                         project_name, model_used, log=log)
            (root / "narration.json").write_text(
                json.dumps(n, indent=2, ensure_ascii=False))
            log(f"[narrate] ✓ {len(n['scenes'])} scenes, {n['total_word_count']} words "
                f"(~{n['estimated_duration_seconds']}s) via {model_used}")
            return n
        except ValueError as exc:
            last_err = exc
            log(f"[narrate] attempt {attempt} rejected: {exc} — retrying")
            user += f"\n\nYOUR PREVIOUS ATTEMPT FAILED VALIDATION: {exc}. Fix exactly that."
    raise ValueError(f"narration failed after 2 attempts: {last_err}")
