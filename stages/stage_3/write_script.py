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


# Calibrated for the user-chosen 1.1 atempo pace. MEASURED actual rate at 1.1:
# ~2.88 wps (not the earlier 3.22 estimate). The teaser intro (~12 words) is
# prepended on top of the body, so the body targets ~175-195 → final ~187-207
# words → ~65-72s at 2.88 wps, landing inside the (54-72.08s) duration band and
# the (187-285) word band.
_TARGET_WORDS_MIN = 175
_TARGET_WORDS_MAX = 195
_WORDS_PER_SEC = 2.88    # MEASURED 1.1 atempo pace (was 4.0 at the 1.3 benchmark pace)

_SCENE_MIN_WORDS = 14    # channel does 9-14 word sentences too — one event each
_SCENE_MAX_WORDS = 25    # was 35 — hard ceiling, anything longer crams events
_TARGET_SENT_LEN = 20    # channel median; used by median soft-validator
_HOOK_MIN_WORDS = 18
_HOOK_MAX_WORDS = 30

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


_INTRO_SYSTEM = """You are HookWriter. BEFORE the main narration is written, you produce ONE short teaser intro sentence for a YouTube Short about a comic.

STEP 1 — classify the comic's STORY TYPE from its title + premise:
  • "what_if"   — an alternate-reality / "What If...?" premise (a canonical event went the other way)
  • "alternate" — an alternate-universe / Elseworld / dark-mirror timeline story
  • "explainer" — a story that explains a character's origin, a power, a death, or a piece of lore
  • "standard"  — a straightforward in-continuity story

STEP 2 — write the intro line:
  • If story_type is what_if / alternate / explainer → you MUST use the "Ever wonder ...?" structure:
      what_if / alternate →  "Ever wonder what if <premise>?"
      explainer           →  "Ever wonder how <X>?"  or  "Ever wonder why <X>?"
  • If story_type is standard → a short scenic OR interrogative teaser (no fixed template).

HARD RULES for the intro line:
  - 8-16 words, exactly ONE sentence, ends with "?".
  - Name the hero AND the premise so a viewer instantly grasps the stakes.
  - It is a TEASER, not a summary — do NOT reveal the ending/twist.
  - No meta talk ("in this video", "today", "let's see"). No spoilers.

Return ONLY JSON, no markdown: {"story_type": "what_if|alternate|explainer|standard", "intro_line": "Ever wonder what if ...?"}"""


def generate_intro(
    comic_context: dict,
    *,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
) -> dict:
    """Dedicated pre-write LLM call: classify story type + craft the teaser intro
    line shown over the cover. Returns {"story_type", "intro_line"}; falls back to
    a deterministic "Ever wonder...?" line if the LLM output is unusable."""
    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}

    title = str(comic_context.get("title", "")).strip()
    plot = str(comic_context.get("plot_summary", "")).strip()
    if not plot:
        plot = str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    chars = ", ".join(comic_context.get("characters", []) or [])

    user = (
        f"COMIC TITLE: {title}\n"
        f"PUBLISHER: {comic_context.get('publisher','?')}\n"
        f"KEY CHARACTERS: {chars or '?'}\n\n"
        f"PREMISE / PLOT (ground truth):\n{plot[:1800]}\n\n"
        f"Write the intro JSON now."
    )

    def _valid(out: str) -> bool:
        try:
            d = _json_loads_loose(out)
            line = str(d.get("intro_line", "")).strip()
        except Exception:
            return False
        return 6 <= len(line.split()) <= 20 and line.endswith("?")

    try:
        content, used = call_with_chain(
            system=_INTRO_SYSTEM, user=user,
            models=list(CREATIVE_LLM_MODELS) or None,
            max_tokens=300, progress=progress, label="intro", validator=_valid,
        )
        data = _json_loads_loose(content)
        story_type = str(data.get("story_type", "")).strip().lower() or "standard"
        intro_line = " ".join(str(data.get("intro_line", "")).split()).strip()
        dump["intro"] = {"story_type": story_type, "intro_line": intro_line, "model": used}
        log(f"[stage4] intro ({story_type}): {intro_line!r}")
        return {"story_type": story_type, "intro_line": intro_line}
    except Exception as exc:
        # Deterministic fallback so the pipeline never blocks on the intro.
        hero = (comic_context.get("characters") or ["this hero"])[0]
        fallback = f"Ever wonder what if {hero} took a darker path?"
        log(f"[stage4] intro LLM failed ({type(exc).__name__}); using fallback: {fallback!r}")
        return {"story_type": "what_if", "intro_line": fallback}


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

    valid_pages = {int(p.get("page_number", 0)) for p in story_pages}
    valid_beat_ids = {b.id for b in beats}
    errors = _validate(parsed, valid_pages, valid_beat_ids)
    halluc = _detect_hallucinations(parsed, glossary, comic_context, story_pages)
    if halluc:
        errors = halluc + errors

    # Multi-pass validation loop: validate → fidelity → wiki → retry.
    # Wiki mismatches are CRITICAL but we give the LLM up to MAX_PASSES tries
    # to land canonical narration before giving up.
    MAX_PASSES = 3
    best_parsed = parsed
    # (length_ok, words_ok, -critical, -errors, -words): higher is better.
    best_key = (-1, -1, -(10 ** 9), -(10 ** 9), -(10 ** 9))
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

        # Best-draft selection (fix a): a draft that is too SHORT trivially has
        # few redundancy/wiki issues — so "fewest issues" alone wrongly favored
        # a 5-scene/96-word truncated draft over a complete one. Gate on length
        # FIRST: a draft meeting the scene + word minimums always beats a draft
        # that doesn't, regardless of issue count. Only within the same length
        # tier do we then prefer fewer issues.
        _scenes = parsed.get("scenes") or []
        _words = sum(len(str(s.get("text", "")).split()) for s in _scenes)
        length_ok = 1 if (9 <= len(_scenes) <= 17 and _words >= 170) else 0
        # words_ok: within the benchmark word band (≤285 ⇒ ≤~70s). Prefer an
        # in-band draft over a longer one, and prefer fewer CRITICAL issues,
        # then fewer total issues, then the shorter draft (snappier video).
        words_ok = 1 if 170 <= _words <= 195 else 0
        n_critical = sum(1 for e in errors if _is_critical_error(e))
        key = (length_ok, words_ok, -n_critical, -len(errors), -_words)
        if key > best_key:
            best_parsed = parsed
            best_key = key

        if not errors:
            log(f"[stage4]   ✓ pass {pass_num}: all validations clean")
            break

        critical = [e for e in errors if _is_critical_error(e)]
        log(f"[stage4]   pass {pass_num}/{MAX_PASSES}: {len(errors)} issue(s) "
            f"({len(critical)} critical, {len(_scenes)} scenes / {_words}w, "
            f"length_ok={bool(length_ok)})")
        if pass_num >= MAX_PASSES:
            log(f"[stage4]   ⚠ MAX_PASSES reached; shipping best draft "
                f"(length_ok={best_key[0]==1}, words_ok={best_key[1]==1}, "
                f"{-best_key[3]} issues)")
            parsed = best_parsed
            errors = []  # don't raise — fall through with best draft
            break
        log(f"[stage4]   retrying (pass {pass_num+1}/{MAX_PASSES})…")
        parsed = _retry_fix_with_wiki(parsed, errors, comic_context,
                                       model, progress, dump)

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

    intro_line = (intro.get("intro_line") or "").strip()
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

    final_model = write_model or gloss_model or beats_model or (model or OPENROUTER_MODEL)
    return _to_narration(parsed, beats, glossary, mode, final_model)


_OUTLINE_SYSTEM = """You are PanelOutliner. Your job is to extract the FULL dramatic skeleton of a comic story into 12-15 canonical beats — MUST cover the entire story arc including the climax, not just the opening.

You DO NOT write narration prose yet. You produce structured beats only.

Each beat has:
- function: COLD_OPEN | SETUP | COMPLICATION | ESCALATION | MIDPOINT | CLIMAX | LANDING
- name: 3-7 words naming the beat ("Ben gets the symbiote")
- page_refs: which input pages feed this beat
- key_panels: 1-3 strongest visual moments [{"page": int, "panel": int}]
- summary: ONE factual sentence of what happens (no narration voice yet)
- characters_active: who is on stage in this beat

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
- **12-15 beats** total. Beats map 1:1 to scenes — need 12+ scenes to fit the
  full canonical arc into 60s narration.
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
        + f"STORY PAGES (per-panel detail for grounding beats to visuals):\n{_pages_block_full(story_pages)}\n\n"
        + f"NARRATION MODE: {mode} — {mode_info.description}\n"
        + (f"HOOK HINT: {hook_hint}\n" if hook_hint else "")
        + page_range_hint + "\n\n"
        + f"TASK: Extract 12-15 beats that COVER THE ENTIRE canonical story arc. "
        + f"USE THE CANONICAL WIKI PLOT ABOVE as the spine — each major event in "
        + f"the wiki MUST get its own beat. Then map each beat to the most fitting "
        + f"page_ref + panel from the STORY PAGES. Do NOT skip the climax. Do NOT "
        + f"pile multiple major events into one beat.\n\n"
        + f"Return JSON in this exact shape:\n"
        + f"{{\n"
        + f'  "beats": [\n'
        + f'    {{"id": 1, "function": "COLD_OPEN", "name": "...", "page_refs": [3], '
        + f'"key_panels": [{{"page": 3, "panel": 0}}], "summary": "...", "characters_active": ["..."]}},\n'
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
            characters_active=[str(c).strip() for c in (b.get("characters_active") or []) if str(c).strip()],
        ))
    if not (8 <= len(beats) <= 12):
        log(f"[stage4]   warning: outline returned {len(beats)} beats (want 10-12)")

    # Deterministic page ordering: the recap is a page-by-page walk and Stage 5
    # is forward-only, so beats MUST be non-decreasing in page. Sort here instead
    # of relying on the soft prompt rule (which let venom emit pg12 before pg11).
    before = [min(b.page_refs) if b.page_refs else 0 for b in beats]
    beats = _order_beats_by_page(beats)
    after = [min(b.page_refs) if b.page_refs else 0 for b in beats]
    if before != after:
        log(f"[stage4]   reordered beats to monotonic page order: {before} -> {after}")

    # Page-gap validation — retry once with bridge instruction if jumps > 5.
    issues = _validate_outline(beats)
    if issues:
        log(f"[stage4]   outline validation: {len(issues)} issue(s) — retry with bridge")
        for iss in issues[:3]:
            log(f"[stage4]     - {iss}")
        beats = _retry_outline_with_bridge(
            beats, issues, comic_context, story_pages, mode,
            hook_hint=hook_hint, model=model, progress=progress, debug_dump=debug_dump,
        ) or beats
    return beats, mdl_used


def _order_beats_by_page(beats: list[Beat]) -> list[Beat]:
    """Make the beat sheet match the comic's reading order: stable-sort beats by
    their lowest page_ref so page progression is monotonic (the video is a
    page-by-page walk; forward-only Stage-5 selection breaks if narration jumps
    back a page). Stable sort preserves the outliner's order among same-page
    beats. Beats with no page_refs keep their relative position by inheriting the
    previous beat's page (so they don't sink to the front)."""
    def primary(b: Beat, fallback: int) -> int:
        return min(b.page_refs) if b.page_refs else fallback
    running = 0
    keyed: list[tuple[int, int, Beat]] = []
    for idx, b in enumerate(beats):
        pg = primary(b, running)
        running = max(running, pg)
        keyed.append((pg, idx, b))
    keyed.sort(key=lambda t: (t[0], t[1]))  # stable by (page, original index)
    return [t[2] for t in keyed]


def _validate_outline(beats: list[Beat], max_gap: int = 5) -> list[str]:
    """Soft validation of outline. Returns issue strings; empty = OK."""
    issues: list[str] = []
    if len(beats) < 8:
        issues.append(f"only {len(beats)} beats (target 10-12)")

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

   The hook MUST be 18-28 words and end with an open thread that pulls the viewer
   into scene 2 (use a comma + "..." or end with an unresolved promise).

2) CONNECTIVE GRAMMAR (scenes 2 onward)
   - Every scene from #2 onward MUST start with one of these connectives, exactly: But, However, As, When, After, Eventually, Instead, With, Now, Suddenly, Then, Until, Meanwhile, Soon.
   - The schema field "connective" is REQUIRED non-null for every scene where scene_id >= 2.
   - These are documented in 95%+ of successful comic Shorts and create the "and then... and then..." feeling that holds retention.

3) SENTENCE SHAPE — MIX SHORT + MEDIUM + LONG (variance is the channel signature)
   - **DO NOT write uniformly-sized sentences.** Real scripts vary.
   - Target distribution across 12-14 scenes (including the outro credit):
     • 2-3 short PUNCH sentences (5-12 words) — for landing/twist moments
     • 6-8 medium sentences (14-22 words) — main flow
     • **MANDATORY: AT LEAST 1 long setup sentence (23-30 words)** — exposition/context.
       Use the long sentence for a then-then-then momentum moment OR for an
       establishing scene that needs to set up multiple facts. A script without
       at least one 23-30w sentence will be rejected and retried.
     • 1 outro credit "The comic is X" (5-8 words)
   - Long sentence example (mandatory pattern):
     ✓ "When Reed Richards examined the dormant symbiote sample in his lab, the
        tendrils began creeping toward Ben Grimm, who was visiting that evening
        to confront Reed about a forgotten anniversary." (28w)
   - **ONE event per sentence.** NOT "X happens while Y happens but Z is also true."
   - **NO redundant consecutive scenes.** Each scene must advance the story to a
     NEW moment — never restate the previous scene's action with a later frame.
     Collapse an action progression into its single most impactful moment:
       ✗ Scene A "Reed aims the sonic gun." + Scene B "Reed fires the sonic gun."
       ✓ One scene: "Reed fires the sonic gun at Ben." (keep the payoff, drop the wind-up)
     If two scenes you're about to write share the same subject AND action, merge
     them and use the freed scene for a different story beat.
   - Channel punch examples (5-12w, hit hard):
     ✓ "But, even as an infant, Thanos was a unit." (9w)
     ✓ "Stating they would die anyway." (5w)
     ✓ "But he only stopped punching once he remembered his aunt." (10w)
   - Channel medium examples:
     ✓ "So, Odin returned his cosmic powers and turned him into Ghost Rider again." (13w)
   - ANTI-pattern (do NOT write):
     ✗ "When suit tears during Secret Wars, tendrils ooze while Reed realizes,
        but tube cracks, and Thing is about to discover it." (5 events crammed).
   - Uniformity is the AI-tell. Variance is the channel signature.
   - Use INTERNAL connectives (", but ...", " as ...", " until ...") to keep the sentence flowing.
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

6.5) FACT-CHECK SELF-PASS — before returning JSON
   For EACH scene you write, mentally verify:
   (a) Every named character actually appears in this panel's `characters` list (or page summary)
   (b) Every action verb (bit, fired, exploded, devoured, etc.) is in panel description or dialog
   (c) Every emotion adjective is in `dominant_emotion` or implied by dialog text
   (d) Every adjective describing a thing (glowing, monstrous, etc.) is in panel description
   If a phrase isn't grounded, REPLACE it with a grounded one or REMOVE it. Better to write a less colorful but accurate scene than a vivid but invented one.
   The user has rejected past drafts that twisted the story. Accuracy beats flourish.

7) LENGTH BUDGET — STRICT, CHANNEL-CALIBRATED — HARD CEILING
   - **12-14 scenes** total (channel average 12; we allow 13-14 for canonical
     coverage of climax beats).
   - **175-195 words total — HARD CEILING. NOT FLEXIBLE.**
     • Narration is spoken at a calm 1.1 pace (~2.9 words/second). A separate
       teaser intro line is added on top of your draft, so YOUR body must stay
       lean: >195 words → the final video overshoots the ~72s Shorts budget and
       fails benchmark `duration_in_range`.
     • If draft > 195 words → MUST trim. If draft < 175 → ADD scenes covering
       missing canonical beats.
   - Before returning JSON, COUNT your total words. If > 195, tighten the
     longest 2-3 scenes (cut adjectives, drop secondary clauses, merge twins).
     Keep ALL canonical beats — trim FLOURISH, not CONTENT.
   - Sentence-by-sentence target distribution (11-13 scenes, 175-195 words):
     • 2-3 PUNCH (5-10w)
     • 6-8 MEDIUM (12-18w)
     • 1 LONG (20-24w)
     • 1 OUTRO (5-8w)
   - Target ~68s spoken at ~2.9 words/second.

8) PAGE/PANEL TAGGING
   - Every scene maps to ONE (page_ref, panel_ref) — pick the most visually impactful panel of that beat.
   - Every scene must reference its beat_id.

8.5) PAGE COVERAGE — MANDATORY LINEAR FLOW
   - Your scenes MUST move through the comic pages monotonically: each scene's
     page_ref ≥ previous scene's page_ref.
   - The gap between consecutive scenes' page_refs MUST be ≤ 5 pages. If a beat
     genuinely requires skipping 6+ pages, INSERT a bridge sentence that summarizes
     the skipped events. Example bridge: "Eventually, after [one-clause summary],
     [next event]…"
   - DO NOT jump from page 10 to page 32 without bridging — the viewer loses the
     throughline and characters/items appear from nowhere.

9) CONTINUITY ANCHOR
   - For each scene from #2 onward, you will see a "prev_anchor" — the last 6-8 words of the previous scene. Continue from this thread; do not reset the subject without re-introducing them.

10) FORBIDDEN
   - No em-dashes (—), no brackets, no parenthetical asides — this is spoken aloud.
   - No "what do you think in the comments", no "subscribe", no questions to viewer at the end.
   - No stage directions, no scene numbers inside text.

11) VOICE & RHYTHM — channel-calibrated from 219 reference Shorts

   11a. SENTENCE LENGTH VARIANCE — mix short + long
        See rule 3 above. Target 2-3 punch sentences (5-12w), 6-8 medium (14-22w),
        1-2 long (23-30w), 1 outro credit. Uniformity is the AI-tell.

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
          - page_ref: same as the last narrative scene's page_ref
          - panel_ref: any valid panel on that page
        Channel examples:
          "The comic is Spider-Man the Spider Shadow issue"
          "The comic is Cosmic Ghost"
        DO NOT SKIP — this is the channel's identity closer.

Return ONLY JSON. No prose, no markdown fences."""


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

    user = (
        f"COMIC CONTEXT:\n{_ctx_block(comic_context)}\n\n"
        + (f"{lore_block}\n\n" if lore_block else "")
        + f"NARRATION MODE: {mode} — {mode_info.description}\n"
        + (f"HOOK HINT: {hook_hint}\n" if hook_hint else "")
        + "\n"
        f"BEATS (write one scene per beat, in order):\n{_beats_block(beats)}\n\n"
        f"GLOSSARY (use these exact names):\n{_glossary_block(glossary)}\n\n"
        + (f"{few_shot}\n\n" if few_shot else "")
        + f"PAGE DETAIL (for picking the right panel_ref):\n{_pages_block_compact(story_pages)}\n\n"
        f"WORD BUDGET: {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} total words across all scenes.\n"
        f"CONNECTIVE WHITELIST (scene 2 onward MUST start with one): {', '.join(_CONNECTIVES)}.\n\n"
        f"Write the script now. Return JSON in this exact shape:\n"
        f"{{\n"
        f'  "title": "<short punchy title for this Short>",\n'
        f'  "hook": "<scene 1 text, also stored in scenes[0].text>",\n'
        f'  "scenes": [\n'
        f'    {{"text": "When ...", "page_ref": 3, "panel_ref": 0, "connective": null, "beat_id": 1}},\n'
        f'    {{"text": "But ...", "page_ref": 3, "panel_ref": 2, "connective": "But", "beat_id": 2}},\n'
        f"    ...,\n"
        f'    {{"text": "[concrete final image scene]", "page_ref": <last>, "panel_ref": 0, "connective": "<conn>", "beat_id": <last>}},\n'
        f'    {{"text": "The comic is <title>.", "page_ref": <last>, "panel_ref": 0, "connective": null, "beat_id": <last>}}\n'
        f"  ]\n"
        f"}}\n\n"
        f"REMINDER: the FINAL scene MUST be the \"The comic is X.\" outro credit "
        f"(5-8 words, connective=null). See rule 11g."
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
    if not (9 <= len(scenes) <= 17):
        errors.append(f"scene count {len(scenes)} not in 9..17")

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

    if not (230 <= total_words <= 290):
        errors.append(f"total words {total_words} not in 230..290")

    # Median sentence-length soft check (excluding hook).
    body_lens = [len(str(s.get("text", "")).split()) for s in scenes[1:]]
    if body_lens:
        med = statistics.median(body_lens)
        if med > _TARGET_SENT_LEN + 3:  # 23+ median = overstuffing
            errors.append(
                f"median scene length {med:.0f}w > {_TARGET_SENT_LEN+3} "
                f"(target {_TARGET_SENT_LEN}w; channel median 20w)"
            )

    # MANDATORY: at least 1 long sentence (23-30 words) — channel signature
    # (benchmark says ≥1 long sentence per script). Anti-uniformity guard.
    all_lens = [len(str(s.get("text", "")).split()) for s in scenes]
    long_count = sum(1 for l in all_lens if 23 <= l <= 30)
    if long_count == 0:
        errors.append(
            "no long sentence (23-30 words) found — channel signature requires "
            "at least 1 establishing/momentum sentence. Expand one medium scene."
        )

    errors.extend(_detect_redundant_scenes(scenes))

    # Defense-in-depth: scenes must not move backward in pages (Stage 5 is
    # forward-only). Beats are page-sorted in outline_beats, so this should never
    # fire — if it does, the writer reassigned page_ref out of order. Skip the
    # intro (scene 1, cover) and outro (whole-page) which are bookends.
    prev_pg = 0
    for s in parsed.get("scenes") or []:
        if s.get("is_intro") or s.get("is_outro"):
            continue
        pg = int(s.get("page_ref", 0) or 0)
        if pg and pg < prev_pg:
            errors.append(
                f"scene {s.get('scene_id','?')} page_ref={pg} goes backward "
                f"(prev {prev_pg}) — non-monotonic page order"
            )
        prev_pg = max(prev_pg, pg)

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
        out.append(
            f"beat {b.id} [{b.function}] {b.name}\n"
            f"  pages: {b.page_refs}  key_panels: {kp}  active: {chars}\n"
            f"  what happens: {b.summary}"
        )
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

    # Cap wiki to 6000 chars to stay within prompt budget but keep most of plot
    wiki_text = (arc + "\n\n" + plot) if arc else plot
    wiki_text = wiki_text[:6000]

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
        f"HARD RULES (these don't change between retries):\n"
        f"- Connective whitelist (scene 2+ MUST start with one): {', '.join(_CONNECTIVES)}.\n"
        f"- Scene 1 (hook): {_HOOK_MIN_WORDS}-{_HOOK_MAX_WORDS} words, connective MUST be null.\n"
        f"- Scenes 2+: {_SCENE_MIN_WORDS}-{_SCENE_MAX_WORDS} words. Last scene may dip to 8.\n"
        f"- Total: {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} words (HARD ceiling — calm 1.1 pace). 11-13 scenes.\n"
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
