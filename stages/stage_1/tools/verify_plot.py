"""Stage 1 STORY-FIRST multi-source plot researcher + verifier.

This is the single SDK web-research step that runs inside `enrich_with_summary`,
so it covers BOTH entry paths: the interactive Stage-1 agent (via cli) and the
headless url_mode path. It takes whatever DRAFT plot exists (Fandom wiki, or the
gather_plot_sdk fallback) and, cross-checking it against INDEPENDENT sources,
rewrites the story so every claim is source-grounded — no made-up story, correct
issue/year, correct ending/twist.

STORY-FIRST rationale (Master 2026-07-16): the writer must be fed the CHANNEL of
the *story* (wiki + reviews), not panel-description language, so narration tells
the story instead of narrating the art ("Blackheart is dragged into the light" =
a panel description; the real beat = Penance Stare + the vortex). So besides the
verified plot this step also gathers what reviewers say the story MEANS and which
moments they call out — additive fields the story-first writer rewiring will read.

Additive fields written to ctx (never removes the existing ones):
  - plot_summary  : verified CAUSAL summary in natural storytelling voice
  - story_meaning : 2-4 sentences on what the story is about / why it lands
  - notable_moments : short list of story beats reviews/wiki emphasise
  - story_sources : [{url, site, type: wiki|review|discussion, summary}]
  - verification  : {confidence, discrepancies, sources_used}  (unchanged shape)

Uses `sdk_complete_web` (the agent does its own WebSearch/WebFetch with enough
turns) — NOT the plain `call_with_chain`/`sdk_complete` path, whose max_turns=2 +
no-tools setup makes a "cross-check the sources" instruction fail with
"Reached maximum number of turns". Degrades gracefully (keeps the draft, marks
'unverified') if the SDK is unavailable or returns nothing. Never raises.
"""
import json
import re
from typing import Callable


_VERIFY_SYSTEM = """You are a comic-book STORY researcher and fact-checker WITH WEB ACCESS
(WebSearch, WebFetch). You receive a comic's identity (title, year, issue) and a DRAFT plot
summary that may be wrong, incomplete, from the WRONG issue/volume, or written in
panel-description language.

GOAL: gather the STORY from multiple independent sources and return a verified, story-first
summary plus what the story MEANS — this feeds a narrator who has NEVER read the comic, so
it must read as a STORY, never as a description of the artwork.

RESEARCH — open and reconcile SEVERAL real sources for THIS exact issue (title + year + issue
must match). Aim for a mix:
- ONE wiki/plot synopsis: the Marvel/DC Fandom database (marvel.fandom.com, dc.fandom.com),
  Wikipedia, Comic Vine, uncannyxmen.net.
- TWO to FOUR independent REVIEWS that actually discuss the story's content: CBR, AIPT
  (aiptcomics.com), comicbookroundup.com (aggregates quotes from many reviewers),
  League of Comic Geeks (leagueofcomicgeeks.com), comic-watch.com, IGN, ScreenRant, or a
  substantive review blog. Prefer sources that TELL what happens and say what it means.
- Discussion/recap snippets returned by search are usable for what fans emphasise (Reddit is
  fine ONLY when it appears in a search snippet — do not rely on fetching it directly).
When you use REVIEWS, take the plot EVENTS and the reviewer's read of MEANING/THEME, but DROP
pure verdict words ("great art", "anticlimactic", "worth it", "slow", "10/10").

WRITE THE PLOT (verified_plot):
- CAUSAL and natural, in a storytelling voice — each event leads to the next (setup ->
  complication -> climax -> ending). Prefer the plain language reviewers/wiki use.
- FORBIDDEN: panel/art-description language. Never write "in this panel", "the art shows",
  "we see", "the page depicts", "is shown". Describe what HAPPENS and WHY, not what a drawing
  looks like. (Bad: "Blackheart is dragged into the light." Good: "The Penance Stare forces
  Blackheart to relive every sin, and the collapsing vortex ends his reign.")
- Use ONLY facts your sources support; if the draft asserts something no source backs, DROP
  it. NEVER invent events. Pay special attention to the ENDING / TWIST — drafts often misread
  it; confirm the real final fate against more than one source.
- ~600-1400 chars.

Then also produce:
- story_meaning: 2-4 sentences on what the story is ABOUT (its theme) and WHY the key moment
  lands emotionally, grounded in what the reviews/wiki actually say. No spoiler-protection.
- notable_moments: 3-8 SHORT beats the sources single out as the most memorable, in plain
  STORY language (not art language). Add a page number ONLY if a source states one.
- story_sources: one entry per real source you actually read, {"url","site","type","summary"}
  where type is exactly "wiki", "review", or "discussion" and summary is one line on what that
  source contributed. REAL urls only — never invent a source.

If you cannot find independent sources for the target issue, keep only what is certain, set
confidence "low", and leave the story_meaning / notable_moments / story_sources you could not
ground as empty — do NOT fabricate to fill the schema.

Your FINAL message must be ONLY this JSON (no prose, no markdown fences):
{"verified_plot": "<source-grounded causal summary, storytelling voice, no art language>",
 "story_meaning": "<2-4 sentences: theme + why the key moment lands>",
 "notable_moments": ["<short story beat>", ...],
 "story_sources": [{"url": "<url>", "site": "<domain/name>", "type": "wiki|review|discussion", "summary": "<one line>"}, ...],
 "confidence": "high|medium|low",
 "discrepancies": ["<each thing the draft got wrong or that is unverifiable, short>"],
 "sources_used": ["<url>", ...]}"""


def _extract_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _coerce_moments(items) -> list[str]:
    """Normalise notable_moments to a list of plain strings (accept dicts with a page)."""
    out: list[str] = []
    for m in items or []:
        if isinstance(m, dict):
            txt = str(m.get("moment") or m.get("text") or m.get("beat") or "").strip()
            page = str(m.get("page") or m.get("page_ref") or "").strip()
            if txt:
                out.append(f"{txt} (p{page})" if page else txt)
        elif str(m).strip():
            out.append(str(m).strip())
    return out[:8]


def _coerce_sources(items) -> list[dict]:
    """Normalise story_sources to [{url, site, type, summary}] (accept bare url strings)."""
    out: list[dict] = []
    for s in items or []:
        if isinstance(s, dict):
            url = str(s.get("url") or "").strip()
            if not url:
                continue
            typ = str(s.get("type") or "").strip().lower()
            out.append({
                "url": url,
                "site": str(s.get("site") or "").strip(),
                "type": typ if typ in ("wiki", "review", "discussion") else "review",
                "summary": str(s.get("summary") or "").strip(),
            })
        elif str(s).strip().lower().startswith("http"):
            out.append({"url": str(s).strip(), "site": "", "type": "review", "summary": ""})
    return out[:8]


def verify_plot(ctx: dict, *, progress: Callable[[str], None] | None = None) -> dict:
    """Story-first multi-source research + cross-check of ctx['plot_summary'].

    Auto-fixes plot_summary and attaches the additive story fields (story_meaning,
    notable_moments, story_sources) plus ctx['verification'] = {confidence,
    discrepancies, sources_used}. Degrades gracefully (keeps the draft, marks
    'unverified') if the SDK is unavailable or returns nothing. Mutates and returns ctx."""
    log = progress or (lambda _m: None)
    plot = (ctx.get("plot_summary") or "").strip()
    title = (ctx.get("title") or "").strip()
    year = str(ctx.get("year") or "").strip()
    issue = str(ctx.get("issues") or ctx.get("issue") or "").strip()
    if not plot or not title:
        log("[stage1] verify: no plot/title — skipping")
        return ctx

    user = (
        f"COMIC: {title} ({year}) {issue}\n\n"
        f"DRAFT PLOT to verify and rewrite story-first:\n{plot}\n\n"
        "Research several independent sources (one wiki + 2-4 reviews) for THIS exact issue, "
        "confirm every event (especially the ending), capture what the story MEANS and the "
        "moments reviewers single out, then return the verified JSON."
    )

    try:
        from stages._claude_sdk import sdk_complete_web
        raw = sdk_complete_web(_VERIFY_SYSTEM, user, log=log)
    except Exception as exc:
        ctx["verification"] = {"confidence": "unverified",
                               "discrepancies": [f"verifier error: {type(exc).__name__}"],
                               "sources_used": []}
        log(f"[stage1] verify: error ({type(exc).__name__}) → kept draft")
        return ctx

    parsed = _extract_json(raw or "")
    if not parsed or not str(parsed.get("verified_plot", "")).strip():
        ctx["verification"] = {"confidence": "unverified",
                               "discrepancies": ["verifier returned no usable plot"],
                               "sources_used": []}
        log("[stage1] verify: no usable result → kept draft, marked unverified")
        return ctx

    disc = [str(d) for d in (parsed.get("discrepancies") or [])][:10]
    sources = _coerce_sources(parsed.get("story_sources"))
    used = [str(s).strip() for s in (parsed.get("sources_used") or []) if str(s).strip()][:8]
    # Keep sources_used and story_sources consistent even when the model fills only one.
    if not used and sources:
        used = [s["url"] for s in sources]
    if not sources and used:
        sources = [{"url": u, "site": "", "type": "review", "summary": ""} for u in used]

    ctx["plot_summary"] = str(parsed["verified_plot"]).strip()   # auto-fix (story-first)
    meaning = str(parsed.get("story_meaning") or "").strip()
    if meaning:
        ctx["story_meaning"] = meaning
    moments = _coerce_moments(parsed.get("notable_moments"))
    if moments:
        ctx["notable_moments"] = moments
    if sources:
        ctx["story_sources"] = sources
    ctx["verification"] = {
        "confidence": (str(parsed.get("confidence", "")).strip() or "medium"),
        "discrepancies": disc,
        "sources_used": used,
    }
    if disc:
        log(f"[stage1] verify: auto-fixed plot — {len(disc)} discrepancy(ies), "
            f"{len(sources)} source(s), meaning={'y' if meaning else 'n'} "
            f"(confidence={ctx['verification']['confidence']})")
    else:
        log(f"[stage1] verify: plot confirmed — {len(sources)} source(s) "
            f"(confidence={ctx['verification']['confidence']})")
    return ctx
