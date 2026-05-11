"""Stage 3 narration writer: outline_beats -> build_glossary -> write_scenes -> validate."""
import json
import re
from typing import Callable

from config import OPENROUTER_MODEL
from .modes import MODES_BY_KEY
from .schema import Beat, CharacterEntry, Glossary, Narration, Scene
from ._llm import call_with_chain


_TARGET_WORDS_MIN = 150
_TARGET_WORDS_MAX = 190
_WORDS_PER_SEC = 3.4

_SCENE_MIN_WORDS = 12
_SCENE_MAX_WORDS = 30
_HOOK_MIN_WORDS = 15
_HOOK_MAX_WORDS = 30

_CONNECTIVES = (
    "But", "However", "As", "When", "After", "Eventually", "Instead",
    "With", "Now", "Suddenly", "Then", "Until", "Meanwhile", "Soon",
)


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
            raise RuntimeError(
                "Stage 4 validation failed after retry:\n  - " + "\n  - ".join(errors)
            )

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
- 5-8 beats total. Fewer if the story is one tight sequence.
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
        f"TASK: Extract 5-8 beats that build a {mode} arc from this story. "
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
    raw, mdl_used = call_with_chain(
        system=_OUTLINE_SYSTEM,
        user=user,
        models=chain,
        max_tokens=2000,
        progress=progress,
        label="outline",
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
    if not (5 <= len(beats) <= 8):
        log(f"[stage4]   warning: outline returned {len(beats)} beats (want 5-8) — continuing")
    return beats, mdl_used


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
    raw, mdl_used = call_with_chain(
        system=_GLOSSARY_SYSTEM,
        user=user,
        models=chain,
        max_tokens=1200,
        progress=progress,
        label="glossary",
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

1) HOOK (scene 1)
   - 18-28 words, drops the viewer in mid-action.
   - Pick exactly ONE archetype:
     A. "After [setup], [Named Character] [does specific action], [twist]."
     B. "Why/How/What [question]?"
     C. "Everyone thinks [X], but [Y]."
     D. "In [time/place], [Named Character] [action]."
   - Must contain at least one canonical name and at least one verb.
   - NO floating fragments ("The war."). NO "In today's video", "Today we're looking at".

2) CONNECTIVE GRAMMAR (scenes 2 onward)
   - Every scene from #2 onward MUST start with one of these connectives, exactly: But, However, As, When, After, Eventually, Instead, With, Now, Suddenly, Then, Until, Meanwhile, Soon.
   - The schema field "connective" is REQUIRED non-null for every scene where scene_id >= 2.
   - These are documented in 95%+ of successful comic Shorts and create the "and then... and then..." feeling that holds retention.

3) SENTENCE SHAPE
   - Each scene = ONE compound sentence, 18-25 words. Median for the channel is 19.
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

7) LENGTH BUDGET
   - 5-8 scenes total.
   - 150-190 words total.
   - Target 58 seconds spoken at 3.4 words/second.

8) PAGE/PANEL TAGGING
   - Every scene maps to ONE (page_ref, panel_ref) — pick the most visually impactful panel of that beat.
   - Every scene must reference its beat_id.

9) CONTINUITY ANCHOR
   - For each scene from #2 onward, you will see a "prev_anchor" — the last 6-8 words of the previous scene. Continue from this thread; do not reset the subject without re-introducing them.

10) FORBIDDEN
   - No em-dashes (—), no brackets, no parenthetical asides — this is spoken aloud.
   - No "what do you think in the comments", no "subscribe", no questions to viewer at the end.
   - No stage directions, no scene numbers inside text.

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

    user = (
        f"COMIC CONTEXT:\n{_ctx_block(comic_context)}\n\n"
        + (f"{lore_block}\n\n" if lore_block else "")
        + f"NARRATION MODE: {mode} — {mode_info.description}\n"
        + (f"HOOK HINT: {hook_hint}\n" if hook_hint else "")
        + "\n"
        f"BEATS (write one scene per beat, in order):\n{_beats_block(beats)}\n\n"
        f"GLOSSARY (use these exact names):\n{_glossary_block(glossary)}\n\n"
        f"PAGE DETAIL (for picking the right panel_ref):\n{_pages_block_compact(story_pages)}\n\n"
        f"WORD BUDGET: {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} total words across all scenes.\n"
        f"CONNECTIVE WHITELIST (scene 2 onward MUST start with one): {', '.join(_CONNECTIVES)}.\n\n"
        f"Write the script now. Return JSON in this exact shape:\n"
        f"{{\n"
        f'  "title": "<short punchy title for this Short>",\n'
        f'  "hook": "<scene 1 text, also stored in scenes[0].text>",\n'
        f'  "scenes": [\n'
        f'    {{"text": "...", "page_ref": 3, "panel_ref": 0, "connective": null, "beat_id": 1}},\n'
        f'    {{"text": "But ...", "page_ref": 3, "panel_ref": 2, "connective": "But", "beat_id": 2}},\n'
        f"    ...\n"
        f"  ]\n"
        f"}}"
    )

    log(f"[stage4]   write prompt: {len(user)} chars, {len(beats)} beats")
    chain = [model] if model else None
    raw, mdl_used = call_with_chain(
        system=_WRITE_SYSTEM,
        user=user,
        models=chain,
        max_tokens=3000,
        progress=progress,
        label="write",
    )
    if debug_dump is not None:
        debug_dump["phase_c_raw"] = raw
        debug_dump["phase_c_model"] = mdl_used
    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("scenes"), list):
        raise RuntimeError(f"Phase C: no scenes array. Raw:\n{raw[:500]}")
    return parsed, mdl_used


def _validate(parsed: dict, valid_pages: set[int], valid_beat_ids: set[int]) -> list[str]:
    errors: list[str] = []
    scenes = parsed.get("scenes") or []
    if not scenes:
        return ["no scenes in output"]
    if not (5 <= len(scenes) <= 8):
        errors.append(f"scene count {len(scenes)} not in 5..8")

    total_words = 0
    for i, s in enumerate(scenes, start=1):
        text = str(s.get("text", "")).strip()
        wc = len(text.split())
        total_words += wc
        is_last = (i == len(scenes))

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
        first_word = text.split(",", 1)[0].split()[0] if text else ""
        first_word = first_word.rstrip(",.;:!?")
        if first_word not in _CONNECTIVES:
            errors.append(f"scene {i} text starts with {first_word!r}, not a whitelist connective")

        floor = 8 if is_last else _SCENE_MIN_WORDS
        if not (floor <= wc <= _SCENE_MAX_WORDS):
            errors.append(f"scene {i} is {wc} words, want {floor}-{_SCENE_MAX_WORDS}")

    if not (140 <= total_words <= 200):
        errors.append(f"total words {total_words} not in 140..200")
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
        f"- Total: 140-200 words ({_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} ideal). 5-8 scenes.\n\n"
        f"PRIOR DRAFT (fix in place, keep beat_id/page_ref/panel_ref unchanged unless they were flagged):\n{prior}\n\n"
        f"Return ONLY the corrected JSON."
    )
    log(f"[stage4]   retry prompt: {len(user)} chars")
    chain = [model] if model else None
    try:
        raw, mdl_used = call_with_chain(
            system=_WRITE_SYSTEM,
            user=user,
            models=chain,
            max_tokens=3000,
            progress=progress,
            label="retry",
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
    for pat in [r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"]:
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
            return json.loads(raw[i: j + 1])
        except json.JSONDecodeError:
            return None
    return None
