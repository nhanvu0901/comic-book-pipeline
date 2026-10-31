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
_ONGOING_QUALIFIER_RE = re.compile(
    r"\(((?:19|20)\d{2})\s*[-–—]\s*(?:(?:19|20)\d{2}|present)?\)",
    re.I,
)
_EXACT_YEAR_QUALIFIER_RE = re.compile(
    r"\(((?:19|20)\d{2})\)",
    re.I,
)
_AFTER_YEAR_RE = re.compile(
    r"^\s*\(((?:19|20)\d{2})(?:\s*[-–—]\s*(?:(?:19|20)\d{2}|present)?)?\)",
    re.I,
)


@dataclass(frozen=True)
class IssueIdentity:
    series: str
    number: str
    year: str = ""
    volume_year: str = ""

    @property
    def label(self) -> str:
        vol = (
            f" ({self.volume_year}-)"
            if self.volume_year and not self.year
            else (
                f" ({self.volume_year})"
                if self.volume_year and self.year and self.volume_year != self.year
                else ""
            )
        )
        yr = f" ({self.year})" if self.year else ""
        return f"{self.series}{vol} #{self.number}{yr}"


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

    prefix_part = value[:match.start()]
    suffix_part = value[match.end():]

    # Look for volume year before #N: (YYYY-) or (YYYY)
    ongoing_before = _ONGOING_QUALIFIER_RE.search(prefix_part)
    exact_before = _EXACT_YEAR_QUALIFIER_RE.search(prefix_part)
    volume_year = (
        ongoing_before.group(1)
        if ongoing_before
        else (exact_before.group(1) if exact_before else "")
    )

    series = _strip_qualifiers(prefix_part).strip(" :/&,\"'“”")
    if not series:
        return None

    # In suffix (after #N): check for multiple years (like 2025/2026)
    suffix_years = set(_YEAR_RE.findall(suffix_part))
    if len(suffix_years) > 1:
        return None

    ongoing_after = _ONGOING_QUALIFIER_RE.search(suffix_part)
    exact_after = _EXACT_YEAR_QUALIFIER_RE.search(suffix_part)

    year = ""
    if ongoing_after:
        if not volume_year:
            volume_year = ongoing_after.group(1)
    elif exact_after:
        year = exact_after.group(1)
    elif suffix_years:
        year = next(iter(suffix_years))

    return IssueIdentity(series, _normal_issue(match.group(1)), year, volume_year)


def _candidate_volume_evidence(candidate: Mapping[str, Any], given: IssueIdentity | None) -> str:
    """Extract verified volume start year from candidate identity and metadata."""
    if given and given.volume_year:
        return given.volume_year

    # Check explicit volume/series start year metadata fields
    for field in ("series_start_year", "volume_year", "series_year"):
        val = candidate.get(field)
        if val is not None:
            val_str = str(val).strip()
            m = _YEAR_RE.search(val_str)
            if m:
                return m.group()

    # Check series/comic name fields for volume qualifiers like (2016-) or (2016)
    for field in ("series", "comic", "source_comic"):
        val = candidate.get(field)
        if val and isinstance(val, str):
            ongoing = _ONGOING_QUALIFIER_RE.search(val)
            if ongoing:
                return ongoing.group(1)
            exact = _EXACT_YEAR_QUALIFIER_RE.search(val)
            if exact:
                return exact.group(1)

    return ""


def _intent_targets(user_intent: str) -> tuple[list[IssueIdentity], bool]:
    named_targets: list[IssueIdentity] = []
    bare_targets: list[tuple[str, str, str]] = []  # (number, year, volume_year)
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

        ongoing_before = _ONGOING_QUALIFIER_RE.search(segment_text)
        exact_before = _EXACT_YEAR_QUALIFIER_RE.search(segment_text)

        ongoing_after = _ONGOING_QUALIFIER_RE.match(suffix.strip())
        exact_after = _EXACT_YEAR_QUALIFIER_RE.match(suffix.strip())

        volume_year = ""
        year = ""

        if ongoing_before:
            volume_year = ongoing_before.group(1)
        elif exact_before:
            volume_year = exact_before.group(1)
            # If no year after #N, exact (YYYY) before #N is also the release year
            if not exact_after and not ongoing_after:
                year = exact_before.group(1)

        if ongoing_after:
            if not volume_year:
                volume_year = ongoing_after.group(1)
            after_match = _AFTER_YEAR_RE.match(suffix)
            last_end = match.end() + after_match.end() if after_match else match.end()
        elif exact_after:
            year = exact_after.group(1)
            after_match = _AFTER_YEAR_RE.match(suffix)
            last_end = match.end() + after_match.end() if after_match else match.end()
        else:
            last_end = match.end()

        issue_number = _normal_issue(match.group(1))

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
            named_targets.append(IssueIdentity(series, issue_number, year, volume_year))
        else:
            bare_targets.append((issue_number, year, volume_year))

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
            primary = IssueIdentity(other.series, primary.number, primary.year, primary.volume_year)

    # Check that all bare mentions agree with the resolved named issue number
    for bare_num, _, _ in bare_targets:
        if bare_num != primary.number:
            return [], True

    # Collect release years
    all_release_years: set[str] = set()
    for target in named_targets:
        if target.year:
            all_release_years.add(target.year)
    for _, bare_yr, _ in bare_targets:
        if bare_yr:
            all_release_years.add(bare_yr)

    if len(all_release_years) > 1:
        return [], True

    resolved_year = next(iter(all_release_years)) if all_release_years else ""

    # Collect volume years
    all_vol_years: set[str] = set()
    for target in named_targets:
        if target.volume_year:
            all_vol_years.add(target.volume_year)
    for _, _, bare_vol in bare_targets:
        if bare_vol:
            all_vol_years.add(bare_vol)

    if len(all_vol_years) > 1:
        return [], True

    resolved_vol_year = next(iter(all_vol_years)) if all_vol_years else ""

    # If both volume_year and release_year are present, check sanity (release >= volume)
    if resolved_year and resolved_vol_year:
        if int(resolved_year) < int(resolved_vol_year):
            return [], True

    primary = IssueIdentity(primary.series, primary.number, resolved_year, resolved_vol_year)
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

    # Release year constraint (if user specified an exact release year)
    if expected.year:
        if given.year:
            if given.year != expected.year:
                return f"Expected {expected.label}; candidate is {given.label} (year missing or different)."
        elif given.volume_year:
            if given.volume_year != expected.year:
                return f"Expected {expected.label}; candidate is {given.label} (year missing or different)."
        else:
            return f"Expected {expected.label}; candidate is {given.label} (year missing or different)."

    # Volume start year constraint (if user specified an ongoing/volume start year)
    if expected.volume_year:
        cand_volume = _candidate_volume_evidence(candidate, given)
        if cand_volume:
            if cand_volume != expected.volume_year:
                return (
                    f"Expected {expected.label}; candidate is {given.label} "
                    f"(volume start year different: expected {expected.volume_year}, got {cand_volume})."
                )
            if given.year and int(given.year) < int(expected.volume_year):
                return (
                    f"Expected {expected.label}; candidate is {given.label} "
                    f"(release year before volume start)."
                )
        else:
            # Candidate has no verified volume evidence.
            # If release year matches the volume start year exactly (e.g. #1 published at launch), accept.
            if given.year == expected.volume_year:
                pass
            elif given.year and int(given.year) < int(expected.volume_year):
                return (
                    f"Expected {expected.label}; candidate is {given.label} "
                    f"(release year before volume start)."
                )
            else:
                return (
                    f"Expected {expected.label}; candidate has no verified volume evidence "
                    f"confirming volume {expected.volume_year}: {given_text or '(missing)'}."
                )

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
        if expected.volume_year and title_target.volume_year and title_target.volume_year != expected.volume_year:
            return f"Expected {expected.label}; candidate title names a different volume: {title}."
    return None
