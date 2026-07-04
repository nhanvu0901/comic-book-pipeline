"""Stage-1 self-ID hardening: catch a plot_summary fetched for the WRONG comic.

Real failure this guards (2026-07-04): Stage 1 resolved "Moon Knight (2021) #9
Stranger" but fetched the PLOT of "Strange (2022) #9" — same writer, same issue
number, near-identical title. Every identity field (title/series/year) was
right; only plot_summary was another comic's story. Nothing downstream could
catch it: the panels are honest, the plot text reads fine on its own, and
verify_plot() cross-checks the plot against the WEB, which happily "confirms"
the wrong comic's own web sources.

The one signal that separates the two comics is PROPER NOUNS: a wrong-comic
plot shares almost no character/place names with either (a) the user's own
identification prompt, or (b) what the panels themselves describe. Calibrated
on real projects: plot~pages proper-noun Jaccard overlap for CORRECT projects
sits at 0.375-0.545; for the wrong-plot case it is ~0.0 (>2.5x margin).
prompt~plot overlap for correct projects is 0.25-0.33.

Two hooks (wired in pipeline.py) use this:
  - Hook 0 (pre-check): flag suspect ctx before VLM extraction, so a wrong
    roster can't bias page_summary generation.
  - Hook 2 (repair): once real page_summaries exist, rebuild plot_summary FROM
    the panels themselves (no web, no wiki) whenever the plot looks wrong,
    missing, or disagrees with the pages — replacing the human hand-fix this
    incident originally required.
"""
import os
import re

# Sentence-initial / grammatical capitalized words that are NOT proper nouns —
# without this filter, "The", "When", "He" etc. would pollute both sets and
# artificially inflate overlap on any two English paragraphs.
_STOPWORDS = {
    "The", "A", "An", "When", "After", "Before", "While", "He", "She", "It", "They",
    "In", "On", "At", "As", "But", "And", "Or", "So", "If", "Then", "This", "That",
    "These", "Those", "His", "Her", "Its", "Their", "We", "You", "I",
    "Marvel", "Comics", "Issue", "Vol",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
}

_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")

# Correct projects measured 0.375-0.545 plot~pages overlap; the wrong-plot case
# measured ~0.0 — 0.15 sits well inside the >2.5x margin between them.
PLOT_PAGES_OVERLAP_FLOOR = 0.15
# Correct projects measured 0.25-0.33 prompt~plot overlap.
PROMPT_PLOT_OVERLAP_FLOOR = 0.10
# Below this many proper nouns in the page corpus, there isn't enough signal to
# judge agreement either way — e.g. a project with zero page_summaries (VLM not
# run yet) must read as "can't tell", never as "disagrees".
MIN_CORPUS_NOUNS = 8
# The user's identification prompt needs a few real names before "shares none
# with the plot" means anything; a one-line prompt with no names is not a signal.
MIN_PROMPT_NOUNS = 3


def _proper_nouns(text: str) -> set[str]:
    return {w for w in _PROPER_NOUN_RE.findall(text or "") if w not in _STOPWORDS}


def proper_noun_overlap(a: str, b: str) -> float:
    """Jaccard similarity of the proper-noun sets found in `a` and `b`. 0.0 when
    either text has NO proper nouns at all (nothing to compare, not "identical")."""
    set_a, set_b = _proper_nouns(a), _proper_nouns(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def proper_noun_count(text: str) -> int:
    return len(_proper_nouns(text))


def prompt_disagrees_with_plot(ctx: dict) -> bool:
    """Hook 0's decision: True when the user's own identification prompt and the
    fetched plot_summary share almost no names. url-mode projects carry no
    user_prompt (identity comes from a URL, not a free-text ask), so this is a
    no-op for them — there is nothing to cross-check against."""
    prompt = str(ctx.get("user_prompt", "") or "")
    plot = str(ctx.get("plot_summary", "") or "")
    if not prompt or not plot or proper_noun_count(prompt) < MIN_PROMPT_NOUNS:
        return False
    return proper_noun_overlap(prompt, plot) < PROMPT_PLOT_OVERLAP_FLOOR


def plot_agrees_with_pages(ctx: dict, story_pages: list[dict]) -> bool | None:
    """None when the page corpus is too thin to judge (e.g. VLM hasn't run yet);
    else True/False by the calibrated overlap floor."""
    corpus = "\n".join(
        s for s in (str(p.get("page_summary", "") or "").strip() for p in story_pages) if s
    )
    if proper_noun_count(corpus) < MIN_CORPUS_NOUNS:
        return None
    plot = str(ctx.get("plot_summary", "") or "")
    return proper_noun_overlap(plot, corpus) >= PLOT_PAGES_OVERLAP_FLOOR


# Kill switch, repo convention (see stage_2/pipeline.py DESC_VERIFY/COVERAGE_GUARD).
PLOT_REBUILD_FROM_PANELS = os.getenv("PLOT_REBUILD_FROM_PANELS", "1").strip().lower() not in (
    "0", "false", "no", "",
)

_REBUILD_SYSTEM = (
    "You are given per-page summaries of a comic issue in reading order. Write a "
    "600-1200 character plot_summary covering setup, escalation, climax, and ending, "
    "in plain prose. Name the characters exactly as the pages name them. Return ONLY "
    "the plot_summary text — no JSON, no markdown fences, no preamble."
)


def rebuild_plot_from_panels(ctx: dict, story_pages: list[dict], *, log=None) -> bool:
    """Replace ctx['plot_summary'] with one distilled from the comic's OWN panels
    (not the web, not a wiki) — the ground truth a wrong-comic mix-up cannot poison,
    since it is built from pages this exact project downloaded and extracted.

    Mutates ctx in place: plot_summary_wiki keeps the old (possibly wrong) text,
    plot_summary/plot_source/plot_status get overwritten. Then rebuilds ctx['summary']
    (the structured roster Stage 3 needs) via summarize_context() directly — NOT
    enrich_with_summary(), because that helper unconditionally runs verify_plot(),
    which cross-checks a plot against the WEB. A panel-sourced plot has no web source
    to check against, and running verify_plot on it would risk pulling the wrong
    comic's web plot right back in, undoing this fix.

    Never raises — returns False (and logs) on any SDK/rebuild failure so a Stage 2
    run keeps its previous plot_summary rather than losing it to an exception."""
    log = log or (lambda _m: None)
    if not PLOT_REBUILD_FROM_PANELS:
        log("[identity] PLOT_REBUILD_FROM_PANELS disabled — skipping rebuild")
        return False

    pages_sorted = sorted(story_pages, key=lambda p: int(p.get("page_number", 0) or 0))
    corpus = "\n".join(
        s for s in (str(p.get("page_summary", "") or "").strip() for p in pages_sorted) if s
    )
    if not corpus.strip():
        log("[identity] no page_summary text on any story page — cannot rebuild plot")
        return False

    try:
        from .._claude_sdk import sdk_complete
        text = sdk_complete(_REBUILD_SYSTEM, corpus, log=log)
    except Exception as exc:
        log(f"[identity] plot rebuild SDK call crashed: {type(exc).__name__}: {exc}")
        return False
    if not text or not text.strip():
        log("[identity] SDK plot rebuild returned nothing — keeping existing plot_summary")
        return False

    ctx["plot_summary_wiki"] = ctx.get("plot_summary", "")
    ctx["plot_summary"] = text.strip()
    ctx["plot_source"] = "panels"
    ctx["plot_status"] = "OK"
    ctx.pop("summary", None)

    try:
        from stages.stage_1.tools.summarize_context import summarize_context
        ctx["summary"] = summarize_context(ctx, progress=log)
    except Exception as exc:
        log(f"[identity] summary re-derivation after plot rebuild failed "
            f"({type(exc).__name__}: {exc}) — plot_summary is still fixed")

    log(f"[identity] plot_summary rebuilt from {len(pages_sorted)} panel page_summaries "
        f"({len(text)} chars)")
    return True


if __name__ == "__main__":
    # ponytail: tiny assert-based self-check, no network, no pytest — run directly
    # (`python -m stages.stage_2.identity_check`) to sanity-check the calibration.
    _wrong_plot = (
        "Doctor Strange battles Clea after a Harvestman ambush inside the Sanctum "
        "Sanctorum, while Wong warns him of a coming Empirikul threat."
    )
    _mk_pages_corpus = (
        "Marc Spector wakes inside the House of Shadows, haunted by Khonshu. "
        "Steven Grant argues with Marc about who controls their body. "
        "Layla El-Faouly arrives to help Moon Knight escape the asylum."
    )
    _right_plot = (
        "Marc Spector, also known as Moon Knight, is trapped inside the House of "
        "Shadows by Khonshu. Steven Grant and Layla El-Faouly help him break free "
        "and confront Khonshu's judgment."
    )
    wrong_overlap = proper_noun_overlap(_wrong_plot, _mk_pages_corpus)
    right_overlap = proper_noun_overlap(_right_plot, _mk_pages_corpus)
    assert wrong_overlap < 0.15, f"wrong-comic overlap too high: {wrong_overlap}"
    assert right_overlap > 0.30, f"same-comic overlap too low: {right_overlap}"
    print(f"OK: wrong-comic overlap={wrong_overlap:.3f}, same-comic overlap={right_overlap:.3f}")
