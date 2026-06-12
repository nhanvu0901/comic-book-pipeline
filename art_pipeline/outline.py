"""A4o: long-form outline — ONE LLM call splits grounded facts into 4-5
chapters following the research playbook order (cold_open → backfill →
evidence → twist → resolution; spec 2026-06-12 §A4o).

Anti-hallucination lives HERE: every fact a chapter claims must be traceable
into the grounded context (substring or >=80% content-token overlap), so the
chapter writer downstream only ever sees verified material."""
import json

from config import CREATIVE_LLM_MODELS
from stages.stage_3._llm import call_with_chain

from ._json import extract_json
from .narrate import cap_facts
from .config import (
    ART_LF_CHAPTER_ROLES_4, ART_LF_CHAPTER_ROLES_5, ART_LF_CHAPTER_WORDS_MAX,
    ART_LF_CHAPTER_WORDS_MIN, ART_LF_MODES, ART_LF_TARGET_WORDS_MAX,
    ART_LF_TARGET_WORDS_MIN, get_art_project_path,
)

_ROLE_SEQUENCES = {4: ART_LF_CHAPTER_ROLES_4, 5: ART_LF_CHAPTER_ROLES_5}

_ROLE_GUIDE = """Chapter roles, in this exact order:
- cold_open: the mystery/contradiction of the artwork itself — concrete and
  surprising. NEVER biography first.
- backfill: only the context that serves the cold_open question (artist, era).
- evidence: close reading of the painting — technique, regions, x-ray/hidden
  details. (In 4-chapter outlines this merges with backfill as
  "backfill_evidence".)
- twist: the reversal — what changes how we read everything before.
- resolution: answer the through-line + a thematic close (no call-to-action)."""

_OUTLINE_SYSTEM = """You are the story architect for an 8-12 minute educational
art video. You receive GROUNDED FACTS and split them into 4 or 5 chapters.

Hard rules:
1. Causal order, not chronology: open with the artwork's mystery, backfill later.
{role_guide}
2. "facts" per chapter: 2-8 VERBATIM quotes copied from the GROUNDED FACTS block
   (short snippets are fine). Never paraphrase into new claims, never invent.
3. A fact may appear in ONLY ONE chapter.
4. target_words per chapter between {cw_min} and {cw_max}; the total across
   chapters between {w_min} and {w_max} (~140 spoken words per minute).
5. through_line: ONE driving question the video answers. For artist_journey it
   MUST be a question about the career, never "the life of X".
6. artist_journey only: every provided artwork id appears in >=1 chapter's
   artwork_ids; each chapter lists the artwork(s) it talks about.
Respond with ONLY valid JSON."""

_USER_TEMPLATE = """MODE: {mode}
ARTWORKS: {artworks}

GROUNDED FACTS (the ONLY allowed source; copy snippets verbatim):
{facts}

Return STRICT JSON only:
{{"mode": "{mode}", "through_line": "<one question>",
 "chapters": [{{"chapter_id": 1, "title": "...", "role": "cold_open",
               "facts": ["<verbatim snippet>", "..."],
               "target_words": 280, "artwork_ids": [{first_id}]}}]}}"""


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def fact_is_grounded(fact: str, context: str) -> bool:
    """A fact is grounded when it is a normalized substring of the context, or
    >=80% of its content tokens (len>=4) appear in the context. The token path
    tolerates the LLM trimming/reordering a quote without letting it invent."""
    nf, nc = _norm(fact), _norm(context)
    if not nf:
        return False
    if nf in nc:
        return True
    toks = [w.strip(".,;:!?\"'()") for w in nf.split()]
    toks = [w for w in toks if len(w) >= 4]
    if len(toks) < 3:
        return False
    ctx_words = set(nc.replace(",", " ").replace(".", " ").split())
    hit = sum(1 for t in toks if t in ctx_words)
    return hit / len(toks) >= 0.8


def build_outline_from_raw(raw: str, ctx: dict, mode_key: str) -> dict:
    """Parse + validate the LLM outline. Raises ValueError with a specific,
    feed-back-able message on every contract violation."""
    data = extract_json(raw)
    if data is None:
        raise ValueError("outline: unparseable JSON")
    chapters = data.get("chapters") or []
    if len(chapters) not in _ROLE_SEQUENCES:
        raise ValueError(f"outline: {len(chapters)} chapters (need 4 or 5)")
    expected_roles = _ROLE_SEQUENCES[len(chapters)]

    context = str(ctx.get("plot_summary") or "")
    known_ids = {int(a.get("object_id") or 0) for a in ctx.get("artworks") or []}
    seen_facts: dict[str, int] = {}
    total_words = 0
    used_ids: set[int] = set()

    for i, (ch, want_role) in enumerate(zip(chapters, expected_roles), start=1):
        if not isinstance(ch, dict):
            raise ValueError(f"outline: chapter {i} is not a JSON object")
        role = str(ch.get("role") or "")
        if role != want_role:
            raise ValueError(
                f"outline: chapter {i} role {role!r} — expected {want_role!r} "
                f"(order: {', '.join(expected_roles)})")
        facts = [str(f) for f in (ch.get("facts") or []) if str(f).strip()]
        if not 2 <= len(facts) <= 8:
            raise ValueError(f"outline: chapter {i} has {len(facts)} facts (need 2-8)")
        for f in facts:
            if not fact_is_grounded(f, context):
                raise ValueError(f"outline: chapter {i} fact not grounded: {f[:70]!r}")
            key = _norm(f)
            if key in seen_facts:
                raise ValueError(
                    f"outline: fact assigned twice (chapters {seen_facts[key]} "
                    f"and {i}): {f[:70]!r}")
            seen_facts[key] = i
        try:
            tw = int(ch.get("target_words") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"outline: chapter {i} non-integer target_words")
        if not ART_LF_CHAPTER_WORDS_MIN <= tw <= ART_LF_CHAPTER_WORDS_MAX:
            raise ValueError(
                f"outline: chapter {i} target_words {tw} outside "
                f"[{ART_LF_CHAPTER_WORDS_MIN}, {ART_LF_CHAPTER_WORDS_MAX}]")
        total_words += tw
        ids = [int(x) for x in (ch.get("artwork_ids") or []) if str(x).strip()]
        bad = [x for x in ids if known_ids and x not in known_ids]
        if bad:
            raise ValueError(f"outline: chapter {i} unknown artwork_ids {bad}")
        used_ids.update(ids)
        ch["chapter_id"] = i
        ch["facts"] = facts
        ch["target_words"] = tw
        ch["artwork_ids"] = ids or sorted(known_ids)

    if not ART_LF_TARGET_WORDS_MIN <= total_words <= ART_LF_TARGET_WORDS_MAX:
        raise ValueError(
            f"outline: total target_words {total_words} outside "
            f"[{ART_LF_TARGET_WORDS_MIN}, {ART_LF_TARGET_WORDS_MAX}]")

    through = str(data.get("through_line") or "").strip()
    if mode_key == "artist_journey":
        if not through.endswith("?"):
            raise ValueError("outline: artist_journey through_line must be a question")
        missing = known_ids - used_ids
        if missing:
            raise ValueError(f"outline: artwork(s) never used: {sorted(missing)}")

    return {"mode": mode_key, "through_line": through, "chapters": chapters}


def write_outline(project_name: str, mode_key: str | None = None, *,
                  force: bool = False, log=print) -> dict:
    root = get_art_project_path(project_name)
    out_path = root / "outline.json"
    if out_path.exists() and not force:
        log("[outline] outline.json exists — reusing (force to regenerate)")
        return json.loads(out_path.read_text())
    ctx = json.loads((root / "art_context.json").read_text())
    mode_key = mode_key or ctx.get("mode") or "painting_story"
    if mode_key not in ART_LF_MODES:
        raise KeyError(f"unknown long-form mode: {mode_key} (use one of {ART_LF_MODES})")

    system = _OUTLINE_SYSTEM.format(
        role_guide=_ROLE_GUIDE,
        cw_min=ART_LF_CHAPTER_WORDS_MIN, cw_max=ART_LF_CHAPTER_WORDS_MAX,
        w_min=ART_LF_TARGET_WORDS_MIN, w_max=ART_LF_TARGET_WORDS_MAX)
    artworks = ", ".join(
        f"{a.get('object_id')}: {a.get('title')}" for a in ctx.get("artworks") or [])
    first_id = (ctx.get("artworks") or [{}])[0].get("object_id") or 0
    user = _USER_TEMPLATE.format(mode=mode_key, artworks=artworks,
                                 facts=cap_facts(ctx["plot_summary"]),
                                 first_id=first_id)

    last_err: Exception | None = None
    for attempt in (1, 2, 3):
        raw, model_used = call_with_chain(
            system=system, user=user, models=CREATIVE_LLM_MODELS,
            max_tokens=3500, progress=log, label=f"art-outline#{attempt}",
            validator=lambda c: extract_json(c) is not None)
        try:
            outline = build_outline_from_raw(raw, ctx, mode_key)
            outline["llm_model"] = model_used
            out_path.write_text(json.dumps(outline, indent=2, ensure_ascii=False))
            log(f"[outline] ✓ {len(outline['chapters'])} chapters via {model_used} "
                f"— through-line: {outline['through_line']!r}")
            return outline
        except ValueError as exc:
            last_err = exc
            log(f"[outline] attempt {attempt} rejected: {exc} — retrying")
            user += f"\n\nYOUR PREVIOUS ATTEMPT FAILED VALIDATION: {exc}. Fix exactly that."
    raise ValueError(f"outline failed after 3 attempts: {last_err}")
