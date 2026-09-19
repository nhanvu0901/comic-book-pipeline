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

from collections.abc import Mapping
from dataclasses import dataclass
import json
import socket
from typing import Any
import urllib.error
import urllib.request


READER_PREFIX = "https://r.jina.ai/"
_USER_AGENT = "comic-scout/1.0"

# Firm, all three. Gating is already one HTTP search plus one model call per
# candidate; cited sources are an addition to that budget, not a new one.
MAX_CITED_URLS = 3
FETCH_TIMEOUT = 10
MAX_SOURCE_CHARS = 6000

_CITATION_FIELDS = ("evidence_urls", "source_urls")
_CITED_HEADING = "CITED SOURCES (fetched from the candidate's own citations)"
_SEARCH_HEADING = "SEARCH RESULTS"
_NO_CITATIONS = "(this candidate cited no URLs)"
_FETCH_FAILED = "COULD NOT FETCH"


@dataclass(frozen=True)
class FetchedSource:
    """One cited URL and what came back — text, or why nothing did."""

    url: str
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text)


def cited_urls(candidate: Any) -> list[str]:
    """The http(s) URLs a candidate cites, in order, deduped, capped."""

    if not isinstance(candidate, Mapping):
        return []
    urls: list[str] = []
    seen: set[str] = set()
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
            if not url.lower().startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
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
    """The gate's RAW EVIDENCE block: what we read, then what we searched.

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
