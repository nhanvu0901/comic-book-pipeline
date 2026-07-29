"""Stage 3 writer for the "explore_answer" (Q&A) mode.

Grounding INVERTS vs narrate mode: a normal comic already has panels to narrate.
Here, Stage 1's answer-research (`projects/<slug>/answer_context.json`) already
found the FACTS (one item per source comic); this module turns those facts into
a deterministic beat-per-item outline, then writes short scenes for them. See
EXPLORE_ANSWER_DESIGN.md (root, addendum v2 is binding) for the full spec.

Additive only — narrate mode never imports this file; write_script() dispatches
here before any of its own machinery runs (see write_script.py's mode check).
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from config import CREATIVE_LLM_MODELS, ENABLE_LOOP_TEASE, OPENROUTER_MODEL, PROJECTS_ROOT
from ..question_archetype import question_archetype, is_statement_lead, is_comparison
from .schema import Beat, Glossary, Narration
from ._llm import call_with_chain
from .story_verify import run_story_verify
from .._arc import issue_index_of_page
from .write_script import (
    _anchor_scenes_to_beats,
    _append_loop_tease,
    _extract_json,
    _outro_is_concrete,
    _to_narration,
    _HOOK_MAX_WORDS,
    _WORDS_PER_SEC,
    generate_loop_tease,
    generate_outro,
)

# Explore-mode word budget — fully self-contained. (Recap's _SCENE_MAX_WORDS / word band
# used to be imported here but were never read; the dead imports were dropped 2026-07-27
# when recap moved to the long variant-profile register, so Q&A can't inherit it.)
# A Q&A scene now
# carries a connective bridge + entity + how/why + a dry remark, so it needs room;
# the total scales with the number of items instead of a fixed band, since a question
# can have anywhere from 3 to 6+ answers.
_EXP_SCENE_MAX_WORDS = 42

# TWO scenes per item (Master 2026-07-28), not one. One scene per item forced the writer to
# carry "who is this / why does it matter" in a subordinate clause, and the trim pass is exactly
# what deletes subordinate clauses — so the context vanished and cold_viewer[relationship] fired
# on video after video ("his only true friend" never named, entities appearing unintroduced).
# Splitting the item gives context its own scene and the moment its own scene, so neither has to
# survive at the other's expense. Measured on loki-told-the-truth: same 3 items, same facts,
# 138w/40s (one scene each, confusing) -> 216w/56s (two scenes each, self-explaining).
_SCENES_PER_ITEM = 2
# Across BOTH of an item's scenes, i.e. ~17 each. LOWER per scene than the old 22, not higher:
# 22 was sized for a scene that had to carry entity + how + why + stakes on its own. With the
# job split, a context scene is legitimately short ("Years earlier the original Loki died." = 6w)
# and a moment scene no longer re-explains anything. Sizing it any higher breaks the ceiling —
# 6 items x 44w = 264w = 78s of body, past the 70s cap no matter what the seconds target says.
_EXP_WORDS_PER_ITEM_MIN = 34

# SECONDS-based soft target for the FINISHED VIDEO (hook + body + outro) — the
# only real research we have (Paddy Galloway, 2023) puts Shorts completion at
# its best around 50-60s; widened slightly for buffer. A flat per-item*n_items
# band (above) has no ceiling on runtime: a measured 3-item Q&A landed ~35.6s
# (too short, near the old floor) while a 6-item one can hit 81s+ of body alone
# (way past the old ceiling). _exp_band() below converts this seconds target
# into a body word band using the recap's MEASURED render pace, so it scales
# with item count instead of hard-coding one question's numbers.
# RAISED 40-55 -> 55-70 (2026-07-28): two scenes per item cannot fit the old ceiling.
_QA_TARGET_MIN_SEC = 55
_QA_TARGET_MAX_SEC = 70
# Rough combined runtime of the two pieces that bookend the body and this
# module doesn't budget directly: the deterministic hook (_build_hook, capped
# at _HOOK_MAX_WORDS=26) and the LLM outro (thematic 4-14w + optional loop
# tease 3-14w, see write_script.py's _OUTRO_SYSTEM/_LOOP_TEASE_SYSTEM). Subtracted
# from the target above so the BODY band actually lands the FULL video in range.
_QA_INTRO_OUTRO_SEC = 10


def _exp_band(n_items: int) -> tuple[int, int]:
    """Total body word band for `n_items` countdown scenes (excludes intro/outro).

    Derived from the seconds target above, not a flat per-item multiply: few
    items (2-3) get MORE words each to reach the floor, many items (5-6) get
    FEWER words each to stay under the ceiling. Still clamped to the per-item
    sanity range — never below the old floor (_EXP_WORDS_PER_ITEM_MIN, a scene
    needs enough words to name the entity + why) and never above what's
    physically reachable (every scene maxed out at _EXP_SCENE_MAX_WORDS, the
    hard per-scene cap enforced below in _validate_explore_scenes)."""
    body_min_sec = max(_QA_TARGET_MIN_SEC - _QA_INTRO_OUTRO_SEC, 0)
    body_max_sec = max(_QA_TARGET_MAX_SEC - _QA_INTRO_OUTRO_SEC, 0)
    sec_min_words = round(body_min_sec * _WORDS_PER_SEC)
    sec_max_words = round(body_max_sec * _WORDS_PER_SEC)
    absolute_min = n_items * _EXP_WORDS_PER_ITEM_MIN
    absolute_max = n_items * _SCENES_PER_ITEM * _EXP_SCENE_MAX_WORDS
    band_min = max(absolute_min, min(sec_min_words, absolute_max))
    band_max = max(absolute_min, min(sec_max_words, absolute_max))
    if band_max < band_min:
        band_max = band_min  # degenerate guard: very few items can't physically reach the floor
    return band_min, band_max


def _body_scenes(scenes: list[dict]) -> list[dict]:
    """Scenes minus a trailing channel-credit scene ("The comic is X.").

    The writer emits that credit as an extra scene and _anchor_scenes_to_beats pops it off to
    rebuild the outro — so it is NOT one of the item scenes and must not be counted as one.
    Both count gates below were written before that was true for Q&A and rejected every
    compliant draft as one-too-many (want 6, got 7), which surfaced only as "SDK failed".
    One convention, three call sites: here, _valid, and the anchor helper."""
    if scenes and "comic is" in str(scenes[-1].get("text", "")).lower():
        return scenes[:-1]
    return scenes


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
  - The bridge belongs on each item's CONTEXT scene (scene A), which is where a new entity enters. The item's MOMENT scene (scene B) needs no bridge — it continues straight out of the scene before it, which is its own setup.
  - The FIRST scene of the whole video opens straight on its entity, no bridge.
  - Every LATER context scene must OPEN with a short connective bridge that links it to the item before and builds momentum toward the final twist — e.g. "Years earlier,...", "Unlike him,...", "Even stranger,...", "But this one...", "Then it gets worse —...". Vary the bridge every time; NEVER reuse the same opener, and NEVER a bare list.
  - The connective is contrast or escalation, NOT a rank. So "Unlike the Punisher, Deadpool..." is good; "the next one", "number three", "third" as a POSITION word is banned.
  - After the bridge, still NAME the entity in that same scene (never a bare pronoun on first mention).

HARD RULES:
  - YOU ARE TRIMMING, NOT RESEARCHING. Every fact you need is already in the items below, verified against real sources. Your whole job is to COMPRESS that into spoken lines: keep the meaning, drop the words. Never add a fact the item does not state, never soften or hedge one it does, and never re-explain something it already says plainly. If an item's notes run three written sentences, the scene is ONE spoken sentence carrying the same meaning — same actor, same act, same consequence, fewer words.
  - THE ITEM NOTES ARE SOURCE, NOT STYLE. They were written to be READ: headings, parentheticals, citations, long subordinate clauses, quoted dialogue. None of that survives into narration — a listener hears the line once and cannot re-read it. Take the fact; throw away the shape it arrived in. Quote at most a few words of dialogue, and only when the exact wording IS the moment.
  - TRIMMING IS NOT VAGUENESS. When you shorten, the CONCRETE part is what stays: the name, the act, the consequence. What goes is qualifiers, background clauses, and anything an earlier scene already established. "He confessed a terrible secret" is a BAD trim of "he admitted he killed his own younger self" — the specific act IS the point of the scene.
  - Plain B2 English. Concrete, no purple prose, no riddles.
  - ONE EVENT PER SENTENCE. Say one thing, then stop — do NOT chain three clauses with em-dashes into a run-on. A short lead-in bridge + one plain event is the whole sentence.
  - TELL THE STORY, NOT THE PICTURES. Your source of truth is the STORY / research (what HAPPENED and WHY), NOT a description of the comic art. Never narrate the artwork ("we see", "a figure holding", "in this panel/frame", colours / poses / camera for their own sake) — write the plain STORY EVENT the panel stands for.
  - WEAVE THE WHY. If an item carries a relationships or stakes_why note, the scene MUST state it in plain words inside the SAME sentence(s) — who the entity is to the others, and why the moment lands — so a zero-context viewer never hears an action without knowing why it matters. NEVER assume the viewer knows any character's history. This is woven-in context, not extra scenes or extra words.
  - STRIP JARGON — a stranger must know every noun. The items below WILL carry obscure proper names (realms, teams, materials, minor characters). Test each noun: would a first-time viewer know it? If not, swap the plain word ("Cancerverse" -> "a dead universe", "the Negative Zone" -> "a prison dimension") or drop it — never make the viewer hit a word they'd have to look up. (Household-name heroes/villains still keep their names — see the name rule below.)
  - Every sentence is a complete subject-verb-object clause (say who does what, or what happens) — NEVER a bare reveal fragment standing alone (banned pattern: a lone line like "They are alive." or "Dummies." dropped with zero surrounding context). State the consequence or twist EXPLICITLY, in that same sentence or the very next one with context — e.g. "They survive — and it means [state the meaning plainly]," never a flat unexplained line. When quoting a message written or drawn inside the art, introduce it naturally inside the sentence ("...and leaves one message on them: [the message]" / "...with the words '[the message]' painted across it") — never drop a floating quoted phrase mid-sentence. A viewer with zero context, hearing the line for the first time, must understand it immediately.
  - Do NOT write a premise, definition or set-up scene explaining the question's subject ("Adamantium is Marvel's unbreakable metal..."). The video's spoken hook already states it, and an extra scene here silently pushes every item's panels one slot out of place. Your FIRST scene is item 1's CONTEXT scene.
  - EXACTLY TWO scenes per item, in the SAME item order. They have different jobs and you must not merge them:
      * SCENE A — CONTEXT. Establish WHO or WHAT this is for a viewer who has never heard the name: who the person is, what their power or role is, what situation they are in. This scene sets up the moment; it does NOT deliver it. If a later scene will hinge on someone's ability ("she can detect any lie"), on a relationship ("his own younger self"), or on a stake, THIS is where it gets stated plainly.
      * SCENE B — THE MOMENT. The act itself and its consequence, now landing on ground the viewer already has. Do not re-explain what scene A established; assume it.
    Scene A never spoils B, and B never needs a parenthetical to be understood. The reason this format exists: with one scene per item, context has to ride inside a subordinate clause, and shortening the line is exactly what deletes it — so the viewer hears an action with no idea who it happened to.
  - NEVER speak a countdown/rank number ("number five", "#3", "third place" — all banned).
  - Each scene is a short lead-in + one or two plain sentences; keep it under 42 words.
  - Total words across ALL scenes must land inside the WORD BUDGET given.
  - Name only household names: an obscure character/place/team/artifact gets a plain one-word descriptor instead of its proper name, chosen once and reused; supporting characters with mainstream movie/TV presence keep their names.
  - Introduce once: role tag/epithet on first mention only; later mentions use the bare name or the same descriptor — never new adjectives, never the same descriptor for two different things.
  - MINI-ARC PER ITEM: each scene moves through setup (the source comic / where we are) -> the VISUAL TURN (the action or event drawn on the page) -> the payoff (its consequence or the twist). Never flatten an item into one wiki-style fact with no beat. This is the SHAPE inside the SAME 1-2 sentences and word budget, NOT extra words — and the visual_beats below split on exactly these beats.
  - VISUAL BEATS (every scene): split each scene into the separate MOMENTS it contains, so Stage 5 can cut to a fresh image on each. ONE fragment = ONE drawable moment of ~8-14 words; split by LENGTH — a scene of 35+ words gives 4-5 fragments, ~20-34 words gives 3, and a short single-event scene (<=12 words) stays ONE fragment = the whole "text". A citation / connective HEAD that opens the scene ("But in <issue name>,..." / "In <issue> #N,...") is ALWAYS its own separate short fragment — it is the establishing shot that sets the place before the action. "visual_beats" is a LIST OF STRINGS — the scene's OWN words, split at its punctuation / connective (comma / dash / and / but / then), each ONE separately-drawable moment. VERBATIM ONLY: the fragments' exact words, in order, must concatenate back to "text" (you may only drop a comma or dash at a split point) — NEVER drop a word, not even a connective (and / but / then must stay, at the start of the next fragment); never reword, add, or reorder words.
  - Return ONLY JSON, no markdown fences.

Return shape:
{"scenes": [{"text": "...", "visual_beats": ["<verbatim fragment 1>", "<verbatim fragment 2>", "<... one fragment per drawable moment, count set by the length rule>"], "connective": null, "beat_id": <id>}, ...]}"""

_EXPLORE_WRITE_SYSTEM_EXPLAIN = """You are QAWriter for a comic-trivia YouTube Short. The video answers ONE Why/How question as an ARGUMENT built from real comic moments — the items are stages of the answer (they may come from one story or from several different comics), given in escalation order (the revelation is the LAST item) — never re-rank them.

THE SCENES BUILD THE ANSWER — this is the point of the format:
  - Every scene must move the viewer CLOSER to the answer: state what happened AND what it means for the question (cause → effect), not just the event.
  - The FIRST scene sets the broken state / the stakes.
  - EVERY scene AFTER the first must OPEN with a short connective bridge of consequence or escalation — e.g. "But that was only the surface...", "Which is when it turns...", "And that changes everything —...". Vary the bridge every time.
  - The FINAL scene MUST state the answer to the question PLAINLY — one clear sentence a tired viewer can repeat ("That's why..." / "It had to be her, because..."), grounded in that item's moment. The ANSWER THESIS you are given is the destination; land it in your own spoken words.

For EACH item, write exactly ONE scene: name who/what it is about, give the how/why in plain words (speak the SOURCE COMIC naturally inside the sentence — "...in Ghost Rider #35..." — never as a citation, parentheses, or trailing credit).

HARD RULES:
  - YOU ARE TRIMMING, NOT RESEARCHING. Every fact you need is already in the items below, verified against real sources. Your whole job is to COMPRESS that into spoken lines: keep the meaning, drop the words. Never add a fact the item does not state, never soften or hedge one it does, and never re-explain something it already says plainly. If an item's notes run three written sentences, the scene is ONE spoken sentence carrying the same meaning — same actor, same act, same consequence, fewer words.
  - THE ITEM NOTES ARE SOURCE, NOT STYLE. They were written to be READ: headings, parentheticals, citations, long subordinate clauses, quoted dialogue. None of that survives into narration — a listener hears the line once and cannot re-read it. Take the fact; throw away the shape it arrived in. Quote at most a few words of dialogue, and only when the exact wording IS the moment.
  - TRIMMING IS NOT VAGUENESS. When you shorten, the CONCRETE part is what stays: the name, the act, the consequence. What goes is qualifiers, background clauses, and anything an earlier scene already established. "He confessed a terrible secret" is a BAD trim of "he admitted he killed his own younger self" — the specific act IS the point of the scene.
  - Plain B2 English. Concrete, no purple prose, no riddles.
  - ONE EVENT PER SENTENCE. Say one thing, then stop — do NOT chain three clauses with em-dashes into a run-on. A short lead-in bridge + one plain event is the whole sentence.
  - TELL THE STORY, NOT THE PICTURES. Your source of truth is the STORY / research (what HAPPENED and WHY), NOT a description of the comic art. Never narrate the artwork ("we see", "a figure holding", "in this panel/frame", colours / poses / camera for their own sake) — write the plain STORY EVENT the panel stands for.
  - WEAVE THE WHY. If an item carries a relationships or stakes_why note, the scene MUST state it in plain words inside the SAME sentence(s) — who the entity is to the others, and why the moment lands — so a zero-context viewer never hears an action without knowing why it matters. NEVER assume the viewer knows any character's history. This is woven-in context, not extra scenes or extra words.
  - STRIP JARGON — a stranger must know every noun. The items below WILL carry obscure proper names (realms, teams, materials, minor characters). Test each noun: would a first-time viewer know it? If not, swap the plain word ("Cancerverse" -> "a dead universe", "the Negative Zone" -> "a prison dimension") or drop it — never make the viewer hit a word they'd have to look up. (Household-name heroes/villains still keep their names — see the name rule below.)
  - Every sentence is a complete subject-verb-object clause (say who does what, or what happens) — NEVER a bare reveal fragment standing alone (banned pattern: a lone line like "They are alive." or "Dummies." dropped with zero surrounding context). State the consequence or twist EXPLICITLY, in that same sentence or the very next one with context — e.g. "They survive — and it means [state the meaning plainly]," never a flat unexplained line. When quoting a message written or drawn inside the art, introduce it naturally inside the sentence ("...and leaves one message on them: [the message]" / "...with the words '[the message]' painted across it") — never drop a floating quoted phrase mid-sentence. A viewer with zero context, hearing the line for the first time, must understand it immediately.
  - Do NOT write a premise, definition or set-up scene explaining the question's subject ("Adamantium is Marvel's unbreakable metal..."). The video's spoken hook already states it, and an extra scene here silently pushes every item's panels one slot out of place. Your FIRST scene is item 1's CONTEXT scene.
  - EXACTLY TWO scenes per item, in the SAME item order. They have different jobs and you must not merge them:
      * SCENE A — CONTEXT. Establish WHO or WHAT this is for a viewer who has never heard the name: who the person is, what their power or role is, what situation they are in. This scene sets up the moment; it does NOT deliver it. If a later scene will hinge on someone's ability ("she can detect any lie"), on a relationship ("his own younger self"), or on a stake, THIS is where it gets stated plainly.
      * SCENE B — THE MOMENT. The act itself and its consequence, now landing on ground the viewer already has. Do not re-explain what scene A established; assume it.
    Scene A never spoils B, and B never needs a parenthetical to be understood. The reason this format exists: with one scene per item, context has to ride inside a subordinate clause, and shortening the line is exactly what deletes it — so the viewer hears an action with no idea who it happened to.
  - This is ONE story, not a list: NEVER use list language ("this list", "the last one", "number three" — all banned).
  - Each scene is a short lead-in + one or two plain sentences; keep it under 42 words.
  - Total words across ALL scenes must land inside the WORD BUDGET given.
  - Name only household names: an obscure character/place/team/artifact gets a plain one-word descriptor instead of its proper name, chosen once and reused; supporting characters with mainstream movie/TV presence keep their names.
  - Introduce once: role tag/epithet on first mention only; later mentions use the bare name or the same descriptor — never new adjectives, never the same descriptor for two different things.
  - MINI-ARC PER ITEM: each scene moves through setup (the source comic / where we are) -> the VISUAL TURN (the action or event drawn on the page) -> the payoff (its consequence or the twist). Never flatten an item into one wiki-style fact with no beat. This is the SHAPE inside the SAME 1-2 sentences and word budget, NOT extra words — and the visual_beats below split on exactly these beats.
  - VISUAL BEATS (every scene): split each scene into the separate MOMENTS it contains, so Stage 5 can cut to a fresh image on each. ONE fragment = ONE drawable moment of ~8-14 words; split by LENGTH — a scene of 35+ words gives 4-5 fragments, ~20-34 words gives 3, and a short single-event scene (<=12 words) stays ONE fragment = the whole "text". A citation / connective HEAD that opens the scene ("But in <issue name>,..." / "In <issue> #N,...") is ALWAYS its own separate short fragment — it is the establishing shot that sets the place before the action. "visual_beats" is a LIST OF STRINGS — the scene's OWN words, split at its punctuation / connective (comma / dash / and / but / then), each ONE separately-drawable moment. VERBATIM ONLY: the fragments' exact words, in order, must concatenate back to "text" (you may only drop a comma or dash at a split point) — NEVER drop a word, not even a connective (and / but / then must stay, at the start of the next fragment); never reword, add, or reorder words.
  - Return ONLY JSON, no markdown fences.

Return shape:
{"scenes": [{"text": "...", "visual_beats": ["<verbatim fragment 1>", "<verbatim fragment 2>", "<... one fragment per drawable moment, count set by the length rule>"], "connective": null, "beat_id": <id>}, ...]}"""


# Q&A-specific outro contract (passed to write_script.generate_outro's
# system_override). The recap _OUTRO_SYSTEM there is written for a SINGLE-STORY
# retelling ("the cost the hero paid") and has no idea a Q&A video asked one
# question then answered it with items from several different comics — it kept
# producing generic thematic lines unrelated to what was just shown (Master:
# "boring, not related, seems come from the guy who not watch the video").
# This system prompt instead lands the QUESTION's answer using the actual items.
_QA_OUTRO_SYSTEM = """You are OutroWriter for a comic-trivia Q&A Short. The video asked ONE question, then answered it with real comic moments (the ITEMS below) in escalation order — the LAST item is the payoff the hook promised. Write ONE closing line: the final sentence the viewer hears right after that last item.

HARD RULES:
  - 4-14 words, exactly ONE sentence, ends with ".".
  - LAND the answer: crown the LAST item or tie all items back to the question's famous "unbreakable" fact — say plainly what the answer proves about it.
  - ONE simple idea, stated plainly. Do NOT restate HOW it happened — no weapon, method, or mechanism details (those belong to the body, the viewer just saw them). Name WHO + the takeaway, nothing else. ✓ "Even the King of Hell couldn't survive his own son." ✗ "...falls when Blackheart's blade carries innocent blood." (mechanism re-told = rejected)
  - MUST contain at least one CONCRETE element from the ITEMS — a character's name, or a physical event (a stab, a fall, a stolen throne). Never introduce a fact that is not in the ITEMS.
  - The viewer just watched these moments — do NOT re-summarize them; give the takeaway.
  - It is NOT a question. No meta talk, no "comic is", no comic titles, no hashtags.
  - IELTS 6.5 / B2 PLAIN ENGLISH. Punchy, quotable, no purple phrasing.

Return ONLY JSON, no markdown: {"outro_line": "..."}"""


def _viewer_context_block(answer_context: dict) -> str:
    """The question-level WHO/WHY a zero-context viewer needs before the items land
    (Stage 1 answer_research's viewer_context + constant_broken). "" when neither is
    present, so an old answer_context.json yields a byte-identical writer prompt."""
    vc = str(answer_context.get("viewer_context", "") or "").strip()
    cb = str(answer_context.get("constant_broken", "") or "").strip()
    parts: list[str] = []
    if vc:
        parts.append("VIEWER CONTEXT (a zero-context viewer must know this before the items "
                     f"make sense — weave it into the FIRST scene): {vc}")
    if cb:
        parts.append(f"THE CONSTANT BEING BROKEN (the famous rule these answers violate): {cb}")
    return ("\n".join(parts) + "\n\n") if parts else ""


def _items_block(beats: list[Beat], items: list[dict]) -> str:
    lines = []
    for b, item in zip(beats, items):
        # relationships / stakes_why are ADDITIVE (Stage 1 answer_research): only appended
        # when present, so old answer_context.json without them produces the exact old block.
        rel = str(item.get("relationships", "") or "").strip()
        stakes = str(item.get("stakes_why", "") or "").strip()
        extra = (f" | relationships={rel!r}" if rel else "") + \
                (f" | stakes_why={stakes!r}" if stakes else "")
        lines.append(
            f"{b.id}. entity={b.name!r} | source_comic={item.get('source_comic', '?')!r} | "
            f"how_or_why={b.cause!r} | moment={item.get('drawable_moment', '')!r}{extra}"
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
    clarity_fixes: str = "",
    context_block: str = "",
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
        f"{context_block}"
        f"{thesis_block}"
        f"{fix_block}"
        f"{clarity_fixes}"
        f"ITEMS — write {_SCENES_PER_ITEM} scenes per item (CONTEXT then MOMENT), in this EXACT order (do not reorder):\n"
        f"{_items_block(beats, items)}\n\n"
        f"WORD BUDGET: {_exp_band(len(beats))[0]}-{_exp_band(len(beats))[1]} words total across all "
        f"{len(beats) * _SCENES_PER_ITEM} scenes — {len(beats)} items x {_SCENES_PER_ITEM} scenes each (every scene under {_EXP_SCENE_MAX_WORDS} words).\n"
        f'Return JSON: {{"scenes": [{{"text": "...", "visual_beats": ["<verbatim fragment 1>", "<verbatim fragment 2>", "<... one per drawable moment>"], '
        f'"connective": null, "beat_id": {beats[0].id}}}, ... TWO per item, context first then moment ...]}}.'
    )

    # Must agree with _validate_explore_scenes' count gate. These are TWO separate gates —
    # this one rejects a response and advances the model chain, that one reports issues on an
    # accepted draft — and leaving them out of sync means a compliant answer gets thrown away
    # and the chain exhausts into "SDK failed" with no hint that the count was the problem.
    expected_scenes = len(beats) * _SCENES_PER_ITEM

    def _valid(raw: str) -> bool:
        p = _extract_json(raw)
        if not (isinstance(p, dict) and isinstance(p.get("scenes"), list)):
            if progress:
                progress(f"[explore_write] validator: unparsable response")
            return False
        got = len(_body_scenes(p["scenes"]))
        if got != expected_scenes and progress:
            progress(f"[explore_write] validator: want {expected_scenes} item scenes, got {got}")
        return got == expected_scenes

    system = (_EXPLORE_WRITE_SYSTEM_EXPLAIN if archetype == "explain"
              else _EXPLORE_WRITE_SYSTEM_LIST)
    chain = [model] if model else list(CREATIVE_LLM_MODELS)
    raw, mdl = call_with_chain(
        # Token budget scales with the scene count — 1600 was sized for one scene per item and
        # truncates a two-scene answer mid-JSON, which reads to the validator as a bad response.
        system=system, user=user, models=chain, max_tokens=900 * _SCENES_PER_ITEM + 800,
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
    scenes = _body_scenes(scenes)
    expected = len(beats) * _SCENES_PER_ITEM
    if len(scenes) != expected:
        issues.append(f"expected {expected} scenes "
                      f"({len(beats)} items x {_SCENES_PER_ITEM}), got {len(scenes)}")
    total = 0
    for i, s in enumerate(scenes):
        text = str(s.get("text", "")).strip()
        wc = len(text.split())
        total += wc
        if wc > _EXP_SCENE_MAX_WORDS:
            issues.append(f"scene {i + 1} is {wc}w (max {_EXP_SCENE_MAX_WORDS})")
        # Scenes come in pairs: scene 2k = item k's CONTEXT, scene 2k+1 = its MOMENT.
        # Only the CONTEXT scene must name the entity — the moment scene is written right
        # after it and correctly says "he"/"it". Demanding the name in both spent words on a
        # re-introduction and still logged a false "never names its entity".
        item_idx = i // _SCENES_PER_ITEM
        is_context = i % _SCENES_PER_ITEM == 0
        if is_context and item_idx < len(beats):
            entity = beats[item_idx].name.strip()
            # Match on the bare first WORD: research entity names arrive with trailing commas
            # and parenthetical glosses ("Nul, Breaker of Worlds (...)"), and comparing the
            # raw token "nul," against prose that says "Nul rips" never matched.
            key = re.sub(r"[^\w'-]", "", entity.split()[0]).lower() if entity else ""
            if key and key not in text.lower():
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


# Tease-line pools for the deterministic hook below. Every video ended on the
# exact same sentence ("The last one on this list shouldn't even be
# possible.") regardless of question, which reads as repeated boilerplate
# across a channel of Q&A Shorts (Master: vary it like a paraphrase, not a
# fixed line). Picked per-project (see _pick_tease), excluding whatever other
# projects' narration.json already used, so consecutive channel videos don't
# collide on one line. Retries within one Stage 3 run on the SAME project still
# land on the same tease (deterministic hash, and the current project's own
# prior hook is never counted as "used" against itself). Keep every variant in
# the same short/punchy register and, for LIST, avoid re-introducing a literal
# rank word (banned by _LIST_LANGUAGE_RE-style rules used elsewhere in this file).
_LIST_TEASE_POOL = (
    "The last one on this list shouldn't even be possible.",
    "And the final one shouldn't even exist.",
    "Wait until you see who did it last.",
    "The last name on this list makes no sense.",
    "Number one breaks every rule.",
    "You won't believe who pulled off the last one.",
)
_EXPLAIN_TEASE_POOL = (
    "The answer is crueler than you think.",
    "The real answer is worse than you'd guess.",
    "Wait until you hear the actual reason.",
    "The truth behind it is darker than it looks.",
    "You won't like why it's true.",
    "The reason is stranger than you'd expect.",
)
# COMPARISON questions ("X things Carnage can do that Venom can't") are a
# capability escalation, not a ranked list of people — no "who"/"list"/rank
# word belongs here (see is_comparison in question_archetype.py).
_COMPARISON_TEASE_POOL = (
    "The last one breaks a rule you thought was absolute.",
    "The last one shouldn't even be possible.",
    "Wait until you see what the last one does.",
    "The last one is the one nobody expects.",
    "The last one changes what he is for good.",
)


def _used_teases(pool: tuple[str, ...], project_slug: str, projects_dir: Path | None = None) -> set[str]:
    """Tease lines from `pool` already spent by OTHER projects' narration.json
    `hook` (the current project is excluded so a retry on itself doesn't starve
    its own candidate list). Unreadable/missing files are skipped, not raised —
    this is a rotation nicety, not a correctness gate."""
    root = projects_dir if projects_dir is not None else PROJECTS_ROOT
    used: set[str] = set()
    if not root.is_dir():
        return used
    for narration_path in root.glob("*/narration.json"):
        if narration_path.parent.name == project_slug:
            continue
        try:
            hook = json.loads(narration_path.read_text()).get("hook", "")
        except (OSError, ValueError, AttributeError):
            continue
        for tease in pool:
            if isinstance(hook, str) and hook.endswith(tease):
                used.add(tease)
    return used


def _pick_tease(pool: tuple[str, ...], project_slug: str, projects_dir: Path | None = None) -> str:
    """Deterministic per-project pick from `pool`, skipping teases other
    projects already closed on (see _used_teases). hashlib (not the `random`
    module) so a Stage 3 retry on the SAME project reproduces the SAME tease
    — `random` would need a seed threaded through every retry path to get
    that guarantee for free."""
    remaining = [t for t in pool if t not in _used_teases(pool, project_slug, projects_dir)]
    if not remaining:
        remaining = list(pool)  # every variant already used elsewhere — fall back to full pool
    digest = hashlib.md5(project_slug.strip().encode("utf-8")).hexdigest()
    return remaining[int(digest, 16) % len(remaining)]


def _build_hook(question: str, answer_context: dict, archetype: str = "list",
                project_slug: str = "") -> str:
    """Deterministic v1 hook template (no LLM): "X? [statement]. [tease]" —
    is_intro scene, ~14-26 words (format spec v2).

    LIST questions keep the countdown tease (rotated from _LIST_TEASE_POOL).
    EXPLAIN questions get a promise-the-answer tease instead (rotated from
    _EXPLAIN_TEASE_POOL), and NEVER speak the answer_summary — for an explain
    video that summary IS the answer (the final scene's landing), so putting
    it in the hook would spoil the whole argument in second two.

    An EXPLAIN question can be a STATEMENT lead ("This is how Batman trains
    himself") rather than a real interrogative ("Why does Batman..."); forcing
    a "?" onto the former reads wrong ("...himself?"), so that register keeps
    its own punctuation (see `is_statement_lead`).

    ponytail: a real paraphrase of an arbitrary question needs grammar this
    template can't fake, so the "statement" clause is a generic placeholder
    unless Stage 1 supplied an explicit one-line summary. Upgrade to an LLM hook
    later (design doc addendum v2) if this reads too flat."""
    q = question.strip()
    if archetype == "explain" and is_statement_lead(question):
        if q and not re.search(r"[.!?]$", q):
            q += "."
        return " ".join((q, _pick_tease(_EXPLAIN_TEASE_POOL, project_slug)))
    if q and not q.endswith("?"):
        q += "?"
    if archetype == "explain":
        return " ".join((q, _pick_tease(_EXPLAIN_TEASE_POOL, project_slug)))
    if is_comparison(question):
        # No ranked person to tease ("who"/"list"/"name") — just the escalating
        # capability the last item shows. Also skips the summary/"Here's the
        # answer" clause: there's no single "answer" to hold back here.
        return " ".join((q, _pick_tease(_COMPARISON_TEASE_POOL, project_slug)))
    summary = str(answer_context.get("summary") or answer_context.get("answer_summary") or "").strip()
    statement = summary if summary else "Here's the answer"
    statement = statement.rstrip(".") + "."
    tease = _pick_tease(_LIST_TEASE_POOL, project_slug)
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
    clarity_fixes: str = "",
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

    # ADDITIVE story-context block (Stage 1 answer_research): the WHO/WHY a zero-context
    # viewer needs. Empty string when neither field is present → old answer_context.json
    # produces a byte-identical writer prompt.
    context_block = _viewer_context_block(answer_context)

    log(f"[explore_answer] writing {len(beats)} item scene(s)… (archetype={archetype})")
    parsed, mdl = _call_explore_writer(beats, items, question, model=model, progress=progress,
                                       debug_dump=dump, archetype=archetype, thesis=thesis,
                                       clarity_fixes=clarity_fixes, context_block=context_block)
    issues = _validate_explore_scenes(parsed.get("scenes") or [], beats, archetype)
    if issues:
        log(f"[explore_answer] draft has {len(issues)} issue(s); retrying once: {issues}")
        parsed, mdl = _call_explore_writer(beats, items, question, model=model, progress=progress,
                                           debug_dump=dump, issues=issues,
                                           archetype=archetype, thesis=thesis,
                                           clarity_fixes=clarity_fixes, context_block=context_block)
        issues = _validate_explore_scenes(parsed.get("scenes") or [], beats, archetype)
        if issues:
            log(f"[explore_answer] shipping with unresolved issue(s): {issues}")

    # Deterministic 1 beat -> 1 scene (same helper narrate mode uses); page_ref/
    # panel_ref come from the beat, never the writer.
    parsed = _anchor_scenes_to_beats(parsed, beats, progress,
                                     scenes_per_beat=_SCENES_PER_ITEM)
    body = parsed.get("scenes") or []

    hook_text = _build_hook(question, answer_context, archetype, project_name)
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
    # Q&A-specific grounding: the recap outro contract has no idea this video
    # answered ONE question with items from several comics (see _QA_OUTRO_SYSTEM's
    # docstring above) — hand it the actual question + items so the closing line
    # lands the answer instead of a generic single-story theme.
    items_ctx = (
        f"QUESTION: {question}\n"
        f"ITEMS (in order, last = payoff):\n"
        + "\n".join(f"- {b.name}: {b.summary[:200]}" for b in beats)
    )
    thematic = generate_outro(comic_context, tone_scenes, model=model,
                              progress=progress, debug_dump=dump, direction=direction,
                              system_override=_QA_OUTRO_SYSTEM, extra_user_context=items_ctx)
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
        # Both LLM helpers failed — close on the last item's proof, not a generic
        # unrelated line (was: "And that's the one nobody saw coming.").
        last_name = beats[-1].name.split(" (")[0].split(" /")[0].strip()
        outro_scene["text"] = f"Nobody stays untouchable — {last_name} proved it."
        log("[explore_answer] outro: generic meaning fallback")

    parsed["scenes"] = [intro_scene] + body + [outro_scene]
    parsed["hook"] = hook_text
    parsed["title"] = question

    final_model = mdl or model or OPENROUTER_MODEL
    nar = _to_narration(parsed, beats, Glossary(), mode, final_model)
    nar.banner_title = question  # verbatim — no LLM banner for this mode

    # STORY_VERIFY (last critic before save): fact-check each body scene against the
    # comic's OWN preprocessed evidence — the writer built these from web research and
    # can invert the story. A CONTRADICTED claim is re-written+re-verified once; if it
    # still contradicts, the original ships with an unresolved issue (never blocks).
    sv_issues = run_story_verify(nar, project_name, progress)
    dump["story_verify_issues"] = sv_issues
    if sv_issues:
        log(f"[explore_answer] STORY_VERIFY shipping with {len(sv_issues)} unresolved "
            f"contradiction(s): {sv_issues}")
    return nar
