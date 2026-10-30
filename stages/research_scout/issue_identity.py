"""A deterministic issue-identity gate for exact micro-moment requests.

Research citations establish that a candidate exists. They do not establish
that it is the comic the user asked for, so that second check happens here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_ISSUE_RE = re.compile(r"#\s*(\d+(?:\.\d+)?[A-Za-z]?)(?!\w)")
_RANGE_RE = re.compile(r"\s*[-–—]\s*\d")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_TITLE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-'’][A-Za-z0-9]+)*|[:/&]")
_TRAILING_QUALIFIER_RE = re.compile(
    r"\s*(?:\((?:19|20)\d{2}(?:\s*[-–—]\s*(?:(?:19|20)\d{2}|present)?)?\)|(?:vol(?:ume)?\.?\s*\d+))\s*$",
    re.I,
)
_TITLE_CONNECTORS = frozenset({"a", "an", "and", "for", "in", "of", "the", "to", "vs", "with"})
_INTRO_WORDS = frozenset({
    "comic", "find", "get", "give", "look", "make", "pick", "research",
    "scout", "show", "tell", "use", "verify", "write",
})
_LEADING_PREPOSITIONS = frozenset({"for", "from", "in", "of", "on"})
_GENERIC_SERIES_WORDS = frozenset({
    "issue", "issues", "comic", "comics", "book", "books",
    "chapter", "chapters", "vol", "volume", "part", "no", "number",
})
_GENERIC_ISSUE_MARKER_RE = re.compile(
    r"\b(?:issues?|chapters?|volumes?|vol\.?|nos?\.?|numbers?)\s*$",
    re.I,
)
_URL_PREFIX_RE = re.compile(r"https?://\S*$", re.I)


@dataclass(frozen=True)
class IssueIdentity:
    series: str
    number: str
    year: str = ""

    @property
    def label(self) -> str:
        return f"{self.series} #{self.number}" + (f" ({self.year})" if self.year else "")


def _strip_qualifiers(value: str) -> str:
    while True:
        stripped = _TRAILING_QUALIFIER_RE.sub("", value)
        if stripped == value:
            return value
        value = stripped


def _normal_issue(value: str) -> str:
    head, dot, tail = value.partition(".")
    numeric = re.match(r"\d+", head)
    if numeric is None:
        return value.casefold()
    suffix = head[numeric.end():]
    return str(int(numeric.group())) + suffix.lower() + (dot + tail.lower() if dot else "")


def _series_tokens(value: str) -> list[str]:
    raw_words = re.findall(r"[a-z0-9]+", value.casefold())
    words: list[str] = []
    for w in raw_words:
        if w in {"versus", "v"}:
            words.append("vs")
        else:
            words.append(w)
    stripped = [w for w in words if w not in {"the", "a", "an"}]
    return stripped if stripped else words


def _normal_series(value: str) -> str:
    return " ".join(_series_tokens(value))


def _series_variants(series: str) -> list[str]:
    variants = [series]
    if ":" in series:
        parts = series.split(":")
        for i in range(1, len(parts)):
            suffix = ":".join(parts[i:]).strip()
            if suffix:
                variants.append(suffix)
    for dash in (" - ", " – ", " — "):
        if dash in series:
            parts = series.split(dash)
            for i in range(1, len(parts)):
                suffix = dash.join(parts[i:]).strip()
                if suffix:
                    variants.append(suffix)
    return variants


def _series_match(series_a: str, series_b: str) -> bool:
    norm_a = _normal_series(series_a)
    norm_b = _normal_series(series_b)
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    variants_a = [_normal_series(v) for v in _series_variants(series_a)]
    variants_b = [_normal_series(v) for v in _series_variants(series_b)]
    # An umbrella/event prefix alias requires that one side's suffix after the prefix
    # matches the other side's full series name (e.g. "DC All In Saga: Darkseid vs. the World"
    # matches "Darkseid vs The World", or vice versa).
    # Two different series that merely share a subtitle (e.g. "Batman: Year One" vs
    # "Green Arrow: Year One") must NOT match.
    if any(va == norm_b for va in variants_a[1:] if va):
        return True
    if any(vb == norm_a for vb in variants_b[1:] if vb):
        return True
    return False


def _intent_series(prefix: str) -> str:
    """Take the title immediately before #N, not the request's leading prose."""
    prefix = _strip_qualifiers(prefix)
    # Periods are part of real series names ("DC K.O.") and abbreviations.
    segment = re.split(r"[!?\n]", prefix)[-1].strip()

    # Preposition boundary trimming: prefer a preposition immediately preceded by
    # lowercase prose to avoid cutting inside capitalized titles (e.g. "DC All In Saga").
    matches = list(re.finditer(r"\b(?:in|from|of|for)\b", segment, re.I))
    cut_done = False
    for prep in matches:
        left_words = [w for w in _TITLE_TOKEN_RE.findall(segment[:prep.start()]) if w not in {":", "/", "&"}]
        if left_words and left_words[-1][0].islower() and left_words[-1].casefold() not in _TITLE_CONNECTORS:
            segment = segment[prep.end():].strip()
            cut_done = True
            break
    if not cut_done:
        for prep in reversed(matches):
            left_words = [w for w in _TITLE_TOKEN_RE.findall(segment[:prep.start()]) if w not in {":", "/", "&"}]
            if any(w[0].islower() and w.casefold() not in _TITLE_CONNECTORS for w in left_words):
                segment = segment[prep.end():].strip()
                break

    tokens = [match.group() for match in _TITLE_TOKEN_RE.finditer(segment)]
    selected: list[str] = []
    for token in reversed(tokens):
        if token in {":", "/", "&"}:
            if selected:
                selected.append(token)
            continue
        if token[0].isupper() or token[0].isdigit() or token.casefold() in _TITLE_CONNECTORS:
            selected.append(token)
        else:
            break
    selected.reverse()
    while selected and (
        selected[0].casefold() in _INTRO_WORDS | _LEADING_PREPOSITIONS
        or selected[0] in {":", "/", "&"}
    ):
        selected.pop(0)
    while selected and selected[-1] in {":", "/", "&"}:
        selected.pop()
    if selected:
        return " ".join(selected)

    # A bare lowercase title (or one following 'in/from') is still an exact
    # request. Avoid treating a whole instruction sentence as a comic title.
    lower_segment = segment.casefold()
    cuts = list(re.finditer(r"\b(?:from|in|comic:)\s+", lower_segment))
    if cuts:
        segment = segment[cuts[-1].end():].strip()
    if segment and len(segment.split()) <= 5 and not re.search(r"[.!?]", segment):
        return segment.strip(" :/&,\"'“”")
    return ""


def _candidate_identity(value: str) -> IssueIdentity | None:
    matches = list(_ISSUE_RE.finditer(value))
    if len(matches) != 1 or _RANGE_RE.match(value[matches[0].end():]):
        return None
    match = matches[0]
    series = _strip_qualifiers(value[:match.start()]).strip(" :/&,\"'“”")
    if not series:
        return None
    years = set(_YEAR_RE.findall(value))
    if len(years) > 1:
        return None
    return IssueIdentity(series, _normal_issue(match.group(1)), next(iter(years), ""))


def _intent_targets(user_intent: str) -> tuple[list[IssueIdentity], bool]:
    named_targets: list[IssueIdentity] = []
    bare_targets: list[tuple[str, str]] = []
    last_end = 0

    for match in _ISSUE_RE.finditer(user_intent):
        if _RANGE_RE.match(user_intent[match.end():]):
            continue  # a range is not one exact issue
        prefix = user_intent[:match.start()]
        # Skip URL fragments (e.g. https://example.com/page#1)
        if _URL_PREFIX_RE.search(prefix):
            continue

        segment_text = user_intent[last_end:match.start()]
        suffix = user_intent[match.end():]
        year_match = re.match(r"\s*\(((?:19|20)\d{2})\)", suffix)
        before_year = re.search(r"\(((?:19|20)\d{2})\)\s*$", segment_text)
        year = year_match.group(1) if year_match else (before_year.group(1) if before_year else "")
        issue_number = _normal_issue(match.group(1))

        if year_match:
            last_end = match.end() + year_match.end()
        else:
            last_end = match.end()

        # Check for trailing generic issue words directly before #N
        marker = _GENERIC_ISSUE_MARKER_RE.search(segment_text)
        if marker:
            candidate_prefix = segment_text[:marker.start()].rstrip()
            series = _intent_series(candidate_prefix)
        else:
            series = _intent_series(segment_text)

        if series and _normal_series(series) in _GENERIC_SERIES_WORDS:
            series = ""

        if series:
            named_targets.append(IssueIdentity(series, issue_number, year))
        else:
            bare_targets.append((issue_number, year))

    if not named_targets and not bare_targets:
        return [], False

    # Bare issue mentions without ANY named series fail closed as ambiguous
    if not named_targets and bare_targets:
        return [], True

    # Resolve named targets: all named targets must agree (via alias matching)
    # with the same issue number.
    primary = named_targets[0]
    for other in named_targets[1:]:
        if other.number != primary.number:
            return [], True
        if not _series_match(other.series, primary.series):
            return [], True
        # Prefer the more complete title
        if len(other.series) > len(primary.series):
            primary = IssueIdentity(other.series, primary.number, primary.year)

    # Check that all bare mentions agree with the resolved named issue number
    for bare_num, _ in bare_targets:
        if bare_num != primary.number:
            return [], True

    # Collect years across all mentions (both named and bare)
    all_years: set[str] = set()
    for target in named_targets:
        if target.year:
            all_years.add(target.year)
    for _, bare_yr in bare_targets:
        if bare_yr:
            all_years.add(bare_yr)

    if len(all_years) > 1:
        return [], True

    resolved_year = next(iter(all_years)) if all_years else ""
    if primary.year != resolved_year:
        primary = IssueIdentity(primary.series, primary.number, resolved_year)

    return [primary], False


def micro_issue_rejection_reason(user_intent: str, candidate: Mapping[str, Any]) -> str | None:
    """Explain a mismatch with an exact series+#issue named in a micro request.

    Broad requests have no target and return None. Repeated mentions of the
    same issue are fine; conflicting or unparseable exact mentions fail closed.
    The candidate must identify its issue in ``series_issue_year`` itself.
    """
    targets, unresolved = _intent_targets(str(user_intent or ""))
    if not targets and not unresolved:
        return None
    if unresolved or not targets:
        return "Ambiguous exact comic issue in user intent; review the requested series and issue."

    expected = targets[0]
    given_text = str(candidate.get("series_issue_year") or "").strip()
    given = _candidate_identity(given_text)
    if given is None:
        return f"Expected {expected.label}; candidate has no unambiguous series and issue: {given_text or '(missing)'}."
    if given.number != expected.number or not _series_match(expected.series, given.series):
        return f"Expected {expected.label}; candidate is {given.label}."
    if expected.year and given.year != expected.year:
        return f"Expected {expected.label}; candidate is {given.label} (year missing or different)."

    title = str(candidate.get("title") or "").strip()
    title_targets, title_unresolved = _intent_targets(title)
    if title_unresolved:
        return f"Expected {expected.label}; candidate title names an ambiguous issue: {title}."
    if title_targets:
        title_target = title_targets[0]
        if title_target.number != expected.number or not _series_match(expected.series, title_target.series):
            return f"Expected {expected.label}; candidate title names {title_target.label}."
        if expected.year and title_target.year and title_target.year != expected.year:
            return f"Expected {expected.label}; candidate title names a different year: {title}."
    return None
