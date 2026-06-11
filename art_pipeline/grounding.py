"""A4a: multi-source grounding → art_context.json (mirrors comic_context.json
keys: title/publisher/plot_summary/wiki_url/plot_source/summary so any helper
expecting the comic shape keeps working).

Sources, in order: Met metadata (always) → Wikipedia article on the artwork +
on the artist → SDK web-research fallback below ART_GROUNDING_MIN_CHARS.
No source, no fact (same anti-fabrication contract as gather_plot_sdk)."""
import json
import re
import urllib.parse
import urllib.request

from .config import (
    ART_GROUNDING_MIN_CHARS, ART_SDK_MIN_STORY_CHARS, MET_USER_AGENT,
    get_art_project_path,
)
from .sources import met

_WIKI_API = "https://en.wikipedia.org/w/api.php"


def fetch_wikipedia_extract(title: str, *, log=print) -> dict | None:
    """Plain-text extract of an enwiki article (redirect-following). None on miss."""
    q = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": "1",
        "redirects": "1", "format": "json", "titles": title,
    })
    req = urllib.request.Request(f"{_WIKI_API}?{q}",
                                 headers={"User-Agent": MET_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        pages = (data.get("query") or {}).get("pages") or {}
        for pid, page in pages.items():
            if pid == "-1":
                continue
            text = (page.get("extract") or "").strip()
            if len(text) >= 200:
                slug = urllib.parse.quote((page.get("title") or title).replace(" ", "_"))
                return {"text": text, "url": f"https://en.wikipedia.org/wiki/{slug}"}
    except Exception as exc:
        log(f"[ground]   wikipedia error for {title!r}: {type(exc).__name__}: {exc}")
    return None


def merge_grounding(met_meta: dict, wiki_art: dict | None, wiki_artist: dict | None) -> tuple[str, str]:
    """Concat Met facts + artwork article + artist article. Returns (text, primary_url)."""
    c = met.parse_candidate(met_meta)
    parts = [
        f"{c['title']} ({c['year']}) by {c['artist']}. {c['medium']}. "
        f"Department: {c['department']}. {c['credit_line']}.",
    ]
    if wiki_art:
        parts.append(f"=== About the artwork ===\n{wiki_art['text']}")
    if wiki_artist:
        parts.append(f"=== About the artist ===\n{wiki_artist['text']}")
    url = (wiki_art or {}).get("url") or (wiki_artist or {}).get("url") or c["object_url"]
    return "\n\n".join(parts), url


def needs_sdk_fallback(text: str) -> bool:
    return len(text or "") < ART_GROUNDING_MIN_CHARS


def build_summary_block(metas: list[dict]) -> dict:
    """summary.characters mirrors comic_context.summary so Stage-3-style helpers
    that read characters keep working: each artist becomes a 'character'."""
    chars, seen = [], set()
    for m in metas:
        a = (m.get("artistDisplayName") or "").strip()
        if a and a not in seen:
            seen.add(a)
            chars.append({"name": a, "role": "artist",
                          "intro_line_hint": f"the artist {a}"})
    return {"characters": chars, "setting": "", "objects": []}


def gather_art_story_sdk(title: str, artist: str, *, log=print) -> dict | None:
    """SDK web-research fallback (same contract as stages/stage_1 gather_plot_sdk:
    real source_url + min length or None — never invents)."""
    try:
        from stages._claude_sdk import sdk_available, sdk_complete_web
    except Exception:
        return None
    if not sdk_available():
        log("[ground] SDK unavailable — skipping web research")
        return None
    system = (
        "You are an art-history research agent. Use WebSearch and WebFetch to find "
        "and read reliable sources about one artwork, then summarize its STORY: who "
        "made it, when and why, what it depicts, its symbolism, and any documented "
        "history (commission, scandal, sale, theft, reception). WRITE IN ENGLISH. "
        "Facts only — no speculation, no opinions. Read at least TWO real sources. "
        "If you cannot find reliable sources, return empty strings. "
        "Output STRICT JSON and nothing else."
    )
    user = (
        f"Research the artwork: {title} by {artist}.\n"
        'Return STRICT JSON only: {"story_summary":"<the documented story IN ENGLISH>",'
        '"source_url":"<main real URL>","confidence":"high|medium|low"}'
    )
    log(f"[ground] SDK web-researching {title!r}…")
    raw = sdk_complete_web(system, user, log=log)
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    story = (data.get("story_summary") or "").strip()
    src = (data.get("source_url") or "").strip()
    if not src or len(story) < ART_SDK_MIN_STORY_CHARS:
        log(f"[ground] SDK rejected (source={bool(src)}, len={len(story)}) — no fabrication")
        return None
    return {"story_summary": story, "source_url": src}


def build_art_context(project_name: str, *, log=print) -> dict:
    root = get_art_project_path(project_name)
    selection = json.loads((root / "selection.json").read_text())
    metas = [json.loads((root / f"met_meta_{oid}.json").read_text())
             for oid in selection["object_ids"]]

    blocks, primary_url, sources = [], "", []
    for m in metas:
        title = m.get("title") or ""
        artist = m.get("artistDisplayName") or ""
        wiki_art = fetch_wikipedia_extract(title, log=log)
        wiki_artist = fetch_wikipedia_extract(artist, log=log) if artist else None
        text, url = merge_grounding(m, wiki_art, wiki_artist)
        log(f"[ground] {title!r}: {len(text)} chars "
            f"(wiki_art={'hit' if wiki_art else 'miss'}, wiki_artist={'hit' if wiki_artist else 'miss'})")
        blocks.append(text)
        sources.append(url)
        if not primary_url:
            primary_url = url

    combined = "\n\n────\n\n".join(blocks)
    plot_source = "met+wikipedia"
    if needs_sdk_fallback(combined):
        log(f"[ground] grounding {len(combined)} chars < {ART_GROUNDING_MIN_CHARS} — trying SDK fallback")
        first = metas[0]
        sdk = gather_art_story_sdk(first.get("title") or "",
                                   first.get("artistDisplayName") or "", log=log)
        if sdk:
            combined += "\n\n=== Web research ===\n" + sdk["story_summary"]
            sources.append(sdk["source_url"])
            plot_source = "met+wikipedia+sdk-web"
    if needs_sdk_fallback(combined):
        raise ValueError(
            f"Grounding too thin ({len(combined)} chars < {ART_GROUNDING_MIN_CHARS}): "
            "not enough verified story to narrate (spec §4-A4 gate). Pick a "
            "story-richer artwork.")

    titles = [m.get("title") or "" for m in metas]
    ctx = {
        "title": titles[0] if len(titles) == 1 else (selection.get("theme") or titles[0]),
        "issue": "",
        "publisher": "The Metropolitan Museum of Art",
        "plot_summary": combined,
        "wiki_url": primary_url,
        "plot_source": plot_source,
        "summary": build_summary_block(metas),
        "artworks": [met.parse_candidate(m) for m in metas],
        "sources": sources,
        "mode": selection.get("mode", "painting_deep_dive"),
    }
    (root / "art_context.json").write_text(json.dumps(ctx, indent=2, ensure_ascii=False))
    log(f"[ground] art_context.json written ({len(combined)} grounded chars, {plot_source})")
    return ctx
