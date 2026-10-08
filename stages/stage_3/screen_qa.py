"""Stage 3 narration writer for the "screen_qa" mode (NEW module — write_script.py is untouched).

The video keeps the Q&A structure of explore_answer:

    spoken HOOK  ->  TWO scenes per item (CONTEXT, then MOMENT)  ->  spoken OUTRO

but its canon is SCREEN media (films / TV / animation) instead of comic issues, so every new
film or series is cited by TITLE + YEAR ("In <Title> (<Year>), ...") and never by an issue
number. Visuals are video clips: each scene is split into verbatim fragments and every
fragment carries a clip-search query.

Interface contract (shared with video-qa/p3-visual and stages/stage_1/screen_research.py):
  * reads  screen_context.json = {question, [answer_summary], items:[{entity, event,
    adaptation_title, year, summary, visual_query, source_urls}]};
  * writes narration.json in the existing Scene schema (stages/stage_3/schema.py) with
    mode "screen_qa" — page_ref 0 / panel_ref -1 (there are no comic pages);
  * every Scene.visual_beats entry is {"text", "query"}: "text" is a verbatim fragment of the
    scene (the fragments rebuild the scene text exactly), "query" a clip-search hint that always
    mentions the film/series. The Stage-4 beat windows ("<scene_id>:<1-based n>") are derived
    from the position of each beat, so the order here is the key clips are stored under.

Reuses (read-only, lazily) the comic Q&A pacing band, clickbait-hook filter, causal-marker /
list-language guards and grounded hook from explore_answer so both Q&A modes stay in step.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from config import CREATIVE_LLM_MODELS, get_project_dirs
from utils.atomic_json import write_json_atomic
from ..question_archetype import question_archetype
from ._llm import call_with_chain
from .beat_split import split_hook_fragments
from .schema import Beat, Narration, Scene

# Narration mode key. Deliberately NOT a config.PipelineMode member and NOT a stage_3.modes.MODES
# entry: the first would break the Stage-1 mode dropdown (it iterates the enum), the second would
# change describe_catalog() — the Stage-3 propose prompt of every comic. screen_qa is written by
# this module only and never proposed.
SCREEN_QA_MODE = "screen_qa"

# Same Short pace as write_script._WORDS_PER_SEC (a test keeps them equal).
_WORDS_PER_SEC = 3.4

# Output budget. A scene is {text + 2-5 beat objects with text AND query} ~ 200 tokens; the comic
# Q&A's string beats fit in 900/item, these do not (a fixed 2600 truncated a 5-item answer mid-JSON
# on a live run). Reasoning models also burn tokens before the first brace, hence the headroom.
_TOKENS_PER_SCENE = 450
_TOKEN_HEADROOM = 1500


# ─── prompts ────────────────────────────────────────────────────────────────

_SHARED_RULES = """HARD RULES:
  - YOU ARE TRIMMING, NOT RESEARCHING. Every fact you need is already in the items below, verified against real sources. Your whole job is to COMPRESS that into spoken lines: keep the meaning, drop the words. Never add a fact the item does not state, never soften or hedge one it does, and never re-explain something it already says plainly.
  - THE ITEM NOTES ARE SOURCE, NOT STYLE. They were written to be READ. A listener hears the line once and cannot re-read it, so take the fact and throw away the shape it arrived in.
  - TRIMMING IS NOT VAGUENESS. The CONCRETE part stays: the name, the act, the consequence. What goes is qualifiers, background clauses and anything an earlier scene already established.
  - CITE THE FILM / SERIES AND ITS YEAR in the spoken line, naturally inside the sentence: "In <Title> (<Year>), ..." or "In the <Year> film <Title>, ...". Do this the FIRST time each new title appears (on that item's CONTEXT scene). When the next item comes from the SAME title and year, do not repeat it. NEVER cite an issue number and never write "#" — this is screen media, not a comic.
  - Plain B2 English. Concrete, no purple prose, no riddles. ONE EVENT PER SENTENCE — do not chain clauses with em-dashes into a run-on.
  - TELL THE STORY, NOT THE FILMMAKING. Say what HAPPENS and WHY. Never narrate camera work, editing, cast or the screen itself ("we see", "the camera", "in this scene").
  - WEAVE THE WHY. State who the entity is to the others and why the moment lands, in the SAME sentence(s), so a viewer who has never seen the title understands it.
  - STRIP JARGON — a stranger must know every noun. Swap an obscure proper name for a plain word, or drop it. Household-name characters keep their names.
  - Every sentence is a complete subject-verb-object clause. NEVER a bare reveal fragment ("They are alive."). State the consequence explicitly.
  - EXACTLY TWO scenes per item, in the SAME item order. They have different jobs and you must not merge them:
      * SCENE A — CONTEXT. Lead with the title/year citation and a sourced situation, action or cause that moves beyond the hook. Add only the role or relationship needed to understand the next scene. It sets up the moment; it does NOT deliver it.
      * SCENE B — THE MOMENT. The act itself and its consequence, landing on ground the viewer already has. Do not re-explain scene A.
  - NEVER speak a countdown or rank number ("number five", "third place" — all banned).
  - Each scene is a short lead-in + one or two plain sentences; keep it under {cap} words. The FIRST scene is item 1's CONTEXT scene — do not write a premise or definition scene before it.
  - Total words across ALL scenes must land inside the WORD BUDGET given.
  - VISUAL BEATS (every scene): split the scene into the separate MOMENTS it contains so the video can cut to a fresh clip on each. ONE fragment = ONE showable moment of ~8-14 words; a scene of 35+ words gives 4-5 fragments, ~20-34 words gives 3, a short single-event scene (<=12 words) stays ONE fragment. A citation head that opens a scene ("In <Title> (<Year>),") is its OWN short fragment — the establishing shot. "visual_beats" is a LIST OF OBJECTS {{"text": "<verbatim fragment>", "query": "<clip search phrase>"}}. "text" is VERBATIM: the fragments' exact words, in order, must concatenate back to the scene "text" (you may only drop a comma or dash at a split point) — never drop, reword, add or reorder a word. "query" is 3-8 words a video search would match: the film/series title, the character, and what is on screen.
  - FIDELITY IS ABSOLUTE — do not invert, do not invent. The research is the source of truth and someone else wrote it on purpose. Never swap which side an event happened to, and never add a belief or motive the research does not state. If the research is thin on a point, write LESS.
  - WRITE THE TITLE, THE HOOK AND THE OUTRO TOO:
      * "title" — 4-8 words, a statement. No question mark, no emoji, no hashtag, no issue number, no series name.
      * "hook" — the FIRST spoken line, at most 26 words, preferably 6-18. Make the question's subject clear and expose one sourced, unusual fact from item 1. {hook_extra}Judge hook, scene A and scene B as one chain, never as isolated slogans.
        BANNED — content-free clickbait: "wait until you see/hear...", "you won't believe...", "shouldn't even be possible", "the last one on this list...", "number one...", "makes no sense", or any line promising a surprise without naming anything. If the hook would still make sense pasted onto a different video, it is wrong.
      * "outro" — the LAST spoken line, 6-16 words. It must NOT restate the final scene in other words. Say what the whole thing MEANS, or land one hard image the body earned.
  - Return ONLY JSON, no markdown fences.

Return shape:
{{"title": "...", "hook": "...", "outro": "...", "scenes": [{{"text": "...", "visual_beats": [{{"text": "<verbatim fragment 1>", "query": "<clip search phrase>"}}, {{"text": "<verbatim fragment 2>", "query": "<clip search phrase>"}}]}}, ...]}}"""

_SYSTEM_LIST_HEAD = """You are QAWriter for a screen-trivia YouTube Short. The video answers ONE question by walking through several real moments from films, TV series or animated adaptations (the ITEMS below), given in order from LEAST surprising to MOST surprising (the biggest payoff is the LAST item) — never re-rank them.

For EACH item write exactly TWO scenes: name the entity, give the how/why in plain words, and you may end on one dry remark.

CONNECT THE ITEMS — this is the point of the format:
  - The bridge belongs on each item's CONTEXT scene. The MOMENT scene continues straight out of the scene before it.
  - The FIRST scene adds a sourced situation or cause after the hook. It does not repeat the hook's answer or start with a character biography.
  - A LATER context scene may bridge from the preceding item only if the facts support a real contrast or connection. Otherwise open directly with its entity and new action. Never invent chronology between separate titles, and never use a stock escalation bridge ("Even stranger", "Then it gets worse") to cover an absent connection.
  - After the bridge, still NAME the entity in that same scene (never a bare pronoun on first mention).

"""

_SYSTEM_EXPLAIN_HEAD = """You are QAWriter for a screen-trivia YouTube Short. The video answers ONE Why/How question as an ARGUMENT built from real moments of films, TV series or animated adaptations — the items are stages of the answer (they may come from one title or several), given in escalation order (the revelation is the LAST item) — never re-rank them.

THE SCENES BUILD THE ANSWER — this is the point of the format:
  - Every scene must move the viewer CLOSER to the answer: state what happened AND what it means for the question (cause -> effect), not just the event.
  - The FIRST scene adds a sourced cause, action or stake after the hook. It must not repeat the hook in slower words.
  - Use a bridge only when the next event actually follows from the preceding one; never invent a causal link or use a stock line such as "And that changes everything".
  - The FINAL scene MUST state the answer to the question PLAINLY — one clear sentence a tired viewer can repeat ("That's why..." / "It had to be her, because..."), grounded in that item's moment. The ANSWER THESIS you are given is the destination; land it in your own spoken words.
  - This is ONE story, not a list: NEVER use list language ("this list", "the last one", "number three" — all banned).

For EACH item write exactly TWO scenes: name who/what it is about and give the how/why in plain words.

"""

_HOOK_EXTRA_LIST = "A direct question, a factual statement or an \"After...\" setup can all work if the source supports it; leave the mechanism for the next scenes. "
_HOOK_EXTRA_EXPLAIN = "Do not give away the final answer thesis; let the next scene add a new causal fact. "

_SYSTEM_LIST = _SYSTEM_LIST_HEAD + _SHARED_RULES.format(cap="{cap}", hook_extra=_HOOK_EXTRA_LIST)
_SYSTEM_EXPLAIN = _SYSTEM_EXPLAIN_HEAD + _SHARED_RULES.format(cap="{cap}", hook_extra=_HOOK_EXTRA_EXPLAIN)


# ─── comic-Q&A building blocks, reused read-only ─────────────────────────────

def _ea():
    """stages.stage_3.explore_answer, imported lazily (it pulls the whole comic writer, ~3 s)."""
    from . import explore_answer
    return explore_answer


def _qa_budget(n_items: int) -> tuple[int, int, int, int]:
    """(body word band min, band max, max words per scene, scenes per item) — the comic Q&A
    pacing, so a screen Short lands in the same 55-70 s window."""
    ea = _ea()
    lo, hi = ea._exp_band(n_items)
    return lo, hi, ea._EXP_SCENE_MAX_WORDS, ea._SCENES_PER_ITEM


# ─── text helpers ───────────────────────────────────────────────────────────

# "The final scene states the answer" marker. The comic Q&A pattern only knows the contracted
# "that's why"; a live run produced "That is why they traveled this way: ..." and was flagged as
# never answering. Union of the comic pattern (kept in step, lazily) and the spelled-out forms.
_SPELLED_ANSWER_MARKER = r"\b(?:that|this)\s+is\s+(?:why|how)\b"

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _contains_run(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def _title_variants(title: str) -> list[list[str]]:
    """Ways a title can be SPOKEN: whole, each side of a colon/dash ('Title: Subtitle' is often
    said as just one half), and without a leading 'The'."""
    variants: list[list[str]] = []

    def add(tokens: list[str]) -> None:
        if tokens and any(len(t) >= 4 for t in tokens) and tokens not in variants:
            variants.append(tokens)

    pieces = [title] + re.split(r"\s*[:\-–—]\s+", title)
    for piece in pieces:
        toks = _tokens(piece)
        add(toks)
        if len(toks) > 1 and toks[0] == "the":
            add(toks[1:])
    return variants


def _mentions_title(text: str, title: str) -> bool:
    toks = _tokens(text)
    return any(_contains_run(toks, v) for v in _title_variants(title))


def _cites(text: str, title: str, year: Any) -> bool:
    return _mentions_title(text, title) and str(year).strip() in _tokens(text)


_ARTICLES = frozenset({"the", "a", "an"})


def _entity_named(text: str, entity: str) -> bool:
    """Does `text` name the entity by any of its aliases? Research writes entities as
    "Ant-Man (Scott Lang)" or "Tony Stark / Iron Man"; the writer may use either name. Each alias
    is matched by its first real word (a leading article alone never counts as naming it)."""
    low = str(text or "").lower()
    names = [a for a in re.split(r"[/()\[\],;&]|\band\b", str(entity or "")) if a.strip()]
    for alias in names:
        words = [w for w in re.findall(r"[\w'-]+", alias.lower())]
        while words and words[0] in _ARTICLES and len(words) > 1:
            words.pop(0)
        if words and words[0] not in _ARTICLES and re.search(rf"(?<![\w-]){re.escape(words[0])}(?![\w-])", low):
            return True
    return not names                                   # nothing to check against


def _anchor_query(query: str, title: str) -> str:
    """A clip query must name the film/series, otherwise 'weld sparks' finds any video."""
    query = " ".join(str(query or "").split())
    if not title or _mentions_title(query, title):
        return query or title
    return f"{title} {query}".strip()


def _extract_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _normalize_visual_beats(raw_beats: list[Any], default_query: str = "") -> list[dict[str, str]]:
    """Coerce whatever the writer returned into a list of {text, query} dicts."""
    out: list[dict[str, str]] = []
    for b in raw_beats or []:
        if isinstance(b, dict):
            txt = str(b.get("text", "")).strip()
            qry = str(b.get("query") or b.get("visual_query") or "").strip() or default_query
        else:
            txt, qry = str(b or "").strip(), default_query
        if txt:
            out.append({"text": txt, "query": qry})
    return out


# Words a fragment must not END on: cutting the video between "trapped in" and "the Quantum Realm"
# is a visible stutter. (split_hook_fragments, shared with the comic modes, can do exactly that.)
_DANGLING = frozenset(
    "a an the of in on at to for from with by into onto over under as and or but nor so yet than "
    "that which who whom whose his her their its our my your this these those".split())
_SENTENCE_PUNCT = ",;:.!?—–-"


def _split_fragments(text: str) -> list[str]:
    """Verbatim, drawable fragments of `text` (the shared hook splitter), with any function word
    that the cut stranded at the end of a fragment moved to the start of the next one."""
    text = " ".join(str(text or "").split())
    frags = split_hook_fragments(text) or [text]
    for i in range(len(frags) - 1):
        words = frags[i].split()
        moved: list[str] = []
        while len(words) > 2 and words[-1][-1] not in _SENTENCE_PUNCT and words[-1].lower() in _DANGLING:
            moved.insert(0, words.pop())
        if moved:
            frags[i] = " ".join(words)
            frags[i + 1] = " ".join([*moved, frags[i + 1]])
    return [f for f in frags if f.strip()]


def _fit_beats(text: str, raw_beats: list[Any], item: dict[str, Any]) -> list[dict[str, str]]:
    """Visual beats for one scene: the writer's fragments when they rebuild the scene text
    verbatim, else a deterministic split of the text. Every query names the item's title."""
    title = str(item.get("adaptation_title", ""))
    default_q = str(item.get("visual_query") or f"{title} {item.get('entity', '')}").strip()
    beats = _normalize_visual_beats(raw_beats, default_query=default_q)
    if not beats or _tokens(" ".join(b["text"] for b in beats)) != _tokens(text):
        beats = [{"text": p, "query": default_q} for p in _split_fragments(text)]
    return [{"text": b["text"], "query": _anchor_query(b["query"], title)} for b in beats]


# ─── validation ─────────────────────────────────────────────────────────────

def _new_title_runs(items: list[dict[str, Any]]) -> list[bool]:
    """True for each item whose (title, year) differs from the previous item's — the items that
    must (re)cite their film. A run of items from one title cites it once."""
    flags: list[bool] = []
    prev: tuple[str, str] | None = None
    for it in items:
        key = (" ".join(_tokens(it.get("adaptation_title", ""))), str(it.get("year", "")).strip())
        flags.append(key != prev)
        prev = key
    return flags


def _validate_screen_scenes(
    scenes: list[dict[str, Any]],
    items: list[dict[str, Any]],
    archetype: str = "list",
    *,
    band: tuple[int, int] | None = None,
    scene_max: int | None = None,
    per: int | None = None,
) -> list[str]:
    """Issues in a draft's BODY scenes (hook/outro excluded). [] = clean. Band / per-scene cap /
    scenes-per-item default to the shared Q&A budget."""
    if band is None or scene_max is None or per is None:
        d_lo, d_hi, d_cap, d_per = _qa_budget(len(items))
        band, scene_max, per = band or (d_lo, d_hi), scene_max or d_cap, per or d_per
    issues: list[str] = []
    expected = len(items) * per
    if len(scenes) != expected:
        issues.append(f"expected {expected} scenes ({len(items)} items x {per}), got {len(scenes)}")

    ea = _ea()
    total = 0
    texts = [str(s.get("text", "")).strip() for s in scenes]
    for i, text in enumerate(texts):
        wc = len(text.split())
        total += wc
        if wc > scene_max:
            issues.append(f"scene {i + 1} is {wc}w (max {scene_max})")
        if "#" in text:
            issues.append(f"scene {i + 1} contains '#' — screen Q&A never cites comic issue numbers")
        item_idx = i // per
        if i % per == 0 and item_idx < len(items):                # CONTEXT scene names its entity
            entity = str(items[item_idx].get("entity", "")).strip()
            if entity and not _entity_named(text, entity):
                issues.append(f"scene {i + 1} never names its entity ({entity!r})")
        if archetype == "explain" and ea._LIST_LANGUAGE_RE.search(text):
            issues.append(f"scene {i + 1} uses list language "
                          f"({ea._LIST_LANGUAGE_RE.search(text).group(0)!r}) — this is one story, not a list")
    if not (band[0] <= total <= band[1]):
        issues.append(f"total {total}w outside band {band[0]}-{band[1]}")

    for idx, needs in enumerate(_new_title_runs(items)):
        if not needs:
            continue
        item_text = " ".join(texts[idx * per: idx * per + per])
        title, year = str(items[idx].get("adaptation_title", "")), items[idx].get("year", "")
        if item_text and not _cites(item_text, title, year):
            issues.append(f"item {idx + 1} scenes never cites its title and year ({title!r}, {year}) — "
                          f"say \"In <Title> ({year}), ...\" on the CONTEXT scene")
    if archetype == "explain" and texts and not (
            ea._CAUSAL_MARKER_RE.search(texts[-1])
            or re.search(_SPELLED_ANSWER_MARKER, texts[-1], re.IGNORECASE)):
        issues.append("final scene never states the ANSWER — it must answer the question plainly "
                      "(a 'that's why / because / it had to be' sentence), not just narrate the last event")
    return issues


# ─── LLM ────────────────────────────────────────────────────────────────────

def _call_llm_chain(
    system: str,
    user: str,
    *,
    models: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
    validator: Callable[[str], bool] | None = None,
    max_tokens: int = 2600,
) -> tuple[str, str]:
    """Dispatch through the project LLM chain (honours config.FREE_MODEL)."""
    return call_with_chain(
        system=system,
        user=user,
        models=models if models else list(CREATIVE_LLM_MODELS),
        max_tokens=max_tokens,
        progress=progress,
        label="screen_write",
        validator=validator,
    )


def _items_block(items: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{i}. entity={str(it.get('entity', '')).strip()!r} | "
        f"film_or_series={str(it.get('adaptation_title', '')).strip()!r} | year={it.get('year', '')} | "
        f"what_happens={str(it.get('summary', '')).strip()!r} | moment={str(it.get('event', '')).strip()!r} | "
        f"clip_search_hint={str(it.get('visual_query', '')).strip()!r}"
        for i, it in enumerate(items, start=1)
    )


def _user_prompt(
    question: str,
    items: list[dict[str, Any]],
    *,
    archetype: str,
    thesis: str,
    hook_hint: str,
    issues: list[str] | None,
    budget: tuple[int, int, int, int],
) -> str:
    lo, hi, cap, per = budget
    thesis_block = (
        f"ANSWER THESIS — the destination of the whole video; the FINAL scene must state this "
        f"plainly in your own spoken words: {thesis.strip()}\n\n"
        if archetype == "explain" and thesis.strip() else ""
    )
    fix_block = ("PREVIOUS DRAFT HAD ISSUES — FIX THESE:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
                 if issues else "")
    hint_block = f"HOOK HINT (a lead from the producer, use only if the items support it): {hook_hint.strip()}\n\n" \
        if hook_hint.strip() else ""
    return (
        f"QUESTION being answered: {question}\n\n"
        f"{hint_block}{thesis_block}{fix_block}"
        f"ITEMS — write {per} scenes per item (CONTEXT then MOMENT), in this EXACT order (do not reorder):\n"
        f"{_items_block(items)}\n\n"
        f"WORD BUDGET: {lo}-{hi} words total across all {len(items) * per} scenes — {len(items)} items x "
        f"{per} scenes each (every scene under {cap} words).\n"
        'Return JSON: {"title": "...", "hook": "...", "outro": "...", "scenes": [{"text": "...", '
        '"visual_beats": [{"text": "<verbatim fragment>", "query": "<clip search phrase>"}]}, '
        "... TWO per item, context first then moment ...]}."
    )


def _lower_first(text: str) -> str:
    text = text.strip()
    return text[:1].lower() + text[1:] if text else text


def _deterministic_script(question: str, items: list[dict[str, Any]], archetype: str, project: str) -> dict[str, Any]:
    """Grounded script used when the LLM is unavailable or returns nothing usable."""
    ea = _ea()
    scenes: list[dict[str, Any]] = []
    for it in items:
        entity = str(it.get("entity", "")).strip()
        title, year = str(it.get("adaptation_title", "")).strip(), it.get("year", "")
        event = _lower_first(str(it.get("event", "")))
        summary = str(it.get("summary", "")).strip()
        scenes.append({"text": f"In {title} ({year}), {entity} {event}".rstrip(" .") + "."})
        scenes.append({"text": summary or f"{entity} {event}".rstrip(" .") + "."})
    last = str(items[-1].get("entity", "")).strip()
    return {
        "title": " ".join(question.rstrip("?.! ").split()[:8]) or "Screen Q&A",
        "hook": ea._build_hook(question, {"items": items}, archetype, project),
        "outro": f"{last} is why the answer holds." if last else "",
        "scenes": scenes,
    }


# ─── assembly ───────────────────────────────────────────────────────────────

def _repair_citations(body: list[dict[str, Any]], items: list[dict[str, Any]], per: int) -> None:
    """Last resort when the writer (twice) never cited a new title: open that item's CONTEXT scene
    with the title and year as its own establishing fragment. In place."""
    for idx, needs in enumerate(_new_title_runs(items)):
        scene_ix = idx * per
        if not needs or scene_ix >= len(body):
            continue
        title, year = str(items[idx].get("adaptation_title", "")).strip(), items[idx].get("year", "")
        joined = " ".join(str(s.get("text", "")) for s in body[scene_ix: scene_ix + per])
        if _cites(joined, title, year):
            continue
        head = f"{title} ({year})."
        scene = body[scene_ix]
        raw = _normalize_visual_beats(scene.get("visual_beats") or [])
        scene["visual_beats"] = [{"text": head, "query": ""}, *raw]
        scene["text"] = f"{head} {str(scene.get('text', '')).strip()}"


def _make_scene(scene_id: int, text: str, beats: list[dict[str, str]], beat_id: int,
                *, intro: bool = False, outro: bool = False) -> Scene:
    wc = len(text.split())
    return Scene(
        scene_id=scene_id,
        text=text,
        page_ref=0,
        panel_ref=-1,
        word_count=wc,
        target_seconds=round(wc / _WORDS_PER_SEC, 2),
        connective=None,
        beat_id=beat_id,
        is_intro=intro,
        is_outro=outro,
        visual_beats=beats,
    )


def _answer_beats(items: list[dict[str, Any]]) -> list[Beat]:
    n = len(items)
    beats: list[Beat] = []
    for pos, it in enumerate(items):
        entity = str(it.get("entity", "")).strip()
        beats.append(Beat(
            id=pos + 1,
            function="COLD_OPEN" if pos == 0 else ("LANDING" if pos == n - 1 else "SETUP"),
            name=entity or f"item {pos + 1}",
            summary=str(it.get("summary", "")).strip(),
            characters_active=[entity] if entity else [],
            cause=str(it.get("event", "")).strip(),
        ))
    return beats


def write_screen_qa(
    project_name: str,
    *,
    hook_hint: str = "",
    progress: Callable[[str], None] | None = None,
    model: str | None = None,
) -> Narration:
    """screen_context.json -> Narration (hook, 2 scenes per item, outro), not yet saved."""
    log = progress or (lambda _m: None)
    root = get_project_dirs(project_name)["root"]
    context_path = root / "screen_context.json"
    if not context_path.exists():
        raise FileNotFoundError(
            f"[screen_qa] missing {context_path} — run Stage 1 screen research first.")
    context = json.loads(context_path.read_text())
    question = str(context.get("question", "")).strip()
    items = [it for it in (context.get("items") or []) if isinstance(it, dict)]
    if not items:
        raise RuntimeError(f"[screen_qa] {context_path} has no items.")
    thesis = str(context.get("answer_summary", "") or "").strip()
    archetype = question_archetype(question)
    budget = _qa_budget(len(items))
    lo, hi, cap, per = budget
    expected = len(items) * per

    log(f"[screen_qa] writing screen Q&A narration for {len(items)} item(s) (archetype={archetype})...")

    system = (_SYSTEM_EXPLAIN if archetype == "explain" else _SYSTEM_LIST).replace("{cap}", str(cap))
    ea = _ea()

    def _valid(raw: str) -> bool:
        p = _extract_json(raw)
        if not (p and isinstance(p.get("scenes"), list)):
            log("[screen_qa] validator: unparsable response")
            return False
        scenes = p["scenes"]
        hook = re.sub(r"\s+", " ", str(p.get("hook") or "").strip()).casefold()
        first = (re.sub(r"\s+", " ", str(scenes[0].get("text") or "").strip()).casefold()
                 if scenes and isinstance(scenes[0], dict) else "")
        if len(hook.split()) >= 4 and first.startswith(hook):
            log("[screen_qa] validator: first context scene repeats hook")
            return False
        return len(scenes) == expected

    best: tuple[int, dict[str, Any], str, list[str]] | None = None
    issues: list[str] | None = None
    for attempt in (1, 2):
        try:
            raw, used = _call_llm_chain(
                system,
                _user_prompt(question, items, archetype=archetype, thesis=thesis,
                             hook_hint=hook_hint, issues=issues, budget=budget),
                models=[model] if model else None, progress=progress, validator=_valid,
                max_tokens=_TOKENS_PER_SCENE * expected + _TOKEN_HEADROOM,
            )
        except Exception as exc:  # noqa: BLE001 - any provider failure -> grounded fallback below
            log(f"[screen_qa] LLM call failed ({exc}); using grounded fallback")
            break
        parsed = _extract_json(raw)
        if not parsed or not isinstance(parsed.get("scenes"), list) or not parsed["scenes"]:
            log("[screen_qa] writer returned no usable scenes")
            break
        found = _validate_screen_scenes(parsed["scenes"], items, archetype,
                                        band=(lo, hi), scene_max=cap, per=per)
        if best is None or len(found) < best[0]:
            best = (len(found), parsed, used, found)
        if not found:
            break
        if attempt == 1:
            log(f"[screen_qa] draft has {len(found)} issue(s); retrying once: {found}")
            issues = found

    if best is None:
        log("[screen_qa] building deterministic fallback script")
        parsed, llm_model, unresolved = _deterministic_script(question, items, archetype, project_name), "", []
    else:
        _, parsed, llm_model, unresolved = best
        if unresolved:
            log(f"[screen_qa] shipping with unresolved issue(s): {unresolved}")

    # ── body: 2 scenes per item (scene i -> item i // per, clamped if the draft is off-count) ──
    body = [dict(s) for s in parsed["scenes"] if isinstance(s, dict) and str(s.get("text", "")).strip()]
    _repair_citations(body, items, per)

    def item_of(scene_ix: int) -> int:
        return min(scene_ix // per, len(items) - 1)

    scenes: list[Scene] = []

    # HOOK: the writer's concrete line, else the grounded question.
    hook_text = " ".join(str(parsed.get("hook") or "").split())
    if not ea._writer_hook_usable(hook_text):
        hook_text = ea._build_hook(question, {"items": items}, archetype, project_name)
        log("[screen_qa] writer hook missing or generic — used grounded fallback")
    first = items[0]
    scenes.append(_make_scene(1, hook_text, _fit_beats(hook_text, [], first), 0, intro=True))

    for ix, sc in enumerate(body):
        it = items[item_of(ix)]
        text = " ".join(str(sc["text"]).split())
        scenes.append(_make_scene(len(scenes) + 1, text, _fit_beats(text, sc.get("visual_beats") or [], it),
                                  item_of(ix) + 1))

    last_item = items[-1]
    outro_text = " ".join(str(parsed.get("outro") or "").split())
    if not outro_text:
        outro_text = f"{str(last_item.get('entity', '')).strip() or 'That'} is why the answer holds."
        log("[screen_qa] outro missing — used grounded fallback")
    scenes.append(_make_scene(len(scenes) + 1, outro_text, _fit_beats(outro_text, [], last_item),
                              len(items), outro=True))

    title = " ".join(str(parsed.get("title") or "").split()) or question
    total_words = sum(s.word_count for s in scenes)
    narration = Narration(
        mode=SCREEN_QA_MODE,
        title=title,
        hook=hook_text,
        banner_title=title,
        scenes=scenes,
        total_word_count=total_words,
        estimated_duration_seconds=round(total_words / _WORDS_PER_SEC, 2),
        words_per_second=_WORDS_PER_SEC,
        source_project=project_name,
        llm_model=llm_model,
        beats=_answer_beats(items),
    )
    log(f"[screen_qa] written {len(scenes)} scenes (~{narration.estimated_duration_seconds}s, {total_words} words)")
    return narration


def save_screen_narration(
    narration: Narration,
    project_name: str,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Save narration.json to the project root."""
    log = progress or (lambda _m: None)
    root = get_project_dirs(project_name)["root"]
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / "narration.json"
    write_json_atomic(out_path, narration.to_dict())
    log(f"[screen_qa] saved narration → {out_path}")
    return out_path
