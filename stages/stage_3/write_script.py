"""Stage 3 narration writer: outline_beats -> build_glossary -> write_scenes -> validate."""
import json
import random
import re
import statistics
from pathlib import Path
from typing import Callable

from config import CREATIVE_LLM_MODELS, OPENROUTER_MODEL
from .modes import MODES_BY_KEY
from .schema import Beat, CharacterEntry, Glossary, Narration, Scene
from ._llm import call_with_chain


_TARGET_WORDS_MIN = 240  # Channel mean 242 (219-video sample), median 241
_TARGET_WORDS_MAX = 280
_WORDS_PER_SEC = 4.0     # Channel mean 3.9 wps

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
    """Run outline -> glossary -> write -> validate (+ 1 retry)."""
    if mode not in MODES_BY_KEY:
        raise ValueError(f"Unknown mode: {mode!r}. Valid: {sorted(MODES_BY_KEY)}")

    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}

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
    dump["validation_pass1"] = errors

    if errors:
        log(f"[stage4]   validation found {len(errors)} issue(s): {errors[:3]}…")
        log("[stage4]   retrying once with fix prompt…")
        parsed = _retry_fix(parsed, errors, model, progress, dump)
        errors = _validate(parsed, valid_pages, valid_beat_ids)
        dump["validation_pass2"] = errors
        if errors:
            critical = [e for e in errors if _is_critical_error(e)]
            soft = [e for e in errors if not _is_critical_error(e)]
            if critical:
                raise RuntimeError(
                    "Stage 4 validation failed after retry (critical):\n  - "
                    + "\n  - ".join(critical)
                )
            log(f"[stage4]   ⚠ accepting narration despite {len(soft)} soft issue(s):")
            for e in soft:
                log(f"[stage4]     - {e}")

    final_model = write_model or gloss_model or beats_model or (model or OPENROUTER_MODEL)
    return _to_narration(parsed, beats, glossary, mode, final_model)


_OUTLINE_SYSTEM = """You are PanelOutliner. Your job is to extract the dramatic skeleton of a comic story into 5-8 named beats.

You DO NOT write narration prose yet. You produce structured beats only.

Each beat has:
- function: COLD_OPEN | SETUP | COMPLICATION | ESCALATION | MIDPOINT | CLIMAX | LANDING
- name: 3-7 words naming the beat ("Ben gets the symbiote")
- page_refs: which input pages feed this beat
- key_panels: 1-3 strongest visual moments [{"page": int, "panel": int}]
- summary: ONE factual sentence of what happens (no narration voice yet)
- characters_active: who is on stage in this beat

Beats are in dramatic order (which is usually but not always chronological). The first beat is COLD_OPEN — the moment that should hook the viewer. The last beat is LANDING — the line that pays it off. Pick beats that compress the story to its 3-5 most cinematic page sequences. Skip filler.

Constraints from successful 60-second comic Shorts (sample of 30 ComicsUnlocked videos):
- 10-12 beats preferred. Channel-benchmark Shorts average 12 sentences
  (across 219 analyzed videos) — beats map 1:1 to scenes, so we need a
  matching beat count to hit the 240-280 word channel target AND give
  Stage 5 enough visual changes for the 35+ caption-chunk cuts.
- Each beat covers 1-4 input pages. Don't spread one beat across the whole comic.
- COLD_OPEN beat must contain a concrete visual action, not exposition.
- LANDING must be a payoff, twist, or final image — never a CTA or question.

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

    user = (
        f"COMIC CONTEXT:\n{_ctx_block(comic_context)}\n\n"
        f"STORY PAGES (full per-panel detail):\n{_pages_block_full(story_pages)}\n\n"
        f"NARRATION MODE: {mode} — {mode_info.description}\n"
        + (f"HOOK HINT: {hook_hint}\n" if hook_hint else "")
        + "\n"
        f"TASK: Extract 10-12 beats that build a {mode} arc from this story. "
        f"Choose beats that compress the comic into its most cinematic moments. "
        f"Reference real page numbers from the input.\n\n"
        f"Return JSON in this exact shape:\n"
        f"{{\n"
        f'  "beats": [\n'
        f'    {{"id": 1, "function": "COLD_OPEN", "name": "...", "page_refs": [3], '
        f'"key_panels": [{{"page": 3, "panel": 0}}], "summary": "...", "characters_active": ["..."]}},\n'
        f"    ...\n"
        f"  ]\n"
        f"}}"
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
     • 1-2 long setup sentences (23-30 words) — exposition/context
     • 1 outro credit "The comic is X" (5-8 words)
   - **ONE event per sentence.** NOT "X happens while Y happens but Z is also true."
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
   - characters who don't appear in the data
   - events that didn't happen on the cited page
   When a panel implies meaning that the data doesn't make explicit, write what's literally happening, not your interpretation. Reread the panel description before each scene.

   ANTI-PATTERN EXAMPLE (do NOT do this):
   Panel data: "Ben says 'YOU FORGOT OUR ANNIVERSARY, REED.' Reed replies 'considering what this date means for you…'"
   BAD: "Ben confronts Reed about forgetting their anniversary."  (sounds romantic, misleads)
   GOOD: "Ben confronts Reed for forgetting the anniversary of the accident that turned him into the Thing."  (anchored to what the comic actually means)

7) LENGTH BUDGET — STRICT, CHANNEL-CALIBRATED
   - **10-12 scenes** total (channel average 12 sentences across 219 videos).
   - **240-280 words total** — non-negotiable lower bound. Channel mean is 242
     words. If your draft is under 240, ADD more scenes (split a complex beat
     in two) or expand consequence/reaction clauses. Do NOT pad with filler —
     add substance.
   - Target 60 seconds spoken at ~4.0 words/second.

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
        "no scenes",          # zero scenes returned — pipeline can't proceed
        "page_ref=",          # scene references a page that doesn't exist
    )
    return any(marker in m for marker in critical_markers)


def _validate(parsed: dict, valid_pages: set[int], valid_beat_ids: set[int]) -> list[str]:
    errors: list[str] = []
    scenes = parsed.get("scenes") or []
    if not scenes:
        return ["no scenes in output"]
    if not (9 <= len(scenes) <= 14):
        errors.append(f"scene count {len(scenes)} not in 9..14")

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
    return errors


def _retry_fix(
    parsed: dict,
    errors: list[str],
    model: str | None,
    progress: Callable[[str], None] | None,
    debug_dump: dict,
) -> dict:
    log = progress or (lambda _msg: None)
    err_block = "\n".join(f"- {e}" for e in errors)
    prior = json.dumps(parsed, indent=2, ensure_ascii=False)

    user = (
        f"Your previous narration draft failed validation. Fix ONLY the listed problems and return the corrected JSON in the same shape.\n\n"
        f"VALIDATION ERRORS:\n{err_block}\n\n"
        f"HARD RULES:\n"
        f"- Connective whitelist (scene 2+ MUST start with one): {', '.join(_CONNECTIVES)}.\n"
        f"- Scene 1 (hook): {_HOOK_MIN_WORDS}-{_HOOK_MAX_WORDS} words, connective MUST be null.\n"
        f"- Scenes 2+: {_SCENE_MIN_WORDS}-{_SCENE_MAX_WORDS} words. Last scene may dip to 8.\n"
        f"- Total: 230-290 words ({_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} ideal). 10-12 scenes.\n\n"
        f"PRIOR DRAFT (fix in place, keep beat_id/page_ref/panel_ref unchanged unless they were flagged):\n{prior}\n\n"
        f"Return ONLY the corrected JSON."
    )
    log(f"[stage4]   retry prompt: {len(user)} chars")
    chain = [model] if model else list(CREATIVE_LLM_MODELS)

    def _has_scenes_retry(c: str) -> bool:
        p = _extract_json(c)
        return isinstance(p, dict) and isinstance(p.get("scenes"), list) and len(p["scenes"]) > 0

    try:
        raw, mdl_used = call_with_chain(
            system=_WRITE_SYSTEM,
            user=user,
            models=chain,
            max_tokens=5000,
            progress=progress,
            label="retry",
            validator=_has_scenes_retry,
        )
    except RuntimeError as exc:
        log(f"[stage4]   retry chain exhausted — falling back to original draft ({exc})")
        if debug_dump is not None:
            debug_dump["phase_c_retry_error"] = str(exc)
        return parsed

    if debug_dump is not None:
        debug_dump["phase_c_retry_raw"] = raw
        debug_dump["phase_c_retry_model"] = mdl_used
    out = _extract_json(raw)
    if not out or not isinstance(out.get("scenes"), list):
        log("[stage4]   retry returned unparseable JSON — falling back to original draft")
        if debug_dump is not None:
            debug_dump["phase_c_retry_unparseable"] = raw[:500]
        return parsed
    return out


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
