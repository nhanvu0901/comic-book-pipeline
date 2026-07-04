"""Stage 3 narration writer: outline_beats -> build_glossary -> write_scenes -> validate."""
import json
import os
import random
import re
import statistics
from pathlib import Path
from typing import Callable

from config import (CREATIVE_LLM_MODELS, ENABLE_LOGIC_CRITIC, ENABLE_LOOP_TEASE,
                    ENABLE_TITLE_BANNER, FIDELITY_LLM_MODELS, LOGIC_CRITIC_MIN_BEATS,
                    OPENROUTER_MODEL, PROJECTS_ROOT)
from .modes import MODES_BY_KEY
from .schema import Beat, CharacterEntry, Glossary, Narration, Scene
from ._llm import call_with_chain
from .._embedding import semantic_sim as _semantic_sim
from .._panel_index import panel_embed_text as _panel_embed_text
from .story_architect import render_story_map_block, _tokens


# CALIBRATED FROM 3 REAL RENDERS (Resemble Carl voice + the default --atempo 1.35,
# which is what we actually ship — NOT the stale 1.1/2.88 figure). Measured:
#   299 words → 84.5s  (3.54 wps)
#   288 words → 87.1s  (3.31 wps)
#   305 words → 87.2s  (3.50 wps)
# → effective rate ≈ 3.4 words/sec. (The old 2.88 was the 1.1-atempo pace we no
# longer use, so it under-estimated duration and let scripts run long.)
#
# LENGTH TARGET = the viral cluster. Verbatim mining of 23 competitor Shorts found
# the hits cluster at 48-71s; an 86s lore-dense one flopped. We aim the FINISHED
# audio at ~60-75s. The teaser intro (~14 words) is prepended on top of the body,
# so at 3.4 wps: body 195-245 + ~14 intro = 209-259 final words → ~61-76s. That is
# the band below. (60-75s ≈ 204-255 final words at 3.4 wps; minus the ~14-word
# intro → 190-241 body → rounded to the 195-245 constants.)
#
# SINGLE SOURCE OF TRUTH for the word budget. Previously three places disagreed
# (system prompt said 175-195, this user-message budget said 175-260, and the
# validator demanded 230-290) — the validator won and pulled output to ~283
# words of long, compound, multi-event sentences. All three now read these
# constants / the validator band below.
_TARGET_WORDS_MIN = 195   # body floor: 195 + ~14 intro = 209 final ≈ 61s at 3.4 wps.
_TARGET_WORDS_MAX = 245   # body ceiling: 245 + ~14 intro = 259 final ≈ 76s at 3.4 wps —
                          # the top of the 60-75s viral cluster. RAISED 220→245 (2026-07-03):
                          # the 2.88-based 220 ceiling was calibrated to the wrong pace and
                          # ran short of the cluster; retuned to the empirical 3.4 wps from the
                          # 3 renders above. Trim still comes from MAIN-POINT FOCUS (rule 2.5),
                          # NOT from dropping climax/ending beats.
_WORDS_PER_SEC = 3.4     # MEASURED at the shipped --atempo 1.35 pace (3 renders above); was 2.88 at the unused 1.1 pace

_SCENE_MIN_WORDS = 5     # punch sentences go as low as 5w ("Stating they would
                         # die anyway.") — floor must allow them, not block them.
_SCENE_MAX_WORDS = 18    # HARD line cap (was 24): the accepted register averages
                         # ~11 words/line; 17w+ lines were the #1 cause of blown
                         # word budgets. A causal 'why' clause must fit inside 18.
_FINALE_MAX_WORDS = 24   # headroom for the LAST TWO story lines only (rule 8.68):
                         # the twist-unpack + thematic-mirror lines carry the story's
                         # biggest idea and the approved register runs 22-24 words
                         # (learned from a hand-fixed ending, 2026-07-03).
_TARGET_SENT_LEN = 14    # channel-punchy median; used by median soft-validator
_PUNCH_MAX_WORDS = 11    # a "punch" sentence: lands one beat hard
_MIN_PUNCH_SCENES = 3    # enforce variance toward SHORT (the channel signature)
_HOOK_MIN_WORDS = 14
_HOOK_MAX_WORDS = 26

# Connectives a scene MAY open with — CHOOSE BY MEANING, never to hit a frequency.
# Contrast words ("But", "However") are reserved for a GENUINE reversal of the prior
# beat; the rest are sequence/time or consequence. A scene needs NO connective when
# none is natural (scene-setting/context) — open subject-first (connective=null) then.
# Order here is deliberate: sequence/time + consequence first, contrast LAST, so the
# writer doesn't reach for "But" as the default.
_CONNECTIVES = (
    "Then", "Now", "Soon", "As", "When", "After", "Eventually", "Meanwhile", "Until",
    "So", "With", "Instead", "Suddenly", "Just then", "That's when",
    "But", "However",
)


def _load_direction(project_name: str) -> dict:
    """Load projects/<name>/direction.json — the human director's spec (POV,
    naming policy, reveal placement, must-have beats). Free-form, all fields
    optional; missing file or bad JSON -> {} (the spec is opt-in, never blocking)."""
    if not project_name:
        return {}
    path = PROJECTS_ROOT / project_name / "direction.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _direction_block(direction: dict) -> str:
    """Render the director's spec as a BINDING prompt block, "" when empty.
    Prepended to a phase's USER message (never the system prompt) so it reads
    as human instruction overriding this run's defaults."""
    if not direction:
        return ""
    lines = [
        "╔══ DIRECTOR'S SPEC (HUMAN — BINDING) ══╗",
        "These are the human director's instructions. They OVERRIDE any "
        "conflicting default rule in this prompt (naming conventions, reveal "
        "placement, POV).",
    ]
    pov = str(direction.get("pov", "")).strip()
    if pov:
        lines.append(f"POV: {pov}")
    naming = [str(n).strip() for n in (direction.get("naming") or []) if str(n).strip()]
    if naming:
        lines.append("NAMING:")
        lines.extend(f"  - {n}" for n in naming)
    reveal = str(direction.get("reveal", "")).strip()
    if reveal:
        lines.append(f"REVEAL: {reveal}")
    beats = [str(b).strip() for b in (direction.get("must_have_beats") or []) if str(b).strip()]
    if beats:
        lines.append("MUST-HAVE BEATS:")
        lines.extend(f"  - {b}" for b in beats)
    notes = str(direction.get("notes", "")).strip()
    if notes:
        lines.append(f"NOTES: {notes}")
    lines.append("╚═════════════════════════════════════╝")
    return "\n".join(lines) + "\n\n"


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
_ALLOWED_HOOK_ARCHETYPES = ("interrogative", "temporal-when", "temporal-other", "scenic", "character_action")


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
    # Character-first ACTION opener: a capitalized subject (1-4 name tokens, e.g.
    # "Bruce Banner") followed by a verb. The old form only matched a SINGLE-token
    # subject + a short fixed verb list, so natural openers the LLM writes
    # ("Bruce Banner became…", "Banner vowed…") fell through to other_character and
    # were rejected by the intro validator. Broadened to any capitalized subject +
    # a lowercase verb-start. KEEP IDENTICAL to benchmark_builder.classify_hook.
    if re.match(r"^[A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+){0,3}\s+[a-z]", first12):
        return "character_action"
    return "other_character"


_HOOK_STOPWORDS = frozenset(
    "a an the and or but of to in on at by for with from into as is are was were be been "
    "her his its their she he they it him them who that this then so when after while during "
    "once had has have did do does no not".split()
)


def _safe_beat_int(v, default: int) -> int:
    """Beat ids from the LLM are sometimes non-numeric ('8b', '8.5', '') — used for
    inserted bridge beats. Extract the leading integer, else fall back to the positional
    default (anchoring re-keys beats positionally, so the exact id is not load-bearing).
    Fixes a hard crash: int('8b') raised ValueError and killed the whole outline."""
    try:
        return int(v)
    except (ValueError, TypeError):
        m = re.match(r"-?\d+", str(v or "").strip())
        return int(m.group()) if m else default


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


_CAP_STOP = {"The", "Then", "Now", "And", "But", "With", "His", "Her", "When",
             "After", "Before", "As", "So", "Inside", "Atop", "This", "That",
             "Year", "Long", "Only", "Still", "Meanwhile", "Later", "Soon", "One",
             "He", "She", "They", "It"}
# NOTE: the PRESENTED identity is never flagged automatically — the hero is called
# by it from the FIRST scenes, so it lands in the early set and can't be reveal-only.


def _reveal_only_names(body_scenes: list[dict]) -> set[str]:
    """Proper-noun names that surface in the LATE (reveal) portion of the narration
    but are NOT established in the opening SETUP. Naming one in the intro/banner
    spoils a concealed-identity twist (e.g. "Reed Richards" when the whole trick is
    that he believes he is Doom) — including the case where the true name is dropped
    once mid-story as a "vision he dismisses" (that still must not lead the hook).
    Compared against the SETUP (first 2 scenes), not all earlier text, so a true name
    revealed mid-story is still caught while names ESTABLISHED up front are safe.
    General — derived from the narration itself, no per-comic hardcoding."""
    kept = [s for s in (body_scenes or [])
            if not s.get("is_intro") and not s.get("is_outro")]
    if len(kept) < 4:
        return set()
    cut = max(1, round(len(kept) * 0.30))
    setup = " ".join(str(s.get("text", "")) for s in kept[:2])
    late = " ".join(str(s.get("text", "")) for s in kept[-cut:])
    setup_names = set(re.findall(r"\b[A-Z][a-z]{2,}\b", setup))
    late_names = set(re.findall(r"\b[A-Z][a-z]{2,}\b", late))
    return {n for n in (late_names - setup_names) if n not in _CAP_STOP}


_INTRO_SYSTEM = """You are HookWriter. You produce ONE short teaser intro sentence for a YouTube Short about a comic. It is the first thing the viewer hears — it must grab attention and tease the premise WITHOUT spoiling the ending.

BEST HOOK SHAPE (channel data — the 3 BIGGEST hits, 3.24M / 1.70M / 1.38M views, ALL used it): a concrete, ordinary moment + a "thought/believed [normal] ... until [dark turn]" contrast pivot. Picture a normal scene, then break it. PREFER this whenever the story allows.
  ✓ "Nightwing thought he had the perfect life with the Titans, until it all fell apart."
  ✓ "When Superman came home, Lois thought he'd been gone hours, until she learned the truth."
  ✓ "The Flash thought he saved an innocent man, until he found out what he really wanted."

Pick the ONE archetype that fits THIS story, then write the line. You MUST begin with the exact opener words shown for your chosen archetype, or the hook is rejected:

  • temporal-when  — a STATEMENT beginning with "When ". BEST for the pivot. Ends with ".".
        e.g. "When Superman came home, Lois thought he'd been gone hours, until she learned the truth."
  • character_action — a STATEMENT beginning with the hero's NAME + a verb (thought/believed/woke/found/broke/entered/stood/was/had). NAME-FIRST IS GOOD here — lead with the hero, then pivot. Ends with ".".
        e.g. "Nightwing thought he had the perfect life, until it all fell apart."
        e.g. "Peter woke in a body that was no longer his."
  • temporal-other — a STATEMENT beginning with "After", "While", "During", or "Once". Ends with ".".
        e.g. "After one ordinary night, Illyana's whole world stopped making sense."
  • scenic         — a STATEMENT beginning with "In a <adjective> universe/reality/world" (or "In <year>"). Ends with ".".
        e.g. "In a broken reality, Magik turned her back on the X-Men for good."
  • interrogative  — LAST RESORT ONLY. A QUESTION beginning with "Ever wonder", "What if", "What would", "Why", "How", "Can", "Could", "Would". Ends with "?". ZERO of the channel's biggest hits opened with a question — use ONLY if no concrete scene + pivot can be built from this story.
        e.g. "Could a girl who clawed out of hell ever feel safe again?"

HARD RULES for the intro line:
  - 7-18 words, exactly ONE sentence.
  - ZERO PRIOR KNOWLEDGE (BINDING): a viewer who has NEVER read this comic must fully
    grasp the line. NO continuity/lore references — no event names, cosmic titles,
    prior-series callbacks, or character history a newcomer wouldn't know. Lore-dense
    hooks are the FLOP signature.
      ✗ "Superman became King Omega due to the sacrifice of the Time Trapper." (lore soup — no newcomer parses it)
      ✓ "Superman came home to a world that had already moved on without him." (plain, instant)
  - PLAIN LANGUAGE (BINDING): use the simplest phrasing a 12-year-old gets INSTANTLY
    on first listen. No abstract riddles, no literary wordplay, no double meanings.
      ✓ "Doom woke up in a ruined future, unable to remember who he was."
        (concrete situation, plain words — the register to aim for)
      ✗ "What if the man who lost everything was the only one who could save it?"
        (abstract riddle — no concrete image, nothing a viewer can picture)
  - LEAD WITH VISCERAL, HUMAN-SCALE STAKES when the story offers one — a concrete image
    beats an abstract one ("found a girl locked in a basement" >> "surrounded by angels
    led by a young leader"). Pick the moment a viewer can feel.
  - Name the hero AND the premise so a viewer instantly grasps the stakes.
  - It is a TEASER, not a summary — do NOT reveal the ending/twist.
  - CONCEALED IDENTITY: if the protagonist's TRUE name/identity is itself the twist —
    they BELIEVE they are someone else and the real identity is disclosed only late —
    you MUST refer to them by the BELIEVED identity, NEVER the true name. Naming who
    they really are here spoils the whole story. (e.g. call him "Doctor Doom", the
    identity he's convinced he is — not the real name revealed at the climax.)
  - No meta talk ("in this video", "today", "let's see"). No spoilers.
  - Begin with the EXACT opener words for your chosen archetype (above).
  - TEASE THE WHOLE STORY'S HOOK — the central premise, conflict, irony, or price
    paid — NOT the literal opening scene. The FIRST narration line already
    describes the opening event, so if your intro just restates that event the
    video says the same thing twice. Frame the broader stakes instead.
    ✗ (restates opening beat) "When Illyana returned from Limbo, she rejected the X-Men."
    ✓ (teases the whole hook)  "Magik survived hell, until she realized she'd become the very monster she fled."
  - BE TRUE TO THE PLOT above — never invent a framing it doesn't support: do NOT say
    a hero "took a darker path" / "turned evil" / "went rogue" unless the plot says so.
  - Do NOT use "What if" / "Ever wonder what if" ALTERNATE-REALITY framing for a
    CANONICAL story — it falsely implies an alternate timeline or a different-dimension
    hero (it confuses the viewer). Reserve "what if" ONLY for genuine What-If /
    alternate-universe comics.

Return ONLY JSON, no markdown: {"archetype": "interrogative|temporal-when|temporal-other|scenic|character_action", "intro_line": "..."}"""


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


def _character_names(comic_context: dict) -> list[str]:
    """Display names from comic_context['characters'], which may be a list of plain
    name STRINGS (top-level convention) OR a list of {name,...} DICTS (summary
    shape / hand-built context). Never raises on a dict — `", ".join(...)` of a
    dict list was a TypeError that crashed generate_intro. Skips empty names."""
    out: list[str] = []
    for c in (comic_context.get("characters") or []):
        nm = (c.get("name", "") if isinstance(c, dict) else str(c)).strip()
        if nm:
            out.append(nm)
    return out


_CONCRETE_EVENT_RE = re.compile(
    r"\b(fall|falls|fell|fallen|die|dies|died|death|dead|kill|kills|killed|"
    r"reveal|revealed|learn|learned|learns|name|window|thrown|threw|fight|"
    r"fought|survive|survived|mask|armor|throne)\b", re.I)


def _outro_is_concrete(line: str, comic_context: dict) -> bool:
    """A thematic outro must contain something CONCRETE — a character name from
    this comic, or a physical ending event — otherwise it's the abstract-wordplay
    style the user rejects ('He spent everything becoming someone he had never
    been.'). Code guard, because the LLM keeps self-grading its own abstraction
    as 'concrete'."""
    low = " " + line.lower() + " "
    for nm in _character_names(comic_context):
        for tok in nm.split():
            if len(tok) > 2 and (" " + tok.lower()) in low:
                return True
    return bool(_CONCRETE_EVENT_RE.search(line))


def generate_intro(
    comic_context: dict,
    *,
    avoid_text: str = "",
    forbid_names: set[str] | None = None,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
    direction: dict | None = None,
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
    chars = ", ".join(_character_names(comic_context))

    avoid_block = ""
    if avoid_text.strip():
        avoid_block = (
            f"\nDO NOT restate, narrate, or paraphrase this OPENING narration line — the "
            f"video already says it, so your intro must tease the broader premise instead, "
            f"with DIFFERENT wording and a different angle:\n  \"{avoid_text.strip()}\"\n"
        )

    forbid = {n for n in (forbid_names or set()) if n}
    forbid_block = ""
    if forbid:
        forbid_block = ("\nFORBIDDEN — these are LATE-REVEAL spoilers (the hero's true "
                        "identity is withheld until the climax); NEVER name them in the "
                        "intro, refer to the hero by the identity he BELIEVES he is: "
                        f"{', '.join(sorted(forbid))}\n")
    user = (
        _direction_block(direction or {})
        + f"COMIC TITLE: {title}\n"
        f"PUBLISHER: {comic_context.get('publisher','?')}\n"
        f"KEY CHARACTERS: {chars or '?'}\n\n"
        f"PREMISE / PLOT (ground truth):\n{plot[:1800]}\n"
        f"{avoid_block}"
        f"{forbid_block}\n"
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
        # Reject an intro that spoils a concealed-identity reveal.
        low = line.lower()
        if any(n.lower() in low for n in forbid):
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
        fallback = f"When {hero} woke up, the whole world had already turned against him."
        log(f"[stage4] intro LLM failed ({type(exc).__name__}); using fallback: {fallback!r}")
        return {"story_type": "temporal-when", "intro_line": fallback}


_OUTRO_SYSTEM = """You are OutroWriter. You write ONE short THEMATIC closing line for a YouTube Short retelling of a comic — the final sentence the viewer hears.

It must capture the EMOTIONAL / THEMATIC core of the story — what it was REALLY about — in a punchy, resonant line. Think of the lesson, the irony, or the cost the hero paid.

HARD RULES:
  - 4-14 words, exactly ONE sentence, ends with ".".
  - NO plot summary, NO "the comic is", NO comic title, NO character names required.
  - It is NOT a question. No meta talk ("in this video"). No hashtags.
  - Grounded in THIS story's actual theme (below) — never a generic platitude.
  - Punchy and shareable — the kind of line a viewer would quote or screenshot.
  - SIMPLE, PLAIN language. Say it straight — no purple/grandiose phrasing, no
    forced poetry. A clear line beats a fancy one.
  - IELTS 6.5 / B2 PLAIN ENGLISH: state the actual ending FACT in short, common
    words — not a riddle, wordplay, or a double meaning stacked on itself.
      ✓ "Reed finally learned his name, right before he died." (concrete ending fact)
      ✗ "The name he refused was the one that always fit." (too abstract — no
        concrete fact, just wordplay)
      ✗ "He spent everything becoming someone he had never been." (still abstract —
        no person, no event)
  - HARD REQUIREMENT: the line MUST contain at least one CONCRETE element from the
    ending — a character's name, or a physical event (a fall, a death, a reveal).
    A line with neither is rejected.

Examples (for OTHER comics — match the TONE, not the words):
  - "The scariest monster is the one you might become."
  - "Power is worthless if it costs you everyone you love."
  - "He lost everything trying to be someone he wasn't."

Return ONLY JSON, no markdown: {"outro_line": "..."}"""


def generate_outro(
    comic_context: dict,
    body_scenes: list[dict],
    *,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
    direction: dict | None = None,
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
        _direction_block(direction or {})
        + f"COMIC TITLE: {title}\n"
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


def _append_loop_tease(closure_text: str, tease: str) -> str:
    """Join the outro closure with a loop tease, normalizing whitespace. Empty
    tease → closure unchanged."""
    closure = " ".join(str(closure_text).split()).strip()
    t = " ".join(str(tease).split()).strip()
    return f"{closure} {t}".strip() if t else closure


_LOOP_TEASE_SYSTEM = """You are LoopWriter. You write ONE short final clause for a YouTube Short retelling of a comic — spoken AFTER the closing line — that makes the viewer want to watch again.

HARD RULES:
  - 3-14 words, exactly ONE sentence.
  - It POINTS FORWARD at the story's aftermath, irony, or unanswered weight — phrased as intrigue.
  - It MUST NOT state a NEW fact that the plot below does not support (no invented sequel, no made-up detail). It teases what was ALREADY implied.
  - NOT a question. No meta talk, no hashtags, no "subscribe", no comic title.
  - Tone: ominous, resonant — matches a dark comic retelling.
  - PLAIN, CONCRETE language (IELTS 6.5 / B2) — the intrigue comes from WHAT is
    left unresolved, not from fancy wording. No poetic double meanings stacked
    on the line.

Examples (for OTHER comics — match the TONE, not the words):
  - "But what he became next, no one dares tell."
  - "The cost was only beginning to show."
  - "Some doors, once opened, never close."

Return ONLY JSON, no markdown: {"loop_tease": "..."}"""


def generate_loop_tease(
    comic_context: dict,
    body_scenes: list[dict],
    *,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
    direction: dict | None = None,
) -> str:
    """Craft a short forward-pointing tease appended after the outro closure.
    Returns "" if unusable (caller keeps the closure-only outro)."""
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
        _direction_block(direction or {})
        + f"COMIC TITLE: {title}\n"
        f"PLOT (ground truth — do NOT go beyond it):\n{plot[:1500]}\n\n"
        f"THE NARRATION (for tone + what was covered):\n{body_text[:1200]}\n\n"
        f"Write the loop-tease JSON now."
    )

    def _valid(out: str) -> bool:
        try:
            d = _json_loads_loose(out)
            line = " ".join(str(d.get("loop_tease", "")).split()).strip()
        except Exception:
            return False
        n = len(line.split())
        return 3 <= n <= 14 and "?" not in line and "subscribe" not in line.lower()

    try:
        content, used = call_with_chain(
            system=_LOOP_TEASE_SYSTEM, user=user,
            models=list(CREATIVE_LLM_MODELS) or None,
            max_tokens=120, progress=progress, label="loop_tease", validator=_valid,
        )
        data = _json_loads_loose(content)
        line = " ".join(str(data.get("loop_tease", "")).split()).strip()
        dump["loop_tease"] = {"loop_tease": line, "model": used}
        return line
    except Exception as exc:
        log(f"[stage4] loop tease LLM failed ({type(exc).__name__}); keeping closure-only outro")
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
    direction: dict | None = None,
) -> Narration:
    """Run intro -> outline -> glossary -> write -> validate (+ retries)."""
    if mode not in MODES_BY_KEY:
        raise ValueError(f"Unknown mode: {mode!r}. Valid: {sorted(MODES_BY_KEY)}")

    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}
    direction = direction or {}

    # Grounding guard: if Stage 1 / url-mode enrichment never grounded a plot
    # (e.g. DC Fandom Cloudflare-blocked AND the SDK web fallback failed), the
    # writer has only the panels to go on and WILL invent/mis-frame events.
    # Surface it LOUDLY here instead of silently shipping a hallucinated draft.
    _plot = str(comic_context.get("plot_summary", "")).strip() \
        or str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    if not _plot or comic_context.get("plot_status") == "MISSING":
        log("⚠️  [stage4] WARNING: comic_context has NO grounded plot_summary "
            "(plot_status=MISSING). Narration will be written from panels alone "
            "and is likely to invent/mis-frame events. Fix the context "
            "(re-run Stage 1 enrichment or hand-populate plot_summary) before trusting this draft.")

    from .story_architect import analyze_story
    story_map = analyze_story(comic_context, story_pages, model=model, progress=progress,
                              direction=direction)
    dump["story_map"] = story_map

    log("[stage4] phase A0 — generating teaser intro…")
    intro = generate_intro(comic_context, model=model, progress=progress, debug_dump=dump,
                           direction=direction)
    cover_page = _find_cover_page(all_pages, story_pages)

    log(f"[stage4] phase A — outlining beats (mode={mode})…")
    beats, beats_model = outline_beats(comic_context, story_pages, mode, hook_hint=hook_hint, model=model,
                                       progress=progress, debug_dump=dump, story_map=story_map,
                                       direction=direction)
    log(f"[stage4] phase A done — {len(beats)} beat(s)")

    # Phase A2 — beat-impact critic: drop low-impact beats so the Short is tight +
    # concise (keeps cold-open/climax/landing + the cause->effect spine, honors a
    # floor). Trims BEFORE writing so 1-beat->1-scene anchoring stays consistent.
    beats = _critique_beats_for_impact(beats, comic_context, model=model, progress=progress,
                                       story_map=story_map)

    log("[stage4] phase B — building glossary…")
    glossary, gloss_model = build_glossary(beats, comic_context, model=model, progress=progress, debug_dump=dump)
    log(f"[stage4] phase B done — {len(glossary.characters)} character(s) glossed")

    log("[stage4] phase C — writing scenes…")
    parsed, write_model = write_scenes(beats, glossary, comic_context, story_pages, mode,
                                       hook_hint=hook_hint, all_pages=all_pages,
                                       model=model, progress=progress, debug_dump=dump,
                                       story_map=story_map, direction=direction)
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
        # Phase F — internal coherence (soft: triggers a revise, but stays
        # non-critical so wiki-fidelity still dominates best-draft selection).
        coherence_issues = _coherence_check(parsed, model=model, progress=progress)
        if coherence_issues:
            errors = errors + [f"coherence: {i}" for i in coherence_issues]
        # Phase G — logic/clarity critic (soft, zero-context viewer + impact). Its
        # `clarity:` directives feed the writer on retry; faithfulness stays with wiki.
        clarity_issues = _logic_clarity_critic(parsed, comic_context, model=model, progress=progress,
                                               story_map=story_map)
        if clarity_issues:
            errors = errors + clarity_issues
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
        complete = 1 if (9 <= len(_scenes) <= 22 and _words >= 155) else 0
        # words_ok: inside the HARD band (no +20 slack — the ceiling is the ceiling, so an
        # over-length draft is never marked ok and loses to a shorter one of equal fidelity).
        words_ok = 1 if _TARGET_WORDS_MIN <= _words <= _TARGET_WORDS_MAX else 0
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
        parsed = _retry_fix_with_wiki(parsed, errors, beats, comic_context,
                                       model, progress, dump, story_map=story_map,
                                       direction=direction)
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
    # Reveal-only names (the withheld true identity) must not appear in the intro
    # either — an opening line that names the twist kills the whole hook.
    reveal_only = _reveal_only_names(body0)
    intro_line = (intro.get("intro_line") or "").strip()
    first_body_line = str(body0[0].get("text", "")).strip() if body0 else ""

    def _intro_bad(line: str) -> bool:
        if not line:
            return True
        if first_body_line and _intro_overlaps(line, first_body_line):
            return True
        low = line.lower()
        return any(n.lower() in low for n in reveal_only)

    if intro_line and _intro_bad(intro_line):
        why = "spoils the reveal" if any(n.lower() in intro_line.lower() for n in reveal_only) \
            else "echoes the opening narration"
        log(f"[stage4]   ⚠ intro {why} — regenerating (avoid_text + forbid_names)")
        intro = generate_intro(comic_context, avoid_text=first_body_line,
                               forbid_names=reveal_only,
                               model=model, progress=progress, debug_dump=dump,
                               direction=direction)
        intro_line = (intro.get("intro_line") or "").strip()
        if _intro_bad(intro_line):
            # last-resort: a question hook. If the fallback hero name IS the concealed
            # identity (shares a name with reveal_only), naming it would spoil the
            # twist — use a name-free hook instead.
            hero = _fallback_hero(comic_context)
            hero_is_spoiler = any(n.lower() in hero.lower() for n in reveal_only)
            # Plain, concrete, name-free fallbacks (never the abstract-riddle style).
            intro_line = ("He woke up with no memory. What he believed almost destroyed him."
                          if hero_is_spoiler
                          else f"When {hero} woke up, the whole world had already turned against him.")
            intro["intro_line"] = intro_line
            log(f"[stage4]   ⚠ still bad; using fallback question hook: {intro_line!r}")
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
                                  model=model, progress=progress, debug_dump=dump,
                                  direction=direction)
        # Concrete-guard: the LLM keeps producing abstract wordplay outros despite
        # the prompt's plain-language rule. Accept the thematic line only if it
        # contains something CONCRETE — a character name from this comic or a
        # physical ending event — else keep the factual credit.
        if thematic and not _outro_is_concrete(thematic, comic_context):
            log(f"[stage4] outro: thematic REJECTED (no concrete element): {thematic!r}")
            thematic = ""
        if thematic:
            scenes_now[outro_idx]["text"] = thematic
            log(f"[stage4] outro: thematic → {thematic!r}")
        else:
            log("[stage4] outro: factual credit (thematic gen failed)")
    elif outro_idx >= 0:
        log("[stage4] outro: factual credit (coin-flip)")
    # Item 2: hybrid loop ending — append a forward-pointing tease after the
    # chosen closure so the ending invites a rewatch (closure is preserved).
    if outro_idx >= 0 and ENABLE_LOOP_TEASE:
        tease = generate_loop_tease(comic_context, scenes_now,
                                    model=model, progress=progress, debug_dump=dump,
                                    direction=direction)
        # Same concrete-guard as the thematic outro — the tease slipped abstract
        # wordplay past the prompt rules ("The stretching arms remember what the
        # mind forgot"). No concrete element → skip the tease, keep clean closure.
        if tease and not _outro_is_concrete(tease, comic_context):
            log(f"[stage4] outro: loop tease REJECTED (no concrete element): {tease!r}")
            tease = ""
        if tease:
            scenes_now[outro_idx]["text"] = _append_loop_tease(
                scenes_now[outro_idx].get("text", ""), tease)
            log(f"[stage4] outro: + loop tease → {tease!r}")

    # B2: normalize confusing character TITLES/acronyms to the most-common name
    # (e.g. M.Y.T.H.O.S. / "Master of Yggdrasil…" → MODOK), reading the comic's own
    # character list. Runs AFTER all text is finalized so the spoken video never
    # carries a name the viewer can't place. Short nicknames are left alone.
    _normalize_titles_to_common(parsed.get("scenes") or [], comic_context, log)
    # Speak "X vs. Y" titles as "versus" — TTS reads "vs." as "vee-ess" (the outro
    # "The comic is Ghost Rider vs. Galactus" sounded like "v.s"). Caption reads fine too.
    for _s in (parsed.get("scenes") or []):
        _s["text"] = re.sub(r"\bvs\b\.?", "versus", _s.get("text", ""), flags=re.IGNORECASE)

    # Persistent on-screen banner title — a short catchy line Stage 5 burns on EVERY
    # frame (high-view comic Shorts always carry one). Generated here so it ships in
    # narration.json (100% pipeline); falls back to the working title if gen fails.
    if ENABLE_TITLE_BANNER:
        parsed["banner_title"] = generate_banner_title(
            comic_context, parsed.get("scenes") or [],
            model=model, progress=progress, debug_dump=dump,
            direction=direction)

    final_model = write_model or gloss_model or beats_model or (model or OPENROUTER_MODEL)
    nar = _to_narration(parsed, beats, glossary, mode, final_model)
    if ENABLE_TITLE_BANNER:
        nar.banner_title = parsed.get("banner_title") or nar.title
        log(f"[stage4] banner title: {nar.banner_title!r}")
    return nar


_BANNER_SYSTEM = """You write ONE short, CONCRETE ON-SCREEN BANNER for a comic Short — the text shown in a small white box on EVERY frame that tells a scroller, plainly, what wild thing happens.

STYLE (important): DESCRIPTIVE and literal, NOT vague clickbait. NAME the real,
recognizable character(s) and state the concrete action/turn of the story.
  ✓ "Rogue Kissed Silver Surfer And Stole His Power"
  ✓ "Bruce Banner Became Galactus's Herald"
  ✗ "Rogue Kissed A God"  (vague, miscalls the character)
  ✗ "Her First Kiss Doomed Her"  (too coy)

HARD RULES:
  - 4-9 words, ONE line. Concrete + intriguing.
  - Grounded in THIS story (below) — name the actual characters; never invent a beat.
  - NO emoji, NO hashtags, NO ending punctuation, NO surrounding quotes.
  - Title Case. Lead with the popular character (the draw).
  - CONCEALED IDENTITY: if who the protagonist REALLY is is the story's late twist,
    name them by the identity they BELIEVE / present as (that IS the recognizable draw
    character), NEVER the hidden true name — revealing it in the banner spoils the
    Short. (e.g. "Doctor Doom Woke Up With No Memory" — not the real name.)

Examples (OTHER comics — match the concrete, descriptive shape):
  - Rogue Kissed Silver Surfer And Stole His Power
  - Bruce Banner Became Galactus's Doomed Herald
  - Magik's Childhood Hero Became Her Monster

Return ONLY JSON: {"banner": "..."}"""


def generate_banner_title(
    comic_context: dict,
    body_scenes: list[dict],
    *,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
    direction: dict | None = None,
) -> str:
    """Craft a 4-9 word catchy on-screen banner title (burned every frame in Stage 5).
    Returns "" if unusable (caller falls back to the working title)."""
    log = progress or (lambda _msg: None)
    dump = debug_dump if debug_dump is not None else {}
    title = str(comic_context.get("title", "")).strip()
    plot = str(comic_context.get("plot_summary", "")).strip()
    if not plot:
        plot = str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    kept = [s for s in (body_scenes or [])
            if not s.get("is_intro") and not s.get("is_outro")]
    # Reveal-only names must never appear in an every-frame banner — they spoil a
    # concealed-identity twist. Feed the banner only the SETUP narration (reveal
    # withheld) so it describes the wild premise, not the ending.
    reveal_only = _reveal_only_names(body_scenes or [])
    early_scenes = kept[:-max(1, round(len(kept) * 0.30))] if len(kept) >= 4 else kept
    body_text = " ".join(str(s.get("text", "")).strip() for s in early_scenes)
    forbid_block = ""
    if reveal_only:
        forbid_block = ("\nFORBIDDEN — these are LATE-REVEAL spoilers; NEVER put them in "
                        f"the banner (use the identity the hero is PRESENTED as instead): "
                        f"{', '.join(sorted(reveal_only))}\n")
    user = (
        _direction_block(direction or {})
        + f"COMIC TITLE: {title}\n"
        f"PLOT (ground truth — do NOT exceed it):\n{plot[:1200]}\n\n"
        f"NARRATION SETUP (tone + what happens — reveal withheld):\n{body_text[:1000]}\n"
        f"{forbid_block}\n"
        f"Write the banner JSON now."
    )

    def _valid(out: str) -> bool:
        try:
            d = _json_loads_loose(out)
            line = " ".join(str(d.get("banner", "")).split()).strip()
        except Exception:
            return False
        n = len(line.split())
        if not (4 <= n <= 9 and "#" not in line and not any(ord(c) >= 0x1F000 for c in line)):
            return False
        low = line.lower()
        if any(name.lower() in low for name in reveal_only):  # rejects spoiler names → retry
            return False
        return True

    try:
        content, used = call_with_chain(
            system=_BANNER_SYSTEM, user=user,
            models=list(CREATIVE_LLM_MODELS) or None,
            max_tokens=80, progress=progress, label="banner", validator=_valid,
        )
        data = _json_loads_loose(content)
        line = " ".join(str(data.get("banner", "")).split()).strip()
        dump["banner_title"] = {"banner": line, "model": used}
        return line
    except Exception as exc:
        log(f"[stage4] banner title LLM failed ({type(exc).__name__}); using working title")
        return ""


_OUTLINE_SYSTEM = """You are PanelOutliner. Your job is to extract the FULL dramatic skeleton of a comic story into 16-20 canonical beats — MUST cover the entire story arc including the climax, not just the opening.

You DO NOT write narration prose yet. You produce structured beats only.

GROUND EVERY BEAT IN THE WIKI PLOT — NEVER INVENT (HARD RULE):
- Each beat's event MUST appear in the WIKI PLOT SUMMARY (your canonical authority).
  The page/panel descriptions only tell you HOW a wiki event looks and WHICH page it's
  on — a panel image ALONE is NOT enough to create a beat.
- Do NOT invent an event the wiki doesn't describe, ESPECIALLY an internal experience
  (telepathy, mind-merge, "phasing into someone's mind", sharing memories, a vision, a
  dream). If a panel seems to show such a thing but the wiki never mentions it, treat
  the panel as a plain physical moment, not a new event — or skip it.
    ✗ beat "Surfer merges with a dying refugee's mind and shares her memories"
      (no such event in the wiki — invented from an ambiguous panel)
    ✓ beat "Surfer reaches for a falling survivor but his phantom hand passes through"
- When the wiki and a panel conflict, the WIKI wins. A thinner beat list that is 100%
  wiki-grounded beats a richer one padded with invented drama.

MAIN FEATURE ONLY — one story, one thread:
- Cover ONLY the issue's MAIN feature story. If the comic also contains a SECONDARY
  / backup story, a prologue or teaser for ANOTHER series, an epilogue that sets up
  a different book, or house ads, IGNORE them — do not make beats from them.
- Within the main story, follow the SINGLE central conflict (hero vs the main
  villain / threat). Drop subplots and minor characters that don't move it. A
  focused, easy-to-follow spine beats a complete-but-confusing one.
- EVERY BEAT MUST EARN ITS PLACE: a beat must ADVANCE the main conflict or SET UP a
  payoff that lands later. If a beat could be deleted and nothing else would change
  — a bystander's reaction, a side character's fate, a dangling consequence (e.g.
  "the guardian is blinded and can only weep") — DO NOT create it.
- FRONT-LOAD THE PREMISE in the first 2-3 beats, in order: (1) WHO the hero is in
  plain, recognizable terms, (2) HOW/WHY the central threat AROSE — the mechanism
  that gave the villain power, not merely that he "has" it, (3) what the hero must
  now do. The reader must grasp the setup before any fight starts.
  EXCEPTION — HIDDEN-ORIGIN TWIST: if the origin itself is the story's concealed
  twist — the protagonist's TRUE IDENTITY, how they really got here, WHO they
  actually are, and neither the protagonist NOR the audience knows it yet — then that
  backstory is NOT premise to front-load. It is the LATE REVEAL (see below): hold the
  whole flashback until AFTER the climactic confrontation, then unspool it. Early
  beats state only what the protagonist BELIEVES (e.g. "he woke certain he was Doom"),
  never the buried truth.

Each beat has:
- function: COLD_OPEN | SETUP | COMPLICATION | ESCALATION | MIDPOINT | CLIMAX | LANDING
- name: 3-7 words naming the beat ("Ben gets the symbiote")
- page_refs: which input pages feed this beat
- key_panels: 1-3 strongest visual moments [{"page": int, "panel": int}]
- summary: ONE factual sentence of what happens (no narration voice yet). Name only
  entities a mainstream audience already knows; refer to an obscure, this-issue-only
  entity (a local cult, a hired assassin) by a plain ROLE descriptor instead of its
  proper name — the writer copies your wording.
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

REVEAL IN STORY ORDER — DON'T SPOIL A LATE TWIST EARLY (HARD RULE):
- Tell information in the order the COMIC reveals it. A fact the story deliberately
  HIDES until a late reveal — a twist, a confession, "what X really did", "the truth
  behind Y" — MUST live in a LATE beat, at the panel where the comic discloses it.
- NEVER state or foreshadow that hidden fact in an early SETUP/flashback beat. A
  flashback shows the hero's STATED reasoning; it must NOT also tell the audience the
  secret the hero doesn't yet know.
    ✗ early beat "what the flashback hides: the Avengers secretly brokered the deal"
      AND late beat "Stark confesses the Avengers brokered the deal" — told TWICE; the
      early beat SPOILS the reveal.
    ✓ early beat "a flashback shows Banner's stated reasoning for taking the offer"
      + late beat "a defeated Stark confesses the Avengers brokered the deal" — the
      twist lands ONCE, where the comic reveals it.
- Each reveal appears EXACTLY ONCE, at its latest natural position. If the canonical
  plot mentions a twist out of order (e.g. "revealed later by…", "what the flashback
  withholds…"), STILL place your beat at the LATE reveal point — never at the early
  mention.
- HELD BACKSTORY (when the origin IS the twist): if the flashback explains a concealed
  identity/origin the protagonist doesn't know, do NOT scatter it early as "setup".
  Order the beats: build-up (protagonist acting on the FALSE belief) → the climactic
  CONFRONTATION/clash → THEN the flashback beats that reveal the true past → RETURN TO
  THE PRESENT for the final on-panel image as LANDING. The past is unspooled only once
  the confrontation forces it. Chronologically the flashback happened first, but
  DRAMATICALLY it belongs late — emit it late so `_order_beats_canonical` keeps it there.
  CRITICAL: the flashback's chronological end (how the present situation came to be) is
  NOT the LANDING — after the flashback you MUST come back to the PRESENT and end on the
  story's actual final image (the decisive defeat / fate / last panel), never trail off
  on "and that is how things came to be". The last beat = the last thing shown on-page.

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
  MORE beats = MORE distinct panels on screen. Cover the FULL canonical arc THROUGH
  the decisive RESOLUTION — every major wiki PLOT event gets its own beat. BUT a
  trailing quiet EPILOGUE after the conflict resolves (hero resting, reuniting with
  an ally, reaffirming themselves) is NOT a "major event" — do not give it a beat.
- Each beat covers 1-4 input pages. Don't spread one beat across the whole comic.
- HIGHLIGHTS — pages/panels marked "★BIG SHOT" are large splash/action/reveal art:
  the visual highlights of the comic. When you cannot cover every page (more pages
  than beats), PRIORITIZE giving each ★BIG-SHOT page its OWN beat, and DROP/merge
  talky small-panel pages instead. Never skip a ★BIG SHOT (a transformation, a big
  reveal, a major clash) — these are the money shots that make the Short. Point that
  beat's key_panels at the ★BIG SHOT panel.
- COLD_OPEN beat must contain a concrete visual action, not exposition. But it must
  NOT be the story's climactic reveal/final image: when the biggest ★BIG SHOT is the
  ENDING twist (a hidden identity made visible, a final fate), that image is RESERVED
  for the LANDING — do NOT spend it as the cold-open hook. Open on an early/mid
  concrete action instead (the hero acting on his FALSE belief). Using the ending
  image to open spoils the payoff and leaves the finale with nothing to land on.
- LANDING = the DECISIVE RESOLUTION of the MAIN conflict (the villain's defeat / fate,
  the world restored) — a payoff, twist, or final image; never a CTA or question.
  When the story is a held-reveal twist, the LANDING IS that final reveal image (the
  concealed truth made visible on the last page — e.g. the plummeting man's powers
  becoming visible) and it appears ONLY here, never earlier. Do NOT stop one beat
  short of it (ending on "he throws him out the window" while omitting the visible
  reveal that follows) — the last on-panel moment is the LANDING.
  Do NOT add a separate aftermath/epilogue beat AFTER it (a calm denouement, a
  hero-and-ally coda) even if the comic has those pages — it dilutes the ending.
  End on the decisive moment; the outro credit follows.
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


# Words signalling a CONCEALED truth being disclosed. The guard only ACTS when 2+
# beats carry such a marker AND describe the same fact (word overlap), so a single
# legitimate "betrayal"/"sacrifice" beat is never dropped.
_REVEAL_MARKERS = (
    "secretly", "broker", "in truth", "all along", "withhold", "withheld",
    "concealed", "the truth", "really was", "hidden", "betray", "in reality",
    "what the flashback", "unbeknownst", "behind his back",
)
_REVEAL_STOP = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "as", "is",
    "was", "his", "her", "their", "they", "he", "she", "it", "that", "this", "with",
    "for", "from", "into", "by", "who", "whom", "then", "than", "not", "no", "be",
    "been", "are", "were", "had", "has", "have", "him", "them", "you", "your", "its",
    "one", "what", "when", "while", "him",
}


def _dedupe_reveal_beats(beats: list[Beat], log: Callable[[str], None]) -> list[Beat]:
    """Drop an EARLY beat that foreshadows a twist a LATER beat reveals (anti-spoiler).

    Fires only when two beats BOTH carry a concealment marker AND share enough
    content (Jaccard ≥ 0.34 on significant words) to be the same reveal — then keeps
    the one at the latest page (where the comic discloses it) and drops the earlier."""
    def _sig(b: Beat) -> set[str]:
        return {w for w in re.findall(r"[a-z']+", (b.summary or "").lower())
                if len(w) > 3 and w not in _REVEAL_STOP}

    def _marked(b: Beat) -> bool:
        s = (b.summary or "").lower()
        return any(m in s for m in _REVEAL_MARKERS)

    marked = [b for b in beats if _marked(b)]
    if len(marked) < 2:
        return beats
    drop_ids: set[int] = set()
    for i in range(len(marked)):
        for j in range(i + 1, len(marked)):
            a, b = marked[i], marked[j]
            if a.id in drop_ids or b.id in drop_ids:
                continue
            sa, sb = _sig(a), _sig(b)
            if not sa or not sb:
                continue
            jac = len(sa & sb) / len(sa | sb)
            if jac >= 0.34:
                pa = max(a.page_refs or [0]); pb = max(b.page_refs or [0])
                early = a if pa <= pb else b
                drop_ids.add(early.id)
                log(f"[stage4]   reveal-dedup: dropped early beat '{early.name}' "
                    f"(p{max(early.page_refs or [0])}) — duplicates a later reveal")
    if drop_ids:
        beats = [b for b in beats if b.id not in drop_ids]
    return beats


# The outline system prompt already instructs the LLM to hold a hidden-origin
# flashback until after the confrontation (see HELD BACKSTORY rule above), but the
# LLM does not reliably obey it — this is the deterministic backstop.
HOLD_FLASHBACK_LATE = os.getenv("HOLD_FLASHBACK_LATE", "1").strip().lower() not in ("0", "false", "no")


def _hold_flashback_beats_late(beats: list[Beat], log: Callable[[str], None]) -> list[Beat]:
    """Move flashback beats to sit right before the final 2 non-flashback beats.

    The outliner is told to hold a hidden-origin flashback until AFTER the climactic
    confrontation, but sometimes emits it early anyway (present-day fight, THEN past
    reveal is the desired dramatic order). Detects flashback beats by the outliner's
    consistent "flashback" wording (no semantic guessing), removes them (preserving
    their relative order), and re-inserts the block immediately before the 2nd-to-last
    non-flashback beat — leaving the confrontation, then the reveal, then the final
    beat(s) (e.g. the LANDING image) intact. A no-op if there are no flashback beats,
    or if they already sit at/after that point."""
    if not HOLD_FLASHBACK_LATE:
        return beats
    flash_ids = {b.id for b in beats
                 if re.search(r"\bflash-?back\b", f"{b.summary or ''} {b.name or ''}", re.I)}
    if not flash_ids:
        return beats
    flash = [b for b in beats if b.id in flash_ids]
    non_flash = [b for b in beats if b.id not in flash_ids]
    if not non_flash:
        return beats

    cut = 2 if len(non_flash) >= 2 else 1
    target_count = len(non_flash) - cut

    count_before = 0
    for b in beats:
        if b.id in flash_ids:
            break
        count_before += 1
    if count_before >= target_count:
        return beats  # already sits at/after the confrontation

    orig_pos = {b.id: i + 1 for i, b in enumerate(beats)}
    new_order = non_flash[:target_count] + flash + non_flash[target_count:]
    new_pos = {b.id: i + 1 for i, b in enumerate(new_order)}
    log(f"[stage4]   flashback-hold: moved {len(flash)} flashback beat(s) after the "
        f"confrontation (positions {orig_pos[flash[0].id]}..{orig_pos[flash[-1].id]} "
        f"→ {new_pos[flash[0].id]})")
    return new_order


def outline_beats(
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    *,
    hook_hint: str = "",
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    debug_dump: dict | None = None,
    story_map: dict | None = None,
    direction: dict | None = None,
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

    # Director's must-have beats (if any) — reinforce inside the TASK instruction
    # so the outliner treats them as required beats, not just a nice-to-have.
    must_have = [str(b).strip() for b in (direction or {}).get("must_have_beats", []) if str(b).strip()]
    must_have_line = ""
    if must_have:
        must_have_line = (
            "MUST-HAVE BEATS (director's spec — EACH one below MUST get its own "
            "dedicated beat; do not omit or merge them into another beat):\n"
            + "\n".join(f"  - {b}" for b in must_have) + "\n\n"
        )

    _smap = render_story_map_block(story_map)
    user = (
        _direction_block(direction or {})
        + _smap
        + canonical_block
        + f"COMIC METADATA:\n{_ctx_block(comic_context)}\n\n"
        # Multi-issue sagas have ~100 pages; the full per-panel block balloons the
        # prompt to ~175K chars and the SDK rejects/rate-limits it. Use the compact
        # block for arcs (enough to anchor beats to pages); single comics keep full.
        + f"STORY PAGES (per-panel detail for grounding beats to visuals):\n"
        + f"{(_pages_block_compact if comic_context.get('is_arc') else _pages_block_full)(story_pages)}\n\n"
        + f"NARRATION MODE: {mode} — {mode_info.description}\n"
        + (f"HOOK HINT: {hook_hint}\n" if hook_hint else "")
        + page_range_hint + "\n\n"
        + must_have_line
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
            id=_safe_beat_int(b.get("id"), i),
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

    # Reveal-dedup guard: a twist the comic hides until late (a confession, "what X
    # really did") sometimes gets emitted TWICE — once foreshadowed in an early
    # setup/flashback beat, once at the real late reveal — which spoils the payoff.
    # Drop the early duplicate, keep the beat at the latest page (where the comic
    # discloses it). Belt-and-suspenders to the REVEAL-IN-STORY-ORDER prompt rule.
    beats = _dedupe_reveal_beats(beats, log)

    # Flashback-hold guard: present-day fight FIRST, past unspooled after (see
    # HELD BACKSTORY rule in the outline prompt) — deterministic backstop for when
    # the LLM emits the flashback too early despite the prompt rule.
    beats = _hold_flashback_beats_late(beats, log)

    # Ending-coverage gate: if the outline stops short of the plot's FINAL on-page
    # event (the outliner sometimes ends on the flashback's chronological close), add
    # a LANDING beat for it so the finale actually lands. Then re-apply bookends.
    beats = _ensure_ending_coverage(beats, comic_context, story_pages, log)
    beats = _order_beats_canonical(beats)

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
    area_by_page: dict[int, int] = {}
    text_blocks_by_page: dict[int, list[dict]] = {}
    for p in story_pages or []:
        pn = int(p.get("page_number", 0) or 0)
        if pn:
            panels_by_page[pn] = p.get("panels") or []
            dims = p.get("image_dimensions") or {}
            area_by_page[pn] = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
            text_blocks_by_page[pn] = p.get("text_blocks") or []

    def _is_big_shot(pg, idx) -> bool:
        pans = panels_by_page.get(pg) or []
        if not (isinstance(idx, int) and 0 <= idx < len(pans)):
            return False
        a = area_by_page.get(pg, 0)
        return a > 0 and _panel_area_frac(pans[idx], a) >= _BIG_SHOT_FRAC

    # KEEP the outliner's ★BIG-SHOT key_panel when it cited one on the beat's OWN
    # page_refs (outliner rule :670-675 deliberately points a money-shot beat at its
    # splash). Re-grounding by description-sim must NOT demote that splash to a small
    # text-match panel — the recurring "money shot lost" bug (Hulkbuster, transform).
    # Each big-shot panel is kept for at most ONE beat; everything else re-grounds.
    kept_keys: set = set()
    regrounded = 0
    for beat in beats:
        kp0 = beat.key_panels[0] if beat.key_panels else None
        if kp0:
            kpg, kidx = kp0.get("page"), kp0.get("panel")
            if (kpg in (beat.page_refs or []) and _is_big_shot(kpg, kidx)
                    and (kpg, kidx) not in kept_keys):
                kept_keys.add((kpg, kidx))
                continue  # keep the outliner's money-shot panel; skip re-grounding
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
            page_tb = text_blocks_by_page.get(pg)
            for idx, panel in enumerate(panels_by_page.get(pg, [])):
                # Ground against the SAME rich signal Stage 5 matches on (description +
                # characters + dominant_emotion + OCR dialog), not description alone —
                # a panel's dialog often names the exact story moment (an unmasking
                # line, a reveal) that the visual description misses, so grounding on
                # description-only can disagree with Stage 5's later panel pick.
                ptext = _panel_embed_text(panel, page_tb)
                if not ptext:
                    continue
                score = _semantic_sim(summary, ptext) - dist_penalty
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

    Choice B (unified panel matching, 2026-06-17), revised 2026-07-02 (C2): the
    panel `_ground_beat_panels` content-matched now travels as a SOFT anchor —
    Stage 5's `_match_panels` gives it a bonus in the cosine matrix but the
    Hungarian/greedy assignment (plus VLM rerank) can still override it when the
    embedding strongly disagrees. Beats without a grounded key_panel keep -1
    ("whole page"; Stage 5 picks freely).
    """
    if beat.key_panels:
        kp = beat.key_panels[0]
        panel = kp.get("panel")
        return int(kp.get("page", 0) or 0), int(panel) if panel is not None else -1
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
    n_beats, n_pool = len(beats), len(pool)
    if n_pool <= n_beats:
        # fewer/equal scenes → front-align; unmatched trailing beats become gaps.
        for i, beat in enumerate(beats):
            if i < n_pool:
                matched[beat.id] = pool[i]
    else:
        # Writer emitted EXTRA scene(s) (split one beat into two). Front-only
        # truncation drops the writer's LAST scene — which is the LANDING (plummet /
        # final reveal), silently losing the payoff. Align BOTH ends instead: front
        # beats → front scenes, back beats → back scenes, so the LANDING beat always
        # keeps the writer's LAST scene; the surplus is dropped from the MIDDLE.
        half = n_beats // 2
        for i in range(half):
            matched[beats[i].id] = pool[i]
        for j in range(1, n_beats - half + 1):
            matched[beats[n_beats - j].id] = pool[n_pool - j]
    if n_pool != n_beats:
        how = f"first {n_pool}" if n_pool < n_beats else "both-ends aligned; dropped middle surplus"
        log(f"[stage4]   ⚠ writer emitted {n_pool} story scenes for {n_beats} "
            f"beats — positional anchoring: {how}")

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
    parsed["_anchor_pool_count"] = len(pool)
    return parsed


_ENDING_STOP = {
    "the", "and", "that", "with", "his", "her", "their", "they", "them", "then",
    "this", "into", "from", "onto", "when", "where", "which", "while", "would",
    "could", "should", "have", "been", "were", "was", "are", "for", "but", "him",
    "she", "had", "who", "whom", "both", "each", "over", "back", "down", "still",
    "issue", "ends", "ending", "story", "comic", "final", "page", "panel", "left",
    "one", "two", "all",
}


def _ending_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _ENDING_STOP}


def _ensure_ending_coverage(
    beats: list[Beat],
    comic_context: dict,
    story_pages: list[dict],
    log: Callable[[str], None],
) -> list[Beat]:
    """Guarantee the outline COVERS the plot's FINAL on-page event.

    The outliner sometimes stops at the flashback's chronological end (how the
    present came to be) and drops the story's actual last image — e.g. the villain
    hurling the hero out the window / the plummeting stretch reveal. The writer then
    CANNOT recover it (1 scene per beat, retries only re-write existing beats). If
    the final beats don't cover the plot_summary's closing event, append a LANDING
    beat for it (grounded in plot_summary → no per-comic logic). LANDING is protected
    from the beat-impact critic, so it survives to the writer."""
    plot = str(comic_context.get("plot_summary", "")).strip()
    if not plot or len(beats) < 6:
        return beats
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", plot) if s.strip()]
    if len(sents) < 3:
        return beats
    # ubiquitous tokens across beat summaries (recurring names/setting) prove nothing
    beat_toks = [_ending_tokens(b.summary or "") for b in beats]
    freq: dict[str, int] = {}
    for ts in beat_toks:
        for w in ts:
            freq[w] = freq.get(w, 0) + 1
    ubiq = {w for w, n in freq.items() if n >= max(3, int(0.34 * len(beats)))}
    # CHECK the CLOSING IMAGE specifically — the last sentence, not the last-2 averaged
    # (averaging let a covered "thrown out the window" mask an uncovered "plummets,
    # stretch powers visible" payoff). Fall back to the prior sentence if the last is
    # too thin to judge.
    closing = _ending_tokens(sents[-1]) - ubiq
    if len(closing) < 2:
        closing = _ending_tokens(" ".join(sents[-2:])) - ubiq
    if len(closing) < 2:
        return beats  # closing image has no distinctive content to check
    tail = set().union(*beat_toks[-2:]) if len(beat_toks) >= 2 else set()
    covered = len(closing & tail) / len(closing)
    if covered >= 0.5:
        return beats  # the closing image already lands in the last beats
    final_event = re.sub(r"^\s*the (?:issue|story|comic) ends with\s*", "",
                         " ".join(sents[-2:]), flags=re.I).strip()
    last_page = max((int(p.get("page_number", 0) or 0) for p in story_pages), default=0)
    new_id = max((b.id for b in beats), default=0) + 1
    beats.append(Beat(
        id=new_id, function="LANDING", name="Final on-page image",
        page_refs=[last_page] if last_page else [],
        key_panels=[], summary=final_event, cause="", characters_active=[],
    ))
    log(f"[stage4]   ending-coverage: closing image only {covered:.0%} covered — "
        f"appended LANDING beat: {final_event[:90]!r}")
    return beats


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

    # A beat spanning too many pages swallows whatever happens on them (e.g. a
    # multi-page confrontation eating the actual fight, leaving it unnarrated).
    for b in beats:
        span = len(set(b.page_refs))
        if span > 4:
            issues.append(
                f"beat {b.id} covers {span} pages {sorted(set(b.page_refs))} — too "
                f"broad; SPLIT into 2+ beats of 1-4 pages each (a multi-page "
                f"confrontation must keep its FIGHT as its own beat)"
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
        f"Your previous outline draft had page-coverage/structure issues. Fix by "
        f"INSERTING new BRIDGE beats that summarize skipped pages so the narrative "
        f"flows linearly. For a 'too broad' beat, SPLIT it into consecutive beats "
        f"(e.g. confrontation + the fight/defeat), keeping ids unique.\n\n"
        f"ISSUES:\n{issue_block}\n\n"
        f"PRIOR OUTLINE:\n{prior}\n\n"
        f"STORY PAGES (for picking bridge content):\n{_pages_block_compact(story_pages)}\n\n"
        f"Return the COMPLETE corrected outline (with bridge/split beats inserted in "
        f"order) in the same JSON shape. KEEP every original beat and INSERT the new "
        f"beats — do NOT drop, merge, or renumber away any existing beat (the total "
        f"can only grow). Page-gap between consecutive beats MUST be ≤ 5, and no "
        f"beat may cover more than 4 pages."
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
            id=_safe_beat_int(b.get("id"), i),
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

The narration we are about to write is read aloud as one tight short-form voiceover. If the script flips between "Ben / the Thing / Venom / the creature" without a clear rule, the listener gets lost. You prevent that.

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


_WRITE_SYSTEM = """You are PanelNarrator, writing short-form narration for YouTube Shorts in the ComicsUnlocked house style. You have already received the story BEATS and a NAMING GLOSSARY. Your job is to render them as final spoken prose.

This voice was reverse-engineered from 30 successful videos. Follow every rule:

0) PLAIN-ENGLISH STYLE (BINDING — OVERRIDES ANY RULE BELOW THAT ENCOURAGES FANCY WORDING)
   - Write at ~IELTS 6.5 / B2 level: short, common words; ONE event per sentence.
   - LINE LENGTH IS THE WORD BUDGET'S MAIN LEVER: aim 8-14 words per scene line
     (hard max 18; sole exceptions: the hook, and the two twist-landing closers of
     rule 8.68 which may reach 24). Trim modifiers and sub-clauses first, never the
     event itself.
     A 20-scene script at ~11 words/line lands the total budget; 17-word lines blow it.
   - NO ornate metaphors or literary flourishes ("a stranger's face clawed at his mind",
     "the truth unspooled", "one name burned through the fog"). Say it plainly instead
     ("kept haunting him", "then the truth came out").
   - Each line must be understandable on FIRST LISTEN by a non-native speaker. If a
     word or phrase needs a second read to parse, it is too fancy — replace it with a
     simpler one.
   - This rule governs WORD CHOICE only — it does not cancel the punch/rhythm/variety
     rules elsewhere (3, 11a-11g); hit those beats using PLAIN words, never fancy ones.
   - VISUAL-CLAIM DISCIPLINE: a scene line may only state what its own beat's panel
     actually shows on screen. An off-panel fact (who sent someone, what's happening
     elsewhere, what's above/behind something unseen) is NOT a visual claim — fold it
     into a short grounded cause clause, or cut it. Never narrate an off-panel detail
     as if the viewer can see it on the current panel.

1) HOOK FORMULA — CONCRETE ORDINARY MOMENT + CONTRAST PIVOT ("thought/believed X — until [dark turn]...")

   PRIMARY SHAPE — this is what wins. Verbatim mining of 23 competitor Shorts: the
   3 BIGGEST hits (3.24M / 1.70M / 1.38M views) ALL open on a concrete, ordinary
   moment and then pivot with "thought/believed [normal] ... until [dark turn]".
   Show a scene the viewer pictures instantly, then break it:
     ✓ "Nightwing thought he was living the perfect life with the Titans, until one thing tore it all apart..." (3.24M)
     ✓ "When Superman came home, Lois thought he'd only been gone a few hours, until she learned the truth..." (1.70M)
     ✓ "The Flash thought he saved an innocent civilian, until he learned what the man really wanted..." (1.38M)
   The umbrella framing "When [event], [twist]..." still works — the thought/until
   pivot is just the PREFERRED way to fill it. Keep the older channel openers too:
     ✓ "When Frank Castle entered Valhalla, he couldn't find peace, so Odin..."

   NAME-FIRST IS ALLOWED when it leads into the pivot (channel data: several virals
   open on the hero's name — "Nightwing thought...", "Spawn's body was just visiting...").
     ✓ "Nightwing thought he was living the perfect life, until it all fell apart..."
   STILL BANNED — a FLAT name-first ACTION opener (no pivot, no hook), and meta-talk:
     ✗ "The Goblin unleashes his deadliest plan." (flat action, no contrast — not a hook)
     ✗ "In an alternate universe..." (different channel's signature, don't copy)
     ✗ "Today we're looking at..." / "In today's video" / any framing meta-talk

   ZERO PRIOR-KNOWLEDGE RULE (HARD): the hook must be fully parseable by a viewer who
   has NEVER read this comic. NO continuity/lore references — no event names, cosmic
   titles, prior-series callbacks, or character history a newcomer wouldn't know.
   Lore-dense hooks are the FLOP signature (these got <10k views):
     ✗ "Superman became King Omega due to the sacrifice of the Time Trapper." (7.8k — lore soup)
     ✗ "When Judas killed Spawn, the Mother of Existence descended..." (4.8k — needs backstory to parse)

   LEAD WITH VISCERAL, HUMAN-SCALE STAKES when the story offers one — a concrete
   image beats an abstract one every time (SAME character, opposite results):
     ✓ Spawn "found a 20-year-old girl locked in a basement..." (1.99M — visceral, human)
     ✗ Spawn "surrounded by angels led by a young leader..." (7.2k — abstract, no felt stakes)

   QUESTION HOOKS ARE LAST RESORT. ZERO of the 15 analyzed virals opened with a
   question ("What if...?", "Ever wonder...?"). Use one ONLY if no concrete scene +
   pivot can be built from this story — never as your default.

   The hook MUST be 14-26 words and end with an open thread that pulls the viewer
   into scene 2 (use a comma + "..." or end with an unresolved promise). The hook
   is the ONE scene allowed to run long — every other scene stays punchy.

   HOOK = FIRST BEAT ONLY — NO PREVIEW OF LATER EVENTS.
   The hook narrates ONLY the first beat's own moment. The "until [dark turn]" clause
   must belong to the FIRST beat's own moment or its immediate tease — it does NOT
   pull an event from a LATER beat into the opener (that contradicts the next scene).
   Crack the door open; do not name the later payoff.
     ✗ "When the symbiote sat imprisoned, it was Ben who set it free..."  then next
        scene "But Ben discovered the caged symbiote." (he frees it, THEN finds the
        cage? — broken. "set it free" belongs to a LATER beat.)
     ✓ "When the Venom symbiote sat imprisoned in Reed Richards' lab, it waited
        bitterly for a way out..."  (only the first beat — the imprisonment.)
   The teaser line shown over the cover is the only place a future twist is hinted.

2) CONNECTIVE GRAMMAR — CHOOSE BY MEANING (scenes 2 onward)
   - Most scenes open with a connective to create the "and then... and then..." flow,
     but it is NOT mandatory. When no transition is natural — a scene-setting / context
     beat, or a fresh beat that doesn't follow from the last one — open SUBJECT-FIRST
     and set "connective" to null. A forced connective on a non-transitional beat reads
     wrong and is an AI-tell.
   - Pick the connective by MEANING, not by habit:
     • CONTRAST / REVERSAL only → "But" / "However". Use ONLY when the beat genuinely
       defies the previous beat or the viewer's expectation. You must be able to point
       at the thing it contradicts. If you can't, it is the WRONG word.
       ✗ "But this is 1970s New York." (no contrast — just setting the scene)
       ✗ "But Patton webbed up a mouse." (just the next action — not a reversal)
       ✓ "But the Penance Stare had no effect." (he expected it to work — it didn't)
     • SEQUENCE / TIME → Then, Now, Soon, As, When, After, Eventually, Meanwhile, Until
     • CONSEQUENCE → So, With, Instead
   - Do NOT chain "But" across scenes, and don't let it become your default opener —
     across the whole script only a FEW scenes should be true contrasts.
   - The schema field "connective" = the opener you actually used, or null if subject-first.

2.5) PACING — PROTECT THE HIGHLIGHT, DON'T DWELL ON SETUP
   - A story has ONE biggest reveal or visual (a transformation, a new foe, a twist).
     Do NOT name it in the SETUP beat that leads into it. Introduce the moment plainly
     first (e.g. "enemies were already waiting"), then let the reveal LAND on its own
     later beat. Naming the payoff inside its own setup kills the tension.
   - Setup and FLASHBACKS are context, not the story. Keep them BRIEF — a flashback or
     backstory should occupy 1-2 scenes, then return to the main action. Spend the bulk
     of the script on the central confrontation and its payoff, not the lead-up. If
     several scenes in a row are still "establishing," collapse them.
   - **MAIN-POINT FOCUS (ruthless — the viewer wants the CORE story, fast).** Tell the
     central spine, not every detail. Every scene must change the MAIN story; if a
     detail, side-character, sub-plot, location, or flavor beat could be cut and the
     core story still fully makes sense, CUT IT. Pick the fewest scenes that still cover
     setup → key turn → climax → ending. A tighter, faster script that nails the main
     point beats a complete-but-bloated one. STILL COVER THE WHOLE ARC — never drop the
     climax or the ending; you are trimming detail WITHIN the story, not skipping beats.
     When unsure whether a detail matters to the outcome, leave it out.

3) SENTENCE SHAPE — SHORT + PUNCHY, ONE EVENT PER SENTENCE (this is the fix)
   - **ONE EVENT PER SENTENCE — HARD RULE.** Each scene is ONE page held on screen
     for only a few seconds, so it can show ONE action. If your sentence names two
     things happening ("X did A as Y did B and warned C"), the viewer sees one page
     while you narrate three things — it looks WRONG. Pick the single most
     important action of the beat and narrate only that. Drop the secondary clauses.
   - **DO NOT write uniformly-sized sentences.** Vary length, but vary toward SHORT.
   - Target distribution across the scenes (one per beat; including the outro credit):
     • **AT LEAST 3 short PUNCH sentences (≤11 words)** — landing/twist moments.
       A script with fewer than 3 punch sentences will be rejected and retried.
     • the rest are MEDIUM (12-17 words) — main flow
     • a CAUSAL scene (one event + its grounded 'why' clause, rule 6.7) MAY run to
       the 18-word line cap; the hook (scene 1) too. Plain scenes over 14w with
       no causal clause are still rejected — don't pad.
     • 1 outro credit "The comic is X" (5-8 words)
   - Punch examples (≤11w, hit hard — these LAND). VARY the opener — most punches do
     NOT start with "But":
     ✓ "The Penance Stare had no effect." (6w)                 — subject-first
     ✓ "Stating they would die anyway." (5w)                   — fragment lead-in
     ✓ "Then Ben crushed the sonic gun and stormed out." (9w)  — sequence opener
     ✓ "But he only stopped once he remembered his aunt." (9w) — TRUE contrast (he wouldn't stop — then did)
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
   - Every scene carries one real event — a punch line may be as short as 5 words, but never pad a thin scene just to hit a length.

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

   ABSOLUTE RULE — NEVER INVENT AN EVENT. The WIKI plot / beat SUMMARY is the ONLY
   authority for WHAT HAPPENED. A panel image only tells you HOW to phrase a moment —
   it does NOT license a new event. If an event, death, mechanism, or INTERNAL
   EXPERIENCE (telepathy, mind-reading, "phasing into someone's mind", a vision, a
   shared memory, a character's private thought) is NOT in the beat summary, the wiki
   plot, or the dialog, you MUST NOT write it — EVEN IF a panel seems to suggest it.
   When a panel is ambiguous, narrate ONLY the plain physical action shown.
     ✗ "in desperation he phased into her dying mind, witnessing the terror firsthand
        before she died"  (invented internal experience + invented death — not in wiki)
     ✓ "he reached for the falling crew member, but his phantom hand passed through her"
        (the plain physical action actually shown)
   If you cannot ground a dramatic beat in the wiki / summary / dialog, DROP it and tell
   the simpler TRUE event. A thinner accurate scene ALWAYS beats a vivid invented one.

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
   A scene that carries a causal clause MAY run up to the 18-word line cap
   (others stay punchy, ≤14). Never invent a cause not in the WHY/summary/wiki.

6.5) FACT-CHECK SELF-PASS — before returning JSON
   For EACH scene you write, mentally verify:
   (a) Every named character actually appears in this panel's `characters` list (or page summary)
   (b) Every action verb (bit, fired, exploded, devoured, etc.) is in panel description or dialog
   (c) Every emotion adjective is in `dominant_emotion` or implied by dialog text
   (d) Every adjective describing a thing (glowing, monstrous, etc.) is in panel description
   If a phrase isn't grounded, REPLACE it with a grounded one or REMOVE it. Better to write a less colorful but accurate scene than a vivid but invented one.
   The user has rejected past drafts that twisted the story. Accuracy beats flourish.

7) LENGTH BUDGET — TIGHT, PUNCHY SHORT (the finished video must stay ~60-75s)
   - **16-20 scenes** total (one per beat — cover the full arc incl. resolution). Keep
     scenes SHORT so more of them still fit the word ceiling (short scenes = more panels).
   - **195-245 words TOTAL — this is a HARD CEILING, not a target to fill.** At the
     MEASURED render pace (~3.4 words/sec at our shipped 1.35 atempo) that + the teaser
     intro is ~61-76s finished — the top of the 48-71s viral cluster. Going over makes the
     Short drag. Aim for the MIDDLE (≈210-225w) unless the arc genuinely needs more.
   - Before returning JSON, COUNT your total words. If > 245, you MUST cut: drop
     adjectives, drop any clause that is NOT a grounded cause, and tighten each sentence —
     keep ONE event (+ its 'why') per sentence. NEVER exceed 245. Keep all canonical beats.
   - If < 195 → ADD a missing canonical/causal beat (never pad with empty adjectives).
   - Sentence length distribution: see rule 3 (≥3 punch / mostly medium / few causal / 1 outro).

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
   - SMOOTH BEAT-TO-BEAT TRANSITIONS: each scene must connect clearly to the one
     before it (cause→effect or plain time order) so a first-time listener never
     feels a jump between beats. The hand-off from one beat/scene to the next is
     what must be easy to follow.

8.6) UNDERSTAND THE STORY LOGIC — you are telling ONE connected story, not labelling panels
   - Before writing, read ALL beats as a single CAUSE→EFFECT chain. Each scene must
     make the NEXT one make sense to a viewer who has NEVER read the comic.
   - Preserve every SETUP→PAYOFF thread the beats contain:
       • If a beat says a character is presumed DEAD ("appears to kill / apparently
         killed"), plant that death CLEARLY, then PAY IT OFF at the beat where they
         are revealed alive — name the reveal ("...steps from the shadows, alive").
       • Any APOLOGY, breakdown, or emotional turn MUST say what it is FOR and why
         NOW — use that beat's WHY/cause. "Apologises for everything" with no reason
         is a FAILURE: state what he is sorry for and what just forced it out of him.
   - ONE BEAT = ONE SCENE, ALWAYS. Each scene is locked to its beat's panel in the
     finished video, so merging two beats into one sentence makes every later image
     show the WRONG moment. Never drop or fold a beat for brevity — tighten wording
     instead.
   - TELL THE EVENT, NOT THE PICTURE: each line says WHAT HAPPENS and WHY in plain
     words. Do NOT narrate incidental panel visuals the listener cannot place — a
     crown, "weeping eyes", a curtain pulled back, crystalline textures, what a hand
     is "holding". If a visual detail does not move the story forward, drop it.
     Answer "what just happened and why", never "what does this panel look like".

8.65) CONCEALED IDENTITY — NARRATOR NEVER ASSERTS THE FALSE NAME AS FACT
   When the protagonist believes a false identity and their TRUE name is the story's
   late reveal, the NARRATOR must never state the false identity as settled fact —
   the comic is hiding this from the reader, so the narrator can't already know it.
   Frame it as the CHARACTER's belief or his own words instead, until the beat where
   the comic itself reveals the truth:
     ✗ "He knew exactly who he was. He was Victor Von Doom."  (narrator asserts the
        false identity as fact — this is the twist being told away)
     ✓ "He believed he was Victor Von Doom."
     ✓ "He kept repeating one name: Victor Von Doom."
   The narrator may assert the TRUE identity as fact ONLY at the beat where the comic
   discloses it — never before.

8.68) TWIST LANDING — UNPACK THE REVEAL, THEN MIRROR IT (when the climax is a reveal/twist)
   The reveal line states the FACT — that alone is not enough. The ending must land in
   two moves, using the comic's own stated meaning (dialog / lore — rule 6 still holds):
   - UNPACK: the scene after the reveal states in plain words what the twist MEANS for
     the whole story — the full implication, not a flat reaction beat. If the beat is
     "characters react/discuss", narrate the implication OVER that beat instead of the
     reaction itself:
       ✗ reveal: "That shard WAS the spark." → next: "His partner urged him to sit
          with it."  (the twist's meaning is never stated — wasted scene)
       ✓ reveal: "That stolen shard was the spark that lit the Big Bang." → next:
          "The universe existed only to carry Doom to this moment, so his soul could
          light the spark."  (the closed loop is now explicit)
   - MIRROR: the FINAL story line (just before the outro credit) ties the twist back
     to the protagonist's defining trait or wound from the setup — "went looking for
     X, and found Y" shape, plain words, still what the final panel shows:
       ✓ "Doom had gone looking for the face of creation, and found his own — a
          universe masked, self-contained, and unknowable, made in his image."
   - These TWO closing lines (unpack + mirror) may run up to 24 words when the idea
     needs it — the only scenes besides the hook allowed past the 18-word cap. Do not
     spend the headroom on modifiers; spend it on the idea.

8.7) KEEP IT SIMPLE — ONE MAIN THREAD, EXPLAIN AS YOU GO
   - Tell ONLY the main event/conflict (the hero vs the central villain/threat).
     A confused viewer is worse than a thin one — drop side characters and subplots.
   - The viewer knows NOTHING going in. Weave the key CONTEXT into the telling so
     each turn makes sense: WHO a named character is + what is happening to them, and
     HOW/WHY the villain did what he did. Never drop a big event bare — give the
     one-clause reason the source provides (not "MODOK conquered nine realms" but
     "by fusing with the Bifrost, MODOK seized the World Tree and conquered nine
     realms").
   - Establish the core SETUP — who the villain is, how the threat arose, what the
     hero must do — plainly in the FIRST 2-3 scenes, before the fighting starts.
   - DO NOT NAME a throwaway / one-scene character who has no recurring story role —
     a random victim, mook, guard, bystander, pilot. Use a plain LABEL the viewer can
     follow instantly ("the criminal", "a soldier", "a fleeing survivor"). A name the
     viewer never hears again is noise that breaks the flow. Only name characters who
     RECUR or matter to the main conflict.
       ✗ "the Penance Stare forced Vinnie to feel every sin"  (who is Vinnie?)
       ✓ "the Penance Stare forced the criminal to feel every sin"

10) FORBIDDEN
   - No em-dashes (—), no brackets, no parenthetical asides — this is spoken aloud.
   - No "what do you think in the comments", no "subscribe", no questions to viewer at the end.
   - No stage directions, no scene numbers inside text.
   - No comic SOUND-EFFECTS / onomatopoeia (KA-THOOM, BOOM, SNIKT, KRAKA). Those are
     lettering ART, not story — describe the ACTION instead ("a thunderclap shook the
     city"), never the sound-word.
   - No spelled-out ACRONYM names with periods (e.g. "M.Y.T.H.O.S."): read aloud they
     become letters one-by-one. Use the character's common, familiar name instead
     (e.g. "MODOK"). If a villain adopts an in-story acronym/title, still call them by
     the recognizable name.

11) VOICE & RHYTHM — channel-calibrated from 219 reference Shorts

   11a. SENTENCE LENGTH VARIANCE — see rule 3 for the punch/medium/causal distribution.
        Uniformity is the AI-tell; cramming two events into one sentence is the bug.

   11b. STORYTELLER VOICE — not panel-reader
        After INTRODUCING a character by canonical name, switch to PRONOUNS
        (he/him/she/her) for the next 2-3 sentences. Re-introduce by name
        only when the scene shifts to a different character.
        Channel: "When Frank Castle entered Valhalla, he couldn't find peace.
        So, Odin returned his cosmic powers and turned him into Ghost Rider again."
        AVOID: "Reed Richards X... Reed Y... Reed Z..." every sentence (AI-tell).

   11b-2. NAME FAMOUS CHARACTERS — don't over-describe them
        Well-known characters (Doom, Spider-Man, Doctor Strange, Magik, Venom…) need
        NO physical introduction — just NAME them, or a clean recognizable epithet
        ("Doom, ruler of Latveria"). Do NOT stack odd/forced adjectives on them.
        ✓ "Doom threw him from the window."   ✓ "the real Doom"
        ✗ "the armored, Latverian Doom"   ✗ "Illyana, armored and defiant"
        Keep the prose SIMPLE and plain. Never bend wording to chase rhythm/rhyme at
        the cost of clarity or matching the panel.

   11b-3. DON'T NAME OBSCURE ONE-ISSUE ENTITIES — use a ROLE descriptor instead
        NAME only entities a mainstream comic audience already knows (major heroes/
        villains). A minor, this-issue-only entity (a local cult, a hired assassin,
        a one-off gadgeteer) must NOT be referred to by its proper name — use a
        plain ROLE descriptor instead ("a doomsday cult", "a hired assassin", "a
        scrap-tech inventor"). An unfamiliar proper name costs the viewer a beat of
        confusion and adds nothing. The famous-name rule above (11b-2) still applies.

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
        "  7. SUPER-SUMMARIZE — this is a WHOLE multi-issue saga in ~90 seconds. Stay\n"
        "     HIGH-LEVEL: tell the big arc (setup → key turn → climax → ending), NOT\n"
        "     every plot beat. Collapse minor steps; keep ONLY the turns that change the\n"
        "     hero's situation. Covering the WHOLE story lightly beats half of it in detail.\n"
        "╚════════════════════════════════════════════════════════════════╝\n\n"
    )


def _orientation_block() -> str:
    """Opening-orientation rule for EVERY mode: the viewer knows nothing about this
    comic, so the narration must establish who + where + premise BEFORE the plot's
    first event (fixes 'who is Thorlief? what story is this?')."""
    return (
        "╔═══ ORIENT THE VIEWER FIRST (all modes) ═══╗\n"
        "The viewer has NOT read this comic and knows NOTHING going in. BEFORE the\n"
        "story's first event, the OPENING narration scene must ORIENT them in plain\n"
        "words: WHO the main character is (name + a short who/what tag) AND the WORLD/\n"
        "PREMISE in one clear phrase. Only AFTER that does the plot start. e.g. don't\n"
        "open on 'When Thorlief found a body…' — first ground it: 'Thorlief is a\n"
        "detective in the Thor Corps, a police force of Thor variants on Battleworld —\n"
        "and when he found a body…'. Never open on a bare name or event the viewer\n"
        "cannot place.\n"
        "ALSO gloss the FIRST mention of any key OBJECT, POWER, or SUBSTANCE with a\n"
        "2-4 word 'what it is' tag — ESPECIALLY when its name could be mistaken for a\n"
        "famous character. e.g. in a Bane comic 'Venom' is the strength DRUG that bulks\n"
        "him up, NOT the Spider-Man symbiote — say 'Venom, the strength drug' (or 'the\n"
        "super-steroid Venom') on first mention, never a bare 'Venom'. Same for any\n"
        "serum, device, or power whose plain name a first-time viewer would misread.\n"
        "NAME THE HERO PLAINLY by their familiar identity, not only an in-story title:\n"
        "'Thor, the King of Asgard' — not just 'the new All-Father' (a newcomer must\n"
        "never wonder 'who, or which version, is this?'). And EXPLAIN THE THREAT'S\n"
        "ORIGIN up front: before the hero acts, say HOW the villain got their power or\n"
        "how the danger began (the mechanism the source gives) — never just the result\n"
        "('X conquered the realms' with no how).\n"
        "╚════════════════════════════════════════════╝\n\n"
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
    story_map: dict | None = None,
    direction: dict | None = None,
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

    _smap = render_story_map_block(story_map)
    user = (
        _direction_block(direction or {})
        + _smap
        + f"COMIC CONTEXT:\n{_ctx_block(comic_context)}\n\n"
        + (f"{wiki_block}\n\n" if wiki_block else "")
        + (f"{lore_block}\n\n" if lore_block else "")
        + f"NARRATION MODE: {mode} — {mode_info.description}\n"
        + (f"HOOK HINT: {hook_hint}\n" if hook_hint else "")
        + "\n"
        + _orientation_block()
        + _saga_clarity_block(comic_context)
        + f"BEATS — write EXACTLY ONE scene for EACH beat, in this SAME order:\n{_beats_block(beats)}\n\n"
        f"GLOSSARY (use these exact names):\n{_glossary_block(glossary)}\n\n"
        + (f"{few_shot}\n\n" if few_shot else "")
        + f"PAGE DETAIL (background grounding — what is actually on each page, so "
        f"your prose stays factual):\n{_pages_block_compact(story_pages)}\n\n"
        f"WORD BUDGET: {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} total words across all scenes.\n"
        f"CONNECTIVES (OPTIONAL — pick by meaning; null if subject-first; But/However = real contrast ONLY): {', '.join(_CONNECTIVES)}.\n\n"
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
        f'    {{"text": "Then ...", "connective": "Then", "beat_id": "<2nd beat id>"}},\n'
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
        "coverage gap",              # writer dropped/merged beats → panels desync
        "words over hard ceiling",   # draft blew way past the word budget
        "over the line cap",         # a single scene line ran too long (blows the budget)
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

    # Coverage-gap guard: the writer MUST emit exactly one story scene per beat. When
    # it merges/drops beats (pool < beats), positional anchoring leaves later beats
    # with no scene and every following panel desyncs from its narration. Flag it as
    # a CRITICAL logic error so the retry loop re-writes with full beat coverage.
    gaps = parsed.get("_coverage_gaps") or []
    pool_n = parsed.get("_anchor_pool_count")
    n_beats = len(valid_beat_ids)
    if gaps or (pool_n is not None and n_beats and pool_n < n_beats):
        detail = (f"beat(s) {gaps} have no scene" if gaps
                  else f"got {pool_n} story scenes for {n_beats} beats")
        errors.append(
            f"coverage gap: {detail} — emit EXACTLY one story scene per beat, in beat "
            f"order (position is binding; a missing or extra scene desyncs every "
            f"following panel)")
    elif pool_n is not None and n_beats and pool_n > n_beats:
        # Writer over-emitted (split a beat into two) — `_anchor_scenes_to_beats`
        # already recovered this by aligning both ends and dropping the middle
        # surplus (gaps==[]), so this is NOT a coverage gap. Soft-report only.
        errors.append(
            f"scene surplus: got {pool_n} story scenes for {n_beats} beats "
            f"(both-ends aligned; middle surplus dropped)")

    total_words = 0
    contrast_flags: list[bool] = []  # per body scene: opens with But/However/Yet?
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

        # Connectives are OPTIONAL and chosen by MEANING (not forced on every scene).
        # If the writer set one, it must be whitelisted AND actually open the text; a
        # subject-first scene (connective=null) is allowed.
        conn = (s.get("connective") or "").strip()
        text_start_conn = _starts_with_connective(text)
        if conn:
            if conn not in _CONNECTIVES:
                errors.append(f"scene {i} connective {conn!r} not in whitelist")
            elif text_start_conn is None or text_start_conn.lower() != conn.lower():
                errors.append(f"scene {i} connective={conn!r} but text does not open with it")
        # Tally contrast openers (But/However/Yet) — guarded for overuse after the loop.
        first_word = text.split()[0].rstrip(",").lower() if text else ""
        contrast_flags.append(first_word in ("but", "however", "yet"))

        floor = 8 if is_last else _SCENE_MIN_WORDS
        # Twist-landing headroom (rule 8.68): the last two STORY lines (the scenes
        # just before the outro credit) may run to _FINALE_MAX_WORDS — they carry
        # the twist's implication + the thematic mirror. Everything else keeps the
        # strict cap.
        cap = _FINALE_MAX_WORDS if i >= len(scenes) - 2 else _SCENE_MAX_WORDS
        if wc > cap:
            # Over-long lines are the #1 cause of blown word budgets (prompt-only
            # caps were ignored 3 runs straight) — enforce as CRITICAL so the retry
            # loop forces a per-line rewrite.
            errors.append(f"scene {i} is {wc} words — over the line cap "
                          f"({cap}); trim modifiers/sub-clauses, keep the event")
        elif wc < floor:
            errors.append(f"scene {i} is {wc} words, want {floor}-{cap}")

    # Total-words band — single source of truth, calibrated DOWN from the old
    # 230..290 (which forced ~283-word, long, compound output). The body (this
    # draft, pre-intro) targets _TARGET_WORDS_MIN.._TARGET_WORDS_MAX; allow a
    # little slack on top so a 1-2 word overshoot doesn't churn the retry loop.
    if not (_TARGET_WORDS_MIN <= total_words <= _TARGET_WORDS_MAX + 20):
        errors.append(
            f"total words {total_words} not in "
            f"{_TARGET_WORDS_MIN}..{_TARGET_WORDS_MAX + 20}"
        )
    # Hard ceiling — a bigger overshoot must force a retry, not just a soft warning
    # that MAX_PASSES can ship anyway (the soft band message above never marks
    # _is_critical_error, so a 300+w draft could still win best-draft).
    if total_words > _TARGET_WORDS_MAX + 40:
        errors.append(
            f"words over hard ceiling: {total_words} exceeds "
            f"{_TARGET_WORDS_MAX + 40} — must cut"
        )

    # Contrast-opener overuse guard — replaces the old "every scene MUST start with a
    # connective" mandate. But/However/Yet must mark a GENUINE reversal, so cap them
    # (MAX safety rail, NOT a target frequency) and forbid back-to-back contrast opens.
    n_contrast = sum(contrast_flags)
    contrast_cap = max(2, len(contrast_flags) // 5)   # ~20% of body scenes, min 2
    if n_contrast > contrast_cap:
        errors.append(
            f"{n_contrast} scenes open with But/However/Yet (max {contrast_cap}) — "
            f"reserve contrast words for real reversals; use sequence words or none"
        )
    if any(a and b for a, b in zip(contrast_flags, contrast_flags[1:])):
        errors.append("two consecutive scenes open with But/However/Yet — vary the opener")

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


def _normalize_titles_to_common(scenes: list[dict], comic_context: dict,
                                log=lambda _m: None) -> None:
    """B2 — replace confusing character TITLES/acronyms in the narration with the
    character's most-common (canonical) name, using the comic's own character list:
      - dotted acronyms ("M.Y.T.H.O.S.")           → the character's name (MODOK)
      - long self-given titles (>=4 words, e.g.
        "Master of Yggdrasil, Tyrant of Humanity…") → the character's name
    Short, recognizable nicknames (Spider-Man, All-Father, Thor Odinson) are LEFT
    alone. Mutates scene dicts in place. The viewer should always hear the name they
    know, never an in-world title they can't place."""
    chars = (comic_context.get("summary") or {}).get("characters") or []
    repl: list[tuple] = []   # (compiled_pattern, canonical_name)
    for ch in chars:
        canon = str(ch.get("name", "") or "").strip()
        if not canon:
            continue
        for alias in ch.get("aliases", []) or []:
            a = str(alias or "").strip()
            if not a or a.lower() == canon.lower():
                continue
            is_dotted = bool(re.fullmatch(r"(?:[A-Za-z]\.){2,}[A-Za-z]?\.?", a))
            if is_dotted:
                letters = re.sub(r"[^A-Za-z]", "", a)          # M.Y.T.H.O.S. → MYTHOS
                pat = re.compile(r"\b" + r"\.?".join(letters) + r"\.?", re.IGNORECASE)
                repl.append((pat, canon))
            elif len(a.split()) >= 4:                           # long self-title
                repl.append((re.compile(re.escape(a), re.IGNORECASE), canon))
    if not repl:
        return
    n = 0
    for s in scenes:
        t = s.get("text", "") or ""
        for pat, canon in repl:
            t, c = pat.subn(canon, t)
            n += c
        s["text"] = t
    if n:
        log(f"[stage4] B2: normalized {n} confusing title(s)/acronym(s) → canonical name")


def _to_narration(parsed: dict, beats: list[Beat], glossary: Glossary,
                  mode: str, mdl: str) -> Narration:
    scenes: list[Scene] = []
    total_words = 0
    raw_scenes = parsed.get("scenes") or []
    prev_toks: set[str] | None = None
    for s in raw_scenes:
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        # Collapse a CONSECUTIVE near-identical scene: the writer occasionally emits
        # the same sentence 2-3× in a row ("Doom vowed to kill any who claim his
        # name" ×3), which would play as a stutter. Drop the repeat, keep the first.
        toks = {w for w in re.findall(r"[a-z0-9]+", text.lower())}
        if prev_toks and toks and not s.get("is_outro") and not s.get("is_intro"):
            jac = len(toks & prev_toks) / len(toks | prev_toks)
            if jac >= 0.9:
                continue
        prev_toks = toks
        wc = len(text.split())
        conn = s.get("connective")
        scenes.append(Scene(
            scene_id=len(scenes) + 1,
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
    from .._panel_index import page_dialog
    recap_chunks: list[str] = []
    allowed_types = {"caption", "narration", "title", "subtitle"}
    for p in all_pages or []:
        if p.get("page_type") not in ("cover", "skip"):
            continue
        for tb in page_dialog(p):
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
    lines.append(f"Characters: {', '.join((c.get('name', '') if isinstance(c, dict) else str(c)) for c in ctx.get('characters', [])) or '?'}")
    plot = ctx.get("plot_summary", "")
    if plot:
        lines.append(f"\nPlot (from wiki):\n{plot[:2000]}")
    return "\n".join(lines)


def _panel_area_frac(panel: dict, page_area: int) -> float:
    """Panel bbox area as a fraction of the whole page (0..1). A big/splash panel
    (a visual highlight: a splash, a large action/reveal shot) is >= 0.40."""
    bb = panel.get("bbox") or {}
    a = int(bb.get("w", 0) or 0) * int(bb.get("h", 0) or 0)
    return a / page_area if page_area > 0 else 0.0


_BIG_SHOT_FRAC = 0.40  # panel filling >=40% of its page = a highlight ("big shot")


def _pages_block_full(story_pages: list[dict]) -> str:
    out: list[str] = []
    for p in story_pages:
        pn = p.get("page_number")
        issue = p.get("issue_label", "")
        summary = (p.get("page_summary") or "").strip()
        dims = p.get("image_dimensions") or {}
        page_area = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
        panels = p.get("panels") or []
        page_big = any(_panel_area_frac(pan, page_area) >= _BIG_SHOT_FRAC for pan in panels)
        head = f"[page {pn}{' ' + issue if issue else ''}]" + (" ★BIG-SHOT PAGE" if page_big else "")
        block = [f"{head} {summary}"]
        for pan in panels:
            desc = pan.get("description", "")
            chars = ", ".join(pan.get("characters", []) or [])
            emo = pan.get("dominant_emotion", "")
            big = " ★BIG SHOT" if _panel_area_frac(pan, page_area) >= _BIG_SHOT_FRAC else ""
            block.append(f"  panel {pan.get('index')}:{big} {desc} [chars: {chars or '?'}] [emotion: {emo or '?'}]")
            from .._panel_index import panel_dialog
            for tb in panel_dialog(pan, p.get("text_blocks")):
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
║    Step 2. Scan EVERY scene listed above for any of those keywords        ║
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
      "scenes_checked": "checked every scene; none mention machine/extract/separate"
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


# Phase F — INTERNAL coherence (a DIFFERENT axis from phase E's wiki-fidelity).
# Phase E asks "does the narration match the canonical plot?"; phase F asks "is the
# narration self-consistent?" — does a line contradict the situation the narration
# itself sets up (e.g. a character railing that others 'only care about Earth' while
# every scene places them on another planet). Narration-ONLY (no panels/bubbles) so
# noisy VLM panel descriptions can't cause false contradictions.
_COHERENCE_SYSTEM = """You check ONE thing: is the narration INTERNALLY consistent? Read the scenes in order and flag a scene ONLY when its claim HARD-CONTRADICTS a fact ANOTHER scene establishes, or is self-defeating given the story's own setup — a contradiction a casual viewer would notice and find nonsensical.

Examples that SHOULD flag:
- One scene places everyone on another planet (a colony in space), and another scene has a character attack the others for "only caring about Earth" — their being there refutes it.
- A character is said to be dead in one scene and acting in a later one with no revival.
- A stated cause that cannot produce the stated effect within the story as told.

Do NOT flag: style, pacing, emphasis, missing detail, plausible omissions, or anything that merely COULD be richer. Do NOT invent a contradiction from outside knowledge. When unsure, DO NOT flag. Most narrations are clean — returning zero issues is the common, correct answer.

Return JSON ONLY: {"issues":[{"scene_id":N,"contradiction":"...","fix_hint":"..."}]}."""


def _coherence_check(
    parsed: dict, *, model: str | None, progress: Callable[[str], None] | None,
) -> list[str]:
    """Phase F: internal/situational coherence (narration-only). Returns issue
    strings — empty when the narration is self-consistent. Never raises (skips on
    any LLM failure) so it can only ADD a soft signal, never block the pipeline."""
    log = progress or (lambda _msg: None)
    scenes = [s for s in (parsed.get("scenes") or []) if not s.get("is_intro")]
    if len(scenes) < 3:
        return []
    nar_lines = [f"S{s.get('scene_id', '?')}: {str(s.get('text', '')).strip()}" for s in scenes]
    user = ("NARRATION SCENES (check internal consistency only):\n"
            + "\n".join(nar_lines) + "\n\nReturn JSON {\"issues\": [...]}.")
    log("[stage4] phase F — internal coherence check…")
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, _mdl = call_with_chain(
            system=_COHERENCE_SYSTEM, user=user, models=chain, max_tokens=2000,
            progress=progress, label="coherence", validator=lambda c: '"issues"' in c)
    except RuntimeError as exc:
        log(f"[stage4]   coherence check chain failed — skipping: {exc}")
        return []
    pc = _extract_json(raw)
    if not isinstance(pc, dict):
        return []
    out = []
    for it in (pc.get("issues") or []):
        c = str(it.get("contradiction", "")).strip()
        if c:
            out.append(f"scene S{it.get('scene_id', '?')}: {c} (fix: {str(it.get('fix_hint', '')).strip()})")
    if out:
        log(f"[stage4]   phase F found {len(out)} coherence issue(s)")
    return out


_LOGIC_CLARITY_SYSTEM = """You are a ruthless STORY EDITOR for a faithful comic-recap \
Short watched by people who know NOTHING about this comic. Read the narration in order \
and flag ONLY scenes that fail a zero-context viewer or waste their time:

FLAG when:
- CLARITY: a name, term, power, object, place, or plot turn a no-context viewer cannot \
follow because it was never explained on first use (needs a 4-8 word gloss), OR a \
cause->effect jump they cannot follow ("suddenly X" with no stated why).
- IMPACT: a scene that is low-impact filler — it says little, restates a prior beat, or \
is flabby/lengthy relative to how much it matters. Fix = tighten to one punchy line.
- PAYOFF: the climax or ending twist does not LAND because its logic is left implicit.

Do NOT flag:
- Faithfulness / canon accuracy — a SEPARATE check owns that; assume the events are correct.
- Style, tone, word choice, or anything that merely COULD be richer.
When unsure, DO NOT flag. A clean narration returns zero issues — that is the common, \
correct answer. Each fix must be ACTIONABLE for the writer (what to add / cut / clarify).

Return JSON ONLY: {"issues":[{"scene_id":N,"problem":"...","fix":"..."}]}."""


def _logic_clarity_critic(
    parsed: dict, comic_context: dict,
    *, model: str | None, progress: Callable[[str], None] | None,
    story_map: dict | None = None,
) -> list[str]:
    """Phase G — story-editor critic: would a ZERO-CONTEXT viewer follow every scene,
    and does each scene earn its screen time? Returns SOFT `clarity:` issue strings
    (missing gloss, unfollowable cause->effect, a twist that doesn't land, low-impact
    flab) that feed back to the writer via the existing retry loop — keeping the writer
    prompt light. Faithfulness is NOT this critic's job (the wiki cross-check owns it).
    Never raises (skips on any LLM failure), so it can only ADD a soft signal."""
    if not ENABLE_LOGIC_CRITIC:
        return []
    log = progress or (lambda _msg: None)
    scenes = [s for s in (parsed.get("scenes") or []) if not s.get("is_intro")]
    if len(scenes) < 3:
        return []
    chars = ", ".join(_character_names(comic_context)) or "?"
    plot = str(comic_context.get("plot_summary", "")).strip()[:1600]
    nar_lines = [f"S{s.get('scene_id', '?')}: {str(s.get('text', '')).strip()}" for s in scenes]
    _smap = render_story_map_block(story_map)
    user = (
        _smap
        + f"CHARACTERS: {chars}\n"
        f"PLOT (ground truth — for YOUR understanding of what's faithful, not to copy):\n{plot}\n\n"
        "NARRATION SCENES (in order — judge zero-context clarity + impact only):\n"
        + "\n".join(nar_lines)
        + "\n\nReturn JSON {\"issues\":[{\"scene_id\":N,\"problem\":\"...\",\"fix\":\"...\"}]}."
    )
    log("[stage4] phase G — logic/clarity critic…")
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, _mdl = call_with_chain(
            system=_LOGIC_CLARITY_SYSTEM, user=user, models=chain, max_tokens=2000,
            progress=progress, label="logic-critic", validator=lambda c: '"issues"' in c)
    except RuntimeError as exc:
        log(f"[stage4]   logic critic chain failed — skipping: {exc}")
        return []
    pc = _extract_json(raw)
    if not isinstance(pc, dict):
        return []
    out = []
    for it in (pc.get("issues") or []):
        p = str(it.get("problem", "")).strip()
        if p:
            out.append(f"clarity: scene S{it.get('scene_id', '?')}: {p} "
                       f"(fix: {str(it.get('fix', '')).strip()})")
    if out:
        log(f"[stage4]   phase G found {len(out)} clarity/impact issue(s)")
    return out


_BEAT_IMPACT_SYSTEM = """You are a STORY EDITOR trimming a beat outline for a TIGHT, \
concise, 100%-faithful comic Short. Decide which beats EARN their screen time and which \
are LOW-IMPACT filler that can be SKIPPED without breaking the story's logic or losing \
its emotional core. A tighter Short that a zero-context viewer absorbs instantly beats a \
complete-but-flabby one.

DROP a beat when it is a side-detour, a redundant escalation, or world-building that the \
main through-line does not need — and the story still reads cause->effect without it.
NEVER drop: a COLD_OPEN, the CLIMAX, the LANDING, or any beat that a later kept beat \
DEPENDS ON (its setup/cause).

LENGTH BUDGET IS BINDING: the caller gives you a MAX beat count the finished Short can fit. \
If the outline is OVER budget you MUST drop the lowest-impact beats down to it — "dropping \
nothing" is valid ONLY when the outline is already at or under budget. When choosing among \
low-impact beats to cut, drop the most skippable first (a detour before a plot step).

Return JSON ONLY: {"drop":[ids...],"reason":"one line"}."""


def _critique_beats_for_impact(
    beats: list[Beat], comic_context: dict,
    *, model: str | None, progress: Callable[[str], None] | None,
    story_map: dict | None = None,
) -> list[Beat]:
    """Phase A2 — beat-impact critic: drop LOW-IMPACT beats so the Short is tight and
    concise while staying 100% faithful and logically whole. NEVER drops the cold-open,
    climax, landing, or a depended-on beat, and honors LOGIC_CRITIC_MIN_BEATS as a hard
    floor so it can't gut the story. Returns the kept beats in their original order.
    Never raises (keeps all beats on any failure)."""
    if not ENABLE_LOGIC_CRITIC or len(beats) <= LOGIC_CRITIC_MIN_BEATS:
        return beats
    log = progress or (lambda _msg: None)
    protected = {"COLD_OPEN", "CLIMAX", "LANDING"}
    plot = str(comic_context.get("plot_summary", "")).strip()[:1600]
    beat_lines = [f"id={b.id} [{b.function}] {b.name}: {b.summary.strip()[:140]}" for b in beats]
    _omit_hint = ""
    if story_map and story_map.get("omit"):
        _omit_hint = ("\nSTORY MAP OMIT HINTS (subplots/details too minor for this recap): "
                      + "; ".join(story_map["omit"]) + "\n")
    # Beat BUDGET — the finished Short must fit _TARGET_WORDS_MAX words (~16 words/scene, one
    # scene per beat), so about _TARGET_WORDS_MAX//16 beats. When the outline exceeds that, the
    # critic MUST trim down to it: the OLD keep-biased prompt gave no target, so a dense plot
    # (Doom 2099: 16 beats) kept every beat and blew the ceiling (345 words). Never below the floor.
    beat_budget = max(LOGIC_CRITIC_MIN_BEATS, _TARGET_WORDS_MAX // 16)
    excess = len(beats) - beat_budget
    if excess > 0:
        budget_line = (
            f"\nLENGTH BUDGET (BINDING): this Short fits ~{_TARGET_WORDS_MAX} words ≈ {beat_budget} beats. "
            f"You have {len(beats)} — DROP the ~{excess} LOWEST-impact beats so about {beat_budget} remain. "
            f"Cut side-detours / redundant escalations / world-building first; keep the cause->effect spine."
        )
    else:
        budget_line = (
            f"\nThis outline ({len(beats)} beats) already fits the ~{_TARGET_WORDS_MAX}-word budget — "
            f"drop a beat ONLY if it is genuinely low-impact."
        )
    user = (
        f"PLOT (ground truth):\n{plot}\n\n"
        f"BEAT OUTLINE ({len(beats)} beats):\n" + "\n".join(beat_lines)
        + _omit_hint
        + budget_line
        + "\n\nKeep the story faithful + logically whole. "
        "Return JSON {\"drop\":[ids],\"reason\":\"...\"}."
    )
    log(f"[stage4] phase A2 — beat-impact critic ({len(beats)} beats)…")
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, _mdl = call_with_chain(
            system=_BEAT_IMPACT_SYSTEM, user=user, models=chain, max_tokens=1200,
            progress=progress, label="beat-critic", validator=lambda c: '"drop"' in c)
    except RuntimeError as exc:
        log(f"[stage4]   beat-impact critic failed — keeping all beats: {exc}")
        return beats
    pc = _extract_json(raw)
    if not isinstance(pc, dict):
        return beats
    drop_ids: set[int] = set()
    for x in (pc.get("drop") or []):
        try:
            drop_ids.add(int(x))
        except (ValueError, TypeError):
            pass
    # Causal guard: the function-label guard below can't see the cause->effect
    # spine, so veto dropping any beat a KEPT beat depends on. A dropped beat is
    # an "antecedent" if a non-dropped beat's `cause` (the why) shares >=2
    # significant tokens with the dropped beat's name+summary — drop it and the
    # narration loses the link a later beat reasons from.
    # ponytail: token-overlap proxy (cause is free prose, not beat-id refs); one
    # pass off the LLM's drop set — good enough, upgrade to a real dep-graph only
    # if beats ever carry structured prereqs.
    # Ubiquitous tokens across ALL beats' name+summary (recurring character/setting
    # names) prove nothing about a real cause->effect dependency — same pattern as
    # _ensure_ending_coverage's `ubiq` set. Without this, a hero's name appearing in
    # every beat made the veto fire on almost every drop.
    _beat_ns_tokens = [set(_tokens(b.name)) | set(_tokens(b.summary)) for b in beats]
    _freq: dict[str, int] = {}
    for _ts in _beat_ns_tokens:
        for _tok in _ts:
            _freq[_tok] = _freq.get(_tok, 0) + 1
    _ubiq_cut = max(3, int(0.34 * len(beats))) if beats else 1
    ubiquitous = {t for t, n in _freq.items() if n >= _ubiq_cut}

    kept_cause_tokens: set[str] = set()
    for b in beats:
        if b.id not in drop_ids or str(b.function).upper() in protected:
            kept_cause_tokens.update(set(_tokens(b.cause)) - ubiquitous)
    vetoed: set[int] = set()
    if kept_cause_tokens:
        for b in beats:
            if b.id in drop_ids and str(b.function).upper() not in protected:
                bt = (set(_tokens(b.name)) | set(_tokens(b.summary))) - ubiquitous
                if len(bt & kept_cause_tokens) >= 2:
                    vetoed.add(b.id)
    if vetoed:
        drop_ids -= vetoed
        log(f"[stage4]   beat-impact critic: kept {len(vetoed)} causal-antecedent "
            f"beat(s) {sorted(vetoed)} a later beat's cause depends on")
    # Never drop protected beats (cold-open / climax / landing).
    kept_ids = {b.id for b in beats
                if b.id not in drop_ids or str(b.function).upper() in protected}
    # Hard floor: if the critic was too aggressive, re-add dropped beats (original
    # order) until we reach LOGIC_CRITIC_MIN_BEATS so the story is never gutted.
    if len(kept_ids) < LOGIC_CRITIC_MIN_BEATS:
        for b in beats:
            if len(kept_ids) >= LOGIC_CRITIC_MIN_BEATS:
                break
            kept_ids.add(b.id)
    kept = [b for b in beats if b.id in kept_ids]   # preserve original order
    dropped = [b.id for b in beats if b.id not in kept_ids]
    if dropped:
        log(f"[stage4]   beat-impact critic dropped {len(dropped)} low-impact beat(s) "
            f"{dropped} → {len(kept)} kept")
    return kept


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
    #
    # A missing-beat claim is a false positive only when a MAJORITY of the beat's
    # DISTINCTIVE keywords are already present. Distinctive = significant tokens
    # minus stop-words minus UBIQUITOUS tokens (words recurring across many scenes
    # — character names, setting). The old "ANY single keyword present → suppress"
    # let one shared "reed"/"doom" silently drop a genuinely-missing ENDING beat
    # (Doom throws Reed out the window + stretch reveal) → the climax-cut bug.
    import re
    scene_texts = [str(s.get("text", "")).lower() for s in scenes]
    narration_text = " ".join(scene_texts)
    _scene_toks = [set(re.findall(r"\b[a-z]{4,}\b", t)) for t in scene_texts]
    _ubiq_cut = max(3, int(0.34 * len(scene_texts))) if scene_texts else 1
    _freq: dict[str, int] = {}
    for _ts in _scene_toks:
        for _tok in _ts:
            _freq[_tok] = _freq.get(_tok, 0) + 1
    _ubiquitous = {t for t, n in _freq.items() if n >= _ubiq_cut}

    def _keyword_present_in_narration(beat_text: str, llm_kws: list[str] | None) -> tuple[bool, str]:
        """False-positive only when >=half the beat's DISTINCTIVE keywords appear
        in narration. Distinctive drops stop-words + ubiquitous names/setting, so
        one shared character name no longer suppresses a real missing beat."""
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
        kws_to_check = set()
        if llm_kws:
            for k in llm_kws:
                w = str(k).lower().strip(",.!?:;\"'")
                if w and len(w) > 2 and w not in stop:
                    kws_to_check.add(w)
        for tok in re.findall(r"\b[a-zA-Z]{4,}\b", beat_text.lower()):
            if tok not in stop:
                kws_to_check.add(tok)
        # distinctive = drop ubiquitous (recurring names/setting); if a beat is
        # ALL common words, fall back to the full set so we don't over-flag.
        distinctive = {k for k in kws_to_check if k not in _ubiquitous}
        pool = distinctive or kws_to_check
        if not pool:
            return False, ""
        present = [k for k in pool if k in narration_text]
        if present and len(present) / len(pool) >= 0.5:
            return True, present[0]
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
    beats: list[Beat],
    comic_context: dict,
    model: str | None,
    progress: Callable[[str], None] | None,
    debug_dump: dict,
    story_map: dict | None = None,
    direction: dict | None = None,
) -> dict:
    """Retry the writer with canonical wiki plot as PRIMARY ground truth.
    Used when wiki cross-check fails — the model needs to see the canonical story
    to correct missing/contradicting beats."""
    log = progress or (lambda _msg: None)
    err_block = "\n".join(f"- {e}" for e in errors[:30])
    prior = json.dumps(parsed, indent=2, ensure_ascii=False)
    plot = (comic_context.get("plot_summary") or "").strip()[:5000]
    arc = (comic_context.get("summary", {}) or {}).get("story_arc", "").strip()[:1500]

    _smap = render_story_map_block(story_map)
    user = (
        _direction_block(direction or {})
        + _smap
        + "Your previous narration draft failed canonical-story validation against the WIKI/FANDOM plot. "
        "Fix it. The wiki plot is GROUND TRUTH — your narration must match it factually.\n\n"
        + (f"CANONICAL STORY ARC:\n{arc}\n\n" if arc else "")
        + f"CANONICAL FULL PLOT (use this as your primary source of truth):\n{plot}\n\n"
        f"VALIDATION ERRORS (fix every one):\n{err_block}\n\n"
        + f"BEATS — emit EXACTLY ONE scene per beat, in this order ({len(beats)} story "
        f"scenes + 1 outro credit). If a 'coverage gap' error is listed above you "
        f"DROPPED or MERGED beats — re-add every missing beat as its own scene:\n"
        f"{_beats_block(beats)}\n\n"
        + _orientation_block()
        + _saga_clarity_block(comic_context)
        + f"HARD RULES (these don't change between retries):\n"
        f"- Connectives (OPTIONAL — pick by meaning; null if subject-first; But/However = real contrast ONLY): {', '.join(_CONNECTIVES)}.\n"
        f"- Scene 1 (hook): {_HOOK_MIN_WORDS}-{_HOOK_MAX_WORDS} words, connective MUST be null.\n"
        f"- Scenes 2+: {_SCENE_MIN_WORDS}-{_SCENE_MAX_WORDS} words (punch lines may be as short as {_SCENE_MIN_WORDS}; NO scene over {_SCENE_MAX_WORDS}).\n"
        f"- EXACTLY {len(beats)} story scenes — ONE per beat, in beat order — PLUS the "
        f"outro credit as the final element. Do NOT merge, skip, split, or reorder "
        f"beats; a missing scene desyncs every following panel.\n"
        f"- Total: {_TARGET_WORDS_MIN}-{_TARGET_WORDS_MAX} words (HARD ceiling).\n"
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
