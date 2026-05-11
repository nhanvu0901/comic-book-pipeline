"""Direct MediaWiki Action API client for Fandom wikis (Stage 1 plot fetch)."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..ui import Colors

_USER_AGENT = "ComicVideoPipeline/1.0"
_TIMEOUT = 15
_MIN_PLOT_CHARS = 200
_PUBLISHER_HINTS = ("marvel", "dc", "image", "darkhorse", "idw", "valiant", "boom")


def _publisher_subdomain_map() -> dict[str, str]:
    """Build {publisher_hint: wiki_domain} from FANDOM_DOMAINS by substring."""
    from config import FANDOM_DOMAINS
    out: dict[str, str] = {}
    for domain in FANDOM_DOMAINS:
        d_lower = domain.lower()
        for hint in _PUBLISHER_HINTS:
            if hint in d_lower and hint not in out:
                out[hint] = domain
    return out


def _priority_order(publisher: str) -> list[str]:
    """Return the wiki priority list, putting the publisher's wiki first if known."""
    from config import FANDOM_DOMAINS
    base = list(FANDOM_DOMAINS)
    pub = (publisher or "").strip().lower()
    if not pub:
        return base
    pmap = _publisher_subdomain_map()
    primary = pmap.get(pub)
    if not primary or primary not in base:
        return base
    ordered = [primary] + [d for d in base if d != primary]
    return ordered


def _http_get_json(url: str) -> dict | None:
    """GET a URL with one retry and parse JSON."""
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = resp.read()
            return json.loads(data.decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if attempt == 0:
                time.sleep(2)
                continue
            return None
    return None


def _search_wiki(wiki: str, query: str) -> str | None:
    """Search a Fandom wiki for the best matching article title."""
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": 0,
        "format": "json",
        "srlimit": 3,
    })
    url = f"https://{wiki}/api.php?{params}"
    payload = _http_get_json(url)
    if not payload:
        return None
    hits = payload.get("query", {}).get("search", [])
    for hit in hits:
        title = hit.get("title", "").strip()
        if title:
            return title
    return None


def _parse_wikitext(wiki: str, title: str) -> str | None:
    """Fetch the raw wikitext for a Fandom page."""
    params = urllib.parse.urlencode({
        "action": "parse",
        "page": title,
        "format": "json",
        "prop": "wikitext",
    })
    url = f"https://{wiki}/api.php?{params}"
    payload = _http_get_json(url)
    if not payload:
        return None
    return payload.get("parse", {}).get("wikitext", {}).get("*")


def _extract_synopsis1(wikitext: str) -> str | None:
    """Pull the | Synopsis1 = ... block from a Fandom comic-issue infobox."""
    m = re.search(r'\|\s*Synopsis1\s*=\s*(.*?)(?=\n\|\s|\n\}\})', wikitext, re.DOTALL)
    if not m:
        return None
    return m.group(1).strip()


def _strip_wiki_links(text: str) -> str:
    """Convert [[link|display]] -> display and [[link]] -> link."""
    text = re.sub(r'\[\[([^\[\]\|]+)\|([^\[\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\[\]]+)\]\]', r'\1', text)
    return text


def _page_url(wiki: str, title: str) -> str:
    """Build a public Fandom URL for a page title."""
    return f"https://{wiki}/wiki/{urllib.parse.quote(title.replace(' ', '_'), safe='_:.,!()')}"


def fetch_fandom(query: str, publisher: str = "") -> dict:
    """Fetch the Synopsis1 plot section from the configured Fandom wiki chain via MediaWiki Action API."""
    order = _priority_order(publisher)
    sources_checked: list[str] = []

    for wiki in order:
        sources_checked.append(f"search:{wiki}")
        title = _search_wiki(wiki, query)
        if not title:
            print(f"  {Colors.DIM}📚 Fandom: {wiki} miss{Colors.END}")
            continue

        sources_checked.append(f"parse:{title}")
        wikitext = _parse_wikitext(wiki, title)
        if not wikitext:
            print(f"  {Colors.DIM}📚 Fandom: {wiki} miss{Colors.END}")
            continue

        synopsis = _extract_synopsis1(wikitext)
        if not synopsis or len(synopsis) < _MIN_PLOT_CHARS:
            print(f"  {Colors.DIM}📚 Fandom: {wiki} miss{Colors.END}")
            continue

        cleaned = _strip_wiki_links(synopsis).strip()
        url = _page_url(wiki, title)
        print(f"  {Colors.DIM}📚 Fandom: {wiki} ✓ ({len(cleaned)} chars){Colors.END}")
        return {
            "plot_text": cleaned,
            "plot_length": len(cleaned),
            "wiki_url": url,
            "title": title,
            "source": "fandom_synopsis1",
            "sources_checked": sources_checked,
        }

    return {
        "plot_text": "",
        "wiki_url": "",
        "title": "",
        "source": "",
        "sources_checked": sources_checked,
    }
