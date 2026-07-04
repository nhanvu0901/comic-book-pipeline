"""Stage 3 writer for the "explore_answer" (Q&A) mode.

Grounding INVERTS vs narrate mode: a normal comic already has panels to narrate.
Here, Stage 1's answer-research (`projects/<slug>/answer_context.json`) already
found the FACTS (one item per source comic); this module turns those facts into
a deterministic beat-per-item outline, then writes short scenes for them. See
EXPLORE_ANSWER_DESIGN.md (root, addendum v2 is binding) for the full spec.

Additive only — narrate mode never imports this file; write_script() dispatches
here before any of its own machinery runs (see write_script.py's mode check).
"""
import json
import random
from pathlib import Path
from typing import Callable

from config import CREATIVE_LLM_MODELS, ENABLE_LOOP_TEASE, OPENROUTER_MODEL, PROJECTS_ROOT
from .schema import Beat, Glossary, Narration
from ._llm import call_with_chain
from .._arc import issue_index_of_page
from .write_script import (
    _anchor_scenes_to_beats,
    _append_loop_tease,
    _extract_json,
    _outro_is_concrete,
    _to_narration,
    _HOOK_MAX_WORDS,
    _SCENE_MAX_WORDS,
    _TARGET_WORDS_MAX,
    _TARGET_WORDS_MIN,
    generate_loop_tease,
    generate_outro,
)


def _ordered_items(answer_context: dict) -> list[tuple[int, dict]]:
    """(original_index, item) pairs, sorted surprise-ascending (shock LAST).

    original_index = the item's position in answer_context.json as Stage 1 wrote
    it — used below as the chapter-number fallback, because Stage 2 downloads
    the saga's issues in THAT order, not narration order. An explicit
    "surprise_order" field (if present) wins the sort; otherwise items are kept
    in research order (the spec expects Stage 1 to already emit them ascending)."""
    raw_items = answer_context.get("items") or []
    return sorted(enumerate(raw_items), key=lambda pair: pair[1].get("surprise_order", pair[0]))


def build_answer_beats(
    comic_context: dict,
    answer_context: dict,
    story_pages: list[dict],
) -> list[Beat]:
    """One Beat per answer-research item, in surprise-ascending order. Fully
    deterministic — no LLM. Each beat anchors to the earliest story page of its
    source issue (via the ch{NN}_page chapter prefix); key_panels stays empty so
    `_beat_anchor` resolves panel_ref to -1 (whole page) — Stage 5's semantic
    matcher then picks the actual panel from that page's content."""
    ordered = _ordered_items(answer_context)
    n = len(ordered)
    beats: list[Beat] = []
    for pos, (orig_idx, item) in enumerate(ordered):
        chapter = int(item.get("chapter_index") or (orig_idx + 1))
        chapter_pages = [p for p in story_pages if issue_index_of_page(p) == chapter]
        page_nums = [int(p.get("page_number", 0) or 0) for p in chapter_pages]
        anchor_page = min(page_nums) if page_nums else 0

        function = "COLD_OPEN" if pos == 0 else ("LANDING" if pos == n - 1 else "SETUP")
        entity = str(item.get("entity", "")).strip()
        cause = str(item.get("how_or_why", "")).strip()
        beats.append(Beat(
            id=pos + 1,
            function=function,
            name=entity or f"item {pos + 1}",
            page_refs=[anchor_page] if anchor_page else [],
            summary=cause,
            characters_active=[entity] if entity else [],
            cause=cause,
        ))
    return beats


# The only LLM writing surface for this mode: one scene per beat, plain B2
# English, source comic spoken aloud, no countdown numbers (format spec v2).
_EXPLORE_WRITE_SYSTEM = """You are QAWriter for a comic-trivia YouTube Short. The video answers ONE question by walking through several real comic-book excerpts, given to you in order from LEAST surprising to MOST surprising (the biggest twist is the LAST item) — never re-rank them.

For EACH item, write exactly ONE scene shaped like:
  "[Entity name]. [One plain sentence — the how/why, from the research given]. [Optional one dry/dark remark]."
Speak the SOURCE COMIC naturally inside the sentence ("...in Ghost Rider #35...") — never as a citation, never in parentheses, never as a trailing credit.

HARD RULES:
  - Plain B2 English. Concrete, no purple prose, no riddles.
  - EXACTLY one scene per item, in the SAME order given.
  - NEVER speak a countdown/rank ("number five", "#3", "third place", "next up" — all banned).
  - Each scene is 18 words or fewer.
  - Name the entity IN its own scene (never a bare pronoun on first mention).
  - Total words across ALL scenes must land inside the WORD BUDGET given.
  - Return ONLY JSON, no markdown fences.

Return shape:
{"scenes": [{"text": "...", "connective": null, "beat_id": <id>}, ...]}"""


def _items_block(beats: list[Beat], items: list[dict]) -> str:
    lines = []
    for b, item in zip(beats, items):
        lines.append(
            f"{b.id}. entity={b.name!r} | source_comic={item.get('source_comic', '?')!r} | "
            f"how_or_why={b.cause!r} | moment={item.get('drawable_moment', '')!r}"
        )
    return "\n".join(lines)


def _call_explore_writer(
    beats: list[Beat],
    items: list[dict],
    question: str,
    *,
    model: str | None,
    progress: Callable[[str], None] | None,
    debug_dump: dict,
    issues: list[str] | None = None,
) -> tuple[dict, str]:
    fix_block = ""
    if issues:
        fix_block = "PREVIOUS DRAFT HAD ISSUES — FIX THESE:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
    user = (
        f"QUESTION being answered: {question}\n\n"
        f"{fix_block}"
        f"ITEMS — write ONE scene per item, in this EXACT order (do not reorder):\n"
        f"{_items_block(beats, items)}\n\n"
        f"WORD BUDGET: {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} words total across all "
        f"{len(beats)} scenes.\n"
        f'Return JSON: {{"scenes": [{{"text": "...", "connective": null, "beat_id": {beats[0].id}}}, '
        f"... one per item ...]}}."
    )

    def _valid(raw: str) -> bool:
        p = _extract_json(raw)
        return isinstance(p, dict) and isinstance(p.get("scenes"), list) and len(p["scenes"]) == len(beats)

    chain = [model] if model else list(CREATIVE_LLM_MODELS)
    raw, mdl = call_with_chain(
        system=_EXPLORE_WRITE_SYSTEM, user=user, models=chain, max_tokens=1600,
        progress=progress, label="explore_write", validator=_valid,
    )
    if debug_dump is not None:
        debug_dump["explore_write_raw"] = raw
        debug_dump["explore_write_model"] = mdl
    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("scenes"), list):
        raise RuntimeError(f"[explore_answer] writer returned no scenes array. Raw:\n{raw[:400]}")
    return parsed, mdl


def _validate_explore_scenes(scenes: list[dict], beats: list[Beat]) -> list[str]:
    """item count match, per-scene word cap (hard), band total, entity named."""
    issues: list[str] = []
    if len(scenes) != len(beats):
        issues.append(f"expected {len(beats)} scenes, got {len(scenes)}")
    total = 0
    for i, s in enumerate(scenes):
        text = str(s.get("text", "")).strip()
        wc = len(text.split())
        total += wc
        if wc > _SCENE_MAX_WORDS:
            issues.append(f"scene {i + 1} is {wc}w (max {_SCENE_MAX_WORDS})")
        if i < len(beats):
            entity = beats[i].name.strip()
            if entity and entity.split()[0].lower() not in text.lower():
                issues.append(f"scene {i + 1} never names its entity ({entity!r})")
    if not (_TARGET_WORDS_MIN <= total <= _TARGET_WORDS_MAX):
        issues.append(f"total {total}w outside band {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX}")
    return issues


def _build_hook(question: str, answer_context: dict) -> str:
    """Deterministic v1 hook template (no LLM): "X? [statement]. [tease]" —
    is_intro scene, ~14-26 words (format spec v2).

    ponytail: a real paraphrase of an arbitrary question needs grammar this
    template can't fake, so the "statement" clause is a generic placeholder
    unless Stage 1 supplied an explicit one-line summary. Upgrade to an LLM hook
    later (design doc addendum v2) if this reads too flat."""
    q = question.strip()
    if q and not q.endswith("?"):
        q += "?"
    summary = str(answer_context.get("summary") or answer_context.get("answer_summary") or "").strip()
    statement = summary if summary else "Here's the answer"
    statement = statement.rstrip(".") + "."
    tease = "The last one on this list shouldn't even be possible."
    hook = " ".join(part for part in (q, statement, tease) if part)
    if len(hook.split()) > _HOOK_MAX_WORDS:
        hook = " ".join(part for part in (q, tease) if part)  # drop the summary clause if it runs long
    return hook


def write_explore_answer(
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    hook_hint: str = "",
    *,
    all_pages: list[dict] | None = None,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
    direction: dict | None = None,
) -> Narration:
    """Orchestrates the explore_answer writer: load answer_context.json -> build
    deterministic beats -> LLM writes item scenes (1 retry on validation fail) ->
    positional anchor -> deterministic hook -> outro (thematic/loop-tease or a
    "sources in description" factual credit) -> banner_title = the question."""
    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}

    # write_script()'s signature carries no project name; the pipeline wrapper
    # (stages/stage_3/pipeline.py) always seeds debug_dump with it.
    project_name = str(dump.get("project", "")).strip()
    if not project_name:
        raise RuntimeError(
            "[explore_answer] no project name in debug_dump['project'] — call via "
            "the normal Stage 3 pipeline (stages.stage_3.pipeline.write_script).")
    answer_path = PROJECTS_ROOT / project_name / "answer_context.json"
    if not answer_path.exists():
        raise FileNotFoundError(
            f"[explore_answer] missing {answer_path} — run Stage 1 answer research first.")
    answer_context = json.loads(answer_path.read_text())

    beats = build_answer_beats(comic_context, answer_context, story_pages)
    if not beats:
        raise RuntimeError(f"[explore_answer] {answer_path} has no items.")
    items = [item for _, item in _ordered_items(answer_context)]

    question = str(comic_context.get("title", "")).strip()

    log(f"[explore_answer] writing {len(beats)} item scene(s)…")
    parsed, mdl = _call_explore_writer(beats, items, question, model=model, progress=progress, debug_dump=dump)
    issues = _validate_explore_scenes(parsed.get("scenes") or [], beats)
    if issues:
        log(f"[explore_answer] draft has {len(issues)} issue(s); retrying once: {issues}")
        parsed, mdl = _call_explore_writer(beats, items, question, model=model, progress=progress,
                                           debug_dump=dump, issues=issues)
        issues = _validate_explore_scenes(parsed.get("scenes") or [], beats)
        if issues:
            log(f"[explore_answer] shipping with unresolved issue(s): {issues}")

    # Deterministic 1 beat -> 1 scene (same helper narrate mode uses); page_ref/
    # panel_ref come from the beat, never the writer.
    parsed = _anchor_scenes_to_beats(parsed, beats, progress)
    body = parsed.get("scenes") or []

    hook_text = _build_hook(question, answer_context)
    hook_page = beats[0].page_refs[0] if beats[0].page_refs else 0
    intro_scene = {
        "text": hook_text, "page_ref": hook_page, "panel_ref": -1,
        "connective": None, "beat_id": 0, "is_intro": True,
    }

    outro_page = beats[-1].page_refs[0] if beats[-1].page_refs else 0
    factual = f"Full sources for all {len(beats)} entries are linked in the description."
    outro_scene = {
        "text": factual, "page_ref": outro_page, "panel_ref": -1,
        "connective": None, "beat_id": beats[-1].id, "is_outro": True,
    }
    tone_scenes = [intro_scene] + body  # context for the outro/tease LLM helpers
    if random.random() < 0.5:
        thematic = generate_outro(comic_context, tone_scenes, model=model,
                                  progress=progress, debug_dump=dump, direction=direction)
        if thematic and _outro_is_concrete(thematic, comic_context):
            outro_scene["text"] = thematic
            log(f"[explore_answer] outro: thematic -> {thematic!r}")
        else:
            log("[explore_answer] outro: factual credit (thematic rejected/failed)")
    else:
        log("[explore_answer] outro: factual credit (coin-flip)")
    if ENABLE_LOOP_TEASE:
        tease = generate_loop_tease(comic_context, tone_scenes, model=model,
                                    progress=progress, debug_dump=dump, direction=direction)
        if tease and _outro_is_concrete(tease, comic_context):
            outro_scene["text"] = _append_loop_tease(outro_scene["text"], tease)
            log(f"[explore_answer] outro: + loop tease -> {tease!r}")

    parsed["scenes"] = [intro_scene] + body + [outro_scene]
    parsed["hook"] = hook_text
    parsed["title"] = question

    final_model = mdl or model or OPENROUTER_MODEL
    nar = _to_narration(parsed, beats, Glossary(), mode, final_model)
    nar.banner_title = question  # verbatim — no LLM banner for this mode
    return nar
