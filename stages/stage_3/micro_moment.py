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
import os
import re
from typing import Callable

from config import (CREATIVE_LLM_MODELS, ENABLE_TITLE_BANNER, FIDELITY_LLM_MODELS,
                    OPENROUTER_MODEL)
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
# Band is a CEILING, not a target: a short one-scene moment self-limits to ~4 beats ×
# ≤40w; only a whole-story context window (LLM-segmenter keeps far setup + payoff)
# approaches the top. Ceiling raised 2026-07-20 (200 → 320w ≈ 94s at 3.4 wps) so
# "tell the WHOLE little story" is never truncated — Master: longer is fine as long as
# the story lands complete. Both env-tunable (MICRO_WORDS_MIN / MICRO_WORDS_MAX).
_MICRO_WORDS_MIN = int(os.getenv("MICRO_WORDS_MIN", "120"))   # ~35s at 3.4 wps
_MICRO_WORDS_MAX = int(os.getenv("MICRO_WORDS_MAX", "320"))   # ~94s at 3.4 wps
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
# never ship these even after retries exhaust; everything else (hook length, you-quota,
# the soft ground-check "not drawn" hint) is a lint that's fine to ship with a log line.
_MICRO_HARD_ISSUE_MARKERS = ("no visual_beats", "do not reconstruct", "expected ")
# GROUND-CHECK floor (2026-07-16): after the TEXT is locked we cosine-match every visual-beat
# fragment against the panels on its OWN pages (the same richer panel embed Stage 5 matches on).
# A body scene whose best panel is below this floor draws NOTHING on its pages → a SOFT retry
# hint ("retell using what's actually there"), the safety net for the new story-first writer
# (which reads plot prose that may mention off-panel events). ponytail: single tuning knob; the
# live-observed on-page cosine sits well above this — raise if false "not drawn" hints appear.
_MICRO_GROUND_FLOOR = float(os.getenv("MICRO_GROUND_COS_FLOOR", "0.34"))


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


def _focus_tokens(title: str, target_moment: str) -> set[str]:
    """Content words (letters only, len>=3) from the title + target_moment — the
    "who/what this Short is about" vocabulary. Used both to focus-filter the
    beat window below (drops team-book subplot beats about OTHER characters)
    and to soft-lint the final draft's on-topic ratio. Deliberately `[a-z]+`
    rather than `_content_tokens`'s `[a-z']{3,}`: a possessive character name
    ("Blackheart's") must tokenize to the bare name ("blackheart") so it still
    matches that same name inside a beat's characters_active list."""
    return {t for t in re.findall(r"[a-z]+", f"{title} {target_moment}".lower()) if len(t) >= 3}


def _beat_shares_token(b: Beat, tokens: set[str]) -> bool:
    """Does the beat name a character whose word overlaps `tokens`? Strict (a real
    subject match, unlike `_on_focus`'s lenient empty-cast pass) — used by the
    backward SETUP-reach to decide a preceding beat is setup ABOUT the same subject.
    Same tokenizing convention as `_focus_tokens` / `_on_focus`."""
    return any(len(tok) >= 3 and tok in tokens
               for name in (b.characters_active or [])
               for tok in re.findall(r"[a-z]+", name.lower()))


def _select_moment_window(
    beats: list[Beat],
    target_moment: str,
    *,
    title: str = "",
    max_beats: int = _MICRO_WINDOW_MAX_BEATS,
    log: Callable[[str], None] | None = None,
) -> list[Beat]:
    """Pick a tight cause->moment->consequence mini-arc: up to
    _MICRO_LEADIN_MAX_BEATS beats leading INTO the described moment, the moment
    ("peak") beat itself, and up to _MICRO_FOLLOWUP_MAX_BEATS consequence beats
    after it — capped at `max_beats` total. This puts the moment around 40-70%
    of the window instead of at the very end (see the module-level comment on
    _MICRO_LEADIN_MAX_BEATS for the bug this fixes). Deterministic (lexical +
    page-hint match; ties break to the earliest beat).

    FOCUS FILTER (team-book fix, v2 — TWO-TIER): a positional window can straddle
    a PAGE range that a team book spends on an entirely different character's
    subplot (real case: a Red Hulk/Ghost Rider moment's window pulled in Flash
    Thompson/Alejandra/X-23 beats AND Blackheart-only beats with zero Red Hulk —
    ~50% of the Short drifted off the title). v1 filtered on title+target_moment
    tokens together, but an OMNIPRESENT villain named in target_moment (present in
    characters_active of nearly every beat) made that filter a no-op: every
    off-topic beat still shared the villain token, so nothing got dropped.

    Tier 1 filters on TITLE WORDS ONLY (`_focus_tokens(title, "")`) — the pipeline
    names the actual subject of the Short in its own title, so a villain who is
    merely omnipresent in the moment description can't poison this vocabulary.
    A beat survives if its `characters_active` shares a token with the title,
    UNLESS it is the peak itself (rule a — the described moment is never
    dropped) or it has NO characters_active at all (rule b — unknown cast is not
    proof of off-focus; protects bridge/LANDING beats with no character tag).
    Widens a step at a time (lead/follow caps raised, capped at lead<=5/
    follow<=4) until >=4 beats survive (4 = hook+why+moment+payoff, the floor
    for a full mini-arc) or the caps max out.

    Tier 2 (only reached if tier 1 never clears 4) re-filters the SAME
    progressively-widened windows on the old title+target_moment vocabulary —
    catches a genuinely relevant side-name (e.g. a co-lead) that the title
    doesn't mention but the moment spec does — at a lower floor of 3.

    Tier 3: still under 3 after both tiers exhaust their widening — ship the
    original UNFILTERED positional window as-is (a short, slightly-off-focus
    Short beats a broken one).

    Every tier's result then passes through `_with_setup` (SETUP-REACH, 2026-07-20):
    it prepends far-back setup beats about the title subject that the positional lead
    cap left out. This is the offline/no-embed/LLM-fail FALLBACK path; the LLM
    segmenter (`_segment_moment_window`) is the primary context-aware picker."""
    if not beats:
        return []
    peak = _peak_index(beats, target_moment)
    peak_id = beats[peak].id

    def _slice(lead_cap: int, follow_cap: int) -> tuple[int, int]:
        lead = min(peak, lead_cap)
        follow = min(len(beats) - peak - 1, follow_cap)
        start, end = peak - lead, peak + follow + 1
        # Safety net for future constant tweaks: trim lead-in first so the
        # consequence beat(s) after the moment are never sacrificed.
        while end - start > max_beats and start < peak:
            start += 1
        return start, end

    start, end = _slice(_MICRO_LEADIN_MAX_BEATS, _MICRO_FOLLOWUP_MAX_BEATS)
    original_window = beats[start:end]

    title_tokens = _focus_tokens(title, "")
    combined_tokens = _focus_tokens(title, target_moment)

    def _with_setup(window: list[Beat]) -> list[Beat]:
        """SETUP-REACH (loosen, 2026-07-20): the positional lead cap (<=3, widened to
        <=5) can leave the setup that explains WHO/WHY far behind the moment out of the
        window entirely — the immortal-hulk case (the gas-station robbery + Bruce being
        shot sit pages before the morgue moment, so the narration jumped straight into
        the payoff and a viewer was blind). Walk BACKWARD from the window's first beat
        through the full outline, prepending each contiguous beat that names the same
        subject as the TITLE, stopping at the first that doesn't (the subplot boundary).
        No-op when the title carries no tokens (window tests pass title="") or the window
        already starts at beat 0. This is the FALLBACK's context reach; the LLM segmenter
        (`_segment_moment_window`) is the primary, smarter context picker.
        ponytail: bounded only by the subplot boundary — on an all-on-focus solo book it
        can pull the whole first act; fine, the word band caps output length."""
        if not window or not title_tokens:
            return window
        first_pos = next((i for i, b in enumerate(beats) if b.id == window[0].id), 0)
        add: list[Beat] = []
        for i in range(first_pos - 1, -1, -1):
            if _beat_shares_token(beats[i], title_tokens):
                add.append(beats[i])
            else:
                break
        if add and log:
            log(f"[micro_moment] setup-reach prepended beat(s) "
                f"{[b.id for b in reversed(add)]} (setup about the title subject)")
        return list(reversed(add)) + window

    if not title_tokens and not combined_tokens:
        return original_window

    def _on_focus(b: Beat, tokens: set[str]) -> bool:
        if b.id == peak_id or not b.characters_active:
            return True
        return any(len(tok) >= 3 and tok in tokens
                   for name in b.characters_active
                   for tok in re.findall(r"[a-z]+", name.lower()))

    def _filter(window: list[Beat], tokens: set[str]) -> list[Beat]:
        return [b for b in window if _on_focus(b, tokens)]

    def _n_distinct(bs: list[Beat]) -> int:
        # Bridge-retry outlines can let two DIFFERENT beats share one numeric id
        # (2026-07-16 bug); count DISTINCT ids against the floor so a duplicate
        # can't pad raw list length into a false tier-1/tier-2 "pass" — that
        # shipped a window `_anchor_scenes_to_beats` later collapsed (its
        # id-keyed dict silently drops one beat's scene per colliding id).
        return len({b.id for b in bs})

    def _widen(tokens: set[str], min_beats: int) -> list[Beat]:
        lead_cap, follow_cap = _MICRO_LEADIN_MAX_BEATS, _MICRO_FOLLOWUP_MAX_BEATS
        filtered = _filter(original_window, tokens)
        while _n_distinct(filtered) < min_beats and (lead_cap < 5 or follow_cap < 4):
            lead_cap, follow_cap = min(lead_cap + 1, 5), min(follow_cap + 1, 4)
            w_start, w_end = _slice(lead_cap, follow_cap)
            filtered = _filter(beats[w_start:w_end], tokens)
        return filtered

    def _dropped(filtered: list[Beat]) -> list[int]:
        kept = {f.id for f in filtered}
        return [b.id for b in original_window if b.id not in kept]

    filtered: list[Beat] = []
    if title_tokens:
        filtered = _widen(title_tokens, 4)
        if _n_distinct(filtered) >= 4:
            dropped = _dropped(filtered)
            if dropped and log:
                log(f"[micro_moment] focus-filter(title) dropped beat(s) {dropped} "
                    f"(characters miss title focus)")
            return _with_setup(filtered)

    if combined_tokens:
        filtered = _widen(combined_tokens, 3)
        if _n_distinct(filtered) >= 3:
            dropped = _dropped(filtered)
            if dropped and log:
                log(f"[micro_moment] focus-filter(title+moment) dropped beat(s) {dropped} "
                    f"(characters miss title/target focus)")
            return _with_setup(filtered)

    if log:
        log(f"[micro_moment] focus-filter still has only {len(filtered)} beat(s) after "
            f"widening to lead<=5/follow<=4 — shipping the unfiltered window "
            f"{[b.id for b in original_window]}")
    return _with_setup(original_window)


def _focus_filter_llm_on() -> bool:
    """FOCUS_FILTER_LLM knob (default ON). =0/false/no → skip the LLM segmenter and use
    the deterministic heuristic (`_select_moment_window`) exactly as before."""
    return os.getenv("FOCUS_FILTER_LLM", "1").strip().lower() in ("1", "true", "yes", "on")


_FOCUS_SEGMENT_SYSTEM = """You are a STORY SEGMENTER for a 40-90 second micro-story video. \
You are given the FULL ordered beat outline of ONE comic issue (each beat: id, story \
function, the page(s) it is on, its cast, a short label and a one-line summary), plus the \
video's TITLE and the single MOMENT the video is about. Your job: group the beats by id so \
a narrator can tell a CLEAN, SINGLE through-line of that moment to a viewer with ZERO prior \
knowledge — never confusing (who is this? why did that happen?), never skipping the setup \
that makes the moment land, and never padding it with a second thread the main story does \
not need.

Return ONLY JSON, no markdown fences: {"focus":[ids],"context":[ids],"payoff":[ids],"drop":[ids]}. \
Put EVERY beat id in EXACTLY ONE of the four groups.

- "focus": the beats that ARE the moment the TITLE / MOMENT describes (the climax cluster).
- "context": the SETUP beats a first-time viewer needs to understand the focus — who the \
main character is, the event that set this in motion, and WHY it happens. Keep a beat as \
context ONLY when it is ESSENTIAL to the MAIN thread: it directly introduces the person, \
place, or event the focus's own character depends on. KEEP that essential setup — a viewer \
must be able to follow the focus without prior knowledge.
- "payoff": the beats right AFTER the focus that show its consequence or meaning FOR THE SAME \
main character — how it ends, what it costs, what it reveals for them.
- "drop": beats that do not carry the main thread: (a) a SEPARATE subplot with no \
cause-and-effect link to the focus, and (b) any beat whose only job is bringing in a SIDE \
character (a reporter, detective, narrator, bystander, witness) to OBSERVE, RECAP, or piece \
together something the focus already showed — that is redundant confirmation, not new stakes \
for the main character, so drop it even when its topic is thematically related. Most beats \
still belong in focus/context/payoff — drop only beats that clearly match (a) or (b).

Keep context that is ESSENTIAL to follow the MAIN thread; when a beat only adds a \
side-character recapping events or a tangential subplot, DROP it. Prefer a clean single \
through-line over completeness — do not pad the window trying to be exhaustive."""


def _segment_spine(beats: list[Beat]) -> str:
    """One line per beat for the segmenter: id, function, pages, cast, label + summary.
    UNLIKE the writer's `_window_block` (which hides summary/cast to stop VLM-tilt), the
    segmenter NEEDS the summary and cast to judge cause-and-effect and which subplot a
    beat belongs to — it only decides grouping, it never writes narration."""
    lines = []
    for b in beats:
        pg = f" p{','.join(map(str, b.page_refs))}" if b.page_refs else ""
        cast = f" [{', '.join(b.characters_active)}]" if b.characters_active else ""
        summ = f" — {b.summary}" if b.summary else ""
        lines.append(f"{b.id}. {b.function}{pg}{cast}: {b.name}{summ}")
    return "\n".join(lines)


def _segment_moment_window(
    beats: list[Beat],
    target_moment: str,
    *,
    title: str = "",
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[Beat] | None:
    """LLM-SEGMENT primary path (context-aware, 2026-07-20). Reads the WHOLE outline and
    asks the model to split beats into focus / context / payoff / drop by id, then returns
    (focus ∪ context ∪ payoff) MINUS drop, in the ORIGINAL causal order — keeping the far
    setup a positional window misses so the narration tells the whole story, not just the
    payoff. Returns None (→ caller falls back to the deterministic `_select_moment_window`
    heuristic) when: the knob is off, STAGE3_NO_EMBED is set (deterministic offline mode),
    the outline is empty, the LLM call fails/raises, the JSON is unparseable, or the model
    names no valid focus beat. NEVER raises — it can only replace the heuristic with a
    better window or defer to it.

    No validator is passed to `call_with_chain` on purpose: this function does its OWN
    JSON+id validation and defers to the heuristic on anything unexpected, and a validator
    here would trip the writer-shaped `call_with_chain` fixtures the micro tests patch in."""
    log = log or (lambda _m: None)
    if not _focus_filter_llm_on():
        return None
    from config import stage3_no_embed
    if stage3_no_embed():
        log("[micro_moment] focus-segment skipped (--no-embed): deterministic heuristic fallback")
        return None
    if not beats:
        return None
    valid_ids = {b.id for b in beats}
    user = (
        f"TITLE: {title}\n"
        f"THE MOMENT THIS VIDEO IS ABOUT: {target_moment}\n\n"
        f"FULL BEAT OUTLINE (in story order):\n{_segment_spine(beats)}\n\n"
        f'Return ONLY JSON: {{"focus":[ids],"context":[ids],"payoff":[ids],"drop":[ids]}}.'
    )
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, _mdl = call_with_chain(
            system=_FOCUS_SEGMENT_SYSTEM, user=user, models=chain,
            max_tokens=700, progress=progress, label="focus-segment")
    except Exception as exc:  # SDK unavailable / all models exhausted / etc.
        log(f"[micro_moment] focus-segment LLM failed ({type(exc).__name__}: {exc}); "
            f"heuristic fallback")
        return None
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        log("[micro_moment] focus-segment returned no JSON object; heuristic fallback")
        return None

    def _ids(key: str) -> list[int]:
        return [i for i in (parsed.get(key) or []) if isinstance(i, int) and i in valid_ids]

    focus = _ids("focus")
    if not focus:
        log("[micro_moment] focus-segment named no valid focus beat; heuristic fallback")
        return None
    keep = (set(focus) | set(_ids("context")) | set(_ids("payoff"))) - set(_ids("drop"))
    keep |= set(focus)  # the described moment is never dropped, even if the model contradicts itself
    window = [b for b in beats if b.id in keep]  # ORIGINAL causal order preserved
    if len(window) < 2:
        log("[micro_moment] focus-segment kept <2 beats; heuristic fallback")
        return None
    dropped = [b.id for b in beats if b.id not in keep]
    log(f"[micro_moment] focus-segment(LLM): focus={sorted(focus)} "
        f"context={sorted(_ids('context'))} payoff={sorted(_ids('payoff'))} "
        f"dropped={dropped} → window {[b.id for b in window]}")
    return window


# Generic English "story is over" vocabulary — used to decide whether the beats the
# window is about to skip actually describe a RESOLUTION (defeat/aftermath), as
# opposed to the story trailing off mid-moment. No character/place/comic names —
# see the module CLAUDE.md rule against overfitting the pipeline to one comic.
# "beat"/"beats" deliberately excluded: too ambiguous ("cannot beat a god of Hell" —
# a mid-story COMPLICATION beat describing FAILURE — shares the word with a real
# defeat) next to the unambiguous "beaten"/"defeated"/etc. already in the set.
_RESOLUTION_KEYWORDS = {
    "defeat", "defeated", "defeats", "beaten",
    "destroy", "destroyed", "destroys", "kill", "killed", "kills",
    "banish", "banished", "banishes", "vanquish", "vanquished", "vanquishes",
    "die", "died", "dies", "death", "fall", "falls", "fell", "fallen",
    "collapse", "collapses", "collapsed", "seal", "sealed", "seals",
    "trap", "trapped", "traps", "capture", "captured", "captures",
    "surrender", "surrendered", "surrenders", "restore", "restored", "restores",
    "save", "saved", "saves", "return", "returned", "returns",
    "revert", "reverted", "reverts", "win", "wins", "won",
    "victory", "victorious", "escape", "escaped", "escapes",
    "stop", "stopped", "stops", "end", "ends", "ended",
}
# How many trailing sentences/moments to scan backward for resolution language.
# Bounded (not the whole document) so we don't reach back into early/mid-story
# COMPLICATION text that happens to share a keyword; comics that tack a short
# epilogue/coda tease (next issue's villain scheming) onto the true climax keep
# that tease within the last handful of sentences, so this window still finds the
# real resolution sentence underneath it.
_RESOLUTION_SCAN_TAIL = 4


def _has_resolution_language(text: str) -> bool:
    """Does `text` read as an actual resolution (defeat/aftermath), as opposed to
    the story trailing off mid-moment? Deterministic keyword check, offline."""
    return bool(set(re.findall(r"[a-z]+", (text or "").lower())) & _RESOLUTION_KEYWORDS)


def _last_resolution_unit(units: list[str]) -> str:
    """Scanning BACKWARD from the end of `units` (sentences or notable_moments,
    already trimmed to the tail window), the LAST-but-scanned-first unit that reads
    as a resolution — "" if none do. Scanning backward (not just checking the
    final unit) matters because a comic's true final sentence/moment is often a
    post-climax epilogue/coda tease for the NEXT story, not this moment's own
    resolution."""
    for u in reversed(units):
        if _has_resolution_language(u):
            return u
    return ""


def _resolution_label(comic_context: dict, tail: list[Beat]) -> str:
    """Short story-language line describing the ending the window is about to skip.
    Checked in order, each only within its own trailing window (`_RESOLUTION_SCAN_TAIL`):
    notable_moments, then plot_summary, then the richer canonical story_arc block —
    all STORY sources already in comic_context, never a VLM panel read. story_arc is
    checked last because plot_summary is often a trimmed synopsis that stops at the
    moment itself and never reaches the real ending, while story_arc is the fuller
    wiki-grounded block that grounds the WHOLE outline past it (the same source
    `outline_beats` itself draws on). Falls back to the outline's own tail-beat
    summaries only if NONE of the three story sources read as a resolution."""
    moments = comic_context.get("notable_moments") or []
    moments = [str(m).strip() for m in moments if str(m).strip()] if isinstance(moments, list) else []
    found = _last_resolution_unit(moments[-_RESOLUTION_SCAN_TAIL:])
    if found:
        return found

    def _sentences(text: str) -> list[str]:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]

    plot = str(comic_context.get("plot_summary", "")).strip()
    found = _last_resolution_unit(_sentences(plot)[-_RESOLUTION_SCAN_TAIL:])
    if found:
        return found
    story_arc = str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    found = _last_resolution_unit(_sentences(story_arc)[-_RESOLUTION_SCAN_TAIL:])
    if found:
        return found
    return " ".join(b.summary or b.name for b in tail).strip()


def _append_resolution_beat(
    window: list[Beat],
    beats_all: list[Beat],
    comic_context: dict,
    *,
    max_beats: int = _MICRO_WINDOW_MAX_BEATS,
    log: Callable[[str], None] | None = None,
) -> list[Beat]:
    """Generic mirror of recap's LANDING gate (write_script._ensure_ending_coverage):
    a mini-arc window (lead-in -> peak -> a couple follow-up beats) can stop well
    short of the STORY's actual ending when the payoff sits deep in a long outline
    (a moment at beat 11 of 20, the villain's defeat at beat 19 — the window's
    follow-up cap never reaches that far, and widening it that much would blow the
    window size budget). If the window doesn't already reach the outline's LAST
    beat, AND the skipped tail genuinely reads as a resolution (defeat/aftermath
    language — generic keyword check, no per-comic naming), append ONE synthesized
    beat so the Short lands moment -> consequence -> villain's defeat instead of
    stopping cold on the moment. No-op when the window already reaches the end, or
    the skipped ending doesn't read as a resolution (an open/ambiguous ending) —
    shipping short is safer than guessing a fake conclusion."""
    if not window or not beats_all or window[-1].id == beats_all[-1].id:
        return window
    if len(window) > max_beats:
        return window  # already at/over cap — nothing to add
    tail = beats_all[-2:] if len(beats_all) >= 2 else beats_all[-1:]
    reference = _resolution_label(comic_context, tail)
    if not _has_resolution_language(reference):
        return window
    prompt_text = _wrap_resolution_reference(reference)
    pages = sorted({pg for b in tail for pg in (b.page_refs or [])})
    chars = sorted({c for b in tail for c in (b.characters_active or [])})
    new_id = max([b.id for b in window] + [b.id for b in beats_all]) + 1
    resolution = Beat(
        id=new_id, function="RESOLUTION", name=prompt_text,
        page_refs=pages, key_panels=[], summary=prompt_text, cause="",
        characters_active=chars,
    )
    if log:
        log(f"[micro_moment] appended RESOLUTION beat (id {new_id}, pages {pages}): "
            f"{reference[:90]!r}")
    return window + [resolution]


# The GIST instruction wrapped around the raw reference sentence below — generic,
# no per-comic wording, mirrors the writer's own "state the outcome directly" rule.
_RESOLUTION_GIST = ("how this conflict is finally resolved — who wins, what happens "
                    "to the villain, and how it ends")


def _wrap_resolution_reference(reference: str) -> str:
    """Wrap the raw STORY-source sentence in a GIST instruction + explicit anti-copy
    marker before it becomes the RESOLUTION beat's `.name` (the only field
    `_window_block` shows the writer — see its docstring). Feeding the writer the
    bare source sentence got it copied near-verbatim, including the SOURCE's own
    vague nouns ("a symbiote tears itself free", "a flaming motorcycle seals the
    breach") — reads like a VLM panel description, not a story with characters in
    it (2026-07-16 tilt). Telling the writer explicitly not to copy it, and to
    retell with NAMED characters, pairs with the NAMED SUBJECT ONLY prompt rule."""
    return (f'Tell {_RESOLUTION_GIST}. Reference wording only — do NOT copy '
            f'verbatim, retell with NAMED characters: "{reference[:220]}"')


_MICRO_WRITE_SYSTEM = """You are MicroNarrator. You write ONE 35-60 second YouTube Short, documentary style, told plainly for a viewer with ZERO context.

TELL THE STORY, NOT THE PICTURES. Your source of truth is the STORY given below — the background plot, its meaning, and the key story moments. It is NOT a description of the comic art. Write what HAPPENS and WHY, the way you would tell a friend the story out loud. NEVER describe the artwork: no "a man with...", no "a figure holding...", no "we see", no "in this panel/frame", no colours / poses / lighting / camera for their own sake. Every scene's SUBJECT must be a story character doing a story action — if a line would only make sense to someone staring at the page, rewrite it as the plain STORY EVENT it stands for.

THE ONE JOB — a micro_moment exists to ANSWER the single question its hook makes a viewer ask.
The hook states a striking outcome; the viewer instantly wonders WHY / HOW did that happen, and SO
WHAT. Every scene you write exists ONLY to answer that: the context that makes the moment matter
(what happened, who was wronged, what is at stake), the moment itself, and what it means. Before
writing, name that question in your head, then make the arc resolve it. A beat that only serves the
question gets a full scene; a beat that is side-detail gets the BAREST bridge clause (or a few
words) — never a paragraph of its own. Weight your words toward the beats that answer WHY/HOW.
  Generic shape: hook "[hero] made [foe] break down." Viewer asks "why would that ever happen?"
  Arc answers: who got hurt / what is at stake -> the moment it breaks the foe -> what it reveals.

You are given the mini-arc as an ORDERED LIST OF BEATS — each is just a short LABEL plus which page(s) it is on, with the ★ PEAK beat marked (the moment itself). The label + page tell you WHICH story event this scene covers and in what order — they are a SPINE, not wording. Take the actual words from the STORY sources above; never copy a beat label verbatim (labels can be rough or carry names a newcomer wouldn't know). Write EXACTLY ONE scene per beat, in the SAME order, PLUS a separate hook line.

  HOOK (separate field, NOT a scene): ONE statement — NEVER a question — that restates the given
  title as a CONCRETE TWIST taken from THIS story. The winning shape is a specific reversal: the
  character does one shocking, concrete thing — then the impossible / opposite turn. That hidden
  contradiction IS the hook; it makes the viewer NEED the answer, so lead with it. (This SHARPENS
  the old "don't force a paradox" note: a paradox that is the story's REAL reversal is exactly
  right — only a disconnected, invented riddle is banned.)
    - CONCRETE, NEVER ABSTRACT. Anchor on something that visibly HAPPENS and a stranger can
      picture. BANNED are vague/poetic hooks with nothing to see: "whether it was worth it", "it
      cost him everything", "she planned every second", "the truth about who he really is". If you
      can't picture the moment, rewrite it as the concrete event.
    - NAME THE MAIN CHARACTER FIRST. Open on the household-name character the moment is about, in
      the first sentence. If the lead is NOT a household name, open on their plain role + the twist
      ("a small-town cop", "their leader") — never make the viewer learn a strange name at second one.
    - It must be the story's REAL twist (from the sources) — never an invented paradox. Restating
      the title's concrete outcome is always safe; sharpening it into the real reversal is better.
    ✓ title "[hero] finally walked away from [foe]" -> hook "[hero] walks away from [foe] the moment he's already won — and the reason is darker than it looks."
    ✗ "Why did [hero] walk away?"  (a question — that is the Q&A format, not this one)
    ✗ "It was the choice that cost him everything."  (abstract — nothing to picture)

  KEEP IT SIMPLE — this is ONE moment, not a plot recap:
    - MAIN CAST ONLY. Anchor on the few characters the moment is ABOUT: whoever is named in the
      title, plus whoever it happens to or because of. Everyone else is background — give them a
      one-word role or leave them out. Do not introduce a character the answer doesn't need.
    - CAMERA STAYS ON THE TITLE CHARACTER. In a beat where side characters act alongside the
      title character, write the sentence with the title character (or their enemy) as the
      SUBJECT; side characters get at most a 3-4 word clause ("while allies hold the line"),
      never their own sentence, never a physical description.
    - NAMED SUBJECT ONLY — no anonymous props acting on their own. Every action's SUBJECT
      must be a character already introduced by name or household role — never a bare,
      unnamed prop/thing ("a symbiote", "a motorcycle", "a figure", "a demon") doing
      something by itself. If you can't tie the actor to a named character, DROP that
      detail and state the outcome directly instead (who wins, who falls).
        ✗ "A symbiote tears itself free, and a flaming motorcycle seals the breach."
        ✓ "Venom tears free of its host, and Ghost Rider's bike seals the breach."
        ✓ (no clean named actor available) "And with that, the villain's reign collapses."
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
    - NEVER SPEAK THE BUBBLES (hard rule): you are the storyteller, not a voice actor —
      never quote character dialogue, no quotation marks around anything a character
      says. The panels already show the words; the viewer reads them there. When a line
      of dialogue IS the event, describe it indirectly in story language instead of
      repeating it:
        ✓ "Venom whispers that it's only temporary."
        ✓ "Peter screams at his own reflection."
        ✓ "Eddie shouts a mocking goodbye as he leaps."
        ✗ any quotation marks around a character's own words, however short or dramatic.
      Keep the narration 100% narrator-voice.
    - Scene 1 gives the MINIMUM setup a zero-context viewer needs — who this is,
      where we are.

  ENDING — the LAST line is THE LOOP. It must be SHORT, QUOTABLE, and CLOSE THE HOOK: bring back
  the hook's key word (or its exact contradiction) in the final line, so the end snaps shut on the
  opening and the viewer loops the Short. A soft, vague inward fade with no punch is the losing
  shape — never end on one.
    ✓ hook turns on "his real name" -> the last line returns to that exact phrase (third person,
      narrator voice, no quotation marks) so it echoes the opening — short, quotable, closes the loop.
    ✗ "he asks his reflection if he is a bad person"  (soft murmur — no quote, no loop)
  Pick ONE style for the LAST scene and declare it in "ending_style":
    - "thesis": ONE sentence stating what the moment MEANS, mirrored onto the character — and
      echoing the hook's key word.
    - "hardcut": the last scene IS the payoff/mic-drop line itself — your own
      narration line, never a character's quoted words — no separate meaning
      line, no landing, the video cuts off right on it.
    - "question": ONE open question that baits a comment (curiosity — never "subscribe").

  VISUAL BEATS (every scene) — split each scene into the 2-3 separate MOMENTS it contains, so
  Stage 5 can cut to a fresh image on each. You do NOT pick pages or panels — the pipeline maps
  each fragment to the right art automatically. Your ONLY job is to split at the visual seams:
    - "visual_beats" is a LIST OF STRINGS: the scene's OWN words, split at ITS punctuation /
      connective (and/but/then/comma/dash) into 2-3 fragments, each ONE separately-drawable
      moment (a new action, a new subject, a beat change).
    - VERBATIM ONLY: the fragments' exact words, in order, must concatenate back to "text" (you
      may only drop a comma/dash/connective at a split point). NEVER reword, add, or drop a word.
    - A short single-event scene (<=12 words) may be ONE fragment = the whole "text" — don't
      force a split where there is only one visual moment.

HARD RULES:
  - Plain B2 English. Concrete, no purple prose, no riddles, no lore a newcomer wouldn't know.
  - WEAVE THE WHY. If a STORY CONTEXT block is given above, state who the characters are to each other and why the moment matters in plain words, early — a zero-context viewer must NEVER watch an action without knowing why it lands (e.g. never show two people fight without saying they were once partners / what one did to the other). Never assume the viewer knows any character's history.
  - EXACTLY one scene per beat, in the SAME order given. Do NOT merge, split, reorder, or skip a beat.
  - Each scene under 40 words.
  - The hook plus ALL scenes together must land inside the WORD BUDGET given.
  - Third person storytelling — no "you" except (optionally) the hook and the final line.
  - Return ONLY JSON, no markdown fences.

Return shape:
{"hook": "<statement hook, not a question>", "ending_style": "thesis|hardcut|question", "scenes": [{"text": "...", "visual_beats": ["<verbatim fragment one>", "<verbatim fragment two>"], "connective": null, "beat_id": <id>}, ...]}"""


def _window_block(window: list[Beat], peak_idx: int) -> str:
    """The mini-arc SPINE fed to the story-first writer: one line per beat = id, function,
    page range, and the short LABEL only. Deliberately NO `summary` and NO `characters_active`
    — those are VLM panel prose / cast tags that made the writer describe the ART instead of
    telling the story (the "VLM tilt", 2026-07-16). The writer takes its wording from the
    STORY sources; this block just fixes scene count, order, and which page each maps to."""
    lines = []
    for i, b in enumerate(window):
        pg = f" (page{'s' if len(b.page_refs) > 1 else ''} {', '.join(map(str, b.page_refs))})" \
            if b.page_refs else ""
        mark = " ★ PEAK (the described moment — tense shift lands here)" if i == peak_idx else ""
        lines.append(f"{b.id}.{mark} {b.function}{pg}: {b.name}")
    return "\n".join(lines)


def _window_dialog_entries(window: list[Beat], story_pages: list[dict] | None) \
        -> list[tuple[int, str, str]]:
    """(page, speaker, verbatim text) dialog/OCR entries for the pages this window
    covers. Magi's OCR is the ground-truth text per the DIALOG_TRUTH contract;
    `page_dialog` already stores that preference on each block (`.ocr` overrides
    the VLM's `.text` at Stage 2 time) — we just read what is already there. []
    when nothing found (no story_pages, no dialog on these pages). Shared by the
    prompt's PANEL DIALOG block (`_window_dialog_block`) and the quote-fidelity
    validator lint (`_quote_issues`) — both must check the SAME verbatim source."""
    if not story_pages:
        return []
    from .._panel_index import page_dialog
    pages_by_no = {p.get("page_number"): p for p in story_pages}
    wanted = sorted({pg for b in window for pg in (b.page_refs or [])})
    out: list[tuple[int, str, str]] = []
    for pn in wanted:
        page = pages_by_no.get(pn)
        if not page:
            continue
        for tb in page_dialog(page):
            text = str(tb.get("ocr") or tb.get("text") or "").strip()
            if text:
                out.append((pn, tb.get("speaker") or "?", text))
    return out


def _named_speaker(spk: str) -> str | None:
    """`spk` if it reads as an actual identified name/role, else None. VLM/OCR
    speaker tags are sometimes a vague placeholder ("a figure", "a man", "?") when
    the art doesn't make the speaker clear — real names/roles are essentially
    never "a/an <noun>", so that prefix (plus the bare "?" filler) is the generic
    tell. Showing a placeholder AS IF it were a real identity is exactly what let
    a writer treat "[a figure]" as license to attribute a quote to whoever was
    convenient nearby (2026-07-16 mis-attribution: a Blackheart line narrated as
    "a burning Hulk who roars, '...'")."""
    s = (spk or "").strip()
    if not s or s == "?" or re.match(r"(?i)^(a|an)\b", s):
        return None
    return s


def _window_dialog_block(window: list[Beat], story_pages: list[dict] | None) -> str:
    """Verbatim dialog/OCR lines from the preprocessed pages this window covers, so
    the writer can QUOTE the payoff line instead of paraphrasing it. A vague
    placeholder speaker tag (see `_named_speaker`) is shown honestly as "speaker
    unclear" rather than passed through — the writer is told (QUOTE SPEAKER rule)
    to cross-check the STORY sources for who actually says it, or drop the quote,
    instead of reading the placeholder as a real identity. "" when nothing found
    (no story_pages, no dialog on these pages) — the writer prompt degrades
    gracefully to paraphrase, same as before this feature existed."""
    entries = _window_dialog_entries(window, story_pages)
    if not entries:
        return ""
    lines = [f'  p{pn} [{_named_speaker(spk) or "speaker unclear"}]: "{text}"'
             for pn, spk, text in entries]
    return ("PANEL DIALOG (for understanding only — NEVER quote these lines in the "
            "narration; the panels already show them, describe the moment in your own "
            "story-narration words instead):\n"
            + "\n".join(lines))


_DQUOTE_RE = re.compile(r'"([^"]+)"')
# Single-quote span: OPENING ' must not be glued to a letter (a real quote starts
# after whitespace/punctuation; a possessive/contraction apostrophe is glued to
# the word before it), content may contain an internal contraction apostrophe
# (THEY'VE) as long as it's immediately followed by a letter, and the CLOSING '
# must not be followed by a letter (a contraction's apostrophe always is).
_SQUOTE_RE = re.compile(r"(?<![A-Za-z])'((?:[^']|'(?=[A-Za-z]))+)'(?![A-Za-z])")


def _extract_quotes(text: str) -> list[str]:
    """Quoted spans in `text` — both "..." and '...' — for the quote-fidelity lint."""
    return [m.group(1) for m in _DQUOTE_RE.finditer(text)] + \
           [m.group(1) for m in _SQUOTE_RE.finditer(text)]


def _norm_quote_text(s: str) -> str:
    """Lower-cased, punctuation-stripped, whitespace-collapsed — for a loose
    substring compare between a scene's quoted text and a verbatim OCR dialog
    line (ellipses/periods/case must not cause a false "not verbatim" flag)."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", s.lower())).strip()


def _quote_issues(scenes: list[dict], dialog_lines: list[str]) -> list[str]:
    """QUOTE-FIDELITY lint (soft, retryable, 2026-07-16): a quoted phrase in a scene
    that is NOT a substring of any verbatim VERIFIED DIALOG line is very likely an
    invented or reworded quote — the system prompt requires quotation marks be
    reserved for lines copied exactly from that block. No-op when there IS no
    dialog block: nothing to verify against (the prompt tells the writer to use
    plain narration with no quotes in that case, but this mechanical check has
    no ground truth to compare to, so it stays silent rather than guess).

    NOTE this is a substring check, not a full-line-equality check: a quote that
    is a genuine (if partial) fragment of a real line — e.g. one word lifted out
    of a longer line — still passes, since those words DO appear in the dialog.
    It reliably catches outright fabricated/reworded quotes; catching a merely
    SHORTENED-but-real fragment is the system prompt's job (QUOTE-FIDELITY rule
    above), not this mechanical backstop's."""
    if not dialog_lines:
        return []
    norm_lines = [_norm_quote_text(l) for l in dialog_lines]
    issues: list[str] = []
    for i, s in enumerate(scenes):
        for q in _extract_quotes(str(s.get("text", ""))):
            nq = _norm_quote_text(q)
            if nq and not any(nq in line for line in norm_lines):
                issues.append(f"quote: {q!r} in scene {i + 1} is not a verbatim line "
                             f"from the VERIFIED DIALOG block")
    return issues


# Speech verbs used to spot WHO a scene attributes a quote to (the word right
# around one of these, in the same sentence as the quote). Generic verb list, no
# per-comic naming.
_SPEECH_VERB_RE = re.compile(
    r"\b(?:roars?|admits?|says?|said|shouts?|yells?|screams?|whispers?|mutters?|"
    r"growls?|declares?|snarls?|hisses?|gasps?|cries?|sneers?)\b", re.IGNORECASE)


def _name_tokens(name: str) -> set[str]:
    """Content-word tokens (len>=3) of a character name — "Red Hulk" -> {"red",
    "hulk"} — so a scene mentioning just "a burning Hulk" still counts as
    mentioning "Red Hulk" (a full-string match would miss it: the scene never
    writes the full name verbatim, per NAME ONLY HOUSEHOLD NAMES/paraphrasing).
    Same tokenizing convention as `_select_moment_window`'s `_on_focus`."""
    return {t for t in re.findall(r"[a-z]+", name.lower()) if len(t) >= 3}


def _quote_speaker_issues(
    scenes: list[dict],
    dialog_entries: list[tuple[int, str, str]],
    known_names: set[str],
) -> list[str]:
    """QUOTE-SPEAKER lint (soft, 2026-07-16): a quote verbatim-matched to a dialog
    entry whose OWN speaker tag is a real, recognized character name (see
    `_named_speaker`) — but the scene's sentence never mentions that name (by
    token, see `_name_tokens`) and instead names a DIFFERENT known character
    around a speech verb (roars/admits/says/...) — is very likely mis-attributed
    (real case: a Blackheart admission narrated as "a burning Hulk who roars,
    '...'"). No-op when the matching entry's speaker tag isn't a real recognized
    name — nothing solid to compare against (see `_named_speaker` / the prompt's
    QUOTE SPEAKER rule, which is the primary defense for that case)."""
    if not dialog_entries or not known_names:
        return []
    name_toks = {n: _name_tokens(n) for n in known_names}
    issues: list[str] = []
    for i, s in enumerate(scenes):
        text = str(s.get("text", ""))
        if not _SPEECH_VERB_RE.search(text):
            continue
        text_toks = set(re.findall(r"[a-z]+", text.lower()))
        for q in _extract_quotes(text):
            nq = _norm_quote_text(q)
            if not nq:
                continue
            for _pn, spk, dtext in dialog_entries:
                named = _named_speaker(spk)
                if not named:
                    continue
                real = named.strip().lower()
                if real not in known_names or nq not in _norm_quote_text(dtext):
                    continue
                if name_toks[real] & text_toks:
                    continue  # sentence DOES mention the real speaker (by token)
                others = sorted(n for n in known_names
                                if n != real and (name_toks[n] & text_toks))
                if others:
                    issues.append(
                        f"quote-speaker: scene {i + 1} quote matches {named!r}'s "
                        f"line but the sentence attributes it to {others!r} instead")
    return issues


# "a/an <noun> <action-verb>" — an anonymous prop/thing doing something on its own,
# the VLM-panel-description tilt the NAMED SUBJECT ONLY prompt rule targets ("a
# symbiote tears itself free", "a flaming motorcycle seals the breach"). Generic verb
# list, no per-comic naming.
_AMBIGUOUS_SUBJECT_RE = re.compile(
    r"\b(?:a|an)\s+[a-z]+\s+(?:tears?|seals?|flies?|rides?|smashes?|crashes?|roars?|"
    r"drags?|hurls?|drives?|throws?|grips?|swings?|strikes?|breaks?|drops?|deals?|"
    r"grabs?|pulls?|pushes?|slams?|charges?|snaps?|rips?|shatters?)\b",
    re.IGNORECASE,
)


def _ambiguous_subject_issues(scenes: list[dict], beats: list[Beat]) -> list[str]:
    """AMBIGUOUS-SUBJECT lint (soft, 2026-07-16): a scene whose only actor is an
    unnamed prop/noun ("a symbiote tears itself free", "a flaming motorcycle seals
    the breach") reads like a VLM panel description, not story — nothing named for
    the viewer to attach to. Flags a scene that matches the "a/an <noun> <verb>"
    shape AND names none of the window's known characters anywhere in its text.
    No-op when the window carries no character names at all (nothing to check against)."""
    names = {c.strip().lower() for b in beats for c in (b.characters_active or [])
             if len(c.strip()) >= 3}
    if not names:
        return []
    issues: list[str] = []
    for i, s in enumerate(scenes):
        text = str(s.get("text", ""))
        if _AMBIGUOUS_SUBJECT_RE.search(text) and not any(n in text.lower() for n in names):
            issues.append(f"ambiguous: scene {i + 1} names no character as the actor — "
                          f"give the action a NAMED subject or state the outcome directly")
    return issues


def _beat_text(b) -> str:
    """The narration words of a visual beat. WRITER-PICKS-PANEL beats are
    {"text","page","panel"} dicts; recap/legacy beats are plain strings — either way
    returns the text."""
    return str(b.get("text", "")).strip() if isinstance(b, dict) else str(b).strip()


def _window_panel_candidates(window: list[Beat], story_pages: list[dict] | None) \
        -> list[tuple[int, int, str]]:
    """(page, panel_idx, embed_text) for EVERY panel on the pages this window covers. panel_idx
    is the reading-order enumerate index — identical to Stage 5's _panel_pool key, so a pin
    binds directly there. embed_text = _panel_index.panel_embed_text (the SAME text Stage 2
    embedded and Stage 5 matches on), so a Stage-3 pin agrees with Stage 5's own matcher. []
    when no pages/panels."""
    if not story_pages:
        return []
    from .._panel_index import panel_embed_text
    pages_by_no = {p.get("page_number"): p for p in story_pages}
    wanted = sorted({pg for b in window for pg in (b.page_refs or [])})
    out: list[tuple[int, int, str]] = []
    for pn in wanted:
        page = pages_by_no.get(pn)
        if not page:
            continue
        page_tb = page.get("text_blocks")
        for idx, panel in enumerate(page.get("panels") or []):
            out.append((int(pn), idx, panel_embed_text(panel, page_tb)))
    return out


def _pin_beats_by_vector(
    scenes: list[dict],
    window: list[Beat],
    story_pages: list[dict] | None,
    *,
    floor: float,
    log: Callable[[str], None],
) -> list[float]:
    """PIN PHASE (story-first, 2026-07-16): after the writer's TEXT is locked, assign each
    visual-beat fragment the window panel whose embedding is closest (cosine) to the fragment,
    writing {"text","page","panel"} on the beat. VECTORS, not a second LLM call and not the
    writer: the pin is decided by the SAME cosine-on-richer-embed basis Stage 5's matcher uses
    (the project's validated best matcher), so the writer never has to read panel PROSE to pin —
    that is exactly what caused the VLM tilt. A fragment whose best panel is below `floor`
    (nothing on those pages draws it) is left UNPINNED (page/panel=None) so Stage 5's fuller
    matcher retries it. Returns per-scene best cosine (drives the ground-check). Graceful []
    when there are no panels or the embedding backend is down — every beat then flows through
    the Stage 5 matcher, byte-identical to the recap/Q&A path.

    STAGE3_NO_EMBED=1 (--no-embed, narration-only test mode) short-circuits this to the
    same graceful [] path — pins stay empty, no network embed call is made, Stage 5's
    matcher fills in panels at render time (valid existing fallback)."""
    from config import stage3_no_embed
    if stage3_no_embed():
        log("[stage3] embed skipped (--no-embed): vector pin (pins left empty, "
            "Stage 5 matcher will assign panels)")
        return []
    cands = _window_panel_candidates(window, story_pages)
    if not cands:
        return []
    from .. import _embedding
    if _embedding.backend_name() == "none":
        return []
    import numpy as np

    cand_vecs = _embedding.embed_batch([c[2] for c in cands])
    frag_texts: list[str] = []
    frag_locs: list[tuple[int, int]] = []
    for si, s in enumerate(scenes):
        for bi, b in enumerate(s.get("visual_beats") or []):
            if _beat_text(b):
                frag_texts.append(_beat_text(b))
                frag_locs.append((si, bi))
    if not frag_texts:
        return []
    frag_vecs = _embedding.embed_batch(frag_texts)

    scene_best = [0.0] * len(scenes)
    pinned = 0
    for (si, bi), fv in zip(frag_locs, frag_vecs):
        vb = scenes[si]["visual_beats"]
        b = vb[bi] if isinstance(vb[bi], dict) else {"text": _beat_text(vb[bi])}
        vb[bi] = b
        best_score, best_key = -1.0, None
        if fv is not None:
            for (pg, idx, _txt), cv in zip(cands, cand_vecs):
                if cv is None:
                    continue
                sc = float(np.dot(fv, cv))
                if sc > best_score:
                    best_score, best_key = sc, (pg, idx)
        if best_key is not None and best_score >= floor:
            b["page"], b["panel"] = best_key
            pinned += 1
        else:
            b["page"] = b["panel"] = None
        scene_best[si] = max(scene_best[si], best_score)
    log(f"[micro_moment] vector-pin: {pinned}/{len(frag_locs)} fragment(s) pinned to a window "
        f"panel (cos>={floor}); the rest fall back to the Stage 5 matcher")
    return scene_best


def _ground_issues(scenes: list[dict], window: list[Beat], scene_best: list[float],
                   floor: float) -> list[str]:
    """GROUND-CHECK: a body scene whose best fragment cosine is below `floor` describes
    something no panel on its OWN pages draws — a SOFT retry hint telling the writer to retell
    it from what is actually on those pages. Skips the LAST scene (the thesis/hardcut/question
    landing is allowed to be a thematic line with no dedicated panel). No scores → no issues
    (embedding unavailable → don't block)."""
    if not scene_best:
        return []
    issues: list[str] = []
    for i, _s in enumerate(scenes):
        if i >= len(scene_best) or i == len(scenes) - 1:
            continue
        if scene_best[i] < floor:
            beat = window[i] if i < len(window) else None
            pages = ", ".join(map(str, beat.page_refs)) if (beat and beat.page_refs) else "?"
            issues.append(
                f"scene {i + 1} describes something not drawn on page(s) {pages} — retell that "
                f"scene using only what actually happens on those pages (best panel match was weak)")
    return issues


def _story_sources_block(comic_context: dict) -> str:
    """The STORY-language sources the writer draws its wording from (2026-07-16 story-first):
    the theme (story_meaning — THEME ONLY, for hook/landing, never a scene) and the key story
    moments (notable_moments — clean story beats). Both are new Stage-1 fields; each is included
    only when present, so old projects that carry neither degrade to plot_summary-only (the
    prior behaviour)."""
    parts: list[str] = []
    meaning = str(comic_context.get("story_meaning", "") or "").strip()
    if meaning:
        parts.append(
            "STORY MEANING (THEME ONLY — use for the hook and the ending/landing line; this is "
            "NOT a scene to render, do not narrate it as an event):\n" + meaning[:700])
    moments = comic_context.get("notable_moments") or []
    moments = [str(m).strip() for m in moments if str(m).strip()] if isinstance(moments, list) else []
    if moments:
        parts.append(
            "KEY STORY MOMENTS (told in story language — draw your scene WORDING from these, not "
            "from the beat labels):\n" + "\n".join(f"- {m}" for m in moments[:12]))
    return "\n\n".join(parts)


def _story_context_block(comic_context: dict) -> str:
    """The WHO/WHY a zero-context viewer needs, from an OPTIONAL top-level
    comic_context["story_context"] = {relationships, stakes_why, constant_broken,
    viewer_context} (any subset). "" when the field is absent or empty — a project
    that never set it produces a byte-identical writer prompt (target_moment path
    unchanged). Mirrors explore_answer's viewer-context block so both modes teach the
    writer the same WEAVE THE WHY discipline."""
    sc = comic_context.get("story_context")
    if not isinstance(sc, dict):
        return ""
    order = (
        ("viewer_context", "VIEWER CONTEXT (say this early — who/what this is, the baseline a stranger needs)"),
        ("relationships", "RELATIONSHIPS (who the characters are to each other — state it plainly in the narration)"),
        ("stakes_why", "WHY IT MATTERS (why this moment is remarkable / what rule it breaks / what it costs)"),
        ("constant_broken", "THE CONSTANT BEING BROKEN (the famous rule this moment violates)"),
    )
    lines = [f"- {label}: {v}" for key, label in order
             if (v := str(sc.get(key, "") or "").strip())]
    if not lines:
        return ""
    return ("STORY CONTEXT (viewer needs this — weave it into the narration in plain words; "
            "never assume the viewer knows any character's history):\n" + "\n".join(lines))


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
    clarity_fixes: str = "",
) -> tuple[dict, str]:
    fix_block = ""
    if issues:
        fix_block = "PREVIOUS DRAFT HAD ISSUES — FIX THESE:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
    title = str(comic_context.get("title", "")).strip()
    plot = str(comic_context.get("plot_summary", "")).strip() \
        or str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    peak_idx = _peak_index(window, target_moment)
    dialog_block = _window_dialog_block(window, story_pages)
    sources_block = _story_sources_block(comic_context)
    context_block = _story_context_block(comic_context)
    user = (
        f"TITLE (mirror this in the hook — restate/paraphrase it, name the character "
        f"in sentence 1): {title}\n"
        f"THE MOMENT TO TELL (do not stray beyond it): {target_moment}\n\n"
        f"{fix_block}"
        f"{clarity_fixes}"
        + (f"{context_block}\n\n" if context_block else "")
        + f"BACKGROUND PLOT (ground truth — every fact + 'why' must come from here):\n"
        f"{plot[:1800]}\n\n"
        + (f"{sources_block}\n\n" if sources_block else "")
        + f"BEATS (spine only — label + page, one scene each, in this EXACT order; take your "
        f"WORDING from the STORY sources above, not from these labels):\n"
        f"{_window_block(window, peak_idx)}\n\n"
        + (f"{dialog_block}\n\n" if dialog_block else "")
        + f"WORD BUDGET: {_MICRO_WORDS_MIN}-{_MICRO_WORDS_MAX} words TOTAL across the hook line "
        f"AND all {len(window)} scenes (each scene under {_MICRO_SCENE_MAX_WORDS} words).\n"
        f'Return JSON: {{"hook": "...", "ending_style": "thesis|hardcut|question", '
        f'"scenes": [{{"text": "...", "visual_beats": ["<verbatim fragment>", "..."], '
        f'"connective": null, "beat_id": {window[0].id}}}, ... one per beat ...]}}.'
    )

    def _valid(raw: str) -> bool:
        p = _extract_json(raw)
        return (isinstance(p, dict) and str(p.get("hook", "")).strip() != ""
                and isinstance(p.get("scenes"), list) and len(p["scenes"]) == len(window))

    chain = [model] if model else list(CREATIVE_LLM_MODELS)
    # 2200 (was 1600) headroom for a whole-story micro: the raised word band (up to
    # ~320w) plus per-scene visual_beats (which repeat the text) can push a many-beat
    # window's JSON past 1600 tokens — a truncated reply fails _valid and wastes retries.
    raw, mdl = call_with_chain(
        system=_MICRO_WRITE_SYSTEM, user=user, models=chain, max_tokens=2200,
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
    focus_tokens: set[str] | None = None,
    dialog_lines: list[str] | None = None,
    dialog_entries: list[tuple[int, str, str]] | None = None,
) -> list[str]:
    """hook is a statement in band that names a character up front, one scene per
    beat, per-scene cap, TOTAL word band, and a declared ending_style (thesis /
    hardcut / question — all three are valid, none are lint-penalized against the
    others). Feeds the bounded retry loop in write_micro_moment(); this function
    never raises itself, but the caller re-raises the structural issues (missing
    visual_beats, non-verbatim beats, wrong scene count) once retries are
    exhausted — everything else here (hook length, you-quota, quote-fidelity,
    quote-speaker, ambiguous-subject, ...) stays a soft, ship-anyway lint. Panel
    PINS are assigned separately by _pin_beats_by_vector AFTER the text is
    locked, so this validator no longer sees or checks them.

    `focus_tokens` (from `_focus_tokens(title, target_moment)`, same set used to
    filter the beat window) is optional and defaults to None = skip: when given,
    lints the SOFT "focus:" issue below (< 60% of body scenes mention a focus
    word) — a signal the draft drifted off the title's moment even though the
    window itself was already filtered.

    `dialog_lines` (raw verbatim OCR/dialog text for the window's pages, from
    `_window_dialog_entries`) is optional and defaults to None = skip: when given
    (non-empty), lints the SOFT "quote:" issue via `_quote_issues` — a quoted
    phrase in a scene that isn't a substring of any of these lines.

    `dialog_entries` (the full (page, speaker, text) tuples from the same
    `_window_dialog_entries` call) is optional and defaults to None = skip: when
    given, lints the SOFT "quote-speaker:" issue via `_quote_speaker_issues` — a
    verbatim-matched quote attributed in the sentence to a DIFFERENT known
    character than the dialog entry's own (real, recognized) speaker."""
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
    if focus_tokens and scenes:
        on_focus = sum(
            1 for s in scenes
            if any(len(tok) >= 3 and tok in focus_tokens
                  for tok in re.findall(r"[a-z]+", str(s.get("text", "")).lower()))
        )
        ratio = on_focus / len(scenes)
        if ratio < 0.6:
            issues.append(f"focus: only {on_focus}/{len(scenes)} scenes ({ratio:.0%}) mention a "
                          f"title/target-moment focus word — draft may be drifting off the moment")
    if dialog_lines:
        issues.extend(_quote_issues(scenes, dialog_lines))
    if dialog_entries:
        issues.extend(_quote_speaker_issues(scenes, dialog_entries, names))
    issues.extend(_ambiguous_subject_issues(scenes, beats))
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
    clarity_fixes: str = "",
) -> Narration:
    """Orchestrate the micro_moment writer: read target_moment -> outline the full
    issue (reuse recap's grounded beat pipeline) -> select the moment mini-arc
    window -> LLM writes hook + one scene per windowed beat from STORY sources only
    (no panel prose) -> vector-pin each fragment to its best window panel + ground-
    check (retry a scene that draws nothing on its pages) -> beat-anchor the body ->
    prepend the hook as the is_intro scene -> banner."""
    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}

    target_moment = str(comic_context.get("target_moment", "") or "").strip()
    if not target_moment:
        raise ValueError(
            "[micro_moment] comic_context.json has no 'target_moment'. This mode tells "
            "ONE moment, so set a top-level string field describing it (and an estimated "
            'page if known), e.g. "target_moment": "Punisher forces Juggernaut to throw '
            'up during their brawl, around page 14". Then re-run Stage 3 --mode micro_moment.')
    title = str(comic_context.get("title", "")).strip()
    # Computed once, reused for the window's character focus-filter AND the
    # validator's soft on-topic lint below — same "who/what this Short is about"
    # vocabulary for both.
    focus_tokens = _focus_tokens(title, target_moment)

    # Reuse the tuned, wiki-grounded, panel-grounded recap outliner for the WHOLE
    # issue, then narrow to the moment. We deliberately do NOT run the recap
    # beat-impact critic (it keeps the whole-story cold-open/climax/landing) — the
    # window IS the trim here.
    beats_all, beats_model = outline_beats(
        comic_context, story_pages, mode, hook_hint=hook_hint, model=model,
        progress=progress, debug_dump=dump, story_map=None, direction=direction)
    # Context-aware LLM segmenter first (keeps far setup so the whole story lands);
    # falls back to the deterministic positional+token heuristic on any failure /
    # knob off / --no-embed (see _segment_moment_window).
    window = _segment_moment_window(beats_all, target_moment, title=title,
                                    model=model, progress=progress, log=log)
    if window is None:
        window = _select_moment_window(beats_all, target_moment, title=title, log=log)
    if not window:
        raise RuntimeError(
            f"[micro_moment] outline produced no beats for {comic_context.get('title', '?')!r} "
            f"— cannot locate the moment. Check Stage 2 preprocessing / plot_summary.")
    # RESOLUTION gate (generic mirror of recap's LANDING gate): a positional mini-arc
    # window can stop well short of the outline's actual ending when the moment sits
    # deep in a long story (moment at beat 11 of 20, the villain's defeat at beat 19).
    # Appends ONE synthesized beat when the skipped tail genuinely reads as a
    # resolution — no-op otherwise (see _append_resolution_beat docstring).
    window = _append_resolution_beat(window, beats_all, comic_context, log=log)
    log(f"[micro_moment] moment window = beat(s) {[b.id for b in window]} of {len(beats_all)} "
        f"(target: {target_moment[:80]!r})")
    dialog_entries = _window_dialog_entries(window, story_pages)
    dialog_lines = [t for _, _, t in dialog_entries]

    def _score(p: dict) -> list[str]:
        """Two-phase per draft: PIN each fragment to its best window panel (vector), then
        validate the TEXT + run the ground-check on the pin cosines. Panels are pinned in
        place on the accepted draft, so no separate pin pass is needed after the loop."""
        scns = p.get("scenes") or []
        scene_best = _pin_beats_by_vector(scns, window, story_pages,
                                          floor=_MICRO_GROUND_FLOOR, log=log)
        iss = _validate_micro_scenes(p.get("hook", ""), scns, window, p.get("ending_style"),
                                     focus_tokens=focus_tokens, dialog_lines=dialog_lines,
                                     dialog_entries=dialog_entries)
        iss += _ground_issues(scns, window, scene_best, _MICRO_GROUND_FLOOR)
        return iss

    parsed, mdl = _call_micro_writer(window, comic_context, target_moment, model=model,
                                     progress=progress, debug_dump=dump, story_pages=story_pages,
                                     clarity_fixes=clarity_fixes)
    issues = _score(parsed)
    for attempt in range(_MICRO_WRITE_MAX_RETRIES):
        if not issues:
            break
        log(f"[micro_moment] draft has {len(issues)} issue(s); retrying "
            f"({attempt + 1}/{_MICRO_WRITE_MAX_RETRIES}): {issues}")
        parsed, mdl = _call_micro_writer(window, comic_context, target_moment, model=model,
                                         progress=progress, debug_dump=dump,
                                         story_pages=story_pages, issues=issues,
                                         clarity_fixes=clarity_fixes)
        issues = _score(parsed)
    if issues:
        hard = [iss for iss in issues if any(m in iss for m in _MICRO_HARD_ISSUE_MARKERS)]
        if hard:
            raise RuntimeError(
                f"[micro_moment] writer draft still structurally broken after "
                f"{_MICRO_WRITE_MAX_RETRIES} retries: {hard}")
        log(f"[micro_moment] shipping with unresolved issue(s): {issues}")

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
