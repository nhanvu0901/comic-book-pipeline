"""Stage 3 writer for the "micro_moment" mode.

A 35-60s Short about ONE moment from a SINGLE issue plus what it MEANS — NOT a
full recap. Market-proven statement-narrative shape ("The Tragic Reason Harley
Quinn Finally Left The Joker", "Punisher Makes Juggernaut Throw Up"): a hook that
mirrors the title, a tight cause->moment->consequence mini-arc, and one of three
landing shapes.

v2 (2026-07-10, see MICRO_MOMENT_V2_SPEC.md part A — forensics on 4 winning
videos vs our own): the hook now MIRRORS the title/target_moment (names the
character in sentence 1) instead of standing alone as a paradox; body sentences
chain 2-3 events paratactically (and/but/then/after/while) in a documentary
voice that shifts past->present tense exactly at the described moment; the
payoff quotes panel dialog VERBATIM when it is available; and the writer
declares one of three ending shapes — thesis / hardcut / question.

Grounding reuse: this mode does NOT invent its own beats. It runs the SAME tuned,
wiki-grounded, panel-grounded `outline_beats` recap uses, then SELECTS a small
mini-arc window of beats around the described moment (lead-in -> moment ->
consequence) and writes short scenes for JUST that window. page_ref/panel_ref
stay beat-anchored via the same `_anchor_scenes_to_beats` helper — so panel
accuracy is identical to recap.

Additive only — recap never imports this file; write_script() dispatches here
before any of its own narrate-mode machinery runs (see write_script.py's mode
check), so the recap / explore_answer paths are byte-for-byte unchanged.

INPUT: a normally-preprocessed single-issue project whose comic_context.json
carries an optional `target_moment` string (what moment to tell, plus an
estimated page if known). No field → a clear error telling the user to set it.
"""
import re
from typing import Callable

from config import CREATIVE_LLM_MODELS, ENABLE_TITLE_BANNER, OPENROUTER_MODEL
from .schema import Beat, Glossary, Narration
from ._llm import call_with_chain
from .beat_split import _verbatim_ok
from .write_script import (
    _anchor_scenes_to_beats,
    _beat_anchor,
    _extract_json,
    _lint_you_quota,
    _to_narration,
    _HOOK_STOPWORDS,
    generate_banner_title,
    outline_beats,
)

# Micro-moment word budget — DISTINCT from recap's band (195-245) and explore's
# per-item band. v2 forensics (MICRO_MOMENT_V2_SPEC.md): 4 winning videos
# (32k-2M views) measured wpm ~183-225, same as ours — pacing was never the
# problem. Their edge was a tight cause->moment->consequence MINI-ARC (not
# "1 scene ± 1 beat") landing 35-60s. At the measured 3.4 wps render pace that
# is ~120-200 spoken words TOTAL (hook + body).
_MICRO_WORDS_MIN = 120          # ~35s at 3.4 wps
_MICRO_WORDS_MAX = 200          # ~59s at 3.4 wps
_MICRO_SCENE_MAX_WORDS = 40     # a scene = one paratactic chained sentence (or a short single-event one)
_MICRO_HOOK_MIN_WORDS = 10
_MICRO_HOOK_MAX_WORDS = 24
# A body scene at/under this length is one visual moment anyway — no lint even with
# no visual_beats (held panel for a short sentence is not the bug).
_MICRO_BEATS_LINT_MIN_WORDS = 12
# Mini-arc window: up to 2-3 lead-in beats, the moment ("peak") itself, 1-2
# consequence beats after — so the moment lands ~40-70% of the window, never as
# the LAST beat. (v1 bug: a moment near the end of the full outline became the
# final scene in the window, cutting the Short off right on the payoff with no
# consequence beat left to land it.)
_MICRO_LEADIN_MAX_BEATS = 3
_MICRO_FOLLOWUP_MAX_BEATS = 2
_MICRO_WINDOW_MAX_BEATS = 7     # ~6-7 beat mini-arc cap total (was a flat 4)
_MICRO_ENDING_STYLES = {"thesis", "hardcut", "question"}
_MICRO_WRITE_MAX_RETRIES = 3
# Issues that mean the draft is structurally broken (Stage 5 can't render it right) —
# never ship these even after retries exhaust; everything else (hook length, you-quota)
# is a soft lint that's fine to ship with a log line.
_MICRO_HARD_ISSUE_MARKERS = ("no visual_beats", "do not reconstruct", "expected ",
                             "is not in the PANEL MENU")


def _content_tokens(text: str) -> set[str]:
    """Lower-cased content words (len>=3, minus hook stopwords) for the deterministic
    lexical matcher below. Purely offline — window selection must NOT depend on the
    embedding server being up (semantic_sim returns 0.0 when it is down, which would
    make the pick non-deterministic and untestable)."""
    return {w for w in re.findall(r"[a-z']{3,}", (text or "").lower())
            if w not in _HOOK_STOPWORDS}


def _page_hints(target_moment: str) -> set[int]:
    """Explicit page numbers named in the moment spec ("page 30", "p. 12", "pg 7").
    Bare integers are ignored on purpose ("2 guards" is not a page reference)."""
    return {int(m) for m in re.findall(r"\b(?:page|pg|p)\.?\s*(\d{1,3})\b",
                                       target_moment or "", flags=re.IGNORECASE)}


def _moment_match_score(beat: Beat, tgt_tokens: set[str], page_hints: set[int]) -> float:
    """How well a beat matches the described moment: content-word Jaccard between the
    moment spec and the beat's name+summary, plus a bonus when an explicitly named
    page falls in the beat's page_refs. Deterministic, offline."""
    btoks = _content_tokens(f"{beat.name} {beat.summary}")
    jac = (len(tgt_tokens & btoks) / len(tgt_tokens | btoks)) if (tgt_tokens and btoks) else 0.0
    if page_hints and beat.page_refs and (set(beat.page_refs) & page_hints):
        jac += 0.5
    return jac


def _peak_index(beats: list[Beat], target_moment: str) -> int:
    """Index of the beat that best matches the described moment (the "peak").
    Shared by window selection and by `_window_block` (which marks it for the
    writer, so the past->present tense shift lands on the right beat)."""
    tgt = _content_tokens(target_moment)
    hints = _page_hints(target_moment)
    return max(range(len(beats)), key=lambda i: (_moment_match_score(beats[i], tgt, hints), -i))


def _select_moment_window(
    beats: list[Beat],
    target_moment: str,
    *,
    max_beats: int = _MICRO_WINDOW_MAX_BEATS,
) -> list[Beat]:
    """Pick a tight cause->moment->consequence mini-arc: up to
    _MICRO_LEADIN_MAX_BEATS beats leading INTO the described moment, the moment
    ("peak") beat itself, and up to _MICRO_FOLLOWUP_MAX_BEATS consequence beats
    after it — capped at `max_beats` total. This puts the moment around 40-70%
    of the window instead of at the very end (see the module-level comment on
    _MICRO_LEADIN_MAX_BEATS for the bug this fixes). Deterministic (lexical +
    page-hint match; ties break to the earliest beat)."""
    if not beats:
        return []
    peak = _peak_index(beats, target_moment)
    lead = min(peak, _MICRO_LEADIN_MAX_BEATS)
    follow = min(len(beats) - peak - 1, _MICRO_FOLLOWUP_MAX_BEATS)
    start, end = peak - lead, peak + follow + 1
    # Safety net for future constant tweaks: trim lead-in first so the
    # consequence beat(s) after the moment are never sacrificed.
    while end - start > max_beats and start < peak:
        start += 1
    return beats[start:end]


_MICRO_WRITE_SYSTEM = """You are MicroNarrator. You write ONE 35-60 second YouTube Short, documentary style, told plainly for a viewer with ZERO context.

THE ONE JOB — a micro_moment exists to ANSWER the single question its hook makes a viewer ask.
The hook states a striking outcome; the viewer instantly wonders WHY / HOW did that happen, and SO
WHAT. Every scene you write exists ONLY to answer that: the context that makes the moment matter
(what happened, who was wronged, what is at stake), the moment itself, and what it means. Before
writing, name that question in your head, then make the arc resolve it. A beat that only serves the
question gets a full scene; a beat that is side-detail gets the BAREST bridge clause (or a few
words) — never a paragraph of its own. Weight your words toward the beats that answer WHY/HOW.
  Generic shape: hook "[hero] made [foe] break down." Viewer asks "why would that ever happen?"
  Arc answers: who got hurt / what is at stake -> the moment it breaks the foe -> what it reveals.

You are given the beats of the mini-arc, in order, with the ★ PEAK beat marked (the moment itself). Write EXACTLY ONE scene per beat, in the SAME order, PLUS a separate hook line.

  HOOK (separate field, NOT a scene): ONE statement — NEVER a question — that MIRRORS/restates the given title. Name the main character in the FIRST sentence. Do NOT force a stand-alone paradox or riddle; a plain, direct restatement of what happens is the winning shape here.
    ✓ title "[hero] finally walked away from [foe]" -> hook "[hero] finally walks away from [foe] — and the reason is darker than it looks."
    ✓ a plain restatement of the title's outcome, present tense, is always safe.
    ✗ "Why did [hero] walk away?"  (a question — that is the Q&A format, not this one)

  KEEP IT SIMPLE — this is ONE moment, not a plot recap:
    - MAIN CAST ONLY. Anchor on the few characters the moment is ABOUT: whoever is named in the
      title, plus whoever it happens to or because of. Everyone else is background — give them a
      one-word role or leave them out. Do not introduce a character the answer doesn't need.
    - TITLE CHARACTERS ALWAYS KEEP THEIR NAME. Anyone named in the title/hook is the anchor of the
      whole Short — use that exact name every time in the body too. NEVER demote a title character
      to a generic descriptor (that makes the viewer lose track of who the story is about).
    - NAME ONLY HOUSEHOLD NAMES. If a character, place, team, realm, or object is NOT something a
      casual reader already knows, do NOT use its proper name — use the plain word a first-timer
      would: "a giant", "their leader", "his home", "an unlikely team", "a faraway world", "an
      unbreakable metal". Household-name heroes and villains keep their names, plainly. A
      supporting character with real mainstream-media presence (movies/TV) also counts as a
      household name — keep their name, with a short role tag on first mention ("X, the lawyer").
    - NO DESCRIPTOR COLLISIONS. Never use the same generic word for two DIFFERENT things. If the
      enemies are "giants", an ally must not also be "a giant" — pick a word that keeps them
      distinct, or use the ally's real name if it is a household name. One word = one thing.
    - STRIP JARGON EVEN WHEN THE BEATS USE IT. The beats below WILL contain obscure proper names,
      places, realms, materials, and minor characters' real names. NEVER copy one into the script
      — swap in the plain word, or drop it if the moment doesn't need it. A viewer must never hit
      a word they'd have to look up. Test each noun: would a stranger know it? If not, generalize.
        a beat like "X sealed himself in a Y-metal bunker on the realm Z"
          ✓ "their leader hid where no one could reach him"
          ✗ copying X / Y / Z verbatim
    - INTRODUCE ONCE, THEN REUSE THE SAME WORD. Give a character its short descriptor the first
      time only; after that reuse that exact word ("a giant" … later "the giant"). Never
      re-introduce or re-describe with new adjectives — the viewer already saw it.
    - NO DECORATIVE DETAIL. State the main subject + the main action. Drop scenery, poses, and
      adjective filler the answer doesn't need.
        ✗ "A huge armored warlord stands over fallen figures and broken ice, vowing revenge."
        ✓ "Their leader swears he will kill them all."
    - CONNECT EVERY BEAT. Each scene follows causally from the one before, so the whole reads as
      ONE moment unfolding toward the answer — never a list of disconnected facts.
    - NO EDITORIALIZING. State what happens; do not explain the character's inner motive in your
      own words ("because he isn't driven by hate"). Let the action carry the meaning; save any
      single interpretive line for the ENDING thesis only.

  SCENES (one per beat, in order) — PARATACTIC chained sentences, documentary voice:
    - Each sentence chains 2-3 events with and / but / then / after / while (do NOT
      write one flat isolated event per sentence) — third person, plain B2
      vocabulary, NO hype-slang.
        ✓ "[hero] corners them at the docks, and [foe] smashes through the wall to reach him."
        ✗ "[hero] corners them. [foe] smashes through the wall."  (choppy, not chained)
    - TENSE SHIFT: PAST TENSE for lead-in/context beats (documentary retrospective).
      At the ★ PEAK beat, switch decisively to PRESENT TENSE and stay present
      through the rest of the scenes — the tense shift itself IS the emotional
      turn, so land it exactly on that beat, not before or after.
    - ANTI-FRAGMENT: every sentence stays a complete subject+verb clause (or chain
      of clauses) — never a bare, unconnected noun-phrase reveal dropped with no
      connective ("They are alive." sitting alone with nothing chaining it in).
      Tie every reveal into the chain instead: "...and it turns out they are alive."
    - QUOTE THE PAYOFF VERBATIM: if a "PANEL DIALOG" block is given below with a
      line for the ★ PEAK beat's page, your payoff/mic-drop sentence MUST
      reproduce that exact line in quotation marks — do NOT paraphrase a real
      quoted line.
    - Scene 1 gives the MINIMUM setup a zero-context viewer needs — who this is,
      where we are.

  ENDING — pick ONE style for the LAST scene and declare it in "ending_style":
    - "thesis": ONE sentence stating what the moment MEANS, mirrored onto the character.
    - "hardcut": the last scene IS the payoff/quote line itself — no separate
      meaning line, no landing, the video cuts off right on it.
    - "question": ONE open question that baits a comment (curiosity — never "subscribe").

  VISUAL BEATS (every scene) — you MAP each narration beat to the EXACT comic panel that
  draws it (1:1 narration<->panel), so Stage 5 shows the right art on every clause:
    - "visual_beats" is a LIST OF OBJECTS: {"text": "<verbatim fragment>", "page": <N>, "panel": <N>}.
    - Split the scene's "text" into 2-3 fragments at ITS OWN punctuation/connective
      (and/but/then/comma/dash) — each fragment is ONE separately-drawable moment (a new
      action, a new subject, a beat change). VERBATIM ONLY: the fragments' exact words, in
      order, must concatenate back to "text" (you may only drop a comma/dash/connective at a
      split point). NEVER reword, add, or drop a word.
    - For EACH fragment set "page"/"panel" to the ONE panel from the PANEL MENU that draws
      that fragment's subject + action. Pick ONLY from the menu — never invent a page/panel.
    - A fragment that QUOTES dialogue MUST point to the panel whose menu line carries that
      exact quote (the speech bubble lives in that panel).
    - Two CONSECUTIVE fragments with the SAME subject AND SAME action MAY reuse the same
      page/panel (Stage 5 zooms deeper on the held panel); otherwise prefer a distinct panel.
    - A short single-event scene (<=12 words) may be ONE beat = the whole "text" pinned to
      its one panel — don't force a split where there is only one visual moment.

HARD RULES:
  - Plain B2 English. Concrete, no purple prose, no riddles, no lore a newcomer wouldn't know.
  - EXACTLY one scene per beat, in the SAME order given. Do NOT merge, split, reorder, or skip a beat.
  - Each scene under 40 words.
  - The hook plus ALL scenes together must land inside the WORD BUDGET given.
  - Third person storytelling — no "you" except (optionally) the hook and the final line.
  - Return ONLY JSON, no markdown fences.

Return shape:
{"hook": "<statement hook, not a question>", "ending_style": "thesis|hardcut|question", "scenes": [{"text": "...", "visual_beats": [{"text": "...", "page": N, "panel": N}, {"text": "...", "page": N, "panel": N}], "connective": null, "beat_id": <id>}, ...]}"""


def _window_block(window: list[Beat], peak_idx: int) -> str:
    lines = []
    for i, b in enumerate(window):
        pg = f" (page {min(b.page_refs)})" if b.page_refs else ""
        chars = ", ".join(b.characters_active) if b.characters_active else "?"
        mark = " ★ PEAK (the described moment — tense shift lands here)" if i == peak_idx else ""
        lines.append(f"{b.id}.{mark} {b.function}{pg}: {b.name} — {b.summary} [chars: {chars}]")
    return "\n".join(lines)


def _window_dialog_block(window: list[Beat], story_pages: list[dict] | None) -> str:
    """Verbatim dialog/OCR lines from the preprocessed pages this window covers, so
    the writer can QUOTE the payoff line instead of paraphrasing it. Magi's OCR is
    the ground-truth text per the DIALOG_TRUTH contract; `page_dialog` already
    stores that preference on each block (`.ocr` overrides the VLM's `.text` at
    Stage 2 time) — we just read what is already there. "" when nothing found
    (no story_pages, no dialog on these pages) — the writer prompt degrades
    gracefully to paraphrase, same as before this feature existed."""
    if not story_pages:
        return ""
    from .._panel_index import page_dialog
    pages_by_no = {p.get("page_number"): p for p in story_pages}
    wanted = sorted({pg for b in window for pg in (b.page_refs or [])})
    lines: list[str] = []
    for pn in wanted:
        page = pages_by_no.get(pn)
        if not page:
            continue
        for tb in page_dialog(page):
            text = str(tb.get("ocr") or tb.get("text") or "").strip()
            if text:
                spk = tb.get("speaker") or "?"
                lines.append(f'  p{pn} [{spk}]: "{text}"')
    if not lines:
        return ""
    return ("PANEL DIALOG (verbatim quotes available — quote the ★ PEAK beat's "
            "payoff line EXACTLY if it appears below, do not paraphrase it):\n"
            + "\n".join(lines))


def _beat_text(b) -> str:
    """The narration words of a visual beat. WRITER-PICKS-PANEL beats are
    {"text","page","panel"} dicts; recap/legacy beats are plain strings — either way
    returns the text."""
    return str(b.get("text", "")).strip() if isinstance(b, dict) else str(b).strip()


def _beat_pin(b) -> tuple[int, int] | None:
    """The (page, panel) a visual-beat dict pins, or None (string beat / no pin / malformed).
    Used to validate the writer's pick against the PANEL MENU and to bind it in Stage 5."""
    if not isinstance(b, dict):
        return None
    pg, pn = b.get("page"), b.get("panel")
    if pg is None or pn is None:
        return None
    try:
        return int(pg), int(pn)
    except (TypeError, ValueError):
        return None


def _panel_menu(window: list[Beat], story_pages: list[dict] | None) -> tuple[str, set[tuple[int, int]]]:
    """Full menu of every panel on the pages this moment-window covers, so the writer can
    PICK the exact (page, panel) that draws each narration beat (WRITER-PICKS-PANEL). One
    line per panel: `p{page}/{idx}: {desc<=120c} | dialog: "{ocr/text}"`. Returns
    (menu_text, valid_keys) where valid_keys = {(page, idx)} for pin validation. Panel idx is
    the panel's reading-order index — identical to Stage 5's _panel_pool key, so a pin binds
    directly there. ("", set()) when no pages/panels (writer degrades to un-pinned beats)."""
    if not story_pages:
        return "", set()
    from .._panel_index import _panel_dialog_text
    pages_by_no = {p.get("page_number"): p for p in story_pages}
    wanted = sorted({pg for b in window for pg in (b.page_refs or [])})
    lines: list[str] = []
    keys: set[tuple[int, int]] = set()
    for pn in wanted:
        page = pages_by_no.get(pn)
        if not page:
            continue
        tbs = page.get("text_blocks")
        for idx, panel in enumerate(page.get("panels") or []):
            keys.add((int(pn), idx))
            desc = " ".join(str(panel.get("description") or "").split())
            if len(desc) > 120:
                desc = desc[:117].rstrip() + "..."
            dlg = _panel_dialog_text(panel, tbs)
            dlg_part = f' | dialog: "{dlg}"' if dlg else ""
            lines.append(f"  p{pn}/{idx}: {desc}{dlg_part}")
    if not lines:
        return "", set()
    menu = ("PANEL MENU (pick from these only — each visual beat's \"page\"/\"panel\" MUST be one "
            "of these; the panel must DRAW that beat's subject + action):\n" + "\n".join(lines))
    return menu, keys


def _drop_invalid_pins(scenes: list[dict], menu_keys: set[tuple[int, int]],
                       log: Callable[[str], None]) -> None:
    """Soft guard: a visual-beat dict pinning a (page, panel) NOT on the PANEL MENU has its
    pin dropped (page/panel -> None) so Stage 5 falls back to the matcher for that beat; the
    beat's text is kept. No-op when there is no menu. Never raises."""
    if not menu_keys:
        return
    dropped = 0
    for s in scenes:
        for b in s.get("visual_beats") or []:
            if isinstance(b, dict) and (pin := _beat_pin(b)) is not None and pin not in menu_keys:
                b["page"] = b["panel"] = None
                dropped += 1
    if dropped:
        log(f"[micro_moment] dropped {dropped} out-of-menu panel pin(s) — those beats fall back "
            f"to the Stage 5 matcher")


def _call_micro_writer(
    window: list[Beat],
    comic_context: dict,
    target_moment: str,
    *,
    model: str | None,
    progress: Callable[[str], None] | None,
    debug_dump: dict,
    story_pages: list[dict] | None = None,
    issues: list[str] | None = None,
) -> tuple[dict, str]:
    fix_block = ""
    if issues:
        fix_block = "PREVIOUS DRAFT HAD ISSUES — FIX THESE:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
    title = str(comic_context.get("title", "")).strip()
    plot = str(comic_context.get("plot_summary", "")).strip() \
        or str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    peak_idx = _peak_index(window, target_moment)
    dialog_block = _window_dialog_block(window, story_pages)
    menu_block, _menu_keys = _panel_menu(window, story_pages)
    user = (
        f"TITLE (mirror this in the hook — restate/paraphrase it, name the character "
        f"in sentence 1): {title}\n"
        f"THE MOMENT TO TELL (do not stray beyond it): {target_moment}\n\n"
        f"{fix_block}"
        f"BACKGROUND PLOT (ground truth — every fact + 'why' must come from here or the beats):\n"
        f"{plot[:1800]}\n\n"
        f"BEATS — write ONE scene per beat, in this EXACT order (do not reorder):\n"
        f"{_window_block(window, peak_idx)}\n\n"
        + (f"{menu_block}\n\n" if menu_block else "")
        + (f"{dialog_block}\n\n" if dialog_block else "")
        + f"WORD BUDGET: {_MICRO_WORDS_MIN}-{_MICRO_WORDS_MAX} words TOTAL across the hook line "
        f"AND all {len(window)} scenes (each scene under {_MICRO_SCENE_MAX_WORDS} words).\n"
        f'Return JSON: {{"hook": "...", "ending_style": "thesis|hardcut|question", '
        f'"scenes": [{{"text": "...", "visual_beats": [{{"text": "...", "page": N, "panel": N}}], '
        f'"connective": null, "beat_id": {window[0].id}}}, ... one per beat ...]}}.'
    )

    def _valid(raw: str) -> bool:
        p = _extract_json(raw)
        return (isinstance(p, dict) and str(p.get("hook", "")).strip() != ""
                and isinstance(p.get("scenes"), list) and len(p["scenes"]) == len(window))

    chain = [model] if model else list(CREATIVE_LLM_MODELS)
    raw, mdl = call_with_chain(
        system=_MICRO_WRITE_SYSTEM, user=user, models=chain, max_tokens=1600,
        progress=progress, label="micro_write", validator=_valid,
    )
    if debug_dump is not None:
        debug_dump["micro_write_raw"] = raw
        debug_dump["micro_write_model"] = mdl
    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("scenes"), list):
        raise RuntimeError(f"[micro_moment] writer returned no scenes array. Raw:\n{raw[:400]}")
    return parsed, mdl


def _first_sentence(text: str) -> str:
    """The first sentence of `text` (split at the first ./!/? + whitespace) — used
    to check the hook names its character UP FRONT (mirror-of-title register),
    not buried after the opening clause."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return parts[0] if parts else text


def _validate_micro_scenes(
    hook: str,
    scenes: list[dict],
    beats: list[Beat],
    ending_style: str | None = None,
    *,
    menu_keys: set[tuple[int, int]] | None = None,
) -> list[str]:
    """hook is a statement in band that names a character up front, one scene per
    beat, per-scene cap, TOTAL word band, and a declared ending_style (thesis /
    hardcut / question — all three are valid, none are lint-penalized against the
    others). Feeds the bounded retry loop in write_micro_moment(); this function
    never raises itself, but the caller re-raises the structural issues (missing
    visual_beats, non-verbatim beats, wrong scene count, off-menu pins) once
    retries are exhausted — everything else here (hook length, you-quota, ...)
    stays a soft, ship-anyway lint."""
    issues: list[str] = []
    hook = (hook or "").strip()
    hw = len(hook.split())
    if hook.endswith("?"):
        issues.append("micro hook is a question — use a STATEMENT that mirrors the title "
                      "(a question is the Q&A format, not micro_moment)")
    if not (_MICRO_HOOK_MIN_WORDS <= hw <= _MICRO_HOOK_MAX_WORDS):
        issues.append(f"micro hook is {hw}w (want {_MICRO_HOOK_MIN_WORDS}-{_MICRO_HOOK_MAX_WORDS})")
    names = {c.strip().lower() for b in beats for c in (b.characters_active or [])
             if len(c.strip()) >= 3}
    if names and not any(n in _first_sentence(hook).lower() for n in names):
        issues.append("micro hook: name a character from the moment in the FIRST "
                      "sentence (mirror-of-title register)")
    if len(scenes) != len(beats):
        issues.append(f"expected {len(beats)} scenes, got {len(scenes)}")
    total = hw
    for i, s in enumerate(scenes):
        text = str(s.get("text", ""))
        wc = len(text.split())
        total += wc
        if wc > _MICRO_SCENE_MAX_WORDS:
            issues.append(f"scene {i + 1} is {wc}w (max {_MICRO_SCENE_MAX_WORDS})")
        # A long body scene with no (or bogus) visual_beats holds ONE panel for the
        # whole scene in Stage 5 (stages/stage_5/shots.py:_build_shots_per_chunk) —
        # the immortal-hulk-13 held-panel bug. This function never raises; the
        # caller (write_micro_moment) treats it as a HARD issue once retries are
        # exhausted (see _MICRO_HARD_ISSUE_MARKERS).
        raw_vbeats = s.get("visual_beats") or []
        vbeats = [_beat_text(b) for b in raw_vbeats if _beat_text(b)]
        if wc > _MICRO_BEATS_LINT_MIN_WORDS:
            if not vbeats:
                issues.append(f"scene {i + 1} is {wc}w with no visual_beats — Stage 5 will hold "
                              f"ONE panel for the whole scene; split into 2-3 verbatim clause beats")
            elif not _verbatim_ok(text, vbeats):
                issues.append(f"scene {i + 1} visual_beats do not reconstruct the scene text "
                              f"verbatim — beats must concatenate back to the exact text")
        # WRITER-PICKS-PANEL: a beat pinning a (page,panel) not on the PANEL MENU is a soft
        # error (writer picked outside the offered panels). Only linted when a menu was built.
        if menu_keys:
            for b in raw_vbeats:
                pin = _beat_pin(b)
                if pin is not None and pin not in menu_keys:
                    issues.append(f"scene {i + 1} visual_beats pin p{pin[0]}/{pin[1]} is not in the "
                                  f"PANEL MENU — pick a page/panel from the menu")
    if not (_MICRO_WORDS_MIN <= total <= _MICRO_WORDS_MAX):
        issues.append(f"micro band: total {total}w outside {_MICRO_WORDS_MIN}-{_MICRO_WORDS_MAX} "
                      f"(this Short must land ~35-60s at 3.4 wps)")
    style = (ending_style or "").strip().lower()
    if style not in _MICRO_ENDING_STYLES:
        issues.append(f"missing/unknown 'ending_style' {ending_style!r} — declare one of "
                      f"{sorted(_MICRO_ENDING_STYLES)}")
    if scenes:
        last_text = str(scenes[-1].get("text", "")).strip()
        if style == "question":
            if not last_text.endswith("?"):
                issues.append("ending_style is 'question' but the last scene doesn't end in '?'")
            if "subscribe" in last_text.lower():
                issues.append("ending_style 'question' should bait a COMMENT, not ask to subscribe")
    # Reuse recap's third-person "you"-quota lint (soft) on the hook + body scene list.
    issues.extend(_lint_you_quota([{"text": hook, "is_intro": True}] + list(scenes)))
    return issues


def write_micro_moment(
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
    """Orchestrate the micro_moment writer: read target_moment -> outline the full
    issue (reuse recap's grounded beat pipeline) -> select the moment mini-arc
    window -> LLM writes hook + one scene per windowed beat (1 retry on
    validation fail) -> beat-anchor the body -> prepend the hook as the is_intro
    scene -> banner."""
    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}

    target_moment = str(comic_context.get("target_moment", "") or "").strip()
    if not target_moment:
        raise ValueError(
            "[micro_moment] comic_context.json has no 'target_moment'. This mode tells "
            "ONE moment, so set a top-level string field describing it (and an estimated "
            'page if known), e.g. "target_moment": "Punisher forces Juggernaut to throw '
            'up during their brawl, around page 14". Then re-run Stage 3 --mode micro_moment.')

    # Reuse the tuned, wiki-grounded, panel-grounded recap outliner for the WHOLE
    # issue, then narrow to the moment. We deliberately do NOT run the recap
    # beat-impact critic (it keeps the whole-story cold-open/climax/landing) — the
    # window IS the trim here.
    beats_all, beats_model = outline_beats(
        comic_context, story_pages, mode, hook_hint=hook_hint, model=model,
        progress=progress, debug_dump=dump, story_map=None, direction=direction)
    window = _select_moment_window(beats_all, target_moment)
    if not window:
        raise RuntimeError(
            f"[micro_moment] outline produced no beats for {comic_context.get('title', '?')!r} "
            f"— cannot locate the moment. Check Stage 2 preprocessing / plot_summary.")
    log(f"[micro_moment] moment window = beat(s) {[b.id for b in window]} of {len(beats_all)} "
        f"(target: {target_moment[:80]!r})")

    # Valid (page, panel) keys the writer may pin a beat to (WRITER-PICKS-PANEL); used to lint
    # out-of-menu pins and to drop them below. Empty set when no panels → pin lint is skipped.
    menu_keys = _panel_menu(window, story_pages)[1]

    parsed, mdl = _call_micro_writer(window, comic_context, target_moment, model=model,
                                     progress=progress, debug_dump=dump, story_pages=story_pages)
    issues = _validate_micro_scenes(parsed.get("hook", ""), parsed.get("scenes") or [],
                                    window, parsed.get("ending_style"), menu_keys=menu_keys)
    for attempt in range(_MICRO_WRITE_MAX_RETRIES):
        if not issues:
            break
        log(f"[micro_moment] draft has {len(issues)} issue(s); retrying "
            f"({attempt + 1}/{_MICRO_WRITE_MAX_RETRIES}): {issues}")
        parsed, mdl = _call_micro_writer(window, comic_context, target_moment, model=model,
                                         progress=progress, debug_dump=dump,
                                         story_pages=story_pages, issues=issues)
        issues = _validate_micro_scenes(parsed.get("hook", ""), parsed.get("scenes") or [],
                                        window, parsed.get("ending_style"), menu_keys=menu_keys)
    if issues:
        hard = [iss for iss in issues if any(m in iss for m in _MICRO_HARD_ISSUE_MARKERS)]
        if hard:
            raise RuntimeError(
                f"[micro_moment] writer draft still structurally broken after "
                f"{_MICRO_WRITE_MAX_RETRIES} retries: {hard}")
        log(f"[micro_moment] shipping with unresolved issue(s): {issues}")

    # Drop any pin the writer picked outside the menu (keep the beat text) so Stage 5 falls
    # back to the matcher for that beat instead of binding a non-existent panel.
    _drop_invalid_pins(parsed.get("scenes") or [], menu_keys, log)

    hook_text = str(parsed.get("hook", "")).strip()
    ending_style = str(parsed.get("ending_style", "")).strip().lower()
    if ending_style not in _MICRO_ENDING_STYLES:
        ending_style = None

    # Deterministic 1 beat -> 1 scene (same helper recap/explore use); page_ref +
    # panel_ref come from the beat's grounded key_panel, never the writer.
    body_only = {"scenes": parsed.get("scenes") or []}
    _anchor_scenes_to_beats(body_only, window, progress)
    body = body_only.get("scenes") or []

    # Hook rides over the moment's opening panel (whole page). is_intro keeps it out
    # of the beat-anchored body, exactly like recap's teaser / explore's hook.
    hook_page, _ = _beat_anchor(window[0])
    intro_scene = {
        "text": hook_text, "page_ref": hook_page, "panel_ref": -1,
        "connective": None, "beat_id": 0, "is_intro": True,
    }

    scenes = [intro_scene] + body
    title = str(comic_context.get("title", "")).strip()
    parsed_final = {"scenes": scenes, "hook": hook_text, "title": title}

    final_model = mdl or beats_model or model or OPENROUTER_MODEL
    nar = _to_narration(parsed_final, window, Glossary(), mode, final_model)
    # Optional field, not part of the Narration dataclass schema (v2 spec: "don't
    # force a rigid schema change") — carried as a plain attribute for whatever
    # downstream consumer (render / QA) wants to branch on the chosen ending shape.
    nar.ending_style = ending_style

    # Descriptive (non-question) banner — same helper recap uses; falls back to the
    # working title when the LLM banner call fails. Nudge its own internal prompt
    # (via the existing director's-notes mechanism) toward a direct flip-statement,
    # without touching generate_banner_title itself.
    if ENABLE_TITLE_BANNER:
        micro_note = ("Banner = a short, direct, flip-style TITLE STATEMENT that mirrors "
                      "this Short's target moment/title plainly — not vague clickbait.")
        banner_direction = dict(direction or {})
        prior_notes = str(banner_direction.get("notes", "")).strip()
        banner_direction["notes"] = f"{prior_notes} {micro_note}".strip()
        banner = generate_banner_title(comic_context, scenes, model=model,
                                       progress=progress, debug_dump=dump,
                                       direction=banner_direction)
        nar.banner_title = banner or nar.title
        log(f"[micro_moment] banner title: {nar.banner_title!r}")
    return nar
