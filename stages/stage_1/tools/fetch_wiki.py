"""
Fetch Wiki tool — retrieves verified plot text from fandom wikis and comic review sites.

Why this matters:
  LLMs hallucinate plot details from training data. This tool fetches the actual
  verified plot synopsis so the script is grounded in facts.

How it works:
  1. If wiki_url is provided, try Tavily extract on it directly.
  2. Search fandom wikis with include_raw_content=True.
  3. If wikis return no raw_content (403s are common), broaden to review sites
     (CBR, ScreenRant, comic-watch, etc.) which DO return raw content.
  4. Combine the best content from multiple sources.
  5. Extract the plot/synopsis section if possible.

No module-level state — reads TAVILY_API_KEY from env like web_search.py.
"""
import os
import re
from ..ui import Colors

# ─── Schema ─────────────────────────────────────────────────────────────────

FETCH_WIKI_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_wiki",
        "description": (
            "Fetch the verified plot synopsis for a comic book story from fandom wikis "
            "or review sites. Returns the actual plot text so you can base your script "
            "on FACTS, not memory. Use this after identifying the comic to get the real "
            "story details. Searches: marvel.fandom.com, dc.fandom.com, comicvine.gamespot.com, "
            "plus review sites (CBR, ScreenRant, etc.) as fallback."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query to find the plot synopsis. Be specific: include the comic title, "
                        "issue numbers, and character names. E.g., 'What If Dark Venom 2023 #1 plot synopsis'"
                    ),
                },
                "wiki_url": {
                    "type": "string",
                    "description": (
                        "Optional: direct URL to the wiki page. If provided, tries extraction first."
                    ),
                },
                "publisher": {
                    "type": "string",
                    "description": (
                        "Optional hint: 'marvel', 'dc', 'image', or 'other'. "
                        "Helps prioritize the right wiki domain."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

# ─── Domains ─────────────────────────────────────────────────────────────────

_WIKI_DOMAINS = [
    "marvel.fandom.com",
    "dc.fandom.com",
    "imagecomics.fandom.com",
    "comicvine.gamespot.com",
]

_REVIEW_DOMAINS = [
    "cbr.com",
    "screenrant.com",
    "comic-watch.com",
    "bleedingcool.com",
    "comicbookresources.com",
    "ign.com",
    "gamesradar.com",
]

_PUBLISHER_WIKI = {
    "marvel": ["marvel.fandom.com", "comicvine.gamespot.com"],
    "dc": ["dc.fandom.com", "comicvine.gamespot.com"],
    "image": ["imagecomics.fandom.com", "comicvine.gamespot.com"],
}

# ─── Plot section extraction ────────────────────────────────────────────────

_PLOT_HEADING_RE = re.compile(
    r"(?:^|\n)(?:#{1,4}\s*|={2,}\s*)"
    r"(Plot|Synopsis|Story|Plot Synopsis|Storyline|Summary|Recap|Review|Solicit Synopsis)"
    r"(?:\s*={2,}|\s*#*)?\s*\n",
    re.IGNORECASE,
)

_NEXT_HEADING_RE = re.compile(
    r"\n(?:#{1,4}\s+|={2,}\s*)\S",
)

# Also match review article body sections that contain plot details
_REVIEW_BODY_RE = re.compile(
    r"(?:^|\n)(?:#{1,4}\s*)"
    r"(.+(?:Destroy|Break|Becomes|Transform|Bond|Fight|Kill|Dies|Dark|Brutal|New Venom|Symbiote).+)"
    r"\s*\n",
    re.IGNORECASE,
)

_MAX_PLOT_LENGTH = 5000


def _extract_plot_section(text: str) -> str | None:
    """Pull out the Plot/Synopsis section from wiki/review text."""
    # Try ALL standard wiki headings and pick the longest substantial match
    best_section = None
    for match in _PLOT_HEADING_RE.finditer(text):
        start = match.end()
        next_heading = _NEXT_HEADING_RE.search(text, start)
        end = next_heading.start() if next_heading else len(text)
        section = text[start:end].strip()
        if len(section) >= 200 and (best_section is None or len(section) > len(best_section)):
            best_section = section

    if best_section:
        return best_section[:_MAX_PLOT_LENGTH]

    # Try review article content headings that describe plot events
    match = _REVIEW_BODY_RE.search(text)
    if match:
        start = match.end()
        remaining = text[start:]
        for stop_pattern in [r"\n#{1,3}\s+Related", r"\n#{1,3}\s+Next:", r"\n#{1,3}\s+Share",
                             r"\n#{1,3}\s+More:", r"\nRelated Posts"]:
            stop_match = re.search(stop_pattern, remaining, re.IGNORECASE)
            if stop_match:
                remaining = remaining[:stop_match.start()]
                break
        plot = remaining.strip()
        if len(plot) >= 200:
            return plot[:_MAX_PLOT_LENGTH]

    return None


def _score_result(result: dict, query_lower: str) -> int:
    """Score a search result by relevance for plot content."""
    score = 0
    raw = result.get("raw_content", "") or ""
    snippet = result.get("content", "") or ""
    url = result.get("url", "").lower()
    title = result.get("title", "").lower()

    # Has raw content at all
    if len(raw) > 500:
        score += 50
    if len(raw) > 2000:
        score += 30

    # Plot-related keywords in content (more = more likely to have the actual plot)
    content_lower = (raw + snippet).lower()
    plot_keywords = ["symbiote", "bonds", "transforms", "fights", "kills", "defeats",
                     "plot", "synopsis", "review", "recap", "spoiler", "summary",
                     "dies", "destroys", "betrayal", "consequences"]
    for kw in plot_keywords:
        if kw in content_lower:
            score += 5
    # Extra bonus for "spoiler" or "recap" — these articles tell what happens
    if "spoiler" in content_lower or "recap" in content_lower:
        score += 20

    # Wiki domains get priority
    for d in _WIKI_DOMAINS:
        if d in url:
            score += 20
            break

    # Review sites with actual content
    for d in _REVIEW_DOMAINS:
        if d in url and len(raw) > 1000:
            score += 15
            break

    # Title relevance
    for word in query_lower.split():
        if word in title:
            score += 3

    return score


# ─── Implementation ─────────────────────────────────────────────────────────

def fetch_wiki(query: str, wiki_url: str = "", publisher: str = "") -> dict:
    """
    Fetch verified plot text from wiki or review sites.

    Strategy:
      1. Try direct URL extraction if wiki_url provided
      2. Search wiki domains with include_raw_content=True
      3. Broaden to review sites if wikis have no raw content
      4. Combine best sources

    Returns:
        {wiki_url, title, plot_text, plot_length, source, sources_checked}
    """
    from tavily import TavilyClient

    print(f"  {Colors.DIM}📚 Fetching verified plot for: {query}{Colors.END}")

    try:
        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    except Exception as e:
        print(f"  {Colors.DIM}   Error: {e}{Colors.END}")
        return {"error": str(e), "plot_text": "", "wiki_url": ""}

    all_results = []
    sources_checked = []

    # ── Step 1: Try direct URL if provided ───────────────────────────────
    if wiki_url and wiki_url.strip():
        try:
            print(f"  {Colors.DIM}   Trying direct extract: {wiki_url}{Colors.END}")
            extract_resp = client.extract(urls=[wiki_url.strip()])
            for res in extract_resp.get("results", []):
                raw = res.get("raw_content", "") or res.get("text", "")
                if raw and len(raw) > 100:
                    print(f"  {Colors.DIM}   ✓ Direct extract: {len(raw)} chars{Colors.END}")
                    all_results.append({
                        "url": wiki_url,
                        "title": res.get("title", query),
                        "raw_content": raw,
                        "content": raw[:500],
                    })
            sources_checked.append(f"extract:{wiki_url}")
        except Exception as e:
            print(f"  {Colors.DIM}   Direct extract failed: {e}{Colors.END}")
            sources_checked.append(f"extract_failed:{wiki_url}")

    # ── Step 2: Search wiki domains ──────────────────────────────────────
    wiki_domains = _PUBLISHER_WIKI.get(publisher.lower(), _WIKI_DOMAINS) if publisher else _WIKI_DOMAINS
    print(f"  {Colors.DIM}   Searching wikis: {wiki_domains}{Colors.END}")

    try:
        wiki_resp = client.search(
            query=f"{query} plot synopsis",
            max_results=5,
            include_domains=wiki_domains,
            include_raw_content=True,
        )
        wiki_results = wiki_resp.get("results", [])
        all_results.extend(wiki_results)
        sources_checked.append(f"wiki_search:{len(wiki_results)} results")

        has_wiki_content = any(
            len(r.get("raw_content", "") or "") > 500 for r in wiki_results
        )
        if not has_wiki_content:
            print(f"  {Colors.DIM}   ⚠ Wikis returned no raw content (likely 403){Colors.END}")
    except Exception as e:
        print(f"  {Colors.DIM}   Wiki search error: {e}{Colors.END}")
        sources_checked.append(f"wiki_search_error:{e}")

    # ── Step 3: Search review sites (always, for broader coverage) ───────
    print(f"  {Colors.DIM}   Searching review sites for plot details...{Colors.END}")
    try:
        review_resp = client.search(
            query=f"{query} plot summary review recap",
            max_results=5,
            include_domains=_REVIEW_DOMAINS,
            include_raw_content=True,
        )
        review_results = review_resp.get("results", [])
        all_results.extend(review_results)
        sources_checked.append(f"review_search:{len(review_results)} results")

        content_count = sum(1 for r in review_results if len(r.get("raw_content", "") or "") > 500)
        print(f"  {Colors.DIM}   Found {content_count} review articles with content{Colors.END}")
    except Exception as e:
        print(f"  {Colors.DIM}   Review search error: {e}{Colors.END}")
        sources_checked.append(f"review_search_error:{e}")

    # ── Step 4: Rank and pick best content ───────────────────────────────
    if not all_results:
        return {
            "error": "No results found from any source",
            "plot_text": "",
            "wiki_url": "",
            "query": query,
            "sources_checked": sources_checked,
        }

    query_lower = query.lower()
    scored = [(r, _score_result(r, query_lower)) for r in all_results]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Try to extract plot section from best results
    best_plot = None
    best_url = ""
    best_title = ""
    best_source = ""

    for result, score in scored:
        raw = result.get("raw_content", "") or ""
        if not raw or len(raw) < 200:
            continue

        # Try structured plot extraction first
        plot = _extract_plot_section(raw)
        if plot and len(plot) > 100:
            best_plot = plot
            best_url = result.get("url", "")
            best_title = result.get("title", "")
            best_source = "plot_section"
            print(f"  {Colors.DIM}   ✓ Found plot section from {best_url} ({len(plot)} chars){Colors.END}")
            break

    # If no structured plot section, use the longest raw content
    if not best_plot:
        for result, score in scored:
            raw = result.get("raw_content", "") or ""
            if len(raw) > 500:
                best_plot = raw[:_MAX_PLOT_LENGTH]
                best_url = result.get("url", "")
                best_title = result.get("title", "")
                best_source = "full_content"
                print(f"  {Colors.DIM}   Using full content from {best_url} ({len(best_plot)} chars){Colors.END}")
                break

    # Last resort: combine all snippets
    if not best_plot:
        snippets = []
        for result, score in scored:
            snippet = result.get("content", "")
            if snippet and len(snippet) > 50:
                snippets.append(snippet)
                if not best_url:
                    best_url = result.get("url", "")
                    best_title = result.get("title", "")
        if snippets:
            best_plot = "\n\n".join(snippets)[:_MAX_PLOT_LENGTH]
            best_source = "combined_snippets"
            print(f"  {Colors.DIM}   Assembled from {len(snippets)} search snippets ({len(best_plot)} chars){Colors.END}")

    if not best_plot:
        return {
            "error": "Found URLs but could not extract plot text",
            "plot_text": "",
            "wiki_url": best_url,
            "title": best_title or query,
            "sources_checked": sources_checked,
        }

    return {
        "wiki_url": best_url,
        "title": best_title or query,
        "plot_text": best_plot,
        "plot_length": len(best_plot),
        "source": best_source,
        "sources_checked": sources_checked,
    }
