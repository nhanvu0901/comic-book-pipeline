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
import re
from pathlib import Path
from typing import Callable

from config import CREATIVE_LLM_MODELS, ENABLE_LOOP_TEASE, OPENROUTER_MODEL, PROJECTS_ROOT
from ..question_archetype import question_archetype
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

# Explore-mode word budget (DISTINCT from recap's imported _SCENE_MAX_WORDS / band —
# those tune a single-comic recap and don't fit a Q&A countdown). A Q&A scene now
# carries a connective bridge + entity + how/why + a dry remark, so it needs room;
# the total scales with the number of items instead of a fixed band, since a question
# can have anywhere from 3 to 6+ answers.
_EXP_SCENE_MAX_WORDS = 42
_EXP_WORDS_PER_ITEM_MIN = 22
_EXP_WORDS_PER_ITEM_MAX = 46


def _exp_band(n_items: int) -> tuple[int, int]:
    """Total body word band for `n_items` countdown scenes (excludes intro/outro)."""
    return n_items * _EXP_WORDS_PER_ITEM_MIN, n_items * _EXP_WORDS_PER_ITEM_MAX


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
    # url→chapter map: chapters on disk are labelled by each URL's FIRST-OCCURRENCE
    # rank in comic_context.reader_urls (download_readers_only dedups duplicates but
    # keeps ranks). Items never carry chapter_index themselves, and a bare orig_idx+1
    # points at a nonexistent chapter as soon as two items cite the same issue.
    url_chapter: dict[str, int] = {}
    for rank, u in enumerate(comic_context.get("reader_urls") or [], start=1):
        u = str(u or "").strip()
        if u and u not in url_chapter:
            url_chapter[u] = rank
    beats: list[Beat] = []
    for pos, (orig_idx, item) in enumerate(ordered):
        item_url = str(item.get("reader_url", "") or "").strip()
        chapter = int(item.get("chapter_index") or url_chapter.get(item_url)
                      or (orig_idx + 1))
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
# Two archetype contracts (see stages/question_archetype.py): a LIST question gets
# the countdown-listicle prompt; an EXPLAIN (Why/How) question gets the argument
# prompt — a countdown of events never answers a "why" (measured failure on the
# first explain question: 4 correct events, zero causal answer).
_EXPLORE_WRITE_SYSTEM_LIST = """You are QAWriter for a comic-trivia YouTube Short. The video answers ONE question by walking through several real comic-book excerpts, given to you in order from LEAST surprising to MOST surprising (the biggest twist is the LAST item) — never re-rank them.

For EACH item, write exactly ONE scene: name the entity, give the how/why in plain words (speak the SOURCE COMIC naturally inside the sentence — "...in Ghost Rider #35..." — never as a citation, parentheses, or trailing credit), and you may end on one dry/dark remark.

CONNECT THE ITEMS — this is the point of the format:
  - The FIRST scene opens straight on its entity.
  - EVERY scene AFTER the first must OPEN with a short connective bridge that links it to the one before and builds momentum toward the final twist — e.g. "And the next one...", "Unlike him,...", "Even stranger,...", "But this one...", "Then it gets worse —...". Vary the bridge every time; NEVER reuse the same opener, and NEVER a bare list.
  - The connective is contrast or escalation, NOT a rank. So "Unlike the Punisher, Deadpool..." is good; "the next one", "number three", "third" as a POSITION word is banned.
  - After the bridge, still NAME the entity in that same scene (never a bare pronoun on first mention).

HARD RULES:
  - Plain B2 English. Concrete, no purple prose, no riddles.
  - EXACTLY one scene per item, in the SAME order given.
  - NEVER speak a countdown/rank number ("number five", "#3", "third place" — all banned).
  - Each scene is a short lead-in + one or two plain sentences; keep it under 42 words.
  - Total words across ALL scenes must land inside the WORD BUDGET given.
  - Return ONLY JSON, no markdown fences.

Return shape:
{"scenes": [{"text": "...", "connective": null, "beat_id": <id>}, ...]}"""

_EXPLORE_WRITE_SYSTEM_EXPLAIN = """You are QAWriter for a comic-trivia YouTube Short. The video answers ONE Why/How question as an ARGUMENT built from real comic moments — the items are stages of the answer (they may come from one story or from several different comics), given in escalation order (the revelation is the LAST item) — never re-rank them.

THE SCENES BUILD THE ANSWER — this is the point of the format:
  - Every scene must move the viewer CLOSER to the answer: state what happened AND what it means for the question (cause → effect), not just the event.
  - The FIRST scene sets the broken state / the stakes.
  - EVERY scene AFTER the first must OPEN with a short connective bridge of consequence or escalation — e.g. "But that was only the surface...", "Which is when it turns...", "And that changes everything —...". Vary the bridge every time.
  - The FINAL scene MUST state the answer to the question PLAINLY — one clear sentence a tired viewer can repeat ("That's why..." / "It had to be her, because..."), grounded in that item's moment. The ANSWER THESIS you are given is the destination; land it in your own spoken words.

For EACH item, write exactly ONE scene: name who/what it is about, give the how/why in plain words (speak the SOURCE COMIC naturally inside the sentence — "...in Ghost Rider #35..." — never as a citation, parentheses, or trailing credit).

HARD RULES:
  - Plain B2 English. Concrete, no purple prose, no riddles.
  - EXACTLY one scene per item, in the SAME order given.
  - This is ONE story, not a list: NEVER use list language ("this list", "the last one", "number three" — all banned).
  - Each scene is a short lead-in + one or two plain sentences; keep it under 42 words.
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
    archetype: str = "list",
    thesis: str = "",
) -> tuple[dict, str]:
    fix_block = ""
    if issues:
        fix_block = "PREVIOUS DRAFT HAD ISSUES — FIX THESE:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
    # EXPLAIN questions carry the researched one-line answer as the writer's
    # destination — without it the writer narrates events and never answers WHY
    # (research already distilled this into answer_summary; it was simply never
    # handed to the writer).
    thesis_block = (
        f"ANSWER THESIS — the destination of the whole video; the FINAL scene must state "
        f"this plainly in your own spoken words: {thesis.strip()}\n\n"
        if (archetype == "explain" and (thesis or "").strip()) else ""
    )
    user = (
        f"QUESTION being answered: {question}\n\n"
        f"{thesis_block}"
        f"{fix_block}"
        f"ITEMS — write ONE scene per item, in this EXACT order (do not reorder):\n"
        f"{_items_block(beats, items)}\n\n"
        f"WORD BUDGET: {_exp_band(len(beats))[0]}-{_exp_band(len(beats))[1]} words total across all "
        f"{len(beats)} scenes (each scene: connective bridge + entity + how/why, under {_EXP_SCENE_MAX_WORDS} words).\n"
        f'Return JSON: {{"scenes": [{{"text": "...", "connective": null, "beat_id": {beats[0].id}}}, '
        f"... one per item ...]}}."
    )

    def _valid(raw: str) -> bool:
        p = _extract_json(raw)
        return isinstance(p, dict) and isinstance(p.get("scenes"), list) and len(p["scenes"]) == len(beats)

    system = (_EXPLORE_WRITE_SYSTEM_EXPLAIN if archetype == "explain"
              else _EXPLORE_WRITE_SYSTEM_LIST)
    chain = [model] if model else list(CREATIVE_LLM_MODELS)
    raw, mdl = call_with_chain(
        system=system, user=user, models=chain, max_tokens=1600,
        progress=progress, label="explore_write", validator=_valid,
    )
    if debug_dump is not None:
        debug_dump["explore_write_raw"] = raw
        debug_dump["explore_write_model"] = mdl
    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("scenes"), list):
        raise RuntimeError(f"[explore_answer] writer returned no scenes array. Raw:\n{raw[:400]}")
    return parsed, mdl


# EXPLAIN-mode guards: the final scene must actually LAND the answer (causal
# marker), and no list language may leak in — both are the format's whole point
# and prompt-only rules proved unenforced (validator feeds the 1-retry loop).
_CAUSAL_MARKER_RE = re.compile(
    r"\b(because|that'?s why|which is why|which is how|the reason|the truth is|"
    r"turns? out|so it had to be|had to be|"
    r"could only|only \w+(?:\s+\w+){0,3} could|is what \w+)\b", re.IGNORECASE)
_LIST_LANGUAGE_RE = re.compile(
    r"\b(on this list|the last one|the next one|number (?:one|two|three|four|five|six|\d+)|"
    r"first place|second place|third place)\b", re.IGNORECASE)


def _validate_explore_scenes(scenes: list[dict], beats: list[Beat],
                             archetype: str = "list") -> list[str]:
    """item count match, per-scene word cap (hard), band total, entity named.
    EXPLAIN questions additionally require the final scene to state the answer
    (causal marker) and ban list language everywhere."""
    issues: list[str] = []
    if len(scenes) != len(beats):
        issues.append(f"expected {len(beats)} scenes, got {len(scenes)}")
    total = 0
    for i, s in enumerate(scenes):
        text = str(s.get("text", "")).strip()
        wc = len(text.split())
        total += wc
        if wc > _EXP_SCENE_MAX_WORDS:
            issues.append(f"scene {i + 1} is {wc}w (max {_EXP_SCENE_MAX_WORDS})")
        if i < len(beats):
            entity = beats[i].name.strip()
            if entity and entity.split()[0].lower() not in text.lower():
                issues.append(f"scene {i + 1} never names its entity ({entity!r})")
        if archetype == "explain" and _LIST_LANGUAGE_RE.search(text):
            issues.append(f"scene {i + 1} uses list language "
                          f"({_LIST_LANGUAGE_RE.search(text).group(0)!r}) — this is one story, not a list")
    band_min, band_max = _exp_band(len(beats))
    if not (band_min <= total <= band_max):
        issues.append(f"total {total}w outside band {band_min}-{band_max}")
    if archetype == "explain" and scenes:
        last = str(scenes[-1].get("text", ""))
        if not _CAUSAL_MARKER_RE.search(last):
            issues.append(
                "final scene never states the ANSWER — it must answer the question plainly "
                "(a 'that's why / because / it had to be' sentence), not just narrate the last event")
    return issues


def _build_hook(question: str, answer_context: dict, archetype: str = "list") -> str:
    """Deterministic v1 hook template (no LLM): "X? [statement]. [tease]" —
    is_intro scene, ~14-26 words (format spec v2).

    LIST questions keep the countdown tease. EXPLAIN questions get a
    promise-the-answer tease instead, and NEVER speak the answer_summary — for
    an explain video that summary IS the answer (the final scene's landing), so
    putting it in the hook would spoil the whole argument in second two.

    ponytail: a real paraphrase of an arbitrary question needs grammar this
    template can't fake, so the "statement" clause is a generic placeholder
    unless Stage 1 supplied an explicit one-line summary. Upgrade to an LLM hook
    later (design doc addendum v2) if this reads too flat."""
    q = question.strip()
    if q and not q.endswith("?"):
        q += "?"
    if archetype == "explain":
        return " ".join((q, "The answer is crueler than you think."))
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
    archetype = question_archetype(question)
    thesis = str(answer_context.get("answer_summary", "") or "").strip()

    log(f"[explore_answer] writing {len(beats)} item scene(s)… (archetype={archetype})")
    parsed, mdl = _call_explore_writer(beats, items, question, model=model, progress=progress,
                                       debug_dump=dump, archetype=archetype, thesis=thesis)
    issues = _validate_explore_scenes(parsed.get("scenes") or [], beats, archetype)
    if issues:
        log(f"[explore_answer] draft has {len(issues)} issue(s); retrying once: {issues}")
        parsed, mdl = _call_explore_writer(beats, items, question, model=model, progress=progress,
                                           debug_dump=dump, issues=issues,
                                           archetype=archetype, thesis=thesis)
        issues = _validate_explore_scenes(parsed.get("scenes") or [], beats, archetype)
        if issues:
            log(f"[explore_answer] shipping with unresolved issue(s): {issues}")

    # Deterministic 1 beat -> 1 scene (same helper narrate mode uses); page_ref/
    # panel_ref come from the beat, never the writer.
    parsed = _anchor_scenes_to_beats(parsed, beats, progress)
    body = parsed.get("scenes") or []

    hook_text = _build_hook(question, answer_context, archetype)
    hook_page = beats[0].page_refs[0] if beats[0].page_refs else 0
    intro_scene = {
        "text": hook_text, "page_ref": hook_page, "panel_ref": -1,
        "connective": None, "beat_id": 0, "is_intro": True,
    }

    outro_page = beats[-1].page_refs[0] if beats[-1].page_refs else 0
    # Meaning-first outro ONLY — no "sources linked in the description" credit
    # (Master: that line adds nothing to a Q&A Short). Always try a thematic closing
    # line; append the loop tease. Fallback when both fail: a plain meaning beat, never
    # a credit.
    outro_scene = {
        "text": "", "page_ref": outro_page, "panel_ref": -1,
        "connective": None, "beat_id": beats[-1].id, "is_outro": True,
    }
    tone_scenes = [intro_scene] + body  # context for the outro/tease LLM helpers
    thematic = generate_outro(comic_context, tone_scenes, model=model,
                              progress=progress, debug_dump=dump, direction=direction)
    if thematic and _outro_is_concrete(thematic, comic_context):
        outro_scene["text"] = thematic
        log(f"[explore_answer] outro: thematic -> {thematic!r}")
    if ENABLE_LOOP_TEASE:
        tease = generate_loop_tease(comic_context, tone_scenes, model=model,
                                    progress=progress, debug_dump=dump, direction=direction)
        if tease and _outro_is_concrete(tease, comic_context):
            outro_scene["text"] = _append_loop_tease(outro_scene["text"], tease)
            log(f"[explore_answer] outro: + loop tease -> {tease!r}")
    if not outro_scene["text"].strip():
        # Both LLM helpers failed — close on the hook's promise, not a credit line.
        outro_scene["text"] = "And that's the one nobody saw coming."
        log("[explore_answer] outro: generic meaning fallback")

    parsed["scenes"] = [intro_scene] + body + [outro_scene]
    parsed["hook"] = hook_text
    parsed["title"] = question

    final_model = mdl or model or OPENROUTER_MODEL
    nar = _to_narration(parsed, beats, Glossary(), mode, final_model)
    nar.banner_title = question  # verbatim — no LLM banner for this mode
    return nar
