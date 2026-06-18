"""Stage 1 cross-source plot verifier.

Before the gathered plot becomes the comic_context that every downstream stage
trusts, a WEB-RESEARCH LLM agent cross-checks it against INDEPENDENT sources and
rewrites it so every claim is source-grounded — no made-up story, correct
issue/year, correct ending/twist. Runs inside `enrich_with_summary`, so BOTH the
interactive Stage-1 agent (via cli) and the headless url_mode path are covered.
Auto-fixes the plot_summary and records a `verification` block for auditing.

Uses `sdk_complete_web` (the agent does its own WebSearch/WebFetch with enough
turns) — NOT the plain `call_with_chain`/`sdk_complete` path, whose max_turns=2 +
no-tools setup makes a "cross-check the sources" instruction fail with
"Reached maximum number of turns".
"""
import json
import re
from typing import Callable


_VERIFY_SYSTEM = """You are a comic-book plot fact-checker WITH WEB ACCESS (WebSearch,
WebFetch). You receive a comic's identity (title, year, issue) and a DRAFT plot summary
that may be wrong, incomplete, or from the WRONG issue or volume.

RESEARCH independent sources on the web for THIS exact issue (prefer the official
publisher, then CBR / IGN / major review sites), then produce a VERIFIED plot summary
that is 100% grounded in what you actually found. Rules:
- Use ONLY facts your sources support. If the draft asserts something no source backs,
  DROP it. NEVER invent events.
- Confirm the RIGHT issue: title + year + issue must match the sources. If the draft
  looks like a DIFFERENT year/volume, correct it; if you cannot find the target issue,
  keep only what is certain and set confidence "low".
- Pay special attention to the ENDING / TWIST — draft summaries often misread it.
- Keep it factual, 600-1200 chars: setup -> complication -> climax -> ending.

Your FINAL message must be ONLY this JSON (no prose, no markdown fences):
{"verified_plot": "<source-grounded summary>",
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


def verify_plot(ctx: dict, *, progress: Callable[[str], None] | None = None) -> dict:
    """Cross-source fact-check ctx['plot_summary'] via a web-research LLM agent;
    auto-fix it and attach ctx['verification'] = {confidence, discrepancies,
    sources_used}. Degrades gracefully (keeps the draft, marks 'unverified') if the
    SDK is unavailable or returns nothing. Mutates and returns ctx."""
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
        f"DRAFT PLOT to verify:\n{plot}\n\n"
        "Research the web to confirm this is the correct issue and that every event "
        "(especially the ending) is accurate, then return the verified JSON."
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
    ctx["plot_summary"] = str(parsed["verified_plot"]).strip()   # auto-fix
    ctx["verification"] = {
        "confidence": (str(parsed.get("confidence", "")).strip() or "medium"),
        "discrepancies": disc,
        "sources_used": [str(s) for s in (parsed.get("sources_used") or [])][:6],
    }
    if disc:
        log(f"[stage1] verify: auto-fixed plot — {len(disc)} discrepancy(ies) "
            f"(confidence={ctx['verification']['confidence']})")
    else:
        log(f"[stage1] verify: plot confirmed "
            f"(confidence={ctx['verification']['confidence']})")
    return ctx
