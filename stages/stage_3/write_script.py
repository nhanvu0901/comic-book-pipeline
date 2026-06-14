"""Stage 3 narration writer: outline_beats -> build_glossary -> write_scenes -> validate."""
import json
import random
import re
import statistics
from pathlib import Path
from typing import Callable

from config import CREATIVE_LLM_MODELS, FIDELITY_LLM_MODELS, OPENROUTER_MODEL
from .modes import MODES_BY_KEY
from .schema import Beat, CharacterEntry, Glossary, Narration, Scene
from ._llm import call_with_chain
from .._embedding import semantic_sim as _semantic_sim


# Calibrated for the user-chosen 1.1 atempo pace. MEASURED actual rate at 1.1:
# ~2.88 wps (not the earlier 3.22 estimate). The teaser intro (~14-18 words) is
# prepended on top of the body, so the body targets ~165-195 → final ~180-213
# words → ~63-74s at 2.88 wps, landing inside the (54-95s) duration band and the
# (187-320) benchmark word band — clearly snappier than the old ~90s/297w output.
#
# SINGLE SOURCE OF TRUTH for the word budget. Previously three places disagreed
# (system prompt said 175-195, this user-message budget said 175-260, and the
# validator demanded 230-290) — the validator won and pulled output to ~283
# words of long, compound, multi-event sentences. All three now read these
# constants / the validator band below.
_TARGET_WORDS_MIN = 165
_TARGET_WORDS_MAX = 270   # body ceiling (~94s at 2.88 wps). Raised to fit MORE beats/scenes (16-20) → more panels. Was 230. Raised from 195 to let
                          # each TURN carry its grounded cause/why clause (causal
                          # narration) — connected story beats one-event-per-scene
                          # over a flat events list. Still trim flourish, not canon.
_WORDS_PER_SEC = 2.88    # MEASURED 1.1 atempo pace (was 4.0 at the 1.3 benchmark pace)

_SCENE_MIN_WORDS = 5     # punch sentences go as low as 5w ("Stating they would
                         # die anyway.") — floor must allow them, not block them.
_SCENE_MAX_WORDS = 24    # was 17 — a CAUSAL scene carries one event + its grounded
                         # 'why' clause (~24w). Punchiness is held by the median +
                         # punch-count gates below, not a low per-scene ceiling.
_TARGET_SENT_LEN = 14    # channel-punchy median; used by median soft-validator
_PUNCH_MAX_WORDS = 11    # a "punch" sentence: lands one beat hard
_MIN_PUNCH_SCENES = 3    # enforce variance toward SHORT (the channel signature)
_HOOK_MIN_WORDS = 14
_HOOK_MAX_WORDS = 26

# Channel connective frequencies (219-video sample): But 16.7%, So 9.0%, When 6.8%,
# However 3.1%, Then 1.8%, After 1.5%. "Just then" / "That's when" are channel-
# signature multi-word pivots — added per pipeline v3 spec.
_CONNECTIVES = (
    "But", "So", "However", "When", "After", "Then", "Eventually",
    "As", "Instead", "With", "Now", "Suddenly", "Until", "Meanwhile", "Soon",
    "Just then", "That's when",
)


def _starts_with_connective(text: str) -> str | None:
    """Return the connective the sentence starts with, or None.

    Match LONGEST first so "Just then" beats "Just" and "That's when" beats
    a hypothetical "That". Case-insensitive prefix match, ignores trailing
    comma/space."""
    t = text.lstrip().lower()
    for c in sorted(_CONNECTIVES, key=len, reverse=True):
        cl = c.lower()
        if t.startswith(cl):
            # Boundary: next char should be space/comma/end
            n = len(cl)
            if n == len(t) or t[n] in " ,.;:!?":
                return c
    return None


# Hook archetypes the benchmark accepts (research/reports/_BENCHMARK_thresholds.json
# → qualifying.hook_archetype_allowed). The intro MUST classify into one of these.
_ALLOWED_HOOK_ARCHETYPES = ("interrogative", "temporal-when", "temporal-other", "scenic")


def _classify_hook(line: str) -> str:
    """Mirror research/scripts/benchmark_builder.classify_hook EXACTLY so the intro
    we emit is guaranteed to land in an allowed archetype (benchmark gate). Keep
    this in sync with that function if the channel's hook taxonomy changes."""
    first12 = " ".join(line.split()[:12])
    s = first12.lower().strip()
    if re.match(r"^(ever wonder|ever wondered|have you ever|what if|what would|"
                r"why|how|where|can|could|would|did|do|does)\b", s):
        return "interrogative"
    if "?" in first12:
        return "interrogative"
    if re.match(r"^when\b", s):
        return "temporal-when"
    if re.match(r"^(after|while|during|once)\b", s):
        return "temporal-other"
    if re.match(r"^(in an? \w+ (universe|reality|world|year|future|past|version)|in [\d]{4})", s):
        return "scenic"
    if re.match(r"^[A-Z][\w-]+\s+(?:was|were|is|are|had|broke|entered|woke|fell|found|wakes|stands)", first12):
        return "character_action"
    return "other_character"


_HOOK_STOPWORDS = frozenset(
    "a an the and or but of to in on at by for with from into as is are was were be been "
    "her his its their she he they it him them who that this then so when after while during "
    "once had has have did do does no not".split()
)


def _intro_overlaps(intro_line: str, body_first_line: str) -> bool:
    """True if the intro echoes the opening body line — either shares the same
    first 4+ words, or its content words (minus stopwords) overlap heavily
    (Jaccard > 0.5). Used to force an intro regenerate so the video does not say
    the same thing twice."""
    def _toks(s: str) -> list[str]:
        return [w.strip(",.!?:;\"'—-").lower() for w in s.split() if w.strip(",.!?:;\"'—-")]

    a, b = _toks(intro_line), _toks(body_first_line)
    if not a or not b:
        return False
    if a[:4] == b[:4]:                      # identical opening clause
        return True
    ca = {w for w in a if w not in _HOOK_STOPWORDS}
    cb = {w for w in b if w not in _HOOK_STOPWORDS}
    if not ca or not cb:
        return False
    jac = len(ca & cb) / len(ca | cb)
    return jac > 0.5


_INTRO_SYSTEM = """You are HookWriter. You produce ONE short teaser intro sentence for a YouTube Short about a comic. It is the first thing the viewer hears — it must grab attention and tease the premise WITHOUT spoiling the ending.

Pick the ONE hook archetype that makes THIS story most intriguing, then write the line. You MUST begin with the exact opener words shown for your chosen archetype, or the hook is rejected:

  • interrogative  — a QUESTION. Begin with one of: "Ever wonder", "What if", "What would", "Why", "How", "Can", "Could", "Would". Ends with "?".
        e.g. "What if Magik refused to be a hero and trained under Doctor Strange?"
        e.g. "Could a girl who clawed out of hell ever feel safe again?"
  • temporal-when  — a STATEMENT beginning with "When ". Ends with ".".
        e.g. "When Illyana returned from Limbo seven years older, she wanted nothing to do with the X-Men."
  • temporal-other — a STATEMENT beginning with "After", "While", "During", or "Once". Ends with ".".
        e.g. "After Limbo broke her, Magik swore she would never be a weapon again."
  • scenic         — a STATEMENT beginning with "In a <adjective> universe/reality/world" (or "In <year>"). Ends with ".".
        e.g. "In a broken reality, Magik turned her back on the X-Men for good."

VARIETY RULE (important): do NOT default to "Ever wonder" — it is only ONE of several interrogative options, and across many comics these openers must VARY. Prefer a different archetype/opener unless an "Ever wonder" question is clearly the strongest fit.

HARD RULES for the intro line:
  - 8-16 words, exactly ONE sentence.
  - Name the hero AND the premise so a viewer instantly grasps the stakes.
  - It is a TEASER, not a summary — do NOT reveal the ending/twist.
  - No meta talk ("in this video", "today", "let's see"). No spoilers.
  - Begin with the EXACT opener words for your chosen archetype (above).
  - TEASE THE WHOLE STORY'S HOOK — the central premise, conflict, irony, or price
    paid — NOT the literal opening scene. The FIRST narration line already
    describes the opening event, so if your intro just restates that event the
    video says the same thing twice. Frame the broader stakes instead.
    ✗ (restates opening beat) "When Illyana returned from Limbo, she rejected the X-Men."
    ✓ (teases the whole hook)  "What if surviving hell meant becoming the very monster you fled?"

Return ONLY JSON, no markdown: {"archetype": "interrogative|temporal-when|temporal-other|scenic", "intro_line": "..."}"""


def _fallback_hero(comic_context: dict) -> str:
    """Best protagonist name for a fallback intro when the LLM intro call fails.
    Saga / url-mode contexts usually have characters=[] but a populated
    summary.characters, so 'this hero' looked broken. Priority: top-level
    characters → summary.characters[0].name (parenthetical aliases stripped) →
    first issue's characters → the comic title → 'this hero'."""
    chars = comic_context.get("characters") or []
    if chars and isinstance(chars[0], str) and chars[0].strip():
        return chars[0].strip()
    summ = (comic_context.get("summary") or {}).get("characters") or []
    if summ:
        first = summ[0]
        nm = first.get("name") if isinstance(first, dict) else str(first)
        nm = re.sub(r"\s*\([^)]*\)", "", nm or "").strip()  # drop "(Thunderer Thor)"
        if nm:
            return nm
    for it in comic_context.get("issues", []) or []:
        ic = it.get("characters") or []
        if ic and str(ic[0]).strip():
            return str(ic[0]).strip()
    return str(comic_context.get("title", "")).strip() or "this hero"


def generate_intro(
    comic_context: dict,
    *,
    avoid_text: str = "",
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
) -> dict:
    """Dedicated pre-write LLM call: classify story type + craft the teaser intro
    line shown over the cover. Returns {"story_type", "intro_line"}; falls back to
    a deterministic "Ever wonder...?" line if the LLM output is unusable.

    `avoid_text` = the first body narration line; when given, the prompt forbids
    restating it AND the validator rejects an intro that overlaps it too much (so
    a regenerate is forced) — prevents the intro echoing the opening beat."""
    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}

    title = str(comic_context.get("title", "")).strip()
    plot = str(comic_context.get("plot_summary", "")).strip()
    if not plot:
        plot = str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    chars = ", ".join(comic_context.get("characters", []) or [])

    avoid_block = ""
    if avoid_text.strip():
        avoid_block = (
            f"\nDO NOT restate, narrate, or paraphrase this OPENING narration line — the "
            f"video already says it, so your intro must tease the broader premise instead, "
            f"with DIFFERENT wording and a different angle:\n  \"{avoid_text.strip()}\"\n"
        )

    user = (
        f"COMIC TITLE: {title}\n"
        f"PUBLISHER: {comic_context.get('publisher','?')}\n"
        f"KEY CHARACTERS: {chars or '?'}\n\n"
        f"PREMISE / PLOT (ground truth):\n{plot[:1800]}\n"
        f"{avoid_block}\n"
        f"Write the intro JSON now."
    )

    def _valid(out: str) -> bool:
        try:
            d = _json_loads_loose(out)
            line = " ".join(str(d.get("intro_line", "")).split()).strip()
        except Exception:
            return False
        # Guarantee benchmark pass: must classify into an allowed hook archetype.
        if not (7 <= len(line.split()) <= 18 and _classify_hook(line) in _ALLOWED_HOOK_ARCHETYPES):
            return False
        # Reject an intro that echoes the opening beat (forces the LLM to retry).
        if avoid_text.strip() and _intro_overlaps(line, avoid_text):
            return False
        return True

    try:
        content, used = call_with_chain(
            system=_INTRO_SYSTEM, user=user,
            models=list(CREATIVE_LLM_MODELS) or None,
            max_tokens=300, progress=progress, label="intro", validator=_valid,
        )
        data = _json_loads_loose(content)
        intro_line = " ".join(str(data.get("intro_line", "")).split()).strip()
        archetype = _classify_hook(intro_line)  # trust the classifier, not the LLM's self-label
        dump["intro"] = {"archetype": archetype, "intro_line": intro_line, "model": used}
        log(f"[stage4] intro ({archetype}): {intro_line!r}")
        return {"story_type": archetype, "intro_line": intro_line}
    except Exception as exc:
        # Deterministic fallback so the pipeline never blocks on the intro.
        hero = _fallback_hero(comic_context)
        fallback = f"Ever wonder what if {hero} took a darker path?"
        log(f"[stage4] intro LLM failed ({type(exc).__name__}); using fallback: {fallback!r}")
        return {"story_type": "what_if", "intro_line": fallback}


_OUTRO_SYSTEM = """You are OutroWriter. You write ONE short THEMATIC closing line for a YouTube Short retelling of a comic — the final sentence the viewer hears.

It must capture the EMOTIONAL / THEMATIC core of the story — what it was REALLY about — in a punchy, resonant line. Think of the lesson, the irony, or the cost the hero paid.

HARD RULES:
  - 5-12 words, exactly ONE sentence, ends with ".".
  - NO plot summary, NO "the comic is", NO comic title, NO character names required.
  - It is NOT a question. No meta talk ("in this video"). No hashtags.
  - Grounded in THIS story's actual theme (below) — never a generic platitude.
  - Punchy and shareable — the kind of line a viewer would quote or screenshot.

Examples (for OTHER comics — match the TONE, not the words):
  - "Sometimes the only monster you fear is the one you could become."
  - "Power means nothing if it costs you everything you love."
  - "Even a god can be undone by what he refuses to let go."

Return ONLY JSON, no markdown: {"outro_line": "..."}"""


def generate_outro(
    comic_context: dict,
    body_scenes: list[dict],
    *,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
) -> str:
    """Dedicated LLM call: craft a punchy THEMATIC closing line — the alternative
    to the factual 'The comic is X.' credit. Returns "" if unusable, so the caller
    falls back to the factual credit (never blocks the pipeline)."""
    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}

    title = str(comic_context.get("title", "")).strip()
    plot = str(comic_context.get("plot_summary", "")).strip()
    if not plot:
        plot = str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    body_text = " ".join(
        str(s.get("text", "")).strip()
        for s in (body_scenes or [])
        if not s.get("is_intro") and not s.get("is_outro")
    )

    user = (
        f"COMIC TITLE: {title}\n"
        f"THEME / PLOT (ground truth):\n{plot[:1500]}\n\n"
        f"THE NARRATION (for tone + what the story covered):\n{body_text[:1200]}\n\n"
        f"Write the thematic outro JSON now."
    )

    def _valid(out: str) -> bool:
        try:
            d = _json_loads_loose(out)
            line = " ".join(str(d.get("outro_line", "")).split()).strip()
        except Exception:
            return False
        n = len(line.split())
        return (4 <= n <= 14 and line.endswith(".")
                and "?" not in line and "comic is" not in line.lower())

    try:
        content, used = call_with_chain(
            system=_OUTRO_SYSTEM, user=user,
            models=list(CREATIVE_LLM_MODELS) or None,
            max_tokens=200, progress=progress, label="outro", validator=_valid,
        )
        data = _json_loads_loose(content)
        line = " ".join(str(data.get("outro_line", "")).split()).strip()
        dump["outro_thematic"] = {"outro_line": line, "model": used}
        return line
    except Exception as exc:
        log(f"[stage4] thematic outro LLM failed ({type(exc).__name__}); keeping factual credit")
        return ""


def _find_cover_page(all_pages: list[dict] | None, story_pages: list[dict]) -> int:
    """Page number of the comic's cover (page_type=='cover'); else the lowest
    page number seen, else 1 — used as the intro scene's visual."""
    for p in (all_pages or []):
        if str(p.get("page_type", "")).lower() == "cover":
            return int(p.get("page_number", 1) or 1)
    pages = [int(p.get("page_number", 0) or 0) for p in (all_pages or story_pages or [])]
    pages = [n for n in pages if n > 0]
    return min(pages) if pages else 1


def _json_loads_loose(text: str) -> dict:
    """Parse JSON that may be wrapped in markdown fences or have prose around it."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def write_script(
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    hook_hint: str = "",
    *,
    all_pages: list[dict] | None = None,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
) -> Narration:
    """Run intro -> outline -> glossary -> write -> validate (+ retries)."""
    if mode not in MODES_BY_KEY:
        raise ValueError(f"Unknown mode: {mode!r}. Valid: {sorted(MODES_BY_KEY)}")

    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}

    log("[stage4] phase A0 — generating teaser intro…")
    intro = generate_intro(comic_context, model=model, progress=progress, debug_dump=dump)
    cover_page = _find_cover_page(all_pages, story_pages)

    log(f"[stage4] phase A — outlining beats (mode={mode})…")
    beats, beats_model = outline_beats(comic_context, story_pages, mode, hook_hint=hook_hint, model=model,
                                       progress=progress, debug_dump=dump)
    log(f"[stage4] phase A done — {len(beats)} beat(s)")

    log("[stage4] phase B — building glossary…")
    glossary, gloss_model = build_glossary(beats, comic_context, model=model, progress=progress, debug_dump=dump)
    log(f"[stage4] phase B done — {len(glossary.characters)} character(s) glossed")

    log("[stage4] phase C — writing scenes…")
    parsed, write_model = write_scenes(beats, glossary, comic_context, story_pages, mode,
                                       hook_hint=hook_hint, all_pages=all_pages,
                                       model=model, progress=progress, debug_dump=dump)
    # Deterministic anchoring: page_ref/panel_ref come from the page-sorted beats,
    # not the writer. 1 beat → 1 scene. This is the single source of truth for
    # which page each scene maps to (see the beat-anchoring design doc).
    parsed = _anchor_scenes_to_beats(parsed, beats, progress)

    valid_pages = {int(p.get("page_number", 0)) for p in story_pages}
    valid_beat_ids = {b.id for b in beats}
    errors = _validate(parsed, valid_pages, valid_beat_ids)
    halluc = _detect_hallucinations(parsed, glossary, comic_context, story_pages)
    if halluc:
        errors = halluc + errors

    # Multi-pass validation loop: validate → fidelity → wiki → retry.
    # Wiki mismatches are CRITICAL but we give the LLM up to MAX_PASSES tries
    # to land canonical narration before giving up. 4 (was 3) gives the order +
    # state-tracking + fidelity checks one more round to converge before best-draft.
    # Multi-issue sagas run EVERY phase through the SDK and make ~2 SDK calls per
    # pass (retry-wiki + wiki-check); 4 passes exhausts the account usage window
    # mid-run. Cap arcs at 2 passes to roughly halve the SDK calls per saga run.
    MAX_PASSES = 2 if comic_context.get("is_arc") else 4
    best_parsed = parsed
    # (length_ok, words_ok, -critical, -errors, -words): higher is better.
    # (complete, -n_critical, words_ok, -errors, -words) — see selection below.
    best_key = (-1, -(10 ** 9), -1, -(10 ** 9), -(10 ** 9))
    pass_num = 0
    while pass_num < MAX_PASSES:
        pass_num += 1
        errors = _validate(parsed, valid_pages, valid_beat_ids)
        halluc = _detect_hallucinations(parsed, glossary, comic_context, story_pages)
        if halluc:
            errors = halluc + errors
        # Single grounding check (merged fidelity + wiki): flags canon
        # contradictions AND invented drama. All grounding issues are critical.
        wiki_issues = _wiki_cross_check(parsed, comic_context,
                                         model=model, progress=progress)
        if wiki_issues:
            errors = errors + [f"wiki: {i}" for i in wiki_issues]
        dump[f"validation_pass{pass_num}"] = errors

        # Best-draft selection. The OLD key gated on length FIRST (words >= 165),
        # which shipped a complete-but-211w draft with 7 CRITICAL wiki/order errors
        # over a clean 160w draft (0 critical) — because 160 < 165 flipped its
        # length bit off. Fidelity must beat a few-word length miss. New priority:
        #   1. COMPLETE (truncation guard, lenient: enough scenes + not drastically
        #      short) — never ship a real 5-scene/96-word truncation.
        #   2. FEWEST CRITICAL issues (order/fidelity/wiki) — this is what matters.
        #   3. in the punchy word band (preference, not a gate).
        #   4. fewest total issues, then shorter.
        _scenes = parsed.get("scenes") or []
        _words = sum(len(str(s.get("text", "")).split()) for s in _scenes)
        complete = 1 if (9 <= len(_scenes) <= 22 and _words >= 130) else 0
        words_ok = 1 if _TARGET_WORDS_MIN <= _words <= _TARGET_WORDS_MAX + 20 else 0
        n_critical = sum(1 for e in errors if _is_critical_error(e))
        key = (complete, -n_critical, words_ok, -len(errors), -_words)
        if key > best_key:
            best_parsed = parsed
            best_key = key

        if not errors:
            log(f"[stage4]   ✓ pass {pass_num}: all validations clean")
            break

        critical = [e for e in errors if _is_critical_error(e)]
        log(f"[stage4]   pass {pass_num}/{MAX_PASSES}: {len(errors)} issue(s) "
            f"({len(critical)} critical, {len(_scenes)} scenes / {_words}w, "
            f"complete={bool(complete)})")
        if pass_num >= MAX_PASSES:
            log(f"[stage4]   ⚠ MAX_PASSES reached; shipping best draft "
                f"(complete={best_key[0]==1}, critical={-best_key[1]}, "
                f"words_ok={best_key[2]==1}, {-best_key[3]} issues)")
            parsed = best_parsed
            errors = []  # don't raise — fall through with best draft
            break
        log(f"[stage4]   retrying (pass {pass_num+1}/{MAX_PASSES})…")
        parsed = _retry_fix_with_wiki(parsed, errors, comic_context,
                                       model, progress, dump)
        # Re-anchor: the retry may rewrite prose / re-key beat_ids, but page_ref
        # and panel_ref stay deterministic so a retry can never re-break paging.
        parsed = _anchor_scenes_to_beats(parsed, beats, progress)

    if errors:
        log(f"[stage4]   ⚠ shipping with {len(errors)} unresolved issue(s) (best draft kept)")
        for e in errors[:10]:
            log(f"[stage4]     - {e}")

    # Prepend the teaser intro as scene 1 (shown over the cover) AFTER the body
    # validation/retry loop — so it never interferes with fidelity/wiki/length
    # checks on the story body. Renumber the body scenes; the original hook
    # scene becomes scene 2 (its "When…" lead-in is a valid connective).
    # Mark the writer's outro credit ("The comic is X.") so Stage 5 can resolve
    # it to the comic's FINAL splash page (the money shot / bookend) rather than
    # reusing the last narrative scene's page. panel_ref=-1 → whole page.
    body0 = parsed.get("scenes") or []
    if body0:
        last = body0[-1]
        if "comic is" in str(last.get("text", "")).lower():
            last["is_outro"] = True
            last["panel_ref"] = -1

    # Anti-repeat: the teaser intro is generated BEFORE the body exists, so a
    # statement-style hook (temporal/scenic) can accidentally restate beat 1
    # ("When Illyana returned from Limbo…") — the same event as the first body
    # scene, making the video say it twice. Now that the body is written, if the
    # intro echoes the opening line, regenerate it with that line as avoid_text.
    intro_line = (intro.get("intro_line") or "").strip()
    first_body_line = str(body0[0].get("text", "")).strip() if body0 else ""
    if intro_line and first_body_line and _intro_overlaps(intro_line, first_body_line):
        log("[stage4]   ⚠ intro echoes the opening narration — regenerating (avoid_text)")
        intro = generate_intro(comic_context, avoid_text=first_body_line,
                               model=model, progress=progress, debug_dump=dump)
        intro_line = (intro.get("intro_line") or "").strip()
        if not intro_line or _intro_overlaps(intro_line, first_body_line):
            # last-resort: a question hook never narrates a beat, so it can't echo
            hero = _fallback_hero(comic_context)
            intro_line = f"What if {hero}'s greatest enemy was the person they became?"
            intro["intro_line"] = intro_line
            log(f"[stage4]   ⚠ still echoed; using fallback question hook: {intro_line!r}")
        else:
            log(f"[stage4]   ✓ intro regenerated: {intro_line!r}")

    if intro_line:
        body = parsed.get("scenes") or []
        for s in body:
            s["scene_id"] = int(s.get("scene_id", 0) or 0) + 1
            if s["scene_id"] == 2 and not s.get("connective"):
                s["connective"] = _starts_with_connective(str(s.get("text", "")))
        intro_scene = {
            "scene_id": 1,
            "text": intro_line,
            "page_ref": cover_page,
            "panel_ref": -1,        # whole cover
            "connective": None,
            "beat_id": 0,
            "is_intro": True,
        }
        parsed["scenes"] = [intro_scene] + body
        parsed["hook"] = intro_line  # thumbnail / opening line is now the teaser

    # Outro variety: 50/50 coin-flip between the factual "The comic is X." credit
    # (channel identity) and a punchy THEMATIC takeaway. The writer ALWAYS emits the
    # factual credit, so beat-anchoring + wiki/validation stay stable on a known
    # phrasing; we only swap the outro scene's TEXT here, after the loop, keeping
    # its is_outro flag + panel_ref=-1 (Stage 5 resolves it to the final splash).
    scenes_now = parsed.get("scenes") or []
    outro_idx = next((i for i, s in enumerate(scenes_now) if s.get("is_outro")), -1)
    if outro_idx >= 0 and random.random() < 0.5:
        thematic = generate_outro(comic_context, scenes_now,
                                  model=model, progress=progress, debug_dump=dump)
        if thematic:
            scenes_now[outro_idx]["text"] = thematic
            log(f"[stage4] outro: thematic → {thematic!r}")
        else:
            log("[stage4] outro: factual credit (thematic gen failed)")
    elif outro_idx >= 0:
        log("[stage4] outro: factual credit (coin-flip)")

    final_model = write_model or gloss_model or beats_model or (model or OPENROUTER_MODEL)
    return _to_narration(parsed, beats, glossary, mode, final_model)


_OUTLINE_SYSTEM = """You are PanelOutliner. Your job is to extract the FULL dramatic skeleton of a comic story into 16-20 canonical beats — MUST cover the entire story arc including the climax, not just the opening.

You DO NOT write narration prose yet. You produce structured beats only.

Each beat has:
- function: COLD_OPEN | SETUP | COMPLICATION | ESCALATION | MIDPOINT | CLIMAX | LANDING
- name: 3-7 words naming the beat ("Ben gets the symbiote")
- page_refs: which input pages feed this beat
- key_panels: 1-3 strongest visual moments [{"page": int, "panel": int}]
- summary: ONE factual sentence of what happens (no narration voice yet)
- cause: the wiki-grounded REASON/MOTIVE this beat happens — the "why" behind it
  (e.g. "Reed's experiment turned Ben into the Thing and he forgot the accident's
  anniversary" → why Ben resents Reed; "the symbiote marked Ben as its perfect
  host to corrupt him" → why it later abandons the Lizard for Ben). "" if none.
- characters_active: who is on stage in this beat

CAUSAL CHAIN — connect cause→effect, set up motives before they pay off:
  The story is a chain of motivated turns, not just events. For each beat, fill
  `cause` from the wiki. CRITICAL: when a motive introduced early pays off later
  (the symbiote choosing Ben as "perfect host" → later abandoning the Lizard to
  reclaim Ben), make sure the SETUP is captured in an EARLY beat's summary/cause
  so the payoff is connected, not out of nowhere.

Beats are in dramatic order (which is usually but not always chronological). The first beat is COLD_OPEN — the moment that should hook the viewer. The last beat is LANDING — the line that pays it off.

╔════════════════════════════════════════════════════════════════════════════╗
║  CRITICAL: WIKI/FANDOM PLOT IS GROUND TRUTH                                 ║
║                                                                              ║
║  When CANONICAL STORY ARC is provided in the user message, your beats MUST  ║
║  cover the ENTIRE arc — beginning, middle, AND climax.                       ║
║                                                                              ║
║  A common failure: outliner picks 12 beats from the FIRST third of the      ║
║  comic and skips the climax (e.g. the twist, the death, the final reveal).  ║
║  This produces incomplete narration. DO NOT do this.                         ║
║                                                                              ║
║  Distribute beats across the WHOLE arc:                                      ║
║    Beats 1-3: setup / inciting incident                                      ║
║    Beats 4-7: complications / mid-story turns                                ║
║    Beats 8-12: climax / consequences / resolution                            ║
║                                                                              ║
║  If the wiki plot mentions a SPECIFIC named subplot (e.g. "Lizard's machine ║
║  betrayal", "Sue's death", "anniversary subplot"), you MUST include a beat  ║
║  for it. Missing a named subplot is a HARD FAILURE.                          ║
╚════════════════════════════════════════════════════════════════════════════╝

Constraints:
- **16-20 beats** total. Beats map 1:1 to scenes (one panel shown per scene), so
  MORE beats = MORE distinct panels on screen. Cover the FULL canonical arc, incl.
  resolution/aftermath — every major wiki event gets its own beat.
- Each beat covers 1-4 input pages. Don't spread one beat across the whole comic.
- COLD_OPEN beat must contain a concrete visual action, not exposition.
- LANDING must be a payoff, twist, or final image — never a CTA or question.
- Spread page_refs across the FULL comic page range (page 1 → final page).
  If your last beat's page_ref is <50% of the comic's total pages, you've
  truncated the arc — go back and add climax beats.
- **ONE DISTINCT MOMENT PER BEAT — no action progressions split across beats.**
  Do NOT make two beats out of one action (e.g. "Reed AIMS the sonic gun" then
  "Reed FIRES the sonic gun"). Collapse it into the single most impactful moment
  ("Reed fires the sonic gun at Ben"). Same for "raises fist"→"punches",
  "lunges"→"strikes". Pick the moment with the most story impact (usually the
  result/payoff, not the wind-up). Each beat must advance the plot, not restate
  the previous beat with a slightly later frame.

Return ONLY JSON. No prose, no markdown fences."""


def outline_beats(
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    *,
    hook_hint: str = "",
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
) -> tuple[list[Beat], str]:
    log = progress or (lambda _msg: None)
    mode_info = MODES_BY_KEY[mode]

    # CANONICAL plot from wiki — primary source of truth for outline.
    plot = (comic_context.get("plot_summary") or "").strip()
    arc = (comic_context.get("summary", {}) or {}).get("story_arc", "").strip()
    canonical_block = ""
    if arc:
        canonical_block += f"╔══ CANONICAL STORY ARC (Marvel/DC Fandom — ground truth) ══╗\n{arc}\n\n"
    if plot:
        canonical_block += f"╔══ CANONICAL FULL PLOT (wiki) ══╗\n{plot[:5000]}\n\n"

    # ── Crossover-saga: spread beats across issues, anchor to each issue's pages ──
    if comic_context.get("is_arc") and comic_context.get("issues"):
        from stages._arc import issue_index_of_page, allocate_beats_across_issues
        issues = comic_context["issues"]
        n_iss = len(issues)
        by_issue: dict[int, list[int]] = {}
        for p in story_pages:
            by_issue.setdefault(issue_index_of_page(p), []).append(int(p.get("page_number", 0) or 0))
        page_counts = [len(by_issue.get(it["chapter_index"], [])) for it in issues]
        alloc = allocate_beats_across_issues(total=20, n_issues=n_iss, page_counts=page_counts)
        arc_lines = []
        for it in issues:
            k = it["chapter_index"]
            pgs = sorted(by_issue.get(k, []))
            rng = f"pages {pgs[0]}-{pgs[-1]}" if pgs else "(no pages)"
            arc_lines.append(
                f"  • {it['label']} ({rng}): write ~{alloc.get(k, 2)} beat(s). "
                f"Plot: {(it.get('plot_summary') or '')[:600]}")
        canonical_block += (
            "\n╔══ MULTI-ISSUE SAGA — COVER EVERY ISSUE IN ORDER ══╗\n"
            "This is a crossover of sequential issues. Allocate beats so EACH issue is\n"
            "represented and every beat's page_refs fall INSIDE that issue's page range:\n"
            + "\n".join(arc_lines) + "\n\n"
        )

    # Determine page range so outliner can spread beats across the full arc.
    page_nums = sorted({int(p.get("page_number", 0) or 0) for p in story_pages})
    page_range_hint = ""
    if page_nums:
        first_pg, last_pg = page_nums[0], page_nums[-1]
        midpoint = (first_pg + last_pg) // 2
        page_range_hint = (
            f"\nPAGE RANGE: comic has pages {first_pg}–{last_pg}. Distribute beats "
            f"across this range — your LAST beat's page_ref must be ≥ page {midpoint+3} "
            f"(else you're truncating the climax)."
        )

    user = (
        canonical_block
        + f"COMIC METADATA:\n{_ctx_block(comic_context)}\n\n"
        # Multi-issue sagas have ~100 pages; the full per-panel block balloons the
        # prompt to ~175K chars and the SDK rejects/rate-limits it. Use the compact
        # block for arcs (enough to anchor beats to pages); single comics keep full.
        + f"STORY PAGES (per-panel detail for grounding beats to visuals):\n"
        + f"{(_pages_block_compact if comic_context.get('is_arc') else _pages_block_full)(story_pages)}\n\n"
        + f"NARRATION MODE: {mode} — {mode_info.description}\n"
        + (f"HOOK HINT: {hook_hint}\n" if hook_hint else "")
        + page_range_hint + "\n\n"
        + f"TASK: Extract 16-20 beats that COVER THE ENTIRE canonical story arc. "
        + f"USE THE CANONICAL WIKI PLOT ABOVE as the spine — each major event in "
        + f"the wiki MUST get its own beat. Then map each beat to the most fitting "
        + f"page_ref + panel from the STORY PAGES. Do NOT skip the climax. Do NOT "
        + f"pile multiple major events into one beat.\n\n"
        + f"Return JSON in this exact shape:\n"
        + f"{{\n"
        + f'  "beats": [\n'
        + f'    {{"id": 1, "function": "COLD_OPEN", "name": "...", "page_refs": [3], '
        + f'"key_panels": [{{"page": 3, "panel": 0}}], "summary": "...", "cause": "...", "characters_active": ["..."]}},\n'
        + f"    ...\n"
        + f"  ]\n"
        + f"}}"
    )

    log(f"[stage4]   outline prompt: {len(user)} chars")
    chain = [model] if model else None

    def _has_beats(c: str) -> bool:
        p = _extract_json(c)
        return isinstance(p, dict) and isinstance(p.get("beats"), list) and len(p["beats"]) > 0

    raw, mdl_used = call_with_chain(
        system=_OUTLINE_SYSTEM,
        user=user,
        models=chain,
        max_tokens=4000,  # reasoning models can burn 1000+ tokens before producing output
        progress=progress,
        label="outline",
        validator=_has_beats,
    )
    if debug_dump is not None:
        debug_dump["phase_a_raw"] = raw
        debug_dump["phase_a_model"] = mdl_used
    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("beats"), list):
        raise RuntimeError(f"Phase A: no beats array. Raw:\n{raw[:500]}")

    beats: list[Beat] = []
    for i, b in enumerate(parsed["beats"], start=1):
        beats.append(Beat(
            id=int(b.get("id", i) or i),
            function=str(b.get("function", "SETUP")).upper().strip(),
            name=str(b.get("name", "")).strip(),
            page_refs=[int(x) for x in (b.get("page_refs") or []) if str(x).strip()],
            key_panels=[{"page": int(kp.get("page", 0)), "panel": int(kp.get("panel", 0))}
                        for kp in (b.get("key_panels") or []) if isinstance(kp, dict)],
            summary=str(b.get("summary", "")).strip(),
            cause=str(b.get("cause", "")).strip(),
            characters_active=[str(c).strip() for c in (b.get("characters_active") or []) if str(c).strip()],
        ))
    if not (12 <= len(beats) <= 20):
        log(f"[stage4]   warning: outline returned {len(beats)} beats (want 16-20)")

    # Canonical (wiki/causal) order — NOT page order. The outliner emits beats in
    # story order; we only force COLD_OPEN first + LANDING last. Page is no longer
    # the ordering authority (the comic's layout ≠ story order broke the timeline);
    # each scene's panel is chosen by content grounding instead.
    beats = _order_beats_canonical(beats)

    # Page-COVERAGE validation — retry once with bridge instruction if a large page
    # range is skipped. (This is about completeness, not order; _validate_outline
    # sorts by page internally just for the gap test.)
    issues = _validate_outline(beats)
    if issues:
        log(f"[stage4]   outline validation: {len(issues)} issue(s) — retry with bridge")
        for iss in issues[:3]:
            log(f"[stage4]     - {iss}")
        beats = _retry_outline_with_bridge(
            beats, issues, comic_context, story_pages, mode,
            hook_hint=hook_hint, model=model, progress=progress, debug_dump=debug_dump,
        ) or beats
        beats = _order_beats_canonical(beats)  # re-apply bookend invariant after bridge

    # Visual grounding: replace the outliner's UNRELIABLE panel-index guess with a
    # content-matched pick (beat summary ↔ panel descriptions). Sets key_panels
    # (the VISUAL anchor); does NOT change narration order.
    beats = _ground_beat_panels(beats, story_pages, progress)
    return beats, mdl_used


def _ground_beat_panels(
    beats: list[Beat],
    story_pages: list[dict],
    progress: Callable[[str], None] | None = None,
) -> list[Beat]:
    """Pick each beat's key_panel by CONTENT, not the outliner's guess.

    The outliner emits key_panels by guessing panel indices from text — unreliable,
    so Stage 5 ends up honoring a panel that doesn't depict the beat. Here we pick,
    among the panels on the beat's own page_refs, the one whose VLM description best
    matches the beat SUMMARY (a rich, stable sentence — unlike the now-punchy
    narration). The chosen (page, panel) overwrites key_panels, so `_beat_anchor`
    and the scene's page_ref/panel_ref become content-grounded and Stage 5's
    relevance + cross-check lock onto the right panel. Deterministic (embedding),
    no LLM. Beats whose pages have no usable panel/description keep their anchor."""
    log = progress or (lambda _msg: None)
    panels_by_page: dict[int, list[dict]] = {}
    for p in story_pages or []:
        pn = int(p.get("page_number", 0) or 0)
        if pn:
            panels_by_page[pn] = p.get("panels") or []

    regrounded = 0
    for beat in beats:
        summary = (beat.summary or "").strip()
        if not summary:
            continue
        active = {c.split()[0].lower() for c in (beat.characters_active or []) if c}
        # Search the beat's own pages PLUS one page either side of that range — the
        # outliner's page_refs are sometimes off by a page, so the true depicting
        # panel can sit just outside (fixes V4). A small per-page distance penalty
        # keeps a locality prior: an out-of-range panel must clearly out-match the
        # in-range ones to win.
        ref_set = {int(p) for p in (beat.page_refs or []) if int(p) in panels_by_page}
        if ref_set:
            lo, hi = min(ref_set), max(ref_set)
            search_pages = [p for p in range(lo - 1, hi + 2) if p in panels_by_page]
        else:
            search_pages = []
        best: tuple[float, int, int] | None = None  # (score, page, panel_index)
        for pg in search_pages:
            dist_penalty = 0.0 if pg in ref_set else 0.04
            for idx, panel in enumerate(panels_by_page.get(pg, [])):
                desc = str(panel.get("description", "") or "").strip()
                if not desc:
                    continue
                score = _semantic_sim(summary, desc) - dist_penalty
                # small nudge: a panel showing the beat's active characters
                pchars = {str(c).split()[0].lower() for c in (panel.get("characters") or []) if c}
                if active and (active & pchars):
                    score += 0.05 * len(active & pchars)
                if best is None or score > best[0]:
                    best = (score, int(pg), idx)
        if best is not None:
            prev = beat.key_panels[0] if beat.key_panels else None
            beat.key_panels = [{"page": best[1], "panel": best[2]}]
            if not prev or prev.get("page") != best[1] or prev.get("panel") != best[2]:
                regrounded += 1
    if regrounded:
        log(f"[stage4]   grounded {regrounded} beat panel(s) by description match")
    return beats


def _beat_anchor(beat: Beat) -> tuple[int, int]:
    """The deterministic (page_ref, panel_ref) a beat maps to.

    The outliner (Phase A) already chose each beat's strongest visual moment with
    full per-panel detail, so the beat — not the writer — owns the visual anchor:
      - first key_panel (the moment the outliner flagged), else
      - the beat's lowest page as a whole-page (-1) shot, else
      - (0, -1) for a beat with no pages (should not happen post-outline).
    panel_ref of -1 means "whole page" in the Scene schema."""
    if beat.key_panels:
        kp = beat.key_panels[0]
        return int(kp.get("page", 0) or 0), int(kp.get("panel", -1))
    if beat.page_refs:
        return min(beat.page_refs), -1
    return 0, -1


def _order_beats_canonical(beats: list[Beat]) -> list[Beat]:
    """Narration order = the comic's CAUSAL/wiki order, NOT page order.

    Earlier we stable-sorted beats by anchor PAGE, on the assumption "page order ==
    reading order". That is false when the comic's layout ≠ story order (Venom: the
    LANDING splash sits on page 30 but the CLIMAX kill is on page 31 → page-sort put
    the landing BEFORE the kill). Page-sorting kept re-breaking the timeline. Now
    that each scene's panel is chosen by CONTENT grounding (`_ground_beat_panels` +
    Stage 5), narration no longer has to be page-monotonic — so we keep the
    outliner's emitted order (it is told to emit beats in wiki causal order) and
    only enforce the two structural invariants that actually matter:

      - all COLD_OPEN beat(s) first (in their emitted order),
      - all LANDING beat(s) last (in their emitted order),
      - everything else keeps the outliner's order.

    This fixes LANDING-before-CLIMAX without risking a mid-story reorder. Stage 5's
    forward-only walk is relaxed to a soft backward penalty (it trusts grounding)."""
    def fn(b: Beat) -> str:
        return (b.function or "").upper().strip()
    cold = [b for b in beats if fn(b) == "COLD_OPEN"]
    land = [b for b in beats if fn(b) == "LANDING"]
    mid = [b for b in beats if fn(b) not in ("COLD_OPEN", "LANDING")]
    return cold + mid + land


def _anchor_scenes_to_beats(
    parsed: dict,
    beats: list[Beat],
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Deterministic 1 beat → 1 scene. The writer authored prose keyed loosely by
    beat_id; we re-key it to the canonical, page-sorted beat list so that:

      - every beat gets exactly one scene (no dropped middle beats — the Venom bug),
      - scenes follow page-sorted beat order (page_ref is monotonic),
      - page_ref/panel_ref come from `_beat_anchor`, never from the writer
        (kills the writer-mis-tags-page class — the Marvel Zombies bug).

    Matching is two-pass and pool-consuming so no writer scene is reused:
      1. exact beat_id match (first non-empty wins),
      2. positional fill of still-unmatched beats from leftover scenes in order
         (recovers a mislabeled beat_id when the writer kept beat order).

    The channel outro credit ("The comic is X.") is not a beat — it is popped off
    the writer's list and re-appended last, anchored to the final beat with
    panel_ref=-1 (Stage 5 resolves it to the final splash bookend).

    This runs on EVERY draft (initial write + each retry) so retries can only
    change prose, never re-introduce a bad page_ref."""
    log = progress or (lambda _msg: None)
    if not beats:
        return parsed

    body = list(parsed.get("scenes") or [])
    outro_src: dict | None = None
    if body and "comic is" in str(body[-1].get("text", "")).lower():
        outro_src = body.pop()

    def _bid(s: dict) -> int:
        try:
            return int(s.get("beat_id", 0) or 0)
        except (TypeError, ValueError):
            return 0

    pool = [s for s in body if str(s.get("text", "")).strip()]

    # PURE POSITIONAL: scene[i] narrates beat[i]. The writer is required (write
    # prompt) to emit EXACTLY one scene per beat, in beat order, so position is the
    # reliable mapping. The older beat_id-then-positional pairing and the semantic
    # re-pairing BOTH proved fragile — they anchored an early event (e.g. "Reed
    # raised the sonic gun") to a late beat, narrating it at the very end after the
    # character was already dead, or scrambled the climax. Position can't drift.
    matched: dict[int, dict] = {}
    for i, beat in enumerate(beats):
        if i < len(pool):
            matched[beat.id] = pool[i]
    if len(pool) != len(beats):
        log(f"[stage4]   ⚠ writer emitted {len(pool)} story scenes for {len(beats)} "
            f"beats — positional anchoring used the first {min(len(pool), len(beats))}")

    anchored: list[dict] = []
    gaps: list[int] = []
    for beat in beats:
        src = matched.get(beat.id)
        if src is None:
            gaps.append(beat.id)
            continue
        page, panel = _beat_anchor(beat)
        anchored.append({
            "text": str(src.get("text", "")).strip(),
            "page_ref": page,
            "panel_ref": panel,
            "connective": src.get("connective"),
            "beat_id": beat.id,
        })

    # Re-append the channel outro credit anchored to the final beat.
    if outro_src is not None:
        page, _ = _beat_anchor(beats[-1])
        anchored.append({
            "text": str(outro_src.get("text", "")).strip(),
            "page_ref": page,
            "panel_ref": -1,
            "connective": None,
            "beat_id": beats[-1].id,
        })

    if gaps:
        log(f"[stage4]   ⚠ {len(gaps)} beat(s) had no prose (coverage gap): {gaps}")

    parsed["scenes"] = anchored
    parsed["_coverage_gaps"] = gaps
    return parsed


def _validate_outline(beats: list[Beat], max_gap: int = 5) -> list[str]:
    """Soft validation of outline. Returns issue strings; empty = OK."""
    issues: list[str] = []
    if len(beats) < 12:
        issues.append(f"only {len(beats)} beats (target 16-20)")

    sorted_beats = sorted(
        [b for b in beats if b.page_refs],
        key=lambda b: min(b.page_refs),
    )
    for prev, nxt in zip(sorted_beats, sorted_beats[1:]):
        prev_end = max(prev.page_refs)
        next_start = min(nxt.page_refs)
        gap = next_start - prev_end
        if gap > max_gap:
            issues.append(
                f"beat {prev.id}→{nxt.id} page-gap {gap} "
                f"(pages {prev_end+1}-{next_start-1} skipped — insert bridge)"
            )
    return issues


def _retry_outline_with_bridge(
    original_beats: list[Beat],
    issues: list[str],
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    *,
    hook_hint: str = "",
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
) -> list[Beat] | None:
    """Re-ask the LLM to fix the outline by inserting bridge beats for skipped pages."""
    log = progress or (lambda _msg: None)
    mode_info = MODES_BY_KEY[mode]

    issue_block = "\n".join(f"- {iss}" for iss in issues)
    prior = json.dumps(
        {"beats": [{
            "id": b.id, "function": b.function, "name": b.name,
            "page_refs": b.page_refs, "summary": b.summary,
            "characters_active": b.characters_active,
        } for b in original_beats]},
        indent=2,
    )
    user = (
        f"Your previous outline draft had page-coverage issues. Fix by INSERTING new "
        f"BRIDGE beats that summarize the skipped pages so the narrative flows linearly.\n\n"
        f"ISSUES:\n{issue_block}\n\n"
        f"PRIOR OUTLINE:\n{prior}\n\n"
        f"STORY PAGES (for picking bridge content):\n{_pages_block_compact(story_pages)}\n\n"
        f"Return the COMPLETE corrected outline (with bridge beats inserted in order) "
        f"in the same JSON shape. Total 10-12 beats. Page-gap between consecutive "
        f"beats MUST be ≤ 5."
    )

    def _has_beats(c: str) -> bool:
        p = _extract_json(c)
        return isinstance(p, dict) and isinstance(p.get("beats"), list) and len(p["beats"]) > 0

    try:
        raw, mdl_used = call_with_chain(
            system=_OUTLINE_SYSTEM, user=user,
            models=[model] if model else None,
            max_tokens=4000, progress=progress, label="outline-bridge",
            validator=_has_beats,
        )
    except RuntimeError as exc:
        log(f"[stage4]   outline-bridge retry chain exhausted — keeping original ({exc})")
        return None

    if debug_dump is not None:
        debug_dump["phase_a_bridge_raw"] = raw
        debug_dump["phase_a_bridge_model"] = mdl_used
    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("beats"), list):
        log(f"[stage4]   outline-bridge unparseable — keeping original")
        return None

    new_beats: list[Beat] = []
    for i, b in enumerate(parsed["beats"], start=1):
        new_beats.append(Beat(
            id=int(b.get("id", i) or i),
            function=str(b.get("function", "SETUP")).upper().strip(),
            name=str(b.get("name", "")).strip(),
            page_refs=[int(x) for x in (b.get("page_refs") or []) if str(x).strip()],
            key_panels=[{"page": int(kp.get("page", 0)), "panel": int(kp.get("panel", 0))}
                        for kp in (b.get("key_panels") or []) if isinstance(kp, dict)],
            summary=str(b.get("summary", "")).strip(),
            cause=str(b.get("cause", "")).strip(),
            characters_active=[str(c).strip() for c in (b.get("characters_active") or []) if str(c).strip()],
        ))
    log(f"[stage4]   outline-bridge ok — {len(new_beats)} beats")
    return new_beats


_GLOSSARY_SYSTEM = """You are PanelGlossarist. Your job is to give the narrator a stable name for every entity in the story.

The narration we are about to write is read aloud as one tight 60-second voiceover. If the script flips between "Ben / the Thing / Venom / the creature" without a clear rule, the listener gets lost. You prevent that.

For every distinct character/entity that appears in any beat, produce:
- canonical_name: the ONE name the narration should default to (real name preferred over hero name unless the hero name is more iconic for this story)
- epithets: alternative phrases the narrator may use ONCE for variety after the canonical_name has been established (e.g. "the Thing", "the rocky hero")
- pronouns: ["he","him"] | ["she","her"] | ["they","them"] | ["it"]
- intro_line_hint: a 4-8 word fragment the narrator can use the FIRST time this entity appears, e.g. "Ben Grimm, better known as the Thing"

CRITICAL: if two distinct entities share an epithet (e.g. both Ben-with-symbiote and the symbiote alone are called "Venom"), invent a clearer canonical_name to disambiguate ("Ben-as-Venom" vs "the symbiote"). The downstream writer will use these exact strings.

Return ONLY JSON. No prose, no markdown fences."""


def build_glossary(
    beats: list[Beat],
    comic_context: dict,
    *,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
) -> tuple[Glossary, str]:
    log = progress or (lambda _msg: None)

    chars_seen: set[str] = set()
    for b in beats:
        for c in b.characters_active:
            chars_seen.add(c)
    for c in comic_context.get("characters", []) or []:
        chars_seen.add(str(c))

    beats_block = "\n".join(
        f"- beat {b.id} ({b.function}) {b.name}: {b.summary} "
        f"[active: {', '.join(b.characters_active) or '?'}]"
        for b in beats
    )

    user = (
        f"COMIC: {comic_context.get('title', '?')} ({comic_context.get('series', '?')})\n"
        f"CHARACTERS observed across the beats: {', '.join(sorted(chars_seen)) or '?'}\n\n"
        f"BEATS:\n{beats_block}\n\n"
        f"TASK: Build the canonical-name glossary. Every entity that appears active in any "
        f"beat must have an entry. Pick a canonical_name that won't drift across the script.\n\n"
        f"Return JSON in this exact shape:\n"
        f"{{\n"
        f'  "characters": {{\n'
        f'    "<entity key>": {{"canonical_name": "...", "epithets": ["...", "..."], '
        f'"pronouns": ["he","him"], "intro_line_hint": "..."}},\n'
        f"    ...\n"
        f"  }}\n"
        f"}}"
    )

    log(f"[stage4]   glossary prompt: {len(user)} chars")
    chain = [model] if model else None

    def _has_characters(c: str) -> bool:
        p = _extract_json(c)
        return isinstance(p, dict) and isinstance(p.get("characters"), dict) and len(p["characters"]) > 0

    raw, mdl_used = call_with_chain(
        system=_GLOSSARY_SYSTEM,
        user=user,
        models=chain,
        max_tokens=4000,
        progress=progress,
        label="glossary",
        validator=_has_characters,
    )
    if debug_dump is not None:
        debug_dump["phase_b_raw"] = raw
        debug_dump["phase_b_model"] = mdl_used
    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("characters"), dict):
        raise RuntimeError(f"Phase B: no characters dict. Raw:\n{raw[:500]}")

    chars: dict[str, CharacterEntry] = {}
    for key, entry in parsed["characters"].items():
        if not isinstance(entry, dict):
            continue
        chars[str(key)] = CharacterEntry(
            canonical_name=str(entry.get("canonical_name", key)).strip(),
            epithets=[str(e).strip() for e in (entry.get("epithets") or []) if str(e).strip()],
            pronouns=[str(p).strip() for p in (entry.get("pronouns") or []) if str(p).strip()],
            intro_line_hint=str(entry.get("intro_line_hint", "")).strip(),
        )
    return Glossary(characters=chars), mdl_used


_WRITE_SYSTEM = """You are PanelNarrator, writing 60-second narration for YouTube Shorts in the ComicsUnlocked house style. You have already received the story BEATS and a NAMING GLOSSARY. Your job is to render them as final spoken prose.

This voice was reverse-engineered from 30 successful videos. Follow every rule:

1) HOOK FORMULA — MANDATORY "When [event], [twist]..." structure

   Channel benchmark (66% of 219 analyzed @TheComicCivilian videos open with "When..."):
     ✓ "When Miles Morales investigated a rooftop while being invisible, he came across..."
     ✓ "When members of the Titans and Justice League were kidnapped, Wonder Woman..."
     ✓ "When Frank Castle entered Valhalla, he couldn't find peace, so Odin..."

   Acceptable but less common alternates:
     ◐ "After [event happened], [character action]..." (~10% of channel)
     ◐ "What if [question]?" (rare on this channel — used by other channels)

   HARD BAN:
     ✗ Starting with a character name as the first word ("The Goblin unleashes..." is WRONG)
     ✗ "In an alternate universe..." (different channel's signature, don't copy)
     ✗ "Today we're looking at..." / "In today's video" / any framing meta-talk

   The hook MUST be 14-26 words and end with an open thread that pulls the viewer
   into scene 2 (use a comma + "..." or end with an unresolved promise). The hook
   is the ONE scene allowed to run long — every other scene stays punchy.

   HOOK = FIRST BEAT ONLY — NO PREVIEW OF LATER EVENTS.
   The hook narrates ONLY the first beat's own moment. Do NOT pull an event from a
   later beat into the opener — that creates a contradiction with the next scene.
     ✗ "When the symbiote sat imprisoned, it was Ben who set it free..."  then next
        scene "But Ben discovered the caged symbiote." (he frees it, THEN finds the
        cage? — broken. "set it free" belongs to a LATER beat.)
     ✓ "When the Venom symbiote sat imprisoned in Reed Richards' lab, it waited
        bitterly for a way out..."  (only the first beat — the imprisonment.)
   The teaser line shown over the cover is the only place a future twist is hinted.

2) CONNECTIVE GRAMMAR (scenes 2 onward)
   - Every scene from #2 onward MUST start with one of these connectives, exactly: But, However, As, When, After, Eventually, Instead, With, Now, Suddenly, Then, Until, Meanwhile, Soon.
   - The schema field "connective" is REQUIRED non-null for every scene where scene_id >= 2.
   - These are documented in 95%+ of successful comic Shorts and create the "and then... and then..." feeling that holds retention.

3) SENTENCE SHAPE — SHORT + PUNCHY, ONE EVENT PER SENTENCE (this is the fix)
   - **ONE EVENT PER SENTENCE — HARD RULE.** Each scene is ONE page held on screen
     for only a few seconds, so it can show ONE action. If your sentence names two
     things happening ("X did A as Y did B and warned C"), the viewer sees one page
     while you narrate three things — it looks WRONG. Pick the single most
     important action of the beat and narrate only that. Drop the secondary clauses.
   - **DO NOT write uniformly-sized sentences.** Vary length, but vary toward SHORT.
   - Target distribution across 12-14 scenes (including the outro credit):
     • **AT LEAST 3 short PUNCH sentences (≤11 words)** — landing/twist moments.
       A script with fewer than 3 punch sentences will be rejected and retried.
     • the rest are MEDIUM (12-17 words) — main flow
     • a CAUSAL scene (one event + its grounded 'why' clause, rule 6.7) MAY run to
       ~24 words; the hook (scene 1) too. Plain scenes over 17w with no causal
       clause are still rejected — don't pad.
     • 1 outro credit "The comic is X" (5-8 words)
   - Punch examples (≤11w, hit hard — these LAND):
     ✓ "But, even as an infant, Thanos was a unit." (9w)
     ✓ "Stating they would die anyway." (5w)
     ✓ "But he only stopped punching once he remembered his aunt." (10w)
     ✓ "But Ben crushed the sonic gun and stormed out." (9w)
   - Medium examples (12-17w):
     ✓ "So, Odin returned his cosmic powers and turned him into Ghost Rider again." (13w)
   - **NO redundant consecutive scenes.** Each scene advances to a NEW moment —
     never restate the previous scene's action with a later frame. Collapse an
     action progression into its single most impactful moment:
       ✗ Scene A "Reed aims the sonic gun." + Scene B "Reed fires the sonic gun."
       ✓ One scene: "Reed fires the sonic gun at Ben." (keep the payoff, drop the wind-up)
   - ANTI-PATTERN — MULTI-EVENT CRAM (do NOT write; this is exactly what we are fixing):
     ✗ "Now restored to his original human form, Ben reveled in the change as a
        horrified Reed raised his sonic gun and warned that the symbiote had
        corrupted Spider-Man's mind." (29w — THREE events on one page)
     ✓ Split the BEAT's single most important action into one punchy line:
        "Now human again, Ben revelled in his restored form." (9w)
        (the gun + the warning belong to OTHER beats — do not cram them here)
   - Uniformity is the AI-tell. Punchy variance is the channel signature.
   - Use AT MOST ONE internal connective per sentence. A second " and / while / as "
     usually means you have crammed a second event — split it out.
   - NO fragments. NO 5-word stub scenes. The only exception: the LAST scene may drop to as low as 8 words for a punchy landing.

4) NAMING / PRONOUN DISCIPLINE
   - Use ONLY the canonical_name and epithets supplied in the GLOSSARY for each entity.
   - When the active subject changes between scenes, name them in full (canonical_name).
   - Pronouns are valid only if the previous scene's main subject is identical.
   - Every entity must appear by canonical_name at least once before any pronoun referring to it.

5) TENSE
   - Present-historic throughout: "Bruce wakes up, collapses, and realizes…"
   - Past-perfect only for backstory: "he had been…"
   - NEVER simple-past for active narrative.

6) PANEL FIDELITY (HARD RULE — applies to every scene)
   Every fact in your narration MUST be derivable from the input data: panel descriptions, dialog text_blocks, characters lists, page_summary, or the LORE NOTES block. Do NOT invent:
   - emotions or motives that aren't in dominant_emotion or dialog
   - relationships not stated (e.g. don't imply a romantic anniversary if the comic shows the anniversary of an accident)
   - characters who don't appear in the data (NEVER substitute a related character — Doc Connors is NOT the Lizard's name in this story unless data says so)
   - events that didn't happen on the cited page (NO "devoured civilian", "blood sprayed", "screamed in agony" unless panel explicitly shows it)
   - poetic metaphors describing visuals (NO "tendrils twitching like a living breath" — describe what's literally shown)
   - rhetorical flourishes invented for impact (NO "it wasn't poison, it was purpose" — only use phrasing derivable from dialog or panel description)
   - sensory details not in panel description (NO "smelled of", "tasted like", "ear-splitting scream" unless explicitly stated)
   When a panel implies meaning that the data doesn't make explicit, write what's literally happening, not your interpretation. Reread the panel description before each scene.

   SPECIAL CASE — "WAR" or BATTLE FRAMING:
   If panel shows heroes fighting a SINGLE villain/entity together, frame it as
   "fought against [entity]" NEVER "war between [hero list]" — that implies the
   heroes fought each other. Examples:
     ✓ "When Spider-Man, Captain America, Thor, and Hawkeye fought a shadowy entity during a war..."
     ✗ "When a war between Spider-Man, Captain America, Thor, and Hawkeye erupted..." (WRONG — sounds like they fought each other)

   ANTI-PATTERN EXAMPLE 1 (anniversary):
   Panel data: "Ben says 'YOU FORGOT OUR ANNIVERSARY, REED.' Reed replies 'considering what this date means for you…'"
   BAD: "Ben confronts Reed about forgetting their anniversary."  (sounds romantic, misleads)
   GOOD: "Ben confronts Reed for forgetting the anniversary of the accident that turned him into the Thing."

   ANTI-PATTERN EXAMPLE 2 (invented action):
   Panel data: "monstrous figure made of black symbiotic material breaks through wall"
   BAD: "It bit Reed's arm — blood sprayed as it revealed Dr. Connors' face in its fangs."  (NONE OF THIS IS IN THE PANEL)
   GOOD: "It crashed through the wall, revealing itself as Venom."  (matches what's shown)

   ANTI-PATTERN EXAMPLE 3 (invented metaphor):
   Panel data: "The Thing pulls back a cloth, revealing a desk with a glowing symbiote container"
   BAD: "tendrils twitching like a living breath" (poetic invention)
   GOOD: "peered beneath the cloth, finding the glowing symbiote in its container"

6.6) CAUSAL FIDELITY — narrate the BEAT SUMMARY, in story order (this is critical)
   Each beat gives you a SUMMARY: one factual, wiki-grounded sentence of what
   happens in that beat. Narrate THAT event and nothing more. The beats are already
   in correct story order — narrate them in that order; do not jump ahead or back.
   - Do NOT invent a mechanism, object, or place not in the summary/wiki.
       ✗ "the Lizard used a machine in a sewer lab to rip the symbiote away"
         (no machine/sewer-lab in the summary) ✓ "the Lizard betrayed Ben and
         bonded with the symbiote himself" (what the summary/wiki says)
   - MATCH THE SEVERITY of the wiki. If the wiki says a character is KILLED, say
     killed — do not soften to "incapacitated" / "defeated".
   - Do NOT assert a state before it becomes true. A character is only "re-bonded"
     AFTER the symbiote returns to them; do not say "fully rebonded" while the
     villain still has the symbiote. Track who holds the symbiote at each beat.
   - Do NOT narrate the same event twice (e.g. two scenes both saying the symbiote
     rebonds with Ben). Each scene is a NEW story step.
   If the summary and a panel description seem to disagree, the SUMMARY (wiki) wins.

6.7) CONNECT CAUSE → EFFECT — no turn from nowhere (this makes the story land)
   Each beat may carry a "WHY" (its cause/motive, from the wiki). When a beat is a
   TURN — a character resents/betrays/decides, or a force changes sides — and the
   reason is not already on screen, weave that WHY into the scene as ONE short
   grounded clause. This is still ONE event (the turn) + its reason — NOT two events.
     ✗ "But Ben resented Reed."  (out of nowhere — why?)
     ✓ "But Ben resented Reed, the friend whose accident had made him the Thing,
        for forgetting the anniversary."  (the turn + its grounded cause)
     ✗ "Then the symbiote abandoned the Lizard and rebonded with Ben."  (why Ben?)
     ✓ "Then the symbiote abandoned the Lizard and reclaimed Ben — the broken host
        it had wanted all along."  (pays off the early 'perfect host' setup)
   SET UP a motive before it pays off: if a later turn relies on an earlier motive
   (the symbiote choosing Ben as its perfect host), PLANT that motive in the
   COLD_OPEN / early scene using that beat's WHY, so the payoff feels earned.
   A scene that carries a causal clause MAY run to ~24 words (others stay punchy,
   ≤17). Never invent a cause not in the WHY/summary/wiki.

6.5) FACT-CHECK SELF-PASS — before returning JSON
   For EACH scene you write, mentally verify:
   (a) Every named character actually appears in this panel's `characters` list (or page summary)
   (b) Every action verb (bit, fired, exploded, devoured, etc.) is in panel description or dialog
   (c) Every emotion adjective is in `dominant_emotion` or implied by dialog text
   (d) Every adjective describing a thing (glowing, monstrous, etc.) is in panel description
   If a phrase isn't grounded, REPLACE it with a grounded one or REMOVE it. Better to write a less colorful but accurate scene than a vivid but invented one.
   The user has rejected past drafts that twisted the story. Accuracy beats flourish.

7) LENGTH BUDGET — CHANNEL-CALIBRATED, CONNECTED-BUT-SNAPPY
   - **16-20 scenes** total (one per beat — more beats = more panels on screen;
     cover the full arc incl. resolution).
   - **165-270 words total.** A calm 1.1 pace (~2.9 wps) → ~63-94s. The extra room
     is for the added beats + CAUSAL clauses (the 'why' of each turn), NOT for
     flourish. If draft > 270 → trim flourish. If < 165 → ADD a missing
     canonical/causal beat (never pad with empty adjectives).
   - Before returning JSON, COUNT your total words. If > 270, tighten by cutting
     adjectives and any clause that is NOT a grounded cause; keep ONE event (+ its
     why) per sentence. Keep ALL canonical beats.
   - Sentence-by-sentence target distribution (16-20 scenes, 165-270 words):
     • ≥3 PUNCH (≤11w)   ← REQUIRED, will be rejected if fewer
     • most MEDIUM (12-17w)
     • a few CAUSAL (≤24w: one event + its grounded 'why'); the hook may be ≤26w
     • 1 OUTRO (5-8w)
   - Target ~65-75s spoken at ~2.9 words/second.

8) BEAT TAGGING — you do NOT pick pages
   - Write EXACTLY ONE scene per beat, in the given beat order, and tag it with
     that beat's beat_id. That is your only structural job.
   - DO NOT output page_ref or panel_ref. The page and panel each scene maps to
     are assigned automatically from the beat — picking them is not your job and
     any value you write is ignored. Spend that effort on the prose.

8.5) NARRATIVE FLOW — beats are already in reading order
   - The beats are pre-sorted into the comic's reading order, so just narrate them
     in sequence and the visuals will follow along page by page.
   - Keep the throughline intact: don't introduce a person, place, or object the
     viewer hasn't met without a half-clause that re-grounds them ("the symbiote
     he had taken", "back at the lab"). No one and nothing should appear from
     nowhere between consecutive scenes.

9) CONTINUITY ANCHOR
   - For each scene from #2 onward, you will see a "prev_anchor" — the last 6-8 words of the previous scene. Continue from this thread; do not reset the subject without re-introducing them.

10) FORBIDDEN
   - No em-dashes (—), no brackets, no parenthetical asides — this is spoken aloud.
   - No "what do you think in the comments", no "subscribe", no questions to viewer at the end.
   - No stage directions, no scene numbers inside text.

11) VOICE & RHYTHM — channel-calibrated from 219 reference Shorts

   11a. SENTENCE LENGTH VARIANCE — punchy, one event (+ optional why) each
        See rule 3 above. ≥3 punch sentences (≤11w), most medium (12-17w), a few
        causal (≤24w: one event + its grounded 'why'), 1 outro credit. ONE EVENT
        per sentence (a cause/why clause is allowed; a second EVENT is not).
        Uniformity is the AI-tell; cramming two events into one sentence is the bug.

   11b. STORYTELLER VOICE — not panel-reader
        After INTRODUCING a character by canonical name, switch to PRONOUNS
        (he/him/she/her) for the next 2-3 sentences. Re-introduce by name
        only when the scene shifts to a different character.
        Channel: "When Frank Castle entered Valhalla, he couldn't find peace.
        So, Odin returned his cosmic powers and turned him into Ghost Rider again."
        AVOID: "Reed Richards X... Reed Y... Reed Z..." every sentence (AI-tell).

   11c. SHOW DON'T TELL — concrete actions over named emotions
        Channel: "Peter unmasked Hobgoblin and threatened to kill him." (concrete)
        AVOID: "voices deep distrust and fear" (you NAMED the emotion).
        Anchor feelings in CONCRETE physical state:
          ✓ "He was haunted by nightmares."
          ✓ "Frank couldn't find peace."
          ✗ "Reed expresses anxiety about the symbiote situation."

   11d. NATURAL TEMPORAL MARKERS
        Prefer narrative-prose phrases over bullet-y connectives:
          ✓ "There he had a nightmare where..."
          ✓ "Then later that night, while visiting Aunt May..."
          ✓ "Frank had traveled to this specific planet to get advice."
        AVOID over-using: "Meanwhile, X." / "Eventually, Y." (too bullet-y).
        Use these sparingly — at most one "Meanwhile" or "Eventually" per script.

   11e. "STATING X" DIALOG ATTRIBUTION
        Channel uses participle phrases for what characters say. Use 1-3 times per script:
          ✓ "...stating the suit was alive and was messing with his head."
          ✓ "Stating a timeline where the Punisher raised Thanos would be even worse."

   11f. CONCRETE FINAL IMAGE
        End the LAST narrative scene (before the outro credit) with a physical,
        visual moment — NOT an abstract concept:
          ✓ "...left Hobgoblin's dead body hanging in a spider web."
          ✓ "...he decides to protect this new universe and the life inside."
          ✗ "...reinforcing man's inherent monstrosity and the darkness within."
        (Abstract endings give the viewer's eye nowhere to land.)

   11g. OUTRO CREDIT — MANDATORY closing line
        After ALL narrative scenes, ADD a FINAL very-short closing scene
        crediting the source. Format: "The comic is [comic title]." (5-8 words).
        Channel uses this in 100% of their videos. The outro scene MUST have:
          - text: "The comic is [actual comic title from COMIC CONTEXT]."
          - connective: null
          - beat_id: the last beat's id
          (its page is assigned automatically — do NOT write page_ref/panel_ref.)
        Channel examples:
          "The comic is Spider-Man the Spider Shadow issue"
          "The comic is Cosmic Ghost"
        DO NOT SKIP — this is the channel's identity closer.

Return ONLY JSON. No prose, no markdown fences."""


def _saga_clarity_block(comic_context: dict) -> str:
    """Extra writer rules for a multi-issue saga (comic_context.is_arc). The viewer
    has NOT read any of the issues, so for a saga clarity beats punchiness. Returns
    "" for single-comic mode → that path is unchanged."""
    if not comic_context.get("is_arc"):
        return ""
    n = comic_context.get("issue_count", 0)
    return (
        f"╔═══ MULTI-ISSUE SAGA ({n} issues) — WRITE FOR MAXIMUM CLARITY ═══╗\n"
        "This compresses a saga the viewer has NEVER read. CLARITY is the #1 goal — a\n"
        "first-time listener must follow the WHOLE story easily. This overrides the\n"
        "usual punchy style where they conflict. Obey:\n"
        "  1. ONE THROUGHLINE — follow the single main hero start to finish; frame\n"
        "     every event around what it means for THEM. Don't scatter across side names.\n"
        "  2. GLOSS ON FIRST MENTION — the first time ANY person/place/power/object is\n"
        "     named, add a 3-6 word plain tag of who/what it is (e.g. 'Zadkiel, the\n"
        "     fallen angel running the games'). Never drop a bare name the viewer can't place.\n"
        "  3. ONE NEW NAME PER SCENE — NO NAME LISTS. Introduce AT MOST one new proper\n"
        "     name per scene and gloss it (rule 2). NEVER list several names in a sentence\n"
        "     ('Blaze, Ketch, Jones and Slade…' is BANNED) — refer to a group collectively\n"
        "     ('the other riders', 'his rivals'). Omit minor proper nouns that don't move\n"
        "     the main story (background bots, gadget/object names like a machine's title) —\n"
        "     describe them in plain words instead ('the device powering the games').\n"
        "  4. TELL IT FORWARD — keep clear time order. If the comic flashes back, fold it\n"
        "     into forward order ('Years earlier…') so the listener never gets lost.\n"
        "  5. CONNECT EVERY TURN — each scene says WHY it follows from the previous one\n"
        "     (cause → effect), so jumps between issues read as ONE continuous story.\n"
        "  6. PLAIN WORDS — simple, concrete language a 12-year-old grasps on first listen.\n"
        "╚════════════════════════════════════════════════════════════════╝\n\n"
    )


def write_scenes(
    beats: list[Beat],
    glossary: Glossary,
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    *,
    hook_hint: str = "",
    all_pages: list[dict] | None = None,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
) -> tuple[dict, str]:
    log = progress or (lambda _msg: None)
    mode_info = MODES_BY_KEY[mode]

    lore_block = _lore_notes_block(comic_context, all_pages or [])
    few_shot = _load_few_shot_examples(n=2)  # v4: full scripts (2 × ~400w each)

    # Give the WRITER the canonical wiki plot DIRECTLY (not just the outliner's
    # distilled beats). The writer used to see only the short story_arc + the
    # beat summaries — so when the outliner's distillation was lossy/wrong, the
    # writer had no ground truth to write accurate, causally-connected prose from.
    # Now it sees the full plot (trimmed) and must source every fact + 'why' here.
    _plot = (comic_context.get("plot_summary") or "").strip()
    _arc = (comic_context.get("summary", {}) or {}).get("story_arc", "").strip()
    wiki_block = ""
    if _plot or _arc:
        wiki_block = ("CANONICAL WIKI PLOT (Marvel/DC Fandom — GROUND TRUTH; every "
                      "fact AND every 'why'/motive in your narration MUST come from "
                      "here, paraphrased):\n")
        if _arc:
            wiki_block += f"[arc] {_arc}\n\n"
        if _plot:
            wiki_block += f"[full plot] {_plot[:4800]}\n"

    user = (
        f"COMIC CONTEXT:\n{_ctx_block(comic_context)}\n\n"
        + (f"{wiki_block}\n\n" if wiki_block else "")
        + (f"{lore_block}\n\n" if lore_block else "")
        + f"NARRATION MODE: {mode} — {mode_info.description}\n"
        + (f"HOOK HINT: {hook_hint}\n" if hook_hint else "")
        + "\n"
        + _saga_clarity_block(comic_context)
        + f"BEATS — write EXACTLY ONE scene for EACH beat, in this SAME order:\n{_beats_block(beats)}\n\n"
        f"GLOSSARY (use these exact names):\n{_glossary_block(glossary)}\n\n"
        + (f"{few_shot}\n\n" if few_shot else "")
        + f"PAGE DETAIL (background grounding — what is actually on each page, so "
        f"your prose stays factual):\n{_pages_block_compact(story_pages)}\n\n"
        f"WORD BUDGET: {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} total words across all scenes.\n"
        f"CONNECTIVE WHITELIST (scene 2 onward MUST start with one): {', '.join(_CONNECTIVES)}.\n\n"
        f"╔═══ STRICT 1-TO-1 OUTPUT (this is how scenes map to the video) ═══╗\n"
        f"The \"scenes\" array MUST have EXACTLY {len(beats)} story scenes — ONE per beat,\n"
        f"in the SAME ORDER as the beats above, PLUS the outro credit as the final\n"
        f"element. scenes[0] narrates beat {beats[0].id}, scenes[1] narrates the next\n"
        f"beat, and so on — POSITION IS BINDING. Do NOT merge two beats into one\n"
        f"scene, do NOT split one beat into two scenes, do NOT reorder, do NOT skip a\n"
        f"beat. Each scene tells ONLY its own beat's summary event (rule 6.6).\n"
        f"╚════════════════════════════════════════════════════════════════╝\n"
        f"DO NOT output page_ref or panel_ref — assigned from the beat (rule 8). "
        f"Return JSON in this exact shape ({len(beats)} story scenes + 1 outro):\n"
        f"{{\n"
        f'  "title": "<short punchy title for this Short>",\n'
        f'  "hook": "<scenes[0] text — narrates the FIRST beat only>",\n'
        f'  "scenes": [\n'
        f'    {{"text": "When ...", "connective": null, "beat_id": {beats[0].id}}},\n'
        f'    {{"text": "But ...", "connective": "But", "beat_id": "<2nd beat id>"}},\n'
        f"    ...  (one per beat, in order) ...,\n"
        f'    {{"text": "The comic is <title>.", "connective": null, "beat_id": {beats[-1].id}}}\n'
        f"  ]\n"
        f"}}\n\n"
        f"REMINDER: {len(beats)} story scenes (one per beat, in order) THEN the "
        f"\"The comic is X.\" outro credit (5-8 words, connective=null). See rule 11g."
    )

    log(f"[stage4]   write prompt: {len(user)} chars, {len(beats)} beats")
    # Phase C uses CREATIVE_LLM_MODELS (Claude Sonnet primary) — sentence-length
    # variance + storyteller voice are HARD for small models. Other phases keep
    # using LLM_MODELS via default call_with_chain behavior.
    chain = [model] if model else list(CREATIVE_LLM_MODELS)

    def _has_scenes(c: str) -> bool:
        p = _extract_json(c)
        return isinstance(p, dict) and isinstance(p.get("scenes"), list) and len(p["scenes"]) > 0

    raw, mdl_used = call_with_chain(
        system=_WRITE_SYSTEM,
        user=user,
        models=chain,
        max_tokens=5000,
        progress=progress,
        label="write",
        validator=_has_scenes,
    )
    if debug_dump is not None:
        debug_dump["phase_c_raw"] = raw
        debug_dump["phase_c_model"] = mdl_used
    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("scenes"), list):
        raise RuntimeError(f"Phase C: no scenes array. Raw:\n{raw[:500]}")
    return parsed, mdl_used


def _is_critical_error(msg: str) -> bool:
    """Errors that mean the narration is structurally broken — must crash.
    Everything else (word count off, connective wording, scene length, beat_id
    mismatch) is a soft warning we tolerate so the pipeline can still ship a video.

    beat_id mismatch is soft because the field is just metadata for tracing
    which outline beat a scene came from — downstream (TTS, video) doesn't
    consume it. When the writer ignores beat limits but still produces good
    scenes, we keep the scenes."""
    m = msg.lower()
    critical_markers = (
        "no scenes",                 # zero scenes returned — pipeline can't proceed
        "page_ref=",                 # scene references a page that doesn't exist
        "hallucination",             # LLM injected content not in source comic
        "wiki:",                     # phase E wiki cross-check found canonical mismatch
        "repeat the same content",   # two consecutive scenes restate the same beat/phrase
        "narrates multiple events",  # single-event guard: >1 event in one sentence
    )
    return any(marker in m for marker in critical_markers)


_HALLUC_BLACKLIST = (
    "meme", "memes", "spammed", "spamming", "spam", "subscribe", "like and",
    "comment below", "morphin", "szn", "online's", "chat with",
    "follow me", "smash that",
)


def _detect_hallucinations(
    parsed: dict, glossary, comic_context: dict, story_pages: list[dict]
) -> list[str]:
    """Hallucination guard — reject narration with fabricated proper nouns or
    obvious online-slang contamination. Hard critical errors (force retry).

    Two passes:
      1. Blacklist scan — "meme", "spammed", "szn", subscriber-bait phrases
      2. Proper-noun cross-check — every Capitalized mid-sentence word not in
         the allowed vocab (glossary names + canonical_name forms + lore terms)
         is suspect."""
    errors: list[str] = []
    scenes = parsed.get("scenes") or []
    text_all = " ".join(str(s.get("text", "")) for s in scenes).lower()

    for word in _HALLUC_BLACKLIST:
        if word in text_all:
            errors.append(
                f"hallucination: forbidden phrase {word!r} appears in narration "
                f"(online slang / contamination not from source)"
            )

    # Allowed proper nouns: glossary entries + comic_context character names +
    # comic_context locations. Pre-filter case-insensitively.
    allowed = set()
    for ch in getattr(glossary, "characters", []) or []:
        nm = getattr(ch, "canonical_name", "") or ""
        allowed.update(nm.lower().split())
        for ep in getattr(ch, "epithets", []) or []:
            allowed.update(str(ep).lower().split())
    for c in comic_context.get("characters") or []:
        allowed.update(str(c).lower().split())
    for loc in comic_context.get("locations") or []:
        allowed.update(str(loc).lower().split())
    for pg in story_pages:
        for ch in pg.get("characters") or []:
            allowed.update(str(ch).lower().split())
    # Common safe words (English vocabulary) — anything that looks like a name
    # but is actually a regular capital-after-period or proper noun in known
    # English vocab.
    allowed.update({
        "the", "i", "a", "an", "but", "and", "or", "however", "as", "when",
        "after", "eventually", "instead", "with", "now", "suddenly", "then",
        "until", "meanwhile", "soon", "marvel", "dc", "earth", "comic", "comics",
        "what", "if", "doctor", "dr",  # common honorifics
    })

    import re
    # Find tokens like "YuanfenOnline" or "FantasticFour" that mash capitals
    # mid-word — strong hallucination signal.
    for s in scenes:
        text = str(s.get("text", ""))
        # Detect camelCase / PascalCase compounds (e.g. YuanfenOnline)
        for m in re.finditer(r"\b([A-Z][a-z]+[A-Z][a-z]+)\b", text):
            errors.append(
                f"hallucination: suspicious camelCase token {m.group(1)!r} in scene "
                f"{s.get('scene_id', '?')} — likely fabricated proper noun"
            )

    return errors


def _validate(parsed: dict, valid_pages: set[int], valid_beat_ids: set[int]) -> list[str]:
    errors: list[str] = []
    scenes = parsed.get("scenes") or []
    if not scenes:
        return ["no scenes in output"]
    if not (9 <= len(scenes) <= 22):
        errors.append(f"scene count {len(scenes)} not in 9..22")

    total_words = 0
    for i, s in enumerate(scenes, start=1):
        text = str(s.get("text", "")).strip()
        wc = len(text.split())
        total_words += wc
        is_last = (i == len(scenes))

        # Outro credit detection: last scene, short, null connective, contains
        # "comic is" — skip per-scene validation (no connective enforcement,
        # no length-floor enforcement). Channel-signature closer per spec 11g.
        is_outro = (
            is_last
            and s.get("connective") in (None, "", "null")
            and wc <= 12
            and "comic is" in text.lower()
        )
        if is_outro:
            continue

        try:
            pref = int(s.get("page_ref", 0) or 0)
        except (TypeError, ValueError):
            pref = 0
        if pref not in valid_pages:
            errors.append(f"scene {i} page_ref={pref} not in input pages")

        try:
            bid = int(s.get("beat_id", 0) or 0)
        except (TypeError, ValueError):
            bid = 0
        if bid not in valid_beat_ids:
            errors.append(f"scene {i} beat_id={bid} not in beats")

        if i == 1:
            if not (_HOOK_MIN_WORDS <= wc <= _HOOK_MAX_WORDS):
                errors.append(f"scene 1 (hook) is {wc} words, want {_HOOK_MIN_WORDS}-{_HOOK_MAX_WORDS}")
            if s.get("connective"):
                errors.append("scene 1 must have connective=null")
            continue

        conn = (s.get("connective") or "").strip()
        if conn not in _CONNECTIVES:
            errors.append(f"scene {i} connective {conn!r} not in whitelist")
        # Match multi-word connectives ("Just then", "That's when") via prefix scan.
        text_start_conn = _starts_with_connective(text)
        if text_start_conn is None:
            first_word = text.split()[0] if text else ""
            errors.append(f"scene {i} text starts with {first_word!r}, not a whitelist connective")

        floor = 8 if is_last else _SCENE_MIN_WORDS
        if not (floor <= wc <= _SCENE_MAX_WORDS):
            errors.append(f"scene {i} is {wc} words, want {floor}-{_SCENE_MAX_WORDS}")

    # Total-words band — single source of truth, calibrated DOWN from the old
    # 230..290 (which forced ~283-word, long, compound output). The body (this
    # draft, pre-intro) targets _TARGET_WORDS_MIN.._TARGET_WORDS_MAX; allow a
    # little slack on top so a 1-2 word overshoot doesn't churn the retry loop.
    if not (_TARGET_WORDS_MIN <= total_words <= _TARGET_WORDS_MAX + 20):
        errors.append(
            f"total words {total_words} not in "
            f"{_TARGET_WORDS_MIN}..{_TARGET_WORDS_MAX + 20}"
        )

    # Median sentence-length soft check (excluding hook). Punchy target: most
    # scenes 12-17w, so a median over _TARGET_SENT_LEN+3 (=16) means overstuffing.
    body_lens = [len(str(s.get("text", "")).split()) for s in scenes[1:]]
    if body_lens:
        med = statistics.median(body_lens)
        if med > _TARGET_SENT_LEN + 3:
            errors.append(
                f"median scene length {med:.0f}w > {_TARGET_SENT_LEN+3} "
                f"(target {_TARGET_SENT_LEN}w; channel is punchy, one event/scene)"
            )

    # ANTI-UNIFORMITY toward SHORT: require >= _MIN_PUNCH_SCENES punch sentences
    # (<= _PUNCH_MAX_WORDS). Replaces the old "must have >=1 long 23-30w sentence"
    # rule, which actively caused multi-event cramming. Count body scenes only
    # (exclude hook scene 1; the outro credit DOES count as a punch).
    body_for_punch = [
        len(str(s.get("text", "")).split())
        for s in scenes[1:]
        if not s.get("is_intro")
    ]
    punch_count = sum(1 for l in body_for_punch if l <= _PUNCH_MAX_WORDS)
    if punch_count < _MIN_PUNCH_SCENES:
        errors.append(
            f"only {punch_count} punch sentence(s) (≤{_PUNCH_MAX_WORDS}w); "
            f"need ≥{_MIN_PUNCH_SCENES} — tighten the longest scenes into short, "
            f"single-event lines (the channel signature). Do NOT pad to medium."
        )

    errors.extend(_detect_redundant_scenes(scenes))
    errors.extend(_detect_multi_event(scenes))

    # Structural order (replaces the old page-monotonic check). Narration now
    # follows CAUSAL order, not page order, so page_ref may step backward (e.g. a
    # LANDING splash on an earlier page than the CLIMAX kill panel) — that is fine.
    # What must hold: the closing credit/outro is the LAST scene, and no outro
    # appears mid-stream. (COLD_OPEN-first is guaranteed by _order_beats_canonical.)
    all_scenes = parsed.get("scenes") or []
    for i, s in enumerate(all_scenes):
        is_last = (i == len(all_scenes) - 1)
        if "comic is" in str(s.get("text", "")).lower() and not is_last:
            errors.append(
                f"scene {s.get('scene_id','?')} is the 'The comic is …' outro but "
                f"is not the last scene — the credit must close the video"
            )

    return errors


_REDUNDANCY_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "with", "that",
    "this", "as", "but", "so", "when", "then", "after", "while", "his", "her",
    "their", "him", "them", "it", "its", "by", "for", "at", "from", "into",
    "over", "now", "fully", "himself", "herself", "becoming", "before", "until",
    "would", "could", "should", "have", "been", "were", "was", "are", "had",
}


def _scene_stems(text: str) -> set[str]:
    """Significant content words → first-5-char stems (so 'overpowered' and
    'overpowering' collapse to 'overp'). Drops stopwords and short words."""
    import re
    out: set[str] = set()
    for w in re.findall(r"[a-zA-Z]+", text.lower()):
        if len(w) >= 4 and w not in _REDUNDANCY_STOP:
            out.add(w[:5])
    return out


def _detect_redundant_scenes(scenes: list[dict], threshold: int = 4) -> list[str]:
    """All-pairs content-repeat detection. Proper nouns (characters/places) recur
    normally across a story, so they are EXCLUDED — only repeated DESCRIPTIVE
    content is flagged ("severed arm"), never entity names ("Reed Richards",
    "Venom symbiote"). Two signals: a repeated descriptive bigram, or
    >=threshold shared non-entity content stems."""
    import re
    issues: list[str] = []
    body = [s for s in scenes if not s.get("is_intro") and not s.get("is_outro")]

    # Capitalized words (minus sentence-openers/connectives) = proper nouns.
    openers = {c.lower() for c in _CONNECTIVES} | {
        "the", "a", "an", "his", "her", "their", "this", "that", "with", "now",
        "then", "soon", "but", "and", "as", "when", "after", "meanwhile",
        "he", "she", "it", "they",
    }
    entities: set[str] = set()
    for s in body:
        for w in re.findall(r"\b[A-Z][a-zA-Z]+\b", str(s.get("text", ""))):
            lw = w.lower()
            if lw not in openers:
                entities.add(lw)
    entity_stems = {w[:5] for w in entities}

    def key_bigrams(text: str) -> set[str]:
        words = [w for w in re.findall(r"[a-zA-Z]+", text.lower())
                 if len(w) >= 4 and w not in _REDUNDANCY_STOP]
        out: set[str] = set()
        for i in range(len(words) - 1):
            w1, w2 = words[i], words[i + 1]
            if w1 in entities or w2 in entities:
                continue  # entity-name bigram → normal recurrence, skip
            out.add(f"{w1} {w2}")
        return out

    n = len(body)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = body[i], body[j]
            sa = _scene_stems(str(a.get("text", ""))) - entity_stems
            sb = _scene_stems(str(b.get("text", ""))) - entity_stems
            shared = sa & sb
            shared_bigrams = key_bigrams(str(a.get("text", ""))) & key_bigrams(str(b.get("text", "")))
            ai = a.get("scene_id", i + 1); bi = b.get("scene_id", j + 1)
            if shared_bigrams:
                issues.append(
                    f"scenes {ai}-{bi} repeat the same content "
                    f"(repeated phrase: {', '.join(sorted(shared_bigrams))}) — "
                    f"rewrite the later scene to advance the story instead of restating it"
                )
            elif len(shared) >= threshold:
                issues.append(
                    f"scenes {ai}-{bi} repeat the same content "
                    f"(shared: {', '.join(sorted(shared))}) — rewrite the later scene "
                    f"to advance the story instead of restating it"
                )
    return issues


# Clause joiners that typically introduce a SECOND independent event (a new
# subject doing a new action). Plain " and " is tracked separately because it
# often just continues ONE subject ("crushed the gun and stormed out") and is
# fine on its own — it only signals cramming when paired with one of these.
_EVENT_JOINERS = (" while ", " as ", " before ", " after ", " then ", " and then ")


def _detect_multi_event(scenes: list[dict]) -> list[str]:
    """Single-event guard. Flags a body scene that narrates MORE THAN ONE event,
    because each scene is one page held on screen for a few seconds and cannot
    visually track two simultaneous actions (the "narration doesn't match the
    scene" bug — e.g. Venom scene 7 narrated 3 events over one whole page).

    Heuristic (deterministic, no LLM — runs every retry, can't throttle):
    flag when the sentence is long (>16 words) AND it both subordinates a second
    clause (while/as/then/before/after) AND coordinates another with 'and', OR it
    stacks two+ subordinate joiners. Short single-event lines never trip it.
    Errors flow into _retry_fix_with_wiki, which is told to split/trim to the
    single most important action (never to add a scene — 1 beat → 1 scene)."""
    issues: list[str] = []
    for i, s in enumerate(scenes):
        if i == 0 or s.get("is_intro") or s.get("is_outro"):
            continue  # hook (scene 1) and bookends are exempt
        text = str(s.get("text", "")).strip()
        if "comic is" in text.lower():
            continue  # outro credit (may not be flagged is_outro yet at validate time)
        wc = len(text.split())
        t = " " + text.lower() + " "
        joins = sum(t.count(j) for j in _EVENT_JOINERS)
        plain_and = t.count(" and ") - t.count(" and then ")  # don't double-count
        # A CAUSAL scene is one event + one grounded 'why' clause (allowed, ≤24w);
        # a CRAM is ≥2 independent events. Flag only a real cram: two+ subordinate
        # event-joiners (while/as/then/before/after), or a long sentence (>18w) that
        # stacks a joiner AND an 'and'. A single causal/relative clause ('whose…',
        # 'for forgetting…', '— the host it wanted') adds no event-joiner, so it
        # passes.
        multi = joins >= 2 or (wc > 18 and (joins + plain_and) >= 2)
        if multi:
            sid = s.get("scene_id", i + 1)
            issues.append(
                f"scene {sid} narrates multiple EVENTS in one sentence "
                f"({wc}w; clause-joiners={joins}, 'and'={plain_and}) — a single page "
                f"can't show two actions. Keep ONE event (you MAY add one grounded "
                f"'why' clause for its cause); drop the OTHER events (they belong to "
                f"other beats). Do NOT add a scene — one beat maps to one scene."
            )
    return issues


def _to_narration(parsed: dict, beats: list[Beat], glossary: Glossary,
                  mode: str, mdl: str) -> Narration:
    scenes: list[Scene] = []
    total_words = 0
    raw_scenes = parsed.get("scenes") or []
    for i, s in enumerate(raw_scenes, start=1):
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        wc = len(text.split())
        conn = s.get("connective")
        scenes.append(Scene(
            scene_id=i,
            text=text,
            page_ref=int(s.get("page_ref", 0) or 0),
            panel_ref=int(s.get("panel_ref", -1) if s.get("panel_ref") is not None else -1),
            word_count=wc,
            target_seconds=round(wc / _WORDS_PER_SEC, 2),
            connective=str(conn).strip() if conn else None,
            beat_id=int(s.get("beat_id", 0) or 0),
            is_intro=bool(s.get("is_intro")),
            is_outro=bool(s.get("is_outro")),
        ))
        total_words += wc

    est_duration = round(total_words / _WORDS_PER_SEC, 2)
    return Narration(
        mode=mode,
        title=str(parsed.get("title", "")).strip(),
        hook=str(parsed.get("hook", scenes[0].text if scenes else "")).strip(),
        scenes=scenes,
        total_word_count=total_words,
        estimated_duration_seconds=est_duration,
        words_per_second=_WORDS_PER_SEC,
        source_project="",
        llm_model=mdl,
        beats=beats,
        glossary=glossary,
    )


_FEW_SHOT_CACHE: str | None = None
_VTT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)\.(\d+)")


def _parse_vtt_cues(vtt_path: Path) -> list[str]:
    """Extract deduped cue texts from a YouTube auto-sub .vtt file."""
    try:
        text = vtt_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    cues: list[str] = []
    cur_text: list[str] = []
    seen: set[str] = set()
    in_cue = False
    for line in text.splitlines():
        if " --> " in line:
            if cur_text:
                t = " ".join(cur_text).strip()
                key = t.lower()
                if t and key not in seen:
                    seen.add(key)
                    cues.append(t)
            cur_text = []
            in_cue = True
            continue
        if in_cue and line.strip() and "<" not in line and not line.startswith(("WEBVTT", "Kind:", "Language:")):
            cleaned = re.sub(r"<[^>]+>", "", line).strip()
            if cleaned:
                cur_text.append(cleaned)
    if cur_text:
        t = " ".join(cur_text).strip()
        if t and t.lower() not in seen:
            cues.append(t)
    return cues


def _load_few_shot_examples(n: int = 2, cap_words: int = 400) -> str:
    """Pick n .vtt files, extract FULL transcript (capped at cap_words for token
    budget), format as end-to-end channel-style demonstration. Strong creative
    LLMs (Sonnet) absorb the full arc — voice, length variance, pronoun discipline,
    \"stating X\" attribution, the \"The comic is X\" outro — from these examples.

    Cached after first call. Deterministic via Random(42). Returns \"\" if
    research/reference/ missing — graceful degrade."""
    global _FEW_SHOT_CACHE
    if _FEW_SHOT_CACHE is not None:
        return _FEW_SHOT_CACHE

    ref_dir = Path(__file__).resolve().parent.parent.parent / "research" / "reference"
    if not ref_dir.exists():
        _FEW_SHOT_CACHE = ""
        return ""
    vtts = sorted(ref_dir.glob("*.en.vtt"))
    if not vtts:
        _FEW_SHOT_CACHE = ""
        return ""

    sample = random.Random(42).sample(vtts, min(n, len(vtts)))
    blocks: list[str] = []
    for vtt in sample:
        cues = _parse_vtt_cues(vtt)
        if not cues:
            continue
        full = " ".join(cues)
        snippet = " ".join(full.split()[:cap_words])
        if snippet:
            blocks.append(f"=== FULL CHANNEL SCRIPT {len(blocks)+1} ===\n{snippet}")

    if not blocks:
        _FEW_SHOT_CACHE = ""
        return ""

    _FEW_SHOT_CACHE = (
        "FULL CHANNEL SCRIPTS BELOW — study the COMPLETE arc: hook → setup → "
        "complications → climax → resolution → outro credit. Mirror the voice, "
        "sentence-length variance, pronoun-after-intro pattern, \"stating X\" "
        "attribution, and the \"The comic is X\" outro.\n\n"
        + "\n\n".join(blocks)
        + "\n\nApply this voice to OUR comic — don't reuse any of THEIR "
          "specific story content."
    )
    return _FEW_SHOT_CACHE


def _lore_notes_block(ctx: dict, all_pages: list[dict]) -> str:
    """Assemble recap text from non-story pages. Story summary is in COMIC CONTEXT already."""
    recap_chunks: list[str] = []
    allowed_types = {"caption", "narration", "title", "subtitle"}
    for p in all_pages or []:
        if p.get("page_type") not in ("cover", "skip"):
            continue
        for tb in (p.get("text_blocks") or []):
            ttype = str(tb.get("type", "")).lower().strip()
            text = str(tb.get("text", "")).strip()
            if ttype in allowed_types and len(text) > 30:
                recap_chunks.append(text)
    if not recap_chunks:
        return ""
    lines = [
        "RECAP TEXT FROM THE BOOK ITSELF (from pages classified as cover/recap/intro — verbatim):",
    ]
    for chunk in recap_chunks:
        lines.append(f"- {chunk}")
    return "\n".join(lines)


def _ctx_block(ctx: dict) -> str:
    from stages.stage_1.tools.summarize_context import format_for_narration
    lines = [
        f"Title: {ctx.get('title', '?')}",
        f"Series: {ctx.get('series', '?')} {ctx.get('issues', '')}".strip(),
        f"Year: {ctx.get('year', '?')}",
    ]
    summary_block = format_for_narration(ctx.get("summary") or {})
    if summary_block:
        lines.append("\n" + summary_block)
        return "\n".join(lines)
    lines.append(f"Characters: {', '.join(ctx.get('characters', [])) or '?'}")
    plot = ctx.get("plot_summary", "")
    if plot:
        lines.append(f"\nPlot (from wiki):\n{plot[:2000]}")
    return "\n".join(lines)


def _pages_block_full(story_pages: list[dict]) -> str:
    out: list[str] = []
    for p in story_pages:
        pn = p.get("page_number")
        issue = p.get("issue_label", "")
        summary = (p.get("page_summary") or "").strip()
        block = [f"[page {pn}{' ' + issue if issue else ''}] {summary}"]
        for pan in (p.get("panels") or []):
            desc = pan.get("description", "")
            chars = ", ".join(pan.get("characters", []) or [])
            emo = pan.get("dominant_emotion", "")
            block.append(f"  panel {pan.get('index')}: {desc} [chars: {chars or '?'}] [emotion: {emo or '?'}]")
            for tb in (p.get("text_blocks") or []):
                if int(tb.get("panel_index", -99)) == pan.get("index"):
                    spk = tb.get("speaker") or "—"
                    ttype = tb.get("type", "speech")
                    block.append(f"    {ttype} [{spk}]: \"{tb.get('text', '')}\"")
        out.append("\n".join(block))
    return "\n\n".join(out) if out else "(no preprocessed pages)"


def _pages_block_compact(story_pages: list[dict]) -> str:
    out: list[str] = []
    for p in story_pages:
        pn = p.get("page_number")
        summary = (p.get("page_summary") or "").strip()
        panels = p.get("panels") or []
        head = f"[page {pn}] {summary}"
        panel_lines = [
            f"  panel {pan.get('index')}: {pan.get('description','')[:100]}"
            for pan in panels
        ]
        out.append("\n".join([head] + panel_lines))
    return "\n".join(out) if out else "(no preprocessed pages)"


def _beats_block(beats: list[Beat]) -> str:
    out = []
    for b in beats:
        kp = ", ".join(f"p{k.get('page')}.{k.get('panel')}" for k in b.key_panels) or "?"
        chars = ", ".join(b.characters_active) or "?"
        block = (
            f"beat {b.id} [{b.function}] {b.name}\n"
            f"  pages: {b.page_refs}  key_panels: {kp}  active: {chars}\n"
            f"  what happens: {b.summary}"
        )
        if (b.cause or "").strip():
            block += f"\n  WHY (cause/motive — weave in if this turn needs it): {b.cause}"
        out.append(block)
    return "\n".join(out)


def _glossary_block(g: Glossary) -> str:
    if not g.characters:
        return "(empty)"
    out = []
    for key, entry in g.characters.items():
        out.append(
            f"- {key}: canonical='{entry.canonical_name}'  "
            f"epithets={entry.epithets}  pronouns={entry.pronouns}  "
            f"intro='{entry.intro_line_hint}'"
        )
    return "\n".join(out)


def _extract_json(raw: str) -> dict | None:
    candidates: list[str] = []
    for pat in [r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"]:
        m = re.search(pat, raw, re.DOTALL)
        if m:
            candidates.append(m.group(1).strip())
    candidates.append(raw.strip())
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j > i:
        candidates.append(raw[i: j + 1])

    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue

    # Fallback: repair JSON truncated mid-array (LLM cut off before closing brackets).
    for c in candidates:
        repaired = _repair_truncated_json(c)
        if repaired is None:
            continue
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            continue
    return None


def _repair_truncated_json(s: str) -> str | None:
    """Trim to last complete array element + close unmatched brackets — best-effort
    repair when an LLM is cut mid-output."""
    if not s:
        return None
    last = s.rfind("},")
    if last == -1:
        last_brace = s.rfind("}")
        if last_brace == -1:
            return None
        truncated = s[: last_brace + 1]
    else:
        truncated = s[: last + 1]
    open_braces = truncated.count("{") - truncated.count("}")
    open_brackets = truncated.count("[") - truncated.count("]")
    if open_braces < 0 or open_brackets < 0:
        return None
    return truncated + ("]" * open_brackets) + ("}" * open_braces)


_FIDELITY_SYSTEM = """You are FactChecker, a strict narration auditor for comic-book Shorts.

You will be given:
  • A NARRATION SCRIPT (numbered scenes with text + page_ref + panel_ref)
  • The PANEL DATA each scene cites (description, characters, dominant_emotion, dialog text_blocks, page_summary)

Your job: identify every CLAIM in the narration that is NOT supported by the panel data. A claim is unsupported if:
  • It names a character/object/location not in the panel's characters or page summary
  • It states an action (verb) that isn't in the panel description or dialog
  • It uses adjectives/emotions not in dominant_emotion or panel description
  • It substitutes a related character (e.g. "Dr. Connors" instead of "the symbiote monster")
  • It invents poetic metaphors not grounded in visual description
  • It misframes a relationship (e.g. "war BETWEEN heroes" when heroes fought a single villain together)

Return STRICT JSON only:
{
  "issues": [
    {"scene_id": 1, "claim": "war between Spider-Man and Captain America", "reason": "panel shows them fighting a shadowy entity TOGETHER, not each other"},
    {"scene_id": 8, "claim": "Dr. Connors' face in its fangs", "reason": "panel data does not mention Dr. Connors"}
  ]
}

If everything is supported, return: {"issues": []}
Do NOT flag stylistic phrasing — only flag claims that are factually unsupported."""


def _fidelity_check(
    parsed: dict, story_pages: list[dict], comic_context: dict,
    *, model: str | None, progress: Callable[[str], None] | None,
) -> list[str]:
    """Phase D: LLM-as-fact-checker. Compares each scene's text against its
    cited panel data and returns list of issue strings.
    Each issue is formatted as: "scene N: claim X — reason Y" for retry prompt."""
    log = progress or (lambda _msg: None)
    scenes = parsed.get("scenes") or []
    if not scenes:
        return []

    # Build panel data block — only for cited (page_ref, panel_ref) pairs
    page_lookup = {int(p.get("page_number", 0)): p for p in story_pages}
    panel_data_lines = []
    nar_lines = []
    for s in scenes:
        sid = s.get("scene_id", "?")
        text = str(s.get("text", "")).strip()
        nar_lines.append(f"S{sid} (page {s.get('page_ref')}, panel {s.get('panel_ref')}): {text}")
        pg = page_lookup.get(int(s.get("page_ref", 0) or 0))
        if not pg:
            panel_data_lines.append(f"S{sid} panel data: <MISSING PAGE>")
            continue
        panels = pg.get("panels") or []
        idx = int(s.get("panel_ref", 0) or 0)
        panel = panels[idx] if 0 <= idx < len(panels) else None
        if panel:
            desc = (panel.get("description") or "")[:300]
            chars = panel.get("characters") or []
            emo = panel.get("dominant_emotion") or ""
            tbs = [
                str(tb.get("text", ""))[:120]
                for tb in (pg.get("text_blocks") or [])
                if tb.get("panel_index") == idx
            ][:4]
            summary = (pg.get("page_summary") or "")[:200]
            panel_data_lines.append(
                f"S{sid} panel data:\n"
                f"  desc: {desc}\n"
                f"  characters: {chars}\n"
                f"  emotion: {emo}\n"
                f"  dialog: {tbs}\n"
                f"  page_summary: {summary}"
            )
        else:
            panel_data_lines.append(f"S{sid} panel data: <PANEL idx={idx} OUT OF BOUNDS>")

    user = (
        "NARRATION SCRIPT:\n" + "\n".join(nar_lines) + "\n\n"
        + "PANEL DATA:\n" + "\n\n".join(panel_data_lines) + "\n\n"
        + "Return JSON {\"issues\": [...]}."
    )

    log("[stage4] phase D — fidelity fact-check (reasoning chain)…")
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, mdl = call_with_chain(
            system=_FIDELITY_SYSTEM,
            user=user,
            models=chain,
            max_tokens=2500,
            progress=progress,
            label="fidelity",
            validator=lambda c: '"issues"' in c,
        )
    except RuntimeError as exc:
        log(f"[stage4]   fidelity check chain failed — skipping: {exc}")
        return []

    parsed_fc = _extract_json(raw)
    if not isinstance(parsed_fc, dict):
        return []
    issues = parsed_fc.get("issues") or []
    out: list[str] = []
    for iss in issues:
        if not isinstance(iss, dict):
            continue
        sid = iss.get("scene_id", "?")
        claim = str(iss.get("claim", ""))[:100]
        reason = str(iss.get("reason", ""))[:150]
        out.append(f"scene {sid}: claim {claim!r} unsupported — {reason}")
    if out:
        log(f"[stage4]   fidelity check found {len(out)} unsupported claim(s)")
        for issue in out[:5]:
            log(f"[stage4]     - {issue}")
    else:
        log("[stage4]   fidelity check: all claims grounded ✓")
    return out


_WIKI_CROSS_CHECK_SYSTEM = """You are WikiAuditor — strictest canonical-story checker for comic-book Shorts.

You will be given:
  • The canonical PLOT SUMMARY for this comic (from Marvel/DC Fandom wiki — ground truth)
  • The narration SCRIPT (numbered scenes)

Your job: identify every scene whose claim CONTRADICTS or MISSES a key beat from the wiki canonical plot.

A scene FAILS if it:
  • Substitutes a character (e.g. "Reed killed Lizard" when wiki says "Ben killed Lizard")
  • Reverses an action (e.g. "Ben attacked Reed" when wiki says "Lizard attacked Reed")
  • Invents an event not in wiki (e.g. "broke through wall" when wiki says "left lab calmly")
  • Skips a CRITICAL plot beat that the wiki spends multiple sentences on
  • Misorders cause/effect (e.g. shows transformation before bonding)
  • Uses wrong location ("amid ruins" vs canonical "in alley")

A scene PASSES if its factual claims appear (even paraphrased) in the wiki plot.

╔══════════════════════════════════════════════════════════════════════════╗
║  CRITICAL: BEFORE flagging anything as MISSING, you MUST search the      ║
║  narration script for that beat. Cite the search you did.                 ║
║                                                                            ║
║  COMMON FALSE POSITIVE: you skim the narration and miss a beat that's     ║
║  actually present in different words. Example:                            ║
║    Wiki says: "Reed fires sonic gun to remove symbiote"                   ║
║    Narration S4: "Reed grabbed his sonic gun and fired"                   ║
║    → THIS IS PRESENT. Do NOT flag as missing.                             ║
║                                                                            ║
║  Process for each potential missing beat:                                  ║
║    Step 1. Extract 2-3 KEYWORDS from the wiki beat (entities + actions)   ║
║    Step 2. Scan ALL 13 scenes for any of those keywords                   ║
║    Step 3. If ANY keyword appears → beat is PRESENT (do NOT flag)         ║
║    Step 4. Only if NO keyword found → flag as missing + cite your search ║
╚══════════════════════════════════════════════════════════════════════════╝

Return STRICT JSON only:
{
  "issues": [
    {"scene_id": 1, "claim": "Ben stormed the Baxter Building", "wiki_says": "Ben entered Reed's lab calmly to discuss anniversary", "severity": "high"}
  ],
  "missing_beats": [
    {
      "beat": "Lizard's machine that extracts symbiote",
      "keywords_searched": ["machine", "extract", "separate", "lizard"],
      "scenes_checked": "S1-S13: no scene mentions machine/extract/separate",
      "verified_absent": true
    }
  ]
}

RULES:
  • Flag a scene when it CONTRADICTS the wiki (substituted character, wrong
    action, wrong order). Stylistic phrasing differences are FINE.
  • ALSO flag a scene when it states a FACT or EMOTION that is NOT supported by
    the wiki plot — invented drama. Examples to flag: "rage consumed him",
    "the city watched in fear", "tongue extended mocking them" when no such
    detail appears in the plot. Grounded pronouns/connectives ("he", "then",
    "meanwhile") are FINE; invented events/feelings are NOT.
  • STATE / POSSESSION TRACKING (flag as high severity): read the scenes IN ORDER
    and track who holds the symbiote / who is transformed at each step. Flag a
    scene that asserts a state BEFORE the wiki says it becomes true:
      ✗ "Now fully rebonded, Ben…" while an earlier/later scene says the Lizard
        still has the symbiote — the rebond only happens AFTER the villain's attack.
      ✗ The opening hook saying a character did something they only do LATER
        ("Ben set it free") before the scene where they discover it.
  • DUPLICATE EVENT (flag): two scenes narrating the SAME story beat (e.g. both
    say the symbiote rebonds with Ben). Keep one; the other must advance the story.
  • INVENTED MECHANISM (flag): a device/object/location not in the wiki ("a machine
    to rip the symbiote away", "a sewer lab") when the wiki states a simpler fact
    ("bonded with the symbiote himself").
  • If everything is canonical and grounded: {"issues": [], "missing_beats": []}"""


def _wiki_cross_check(
    parsed: dict, comic_context: dict,
    *, model: str | None, progress: Callable[[str], None] | None,
) -> list[str]:
    """Phase E: cross-check narration against wiki plot_summary (ground truth).
    Returns list of issue strings — empty if narration is canonical.
    """
    log = progress or (lambda _msg: None)
    plot = (comic_context.get("plot_summary") or "").strip()
    arc = (comic_context.get("summary", {}) or {}).get("story_arc", "").strip()
    if not plot and not arc:
        log("[stage4]   phase E: no wiki plot_summary available — skipping cross-check")
        return []

    # Skip the teaser intro scene — it is a deliberately speculative "Ever
    # wonder...?" hook, not a panel/plot-grounded claim, so it must not be
    # cross-checked against the canonical plot.
    scenes = [s for s in (parsed.get("scenes") or []) if not s.get("is_intro")]
    if not scenes:
        return []

    nar_lines = []
    for s in scenes:
        sid = s.get("scene_id", "?")
        text = str(s.get("text", "")).strip()
        nar_lines.append(f"S{sid}: {text}")

    # Cap wiki to stay within prompt budget. For a multi-issue saga, LABEL each
    # issue's canon plot so a beat is checked against ITS issue (per-issue grounding).
    if comic_context.get("is_arc") and comic_context.get("issues"):
        parts = []
        for it in comic_context["issues"]:
            p = (it.get("plot_summary") or "").strip()
            if p:
                parts.append(f"=== {it['label']} (canon) ===\n{p}")
        wiki_text = "\n\n".join(parts)[:8000] if parts else (((arc + "\n\n" + plot) if arc else plot)[:6000])
    else:
        wiki_text = ((arc + "\n\n" + plot) if arc else plot)[:6000]

    user = (
        "CANONICAL WIKI PLOT (ground truth):\n"
        f"{wiki_text}\n\n"
        "NARRATION SCRIPT (audit each scene):\n"
        + "\n".join(nar_lines) + "\n\n"
        "Return JSON {\"issues\": [...], \"missing_beats\": [...]}."
    )

    log("[stage4] phase E — wiki cross-check (reasoning chain)…")
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, mdl = call_with_chain(
            system=_WIKI_CROSS_CHECK_SYSTEM,
            user=user,
            models=chain,
            max_tokens=3000,
            progress=progress,
            label="wiki-check",
            validator=lambda c: '"issues"' in c,
        )
    except RuntimeError as exc:
        log(f"[stage4]   wiki cross-check chain failed — skipping: {exc}")
        return []

    parsed_wc = _extract_json(raw)
    if not isinstance(parsed_wc, dict):
        return []
    issues = parsed_wc.get("issues") or []
    missing = parsed_wc.get("missing_beats") or []

    # Layer 2: code-side keyword post-verify. LLM Phase E often false-positives
    # "missing" beats that ARE in narration with different wording. Before
    # accepting a missing_beat claim, scan narration for keywords from the beat.
    # If ANY keyword appears in narration → silently drop (false positive).
    narration_text = " ".join(
        str(s.get("text", "")) for s in scenes
    ).lower()

    def _keyword_present_in_narration(beat_text: str, llm_kws: list[str] | None) -> tuple[bool, str]:
        """Return (is_present_in_narration, kw_that_matched). Uses both
        LLM-claimed keywords_searched and code-extracted nouns/verbs from beat."""
        # Words to skip — too generic, will false-match anything
        stop = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "with",
                "his", "her", "their", "by", "for", "from", "as", "is", "was",
                "this", "that", "these", "those", "be", "been", "being", "have",
                "has", "had", "do", "does", "did", "would", "could", "should",
                "scene", "wiki", "narration", "comic", "beat", "story", "panel",
                "page", "all", "any", "some", "after", "before", "out", "up",
                "down", "into", "than", "then", "now", "also", "but", "so",
                "if", "not", "no", "yes", "him", "she", "he", "it", "they",
                "we", "you", "i", "me", "my", "our", "your"}
        # 1. LLM-provided keywords
        kws_to_check = set()
        if llm_kws:
            for k in llm_kws:
                w = str(k).lower().strip(",.!?:;\"'")
                if w and len(w) > 2 and w not in stop:
                    kws_to_check.add(w)
        # 2. Extract from beat text too (catch case LLM didn't provide kws)
        import re
        for tok in re.findall(r"\b[a-zA-Z]{4,}\b", beat_text.lower()):
            if tok not in stop:
                kws_to_check.add(tok)
        # Check each keyword
        for kw in kws_to_check:
            if kw in narration_text:
                return True, kw
        return False, ""

    out: list[str] = []
    suppressed = 0
    for iss in issues:
        if not isinstance(iss, dict):
            continue
        sid = iss.get("scene_id", "?")
        claim = str(iss.get("claim", ""))[:100]
        wiki_says = str(iss.get("wiki_says", ""))[:200]
        severity = str(iss.get("severity", "high"))
        if severity.lower() not in ("high", "critical"):
            continue
        out.append(f"scene {sid} [{severity}]: {claim!r} vs wiki: {wiki_says}")

    for beat in missing:
        if isinstance(beat, dict):
            beat_text = str(beat.get("beat", ""))
            llm_kws = beat.get("keywords_searched") or []
        else:
            beat_text = str(beat)
            llm_kws = []
        present, matched_kw = _keyword_present_in_narration(beat_text, llm_kws)
        if present:
            suppressed += 1
            log(f"[stage4]   phase E: suppressed false-positive "
                f"(beat={beat_text[:60]!r} found via kw={matched_kw!r})")
            continue
        out.append(f"MISSING canonical beat: {beat_text[:150]}")

    if suppressed:
        log(f"[stage4]   phase E: suppressed {suppressed} false-positive missing_beats")
    if out:
        log(f"[stage4]   phase E found {len(out)} wiki mismatch(es)")
        for issue in out[:5]:
            log(f"[stage4]     - {issue}")
    else:
        log("[stage4]   phase E: narration matches wiki canonical ✓")
    return out


def _retry_fix_with_wiki(
    parsed: dict,
    errors: list[str],
    comic_context: dict,
    model: str | None,
    progress: Callable[[str], None] | None,
    debug_dump: dict,
) -> dict:
    """Retry the writer with canonical wiki plot as PRIMARY ground truth.
    Used when wiki cross-check fails — the model needs to see the canonical story
    to correct missing/contradicting beats."""
    log = progress or (lambda _msg: None)
    err_block = "\n".join(f"- {e}" for e in errors[:30])
    prior = json.dumps(parsed, indent=2, ensure_ascii=False)
    plot = (comic_context.get("plot_summary") or "").strip()[:5000]
    arc = (comic_context.get("summary", {}) or {}).get("story_arc", "").strip()[:1500]

    user = (
        "Your previous narration draft failed canonical-story validation against the WIKI/FANDOM plot. "
        "Fix it. The wiki plot is GROUND TRUTH — your narration must match it factually.\n\n"
        + (f"CANONICAL STORY ARC:\n{arc}\n\n" if arc else "")
        + f"CANONICAL FULL PLOT (use this as your primary source of truth):\n{plot}\n\n"
        f"VALIDATION ERRORS (fix every one):\n{err_block}\n\n"
        + _saga_clarity_block(comic_context)
        + f"HARD RULES (these don't change between retries):\n"
        f"- Connective whitelist (scene 2+ MUST start with one): {', '.join(_CONNECTIVES)}.\n"
        f"- Scene 1 (hook): {_HOOK_MIN_WORDS}-{_HOOK_MAX_WORDS} words, connective MUST be null.\n"
        f"- Scenes 2+: {_SCENE_MIN_WORDS}-{_SCENE_MAX_WORDS} words (punch lines may be as short as {_SCENE_MIN_WORDS}; NO scene over {_SCENE_MAX_WORDS}).\n"
        f"- Total: {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} words (HARD ceiling — calm 1.1 pace). 12-14 scenes.\n"
        f"- ONE EVENT PER SENTENCE. If a 'narrates multiple events' error is listed, "
        f"keep ONLY that beat's single most important action as a punchy line and "
        f"drop the other clauses — do NOT add a scene (one beat → one scene).\n"
        f"- ≥{_MIN_PUNCH_SCENES} sentences must be ≤{_PUNCH_MAX_WORDS} words (punch). "
        f"Tighten long scenes into short single-event lines; never pad short ones.\n"
        f"- ANCHOR every claim to the canonical wiki plot above. Do NOT invent.\n"
        f"- Include missing canonical beats (anniversary, Sue Storm, Lizard's machine, etc.) if flagged.\n"
        f"- CRITICAL — NO two consecutive scenes may restate the same beat. If a "
        f"'repeat the same content' error is listed, REWRITE the LATER scene to ADVANCE "
        f"the action with completely fresh wording — do NOT reuse the repeated nouns/"
        f"adjectives (e.g. don't say 'fully corrupted Thing' / 'monster' twice). Keep both "
        f"scenes' distinct events and their page_ref/panel_ref, just rephrase so nothing repeats.\n\n"
        f"PRIOR DRAFT (rewrite freely — don't be afraid to restructure):\n{prior}\n\n"
        "Return ONLY the corrected JSON in the same schema."
    )
    log(f"[stage4]   retry-with-wiki prompt: {len(user)} chars")
    chain = [model] if model else list(CREATIVE_LLM_MODELS)

    def _has_scenes(c: str) -> bool:
        p = _extract_json(c)
        return isinstance(p, dict) and isinstance(p.get("scenes"), list) and len(p["scenes"]) > 0

    try:
        raw, mdl_used = call_with_chain(
            system=_WRITE_SYSTEM,
            user=user,
            models=chain,
            max_tokens=6000,
            progress=progress,
            label="retry-wiki",
            validator=_has_scenes,
        )
    except RuntimeError as exc:
        log(f"[stage4]   retry-wiki chain exhausted — keeping prior draft ({exc})")
        debug_dump["retry_wiki_failed"] = str(exc)
        return parsed

    extracted = _extract_json(raw)
    if not isinstance(extracted, dict) or not extracted.get("scenes"):
        log("[stage4]   retry-wiki: unparseable JSON — keeping prior draft")
        return parsed
    debug_dump["retry_wiki_model"] = mdl_used
    return extracted
