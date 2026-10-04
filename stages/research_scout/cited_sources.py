"""Fetch the pages a scout candidate cited, so the evidence gate can read them.

The gate is asked to check "every cited URL". Until now nothing retrieved any
of them: the URLs appeared in the search query as text and nowhere else, so a
candidate citing CBR or Fandom could only ever come back unverifiable.

Retrieval goes through the ``r.jina.ai`` reader, the pattern already proven in
``stages/stage_1/tools/fetch_fandom.py::_fetch_via_jina`` — Cloudflare blocks
direct reads of several of these sites. That helper parses MediaWiki JSON, so
this is a sibling rather than a caller: what the gate needs back is text.

Nothing here raises. Gating runs one thread per candidate and the caller reads
an exception as "this branch failed", which would throw away a verdict over a
page we merely could not open. A failed fetch is a finding, and the gate is
told the difference between "we read it and it does not say that" and "we
never managed to look".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
import socket
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit


READER_PREFIX = "https://r.jina.ai/"
_USER_AGENT = "comic-scout/1.0"

# Firm, all three. Gating is already one HTTP search plus one model call per
# candidate; cited sources are an addition to that budget, not a new one.
MAX_CITED_URLS = 3
FETCH_TIMEOUT = 10
MAX_SOURCE_CHARS = 6000

_CITATION_FIELDS = ("evidence_urls", "source_urls")
_CITED_HEADING = "CITED SOURCES (fetched from the candidate's own citations)"
_SEARCH_HEADING = "VERIFICATION RESEARCH"
_NO_CITATIONS = "(this candidate cited no URLs)"
_FETCH_FAILED = "COULD NOT FETCH"
_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})
_INLINE_LINK = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^()]*\))*\)")
_RAW_URL = re.compile(r"https?://\S+", re.IGNORECASE)

# Kept here rather than duplicated in the two strict research schemas.  The
# Research API requires every object property to be declared and required, so
# callers take a copy before embedding it in their larger schema.
CLAIM_CITATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "url": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["url", "quote"],
}


@dataclass(frozen=True)
class FetchedSource:
    """One cited URL and what came back — text, or why nothing did."""

    url: str
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text)


@dataclass(frozen=True)
class ClaimCitation:
    """The one source sentence a new candidate binds to its own claim."""

    url: str
    quote: str


@dataclass(frozen=True)
class URLRebind:
    """A model citation safely rebound to a URL returned by You.com."""

    url: str
    reason: str


def match_key(url: str) -> str:
    """Canonical comparison key for harmless URL formatting/model rewrites.

    `canonical_url` intentionally remains unchanged for existing evidence
    identity checks. This broader key additionally folds mobile/www hosts,
    default ports, percent escapes and MediaWiki underscore/space variants.
    """
    try:
        parts = urlsplit(str(url).strip())
        host = _normalized_host(parts.hostname or "")
        if not host:
            return ""
        port = parts.port
        if port and not ((parts.scheme.casefold() == "https" and port == 443)
                         or (parts.scheme.casefold() == "http" and port == 80)):
            host = f"{host}:{port}"
        path = unquote(parts.path).rstrip("/") or "/"
        if "/wiki/" in path:
            path = path.replace("_", " ")
        kept = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                      if not k.casefold().startswith("utm_")
                      and k.casefold() not in _TRACKING_QUERY_KEYS)
        return f"{host}{path}" + (f"?{urlencode(kept, doseq=True)}" if kept else "")
    except (TypeError, ValueError):
        return ""


def rebind_citation_url(
    model_url: str,
    returned_sources: Sequence[Mapping[str, Any]],
    quote: str = "",
) -> URLRebind | None:
    """Bind a rewritten model URL to one returned source using T1–T3.

    T2 only accepts matching source-specific structural IDs and refuses any
    numeric-token disagreement. T3 is the sole fallback permitted to override
    a numeric URL disagreement, and only when the normalized quote occurs in
    exactly one retrieved snippet.
    """
    model_key = match_key(model_url)
    if not model_key:
        return None
    rows = [s for s in returned_sources if isinstance(s, Mapping)
            and isinstance(s.get("url"), str) and match_key(s["url"])]
    exact = [s for s in rows if match_key(s["url"]) == model_key]
    if len(exact) == 1:
        return URLRebind(str(exact[0]["url"]), "t1_match_key")

    model_parts = _url_structure(model_url)
    if model_parts:
        model_nums = _url_numbers(model_url)
        structural = []
        for source in rows:
            url = str(source["url"])
            if _same_host(model_url, url) and _url_structure(url) == model_parts:
                if model_nums == _url_numbers(url):
                    structural.append(source)
        if len(structural) == 1:
            return URLRebind(str(structural[0]["url"]), "t2_structural_id")

    normalized_quote = normalize_quote(quote)
    if normalized_quote:
        quote_hits = []
        for source in rows:
            snippets = source.get("snippets")
            if isinstance(snippets, str):
                snippets = [snippets]
            if not isinstance(snippets, (list, tuple)):
                continue
            snippet_text = normalize_quote(" ".join(str(x) for x in snippets if x))
            if normalized_quote in snippet_text:
                quote_hits.append(source)
        if len(quote_hits) == 1:
            return URLRebind(str(quote_hits[0]["url"]), "t3_unique_snippet")
    return None


def _same_host(left: str, right: str) -> bool:
    try:
        a, b = urlsplit(left), urlsplit(right)
        return _normalized_host(a.hostname or "") == _normalized_host(b.hostname or "")
    except ValueError:
        return False


def _normalized_host(host: str) -> str:
    """Fold presentation aliases consistently for T1 and source-family IDs."""
    return re.sub(r"^(?:www|m|amp)\.", "", host.casefold())


def _url_numbers(url: str) -> tuple[str, ...]:
    try:
        return tuple(re.findall(r"\d+", unquote(urlsplit(url).path)))
    except ValueError:
        return ()


def _url_structure(url: str) -> tuple[str, ...] | None:
    """Recognize stable issue/page IDs on common comic source URL families."""
    try:
        parts = urlsplit(url)
        host = _normalized_host(parts.hostname or "")
        path = unquote(parts.path)
    except ValueError:
        return None
    if host.endswith("fandom.com"):
        title = path.rsplit("/wiki/", 1)[-1]
        match = re.match(r"(.+)_Vol_(\d+)_(\d+)(?:_|$)", title, re.IGNORECASE)
        if match:
            return ("fandom", re.sub(r"[_ ]+", "_", match[1]).casefold(), match[2], match[3])
    if host == "leagueofcomicgeeks.com":
        match = re.search(r"/comic/(\d+)(?:/|$)", path, re.IGNORECASE)
        if match:
            return ("leagueofcomicgeeks", match[1])
    if host.endswith("batcave.biz"):
        match = re.search(r"/(\d+)-[^/]+/?$", path, re.IGNORECASE)
        if match:
            return ("batcave", match[1])
    return None


def claim_citation(candidate: Any) -> ClaimCitation | None:
    """Read a well-formed source-bound claim citation, or return ``None``.

    ``None`` deliberately also represents legacy candidate artifacts written
    before source binding existed.  New `run_general` artifacts are screened
    separately; this reader must stay permissive so an old paid-for session can
    still be reviewed with its original evidence path.
    """

    if not isinstance(candidate, Mapping):
        return None
    value = candidate.get("claim_citation")
    if not isinstance(value, Mapping):
        return None
    url = value.get("url")
    quote = value.get("quote")
    if not isinstance(url, str) or not isinstance(quote, str):
        return None
    url = url.strip()
    quote = quote.strip()
    if (
        not url.lower().startswith(("http://", "https://"))
        or not canonical_url(url)
        or not quote
    ):
        return None
    return ClaimCitation(url=url, quote=quote)


def canonical_url(url: str) -> str:
    """Compare web sources by page identity, ignoring display-only tracking."""

    try:
        parts = urlsplit(url.strip())
    except (TypeError, ValueError):
        return ""
    host = parts.netloc.casefold()
    if not host:
        return ""
    path = parts.path.rstrip("/") or "/"
    kept_query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_QUERY_KEYS
    )
    query = urlencode(kept_query, doseq=True)
    return f"{host}{path}" + (f"?{query}" if query else "")


def normalize_quote(text: str) -> str:
    """Make harmless page-formatting differences irrelevant to a quote match."""

    translation = str.maketrans({
        "\u2018": "'", "\u2019": "'", "\u201b": "'",
        "\u201c": '"', "\u201d": '"', "\u201f": '"', "\u00a0": " ",
    })
    return " ".join(str(text).translate(translation).casefold().split())


def _visible_markdown_text(text: str) -> str:
    """Drop Markdown transport syntax before comparing human-visible quotes."""

    visible = str(text)
    # One nesting level covers normal Jina URLs containing a parenthesised year.
    # Iterate in case a page wraps a link label in another link-like construct.
    while True:
        replaced = _INLINE_LINK.sub(r"\1", visible)
        if replaced == visible:
            break
        visible = replaced
    visible = _RAW_URL.sub("", visible)
    return visible.replace("**", "").replace("__", "").replace("~~", "").replace("`", "")


def citation_fingerprint(citation: ClaimCitation) -> tuple[str, str]:
    """Stable exact source+quote identity used to reject padded duplicates."""

    return canonical_url(citation.url), normalize_quote(citation.quote)


def quote_matches_source(citation: ClaimCitation, source: FetchedSource) -> bool:
    """Whether the fetched bound page actually contains the claimed sentence."""

    if not source.ok or canonical_url(citation.url) != canonical_url(source.url):
        return False
    quote = normalize_quote(_visible_markdown_text(citation.quote))
    return bool(quote) and quote in normalize_quote(_visible_markdown_text(source.text))


def cited_urls(candidate: Any) -> list[str]:
    """The http(s) URLs a candidate cites, in order, deduped, capped."""

    if not isinstance(candidate, Mapping):
        return []
    urls: list[str] = []
    seen: set[str] = set()
    bound = claim_citation(candidate)
    if bound is not None:
        urls.append(bound.url)
        seen.add(canonical_url(bound.url))
    for field in _CITATION_FIELDS:
        value = candidate.get(field)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            continue
        for item in value:
            if not isinstance(item, str):
                continue
            url = item.strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            canonical = canonical_url(url)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            urls.append(url)
            if len(urls) >= MAX_CITED_URLS:
                return urls
    return urls


def fetch_source(
    url: str, *, timeout: float = FETCH_TIMEOUT, max_chars: int = MAX_SOURCE_CHARS
) -> FetchedSource:
    """Read one cited URL through the reader proxy. Never raises."""

    request = urllib.request.Request(
        f"{READER_PREFIX}{url}",
        headers={"User-Agent": _USER_AGENT, "Accept": "text/plain"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except Exception as exc:  # a page we cannot open is data, never an error
        return FetchedSource(url=url, error=_reason(exc))
    try:
        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    except Exception as exc:
        return FetchedSource(url=url, error=_reason(exc))
    text = text.strip()
    if not text:
        return FetchedSource(url=url, error="empty response")
    return FetchedSource(url=url, text=text[:max_chars])


def fetch_cited_sources(candidate: Any) -> list[FetchedSource]:
    """Fetch a candidate's citations one after another on the calling thread.

    Sequential on purpose: ``ScoutWorkflow.verify_selected`` already fans the
    candidates out across a ``ThreadPoolExecutor``, and a pool nested inside
    each worker multiplies out.
    """

    return [fetch_source(url) for url in cited_urls(candidate)]


def _reason(exc: BaseException) -> str:
    """Why a fetch failed, in words safe to hand a model — never the host,
    query string or key that might be inside the transport error's message."""

    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return "timeout"
        return "URLError"
    return type(exc).__name__


def build_raw_evidence(sources: list[FetchedSource], search_payload: Any) -> str:
    """The gate's RAW EVIDENCE block: what we read, then what we went and checked.

    Two labelled sections rather than one blob, because "we fetched the page
    and it does not support this" and "we never managed to open the page" are
    different findings. Collapsed into one they both come back `inconclusive`,
    which is the state session f56a9bc0 got stuck in.
    """

    lines = [_CITED_HEADING]
    if not sources:
        lines.append(f"  {_NO_CITATIONS}")
    for position, source in enumerate(sources, start=1):
        if source.ok:
            lines.append(f"  [{position}] {source.url} — fetched, {len(source.text):,} chars")
            lines.extend(f"      {line}" for line in source.text.splitlines())
        else:
            reason = source.error or "unknown reason"
            lines.append(f"  [{position}] {source.url} — {_FETCH_FAILED} ({reason})")
    lines.append("")
    lines.append(_SEARCH_HEADING)
    lines.append(f"  {json.dumps(search_payload, ensure_ascii=False)}")
    return "\n".join(lines)


def extract_sources_from_payload(
    payload: Any, candidate: Any = None
) -> list[FetchedSource]:
    """Extract sources and livecrawl snippets directly from a You.com research payload.

    Only text supplied by You.com counts as retrieved evidence. ``candidate`` is
    accepted for callers using the older signature but its own quote must never
    be copied into the source list: that would make a fabricated quote pass a
    mechanical source check.
    """
    fetched: list[FetchedSource] = []
    seen: set[str] = set()

    if isinstance(payload, Mapping):
        output = payload.get("output")
        container = output if isinstance(output, Mapping) else payload
        sources = container.get("sources")
        if isinstance(sources, list):
            for s in sources:
                if not isinstance(s, Mapping):
                    continue
                url = str(s.get("url") or "").strip()
                canonical = canonical_url(url)
                if not canonical or canonical in seen:
                    continue
                seen.add(canonical)
                snippets = s.get("snippets")
                if isinstance(snippets, list) and snippets:
                    text = "\n\n".join(str(snip) for snip in snippets if snip)
                else:
                    text = str(s.get("description") or s.get("title") or "")
                if text:
                    fetched.append(FetchedSource(url=url, text=text))

    return fetched
