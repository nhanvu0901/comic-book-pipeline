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
    r"\s*(?:\((?:19|20)\d{2}\)|(?:vol(?:ume)?\.?\s*\d+))\s*$", re.I
)
_TITLE_CONNECTORS = frozenset({"a", "an", "and", "for", "in", "of", "the", "to", "vs", "with"})
_INTRO_WORDS = frozenset({
    "comic", "find", "get", "give", "look", "make", "pick", "research",
    "scout", "show", "tell", "use", "verify", "write",
})
_LEADING_PREPOSITIONS = frozenset({"for", "from", "in", "of", "on"})


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


def _normal_series(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def _intent_series(prefix: str) -> str:
    """Take the title immediately before #N, not the request's leading prose."""
    prefix = _strip_qualifiers(prefix)
    # Periods are part of real series names ("DC K.O.") and abbreviations.
    segment = re.split(r"[!?\n]", prefix)[-1].strip()
    for preposition in reversed(list(re.finditer(r"\b(?:in|from|of|for)\b", segment, re.I))):
        left_words = _TITLE_TOKEN_RE.findall(segment[:preposition.start()])
        if any(word[0].islower() and word.casefold() not in _TITLE_CONNECTORS
               for word in left_words if word not in {":", "/", "&"}):
            segment = segment[preposition.end():].strip()
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
    targets: list[IssueIdentity] = []
    unresolved = False
    for match in _ISSUE_RE.finditer(user_intent):
        if _RANGE_RE.match(user_intent[match.end():]):
            continue  # a range is not one exact issue
        prefix = user_intent[:match.start()]
        series = _intent_series(prefix)
        if not series:
            unresolved = True
            continue
        suffix = user_intent[match.end():]
        year_match = re.match(r"\s*\(((?:19|20)\d{2})\)", suffix)
        before_year = re.search(r"\(((?:19|20)\d{2})\)\s*$", prefix)
        year = year_match.group(1) if year_match else (before_year.group(1) if before_year else "")
        targets.append(IssueIdentity(series, _normal_issue(match.group(1)), year))
    return targets, unresolved


def micro_issue_rejection_reason(user_intent: str, candidate: Mapping[str, Any]) -> str | None:
    """Explain a mismatch with an exact series+#issue named in a micro request.

    Broad requests have no target and return None. Repeated mentions of the
    same issue are fine; conflicting or unparseable exact mentions fail closed.
    The candidate must identify its issue in ``series_issue_year`` itself.
    """
    targets, unresolved = _intent_targets(str(user_intent or ""))
    if not targets and not unresolved:
        return None
    keys = {(_normal_series(target.series), target.number) for target in targets}
    years = {target.year for target in targets if target.year}
    if unresolved or len(keys) != 1 or len(years) > 1:
        return "Ambiguous exact comic issue in user intent; review the requested series and issue."

    expected = next((target for target in targets if target.year), targets[0])
    given_text = str(candidate.get("series_issue_year") or "").strip()
    given = _candidate_identity(given_text)
    if given is None:
        return f"Expected {expected.label}; candidate has no unambiguous series and issue: {given_text or '(missing)'}."
    if (_normal_series(given.series), given.number) != next(iter(keys)):
        return f"Expected {expected.label}; candidate is {given.label}."
    if years and given.year not in years:
        return f"Expected {expected.label}; candidate is {given.label} (year missing or different)."
    title = str(candidate.get("title") or "").strip()
    title_targets, title_unresolved = _intent_targets(title)
    title_keys = {(_normal_series(target.series), target.number) for target in title_targets}
    if title_unresolved or len(title_keys) > 1:
        return f"Expected {expected.label}; candidate title names an ambiguous issue: {title}."
    if title_keys and title_keys != keys:
        return f"Expected {expected.label}; candidate title names {title_targets[0].label}."
    if title_targets and years and any(target.year and target.year not in years for target in title_targets):
        return f"Expected {expected.label}; candidate title names a different year: {title}."
    return None
