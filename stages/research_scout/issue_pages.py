"""Fetch the issue page a candidate cites, in a form the gate can read.

The free reader (r.jina.ai) is the primary route, but it is not always an article:
roughly a quarter of live fetches came back as a Cloudflare "Just a moment..." page
(HTTP 200), and a wrong wiki title comes back as a cookie-banner stub. Both used to
count as ``ok`` and produced misleading reasons (series_title_mismatch, year_not_found).

Here such pages are detected, and for wiki hosts the MediaWiki API (``action=parse``,
a plain GET that does not go through the reader) supplies the rendered page instead.
Its HTML is flattened to the same markdown-ish text the reader produces — headings
as ``##``, pull-quotes as ``>``, infobox rows as ``Label: value``, links kept as
``[text](href "title")`` — so every downstream check reads one format.

Nothing here raises, and nothing here is paid.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from . import cited_sources
from .cited_sources import FetchedSource

# Wiki pages arrive with 13k+ chars of navigation before the article (and run to
# ~120k with appearance lists), so the cap only bounds a runaway response.
ISSUE_PAGE_MAX_CHARS = 250_000
ISSUE_FETCH_TIMEOUT = 15
ISSUE_FETCH_RETRIES = 1

_USER_AGENT = "comic-scout/1.0"
_CHALLENGE = re.compile(
    r"just a moment|performing security verification|enable javascript and cookies to continue"
    r"|attention required|verify you are human", re.IGNORECASE)
_HEADING = re.compile(r"(?m)^#{1,6}\s+(.*)$")
_COOKIE_HEADINGS = re.compile(
    r"cookie|categories\s*&\s*purposes|consent|privacy|purposes list", re.IGNORECASE)
_URL_SOURCE = re.compile(r"(?m)^URL Source:\s*(\S+)")
_MONTH = (r"January|February|March|April|May|June|July|August|September|October|November|"
          r"December")


# --- what came back ------------------------------------------------------------------

def _is_wiki(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "").casefold().endswith(".fandom.com") and "/wiki/" in parts.path


def page_problem(text: str) -> str | None:
    """``"challenge"`` for a bot-check page, ``"page_missing"`` for a wiki stub that has
    no article at all, else None. Flat (non-wiki) pages are never judged here."""
    if len(text) < 10_000 and _CHALLENGE.search(text):
        return "challenge"
    source = _URL_SOURCE.search(text)
    if source and _is_wiki(source.group(1)):
        articles = [h for h in _HEADING.findall(text) if not _COOKIE_HEADINGS.search(h)]
        if not articles:
            return "page_missing"
    return None


# --- the MediaWiki API route -------------------------------------------------------------

def _api_get(url: str) -> dict | None:
    """One GET of a MediaWiki API URL, parsed. None on any failure."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=ISSUE_FETCH_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            return data if isinstance(data, dict) else None
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                return None
        except Exception:
            pass
        if attempt == 0:
            time.sleep(cited_sources.RETRY_DELAY)
    return None


def _fetch_via_api(url: str) -> FetchedSource:
    parts = urlsplit(url)
    title = unquote(parts.path.split("/wiki/", 1)[1])
    api = (f"https://{parts.hostname}/api.php?action=parse"
           f"&page={quote(title, safe='/:()_-,!')}&prop=text%7Ccategories&format=json"
           "&redirects=1&disableeditsection=1")
    data = _api_get(api)
    if data is None:
        return FetchedSource(url=url, error="api_unavailable")
    error = data.get("error")
    if isinstance(error, dict):
        return FetchedSource(
            url=url, error="page_missing" if error.get("code") == "missingtitle" else "api_error")
    try:
        parse = data["parse"]
        body = wiki_html_to_text(parse["text"]["*"])
        names = [str(c.get("*", "")).replace("_", " ") for c in parse.get("categories", [])]
        heading = str(parse.get("title") or title).replace("_", " ")
    except (KeyError, TypeError, AttributeError):
        return FetchedSource(url=url, error="api_error")
    dates = release_date_lines(names)
    text = f"Title: {heading}\n\nURL Source: {url}\n\nMarkdown Content:\n{body}\n\n{dates}"
    return FetchedSource(url=url, text=text.strip()[:ISSUE_PAGE_MAX_CHARS])


def release_date_lines(category_names) -> str:
    """Labelled date lines from wiki category names (release dates first-class, cover
    dates too); anything else is ignored."""
    lines: list[str] = []
    for raw in category_names:
        name = str(raw).removeprefix("Category:").strip()
        for pattern, label, render in (
            (r"Comics Released in (.+)", "Release Date", lambda m: m.group(1)),
            (rf"((?:{_MONTH}) \d{{1,2}}, (?:19|20)\d{{2}}) \(Publication\)", "Release Date",
             lambda m: m.group(1)),
            (r"((?:19|20)\d{2}), ([A-Za-z]+) \(Publication\)", "Release Date",
             lambda m: f"{m.group(2)}, {m.group(1)}"),
            (r"((?:19|20)\d{2}), ([A-Za-z]+) Cover Date", "Cover Date",
             lambda m: f"{m.group(2)}, {m.group(1)}"),
        ):
            match = re.fullmatch(pattern, name)
            if match:
                lines.append(f"{label}: {render(match)}")
                break
    return "\n".join(dict.fromkeys(lines))


class _WikiHtml(HTMLParser):
    """Rendered MediaWiki HTML -> the reader's markdown-ish text."""

    _SKIP = {"script", "style", "noscript", "sup", "img", "svg"}
    _BLOCK = {"p", "div", "ul", "ol", "table", "tr", "section", "aside", "dl", "dt", "dd",
              "figure", "figcaption"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._heading: int | None = None
        self._heading_text: list[str] = []
        self._infobox = 0
        self._label: list[str] | None = None
        self._value: list[str] | None = None
        self._row_label = ""
        self._quote: list[list[str]] = []
        self._links: list[tuple[str, str]] = []

    def _emit(self, text: str) -> None:
        if self._heading is not None:
            self._heading_text.append(text)
        elif self._label is not None:
            self._label.append(text)
        elif self._value is not None:
            self._value.append(text)
        elif self._quote:
            self._quote[-1].append(text)
        else:
            self.out.append(text)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        css = attributes.get("class") or ""
        if tag in self._SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "aside" and "portable-infobox" in css:
            self._infobox += 1
            return
        if self._infobox:
            if tag == "h3" and "pi-data-label" in css:
                self._label = []
            elif tag == "div" and "pi-data-value" in css:
                self._value = []
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading, self._heading_text = int(tag[1]), []
        elif tag == "blockquote":
            self._quote.append([])
        elif tag == "li":
            self._emit("\n*   ")
        elif tag == "br":
            self._emit("\n")
        elif tag in ("i", "em"):
            self._emit("_")
        elif tag in ("b", "strong"):
            self._emit("**")
        elif tag == "a":
            self._links.append((attributes.get("href") or "", attributes.get("title") or ""))
            self._emit("[")
        elif tag in self._BLOCK:
            self._emit("\n\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if self._infobox:
            if tag == "aside":
                self._infobox -= 1
            elif tag == "h3" and self._label is not None:
                self._row_label = " ".join("".join(self._label).split())
                self._label = None
            elif tag == "div" and self._value is not None:
                value = " ".join("".join(self._value).split())
                self._value = None
                if self._row_label and value:
                    self.out.append(f"{self._row_label}: {value}\n")
                self._row_label = ""
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading is not None:
            text = " ".join("".join(self._heading_text).split())
            level, self._heading = max(2, self._heading), None
            if text and text != "Contents":
                self._emit(f"\n\n{'#' * level} {text}\n\n")
        elif tag == "blockquote" and self._quote:
            lines = [ln.strip() for ln in "".join(self._quote.pop()).splitlines() if ln.strip()]
            self._emit("\n\n" + "\n".join(f"> {ln}" for ln in lines) + "\n\n")
        elif tag in ("i", "em"):
            self._emit("_")
        elif tag in ("b", "strong"):
            self._emit("**")
        elif tag == "a" and self._links:
            href, title = self._links.pop()
            self._emit(f"]({href}" + (f' "{title}"' if title else "") + ")")
        elif tag == "td" or tag == "th":
            self._emit(" ")
        elif tag in self._BLOCK or tag == "li":
            self._emit("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._infobox and self._label is None and self._value is None:
            return
        self._emit(data)


def wiki_html_to_text(html: str) -> str:
    converter = _WikiHtml()
    converter.feed(html)
    converter.close()
    text = re.sub(r"[ \t]+\n", "\n", "".join(converter.out))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- titles with a "/" -----------------------------------------------------------------------

def _alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def slug_variants(url: str, issue: str) -> list[str]:
    """Other spellings of a wiki issue URL that the *issue label* supports.

    The scout sometimes writes a title containing "/" with an underscore
    (``Series_Other_Vol_1_72`` for ``Series/Other Vol 1 72``). Only a label that really
    has the slash can justify the corrected slug, so nothing is guessed otherwise."""
    parts = urlsplit(url)
    match = re.match(r"^(/wiki/)(.+?)(_Vol_\d+_\d+.*)$", unquote(parts.path), re.IGNORECASE)
    if match is None:
        return []
    prefix, slug_series, tail = match.groups()
    label = issue.split("#", 1)[0]
    label = re.sub(r"\(\s*(?:19|20)\d{2}\s*\)", "", label)
    label = re.sub(r"\bVol(?:ume)?\.?\s*\d+\s*$", "", label.strip(), flags=re.IGNORECASE).strip()
    if "/" not in label:
        return []
    wanted = re.sub(r"\s+", "_", label)
    if _alnum(wanted) != _alnum(slug_series) or wanted == slug_series:
        return []
    return [urlunsplit(parts._replace(path=quote(f"{prefix}{wanted}{tail}", safe="/:()_-,!'")))]


# --- the entry point ------------------------------------------------------------------------------

def _with_release_dates(url: str, source: FetchedSource) -> FetchedSource:
    from .qa_discovery_gate import _publication_years  # local: the gate owns date reading

    if _publication_years(source.text):
        return source
    parts = urlsplit(url)
    if not (parts.hostname or "").casefold().endswith(".fandom.com") or "/wiki/" not in parts.path:
        return source
    api = (f"https://{parts.hostname}/api.php?action=query&prop=categories"
           f"&titles={parts.path.split('/wiki/', 1)[1]}&cllimit=max&format=json")
    reply = cited_sources.fetch_source(
        api, timeout=ISSUE_FETCH_TIMEOUT, max_chars=200_000, retries=ISSUE_FETCH_RETRIES)
    match = re.search(r"\{.*\}", reply.text, re.DOTALL) if reply.ok else None
    if match is None:
        return source
    try:
        pages = json.loads(match.group(0))["query"]["pages"].values()
        names = [str(c["title"]) for page in pages for c in page.get("categories", [])]
    except (ValueError, KeyError, TypeError, AttributeError):
        return source
    dates = release_date_lines(names)
    return FetchedSource(url=source.url, text=f"{source.text}\n\n{dates}") if dates else source


def fetch_issue_page(url: str) -> FetchedSource:
    """The cited page as readable text, or a ``FetchedSource`` whose ``error`` says why not:
    ``timeout`` / ``HTTP nnn`` (reader), ``challenge`` (bot check, no API route),
    ``page_missing`` (no such wiki title), ``api_unavailable``/``api_error``."""
    source = cited_sources.fetch_source(
        url, timeout=ISSUE_FETCH_TIMEOUT, max_chars=ISSUE_PAGE_MAX_CHARS,
        retries=ISSUE_FETCH_RETRIES)
    problem = page_problem(source.text) if source.ok else None
    if source.ok and problem is None:
        return _with_release_dates(url, source)
    if not _is_wiki(url):
        return FetchedSource(url=url, error=problem or source.error)
    fallback = _fetch_via_api(url)
    if fallback.ok or fallback.error == "page_missing":
        return fallback
    return FetchedSource(url=url, error=problem or source.error or fallback.error)
