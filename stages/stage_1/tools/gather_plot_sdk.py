"""Last-resort plot grounding.

When `fetch_fandom` (MediaWiki) misses or returns only a stub, spawn a
web-enabled Claude SDK agent to research the comic issue's plot from a real
source and return a grounded summary + source URL.

Anti-fabrication: a real `source_url` AND a summary >= _MIN_PLOT_CHARS are
required, otherwise this returns None — no source, no plot (never invents).
Never raises; the caller keeps its weaker-context behaviour on None.
Run standalone — the SDK throttles when another agent runs concurrently.
"""
import json
import re

_MIN_PLOT_CHARS = 200

_SYSTEM = """You are a comic-plot research agent. Given a comic issue, use the \
WebSearch and WebFetch tools to find and read reliable descriptions of that exact \
issue's story, then write a faithful summary of what HAPPENS, start to finish.

Rules:
- WRITE plot_summary IN ENGLISH — the comic and the downstream narration are English. \
Ignore any other language preference; the summary MUST be English.
- PREFER neutral plot/recap sources that describe EVENTS: marvel.com / dc.com articles, \
Comic Vine (comicvine.gamespot.com), uncannyxmen.net, Wikipedia, dedicated recaps. Use \
opinion REVIEWS (comic-watch, CBR / ScreenRant op-eds, etc.) ONLY if nothing else exists \
— and then extract ONLY the plot EVENTS, NEVER the reviewer's opinions (drop words like \
"anticlimactic", "great art", "worth it", "slow", "disappointing").
- Open and read at least TWO real sources with WebFetch and reconcile them, so no major \
beat is missed (key fights, twists, deaths, and the ENDING / final fate of each character).
- Describe what HAPPENS, not whether it is good. Cover the full arc: setup -> key events \
-> climax -> ending.
- Summarize THIS specific issue/arc only. Do NOT invent events. If you cannot find a \
reliable source describing the plot, return an empty plot_summary.
- YEAR/VOLUME MATCH (critical): many comics share a title across different years/volumes \
(e.g. "Thor Annual" exists in 1966, the 2000s, and 2023). When a year is given, use ONLY \
sources describing THAT exact year's issue. If the only sources you find are a same-named \
issue from a DIFFERENT year/volume, do NOT use them — return an empty plot_summary instead.
- Output STRICT JSON and nothing else."""


def _extract_json(text: str) -> dict | None:
    """Grab the first {...} object from the model text and parse it."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def gather_plot_sdk(
    title: str, issue: str = "", publisher: str = "", *, year: str = "", log=lambda _m: None
) -> dict | None:
    """Research the issue's plot via a web-enabled Claude SDK agent.

    Returns {"plot_summary", "source_url", "confidence"} when a real source is
    found and the summary is >= _MIN_PLOT_CHARS, else None. Never raises.
    """
    title = (title or "").strip()
    if not title:
        return None

    def emit(m):  # tee to terminal (print) AND the UI log panel (log callback)
        print(m)
        log(m)

    try:
        from stages._claude_sdk import sdk_complete_web, sdk_available
    except Exception:
        return None
    if not sdk_available():
        emit("[gather-plot-sdk] SDK unavailable — skipping web research")
        return None

    who = title
    if year:
        who = f"{who} ({year})"
    if issue:
        who = f"{who} {issue}"
    pub = f" (publisher: {publisher})" if publisher else ""
    user = (
        f"Research the plot of the comic: {who}{pub}.\n"
        + (f"This is the {year} issue — do NOT use a same-named issue from another year.\n" if year else "")
        + "Return STRICT JSON only, no prose around it:\n"
        '{"plot_summary":"<full start-to-finish plot IN ENGLISH — events only, no opinions>",'
        '"source_url":"<the main real URL you relied on>",'
        '"confidence":"high|medium|low"}\n'
        "If you cannot find a reliable source, set plot_summary and source_url to empty strings."
    )
    emit(f"[gather-plot-sdk] web-researching plot for {who!r} …")
    try:
        raw = sdk_complete_web(_SYSTEM, user, log=emit)
    except Exception as exc:  # defensive — sdk layer already swallows, but never raise
        emit(f"[gather-plot-sdk] SDK error: {exc}")
        return None
    if not raw:
        emit("[gather-plot-sdk] no SDK output — keeping weaker context")
        return None

    data = _extract_json(raw)
    if data is None:
        emit("[gather-plot-sdk] could not parse JSON — skipping")
        return None
    plot = (data.get("plot_summary") or "").strip()
    src = (data.get("source_url") or "").strip()
    if not src or len(plot) < _MIN_PLOT_CHARS:
        emit(f"[gather-plot-sdk] rejected (source={bool(src)}, plot_len={len(plot)}) — no fabrication")
        return None
    emit(f"[gather-plot-sdk] ✓ {len(plot)} chars from {src} (confidence={data.get('confidence', '?')})")
    return {"plot_summary": plot, "source_url": src, "confidence": data.get("confidence", "")}
