"""Direct MediaWiki Action API client for Fandom wikis (Stage 1 plot fetch)."""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..ui import Colors

_USER_AGENT = "ComicVideoPipeline/1.0"
_TIMEOUT = 15
_JINA_TIMEOUT = 20
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
    """Return the wiki list to query.

    If the publisher is known and maps to a configured wiki, query ONLY that
    wiki — no fallback to other publishers' wikis (a Marvel comic is never on
    dc.fandom.com, so trying DC/Image is wasted API calls).

    If the publisher is unknown/unmapped, fall back to trying all configured
    wikis in priority order.
    """
    from config import FANDOM_DOMAINS
    base = list(FANDOM_DOMAINS)
    pub = (publisher or "").strip().lower()
    if not pub:
        return base
    pmap = _publisher_subdomain_map()
    primary = pmap.get(pub)
    if not primary or primary not in base:
        return base
    return [primary]  # publisher known → query only its wiki, skip the rest


def _fetch_via_jina(url: str) -> dict | None:
    """Fallback for Cloudflare-blocked direct requests: r.jina.ai fetches the page
    server-side (bypassing the challenge) and returns it as text. The MediaWiki
    JSON comes back verbatim or wrapped in a ```...``` fence — strip that, parse."""
    proxy_url = f"https://r.jina.ai/{url}"
    try:
        req = urllib.request.Request(proxy_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_JINA_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  {Colors.DIM}[fandom] jina FAILED too ({e}){Colors.END}")
        return None
    body = text.strip()
    m = re.search(r'\{.*\}', body, re.DOTALL)  # strip markdown fence/prose jina may add
    if m:
        body = m.group(0)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        print(f"  {Colors.DIM}[fandom] jina FAILED too (bad JSON: {e}){Colors.END}")
        return None
    print(f"  {Colors.DIM}[fandom] jina OK ({len(text)} chars){Colors.END}")
    return parsed


def _http_get_json(url: str) -> dict | None:
    """GET a URL with one retry and parse JSON; if direct access keeps failing
    (403/429/timeout/Cloudflare challenge), fall back once to the r.jina.ai
    reader proxy (gate: FANDOM_PROXY env, default "1"=on)."""
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
            break
    status = getattr(last_err, "code", type(last_err).__name__)
    print(f"  {Colors.DIM}[fandom] direct {status} → jina proxy...{Colors.END}")
    if os.environ.get("FANDOM_PROXY", "1") != "1":
        return None
    return _fetch_via_jina(url)


def _search_wiki(wiki: str, query: str) -> list[str]:
    """Search a Fandom wiki, returning ALL candidate article titles (best first).

    Returns a list (not just the top hit): on Marvel the #1 hit is often the
    VOLUME container page ("... Vol 1", no |Synopsis1=), while the real plot
    lives on the ISSUE page ("... Vol 1 1") ranked #2. The caller iterates these
    candidates until one yields a usable synopsis."""
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
        return []
    hits = payload.get("query", {}).get("search", [])
    return [t for hit in hits if (t := hit.get("title", "").strip())]


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
    body = m.group(1).strip()
    # Empty Synopsis1 can bleed into the next infobox field (| Appearing1 = ...).
    # A real synopsis is prose, never a pipe-field list.
    if body.startswith("|") or "Featured Characters:" in body:
        return None
    return body


def _title_matches(query: str, title: str) -> bool:
    """Guard against wrong-wiki fallthrough: a search hit must share most of the
    query's significant words (e.g. 'Power Rangers: Ranger Slayer' must NOT
    accept dc.fandom's best fuzzy hit 'Unknown Soldier Vol 4 2')."""
    def norm(s: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", s.lower())
    skip = {"vol", "the", "comic", "issue", "and"}
    q = [w for w in norm(query) if len(w) > 2 and w not in skip]
    if not q:
        return True
    t = set(norm(title))
    hits = sum(1 for w in q if w in t)
    return hits >= max(1, int(len(q) * 0.6))


def _extract_section(wikitext: str, names: tuple[str, ...] = ("Plot", "Synopsis", "Summary", "Story")) -> str | None:
    """Pull a ==Plot== / ==Synopsis== h2 section body (non-Marvel wikis like
    powerrangers.fandom use sections instead of the |Synopsis1= infobox field).
    Tries names in order — Plot first, as it is the detailed scene-by-scene one."""
    for name in names:
        m = re.search(
            rf'^==\s*{name}\s*==\s*\n(.*?)(?=\n==[^=]|\Z)',
            wikitext, re.DOTALL | re.IGNORECASE | re.MULTILINE,
        )
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def _strip_wiki_links(text: str) -> str:
    """Convert [[link|display]] -> display and [[link]] -> link; drop refs/templates."""
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)
    text = re.sub(r'\{\{[^{}]*\}\}', '', text)
    text = re.sub(r'\[\[(?:File|Image):[^\[\]]*\]\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\[([^\[\]\|]+)\|([^\[\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\[\]]+)\]\]', r'\1', text)
    text = text.replace("'''", "").replace("''", "")
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
        candidates = _search_wiki(wiki, query)
        if not candidates:
            print(f"  {Colors.DIM}📚 Fandom: {wiki} miss{Colors.END}")
            continue

        # Try each candidate in rank order until one yields a usable synopsis.
        # The #1 hit is often a VOLUME stub ("... Vol 1") with no |Synopsis1=;
        # the real plot lives on the ISSUE page ("... Vol 1 1") ranked lower.
        matched_any = False
        for title in candidates:
            if not _title_matches(query, title):
                continue
            matched_any = True
            sources_checked.append(f"parse:{title}")
            wikitext = _parse_wikitext(wiki, title)
            if not wikitext:
                continue

            synopsis = _extract_synopsis1(wikitext)
            source = "fandom_synopsis1"
            if not synopsis or len(synopsis) < _MIN_PLOT_CHARS:
                # Non-Marvel wikis (powerrangers, starwars, ...) use ==Plot==/==Synopsis==
                # sections instead of the |Synopsis1= infobox field.
                synopsis = _extract_section(wikitext)
                source = "fandom_section"
            # TPB fallback: a Marvel search often returns the COLLECTED edition page
            # ("... TPB Vol 1 1") whose Synopsis1 is empty — the real plot lives on
            # the single-issue page ("... Vol 1 1"). Retry without "TPB".
            if (not synopsis or len(synopsis) < _MIN_PLOT_CHARS) and "TPB" in title:
                alt = title.replace("TPB ", "").replace(" TPB", "").strip()
                alt_wt = _parse_wikitext(wiki, alt)
                if alt_wt:
                    alt_syn = _extract_synopsis1(alt_wt) or _extract_section(alt_wt)
                    if alt_syn and len(alt_syn) >= _MIN_PLOT_CHARS:
                        synopsis, title, source = alt_syn, alt, "fandom_synopsis1_detpb"
            if not synopsis or len(synopsis) < _MIN_PLOT_CHARS:
                continue  # this candidate had no plot — try the next hit

            cleaned = _strip_wiki_links(synopsis).strip()
            url = _page_url(wiki, title)
            print(f"  {Colors.DIM}📚 Fandom: {wiki} ✓ ({len(cleaned)} chars, {source}){Colors.END}")
            return {
                "plot_text": cleaned,
                "plot_length": len(cleaned),
                "wiki_url": url,
                "title": title,
                "source": source,
                "sources_checked": sources_checked,
            }

        if not matched_any:
            print(f"  {Colors.DIM}📚 Fandom: {wiki} irrelevant hit {candidates[0]!r} — skip{Colors.END}")
        else:
            print(f"  {Colors.DIM}📚 Fandom: {wiki} miss{Colors.END}")

    return {
        "plot_text": "",
        "wiki_url": "",
        "title": "",
        "source": "",
        "sources_checked": sources_checked,
    }
