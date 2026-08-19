"""Deterministic provenance and candidate-shape gates for scout results."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import re
from typing import Any

from .models import ScoutMode
from .youcom import RawCall


class GateFlag(str, Enum):
    EXACT_ISSUE_REQUIRED = "exact_issue_required"
    NO_VISUAL_EVENT = "no_visual_event"
    URL_NOT_RETURNED = "url_not_returned"
    DUPLICATE = "duplicate"
    MALFORMED_OUTPUT = "malformed_output"
    MALFORMED = "malformed_output"


def validate_candidate(candidate: Any, raw_call: RawCall, mode: ScoutMode) -> list[GateFlag]:
    """Return stable hard-gate flags without trusting model claims over raw evidence."""

    flags: list[GateFlag] = []
    data = _candidate_mapping(candidate)
    try:
        selected_mode = mode if isinstance(mode, ScoutMode) else ScoutMode(mode)
    except (TypeError, ValueError):
        selected_mode = None

    if data is None or not isinstance(raw_call, RawCall) or selected_mode is None:
        return [GateFlag.MALFORMED_OUTPUT]

    if not _has_exact_issue_and_year(data):
        flags.append(GateFlag.EXACT_ISSUE_REQUIRED)
    if selected_mode is ScoutMode.MICRO and not _has_visual_event(data):
        flags.append(GateFlag.NO_VISUAL_EVENT)

    returned_urls, malformed_sources = _returned_urls(raw_call)
    if malformed_sources or raw_call.error is not None:
        flags.append(GateFlag.MALFORMED_OUTPUT)
    cited_urls = _candidate_urls(data)
    if not cited_urls or any(url not in returned_urls for url in cited_urls):
        flags.append(GateFlag.URL_NOT_RETURNED)

    if _is_duplicate(data):
        flags.append(GateFlag.DUPLICATE)
    return flags


def _candidate_mapping(candidate: Any) -> Mapping[str, Any] | None:
    if isinstance(candidate, Mapping):
        return candidate
    model_dump = getattr(candidate, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
        return value if isinstance(value, Mapping) else None
    return None


def _has_exact_issue_and_year(candidate: Mapping[str, Any]) -> bool:
    value = str(candidate.get("series_issue_year", "")).strip()
    has_issue = bool(re.search(r"(?:#|\bissue\s*)\s*\d+", value, re.IGNORECASE))
    has_year = bool(re.search(r"\b(?:19|20)\d{2}\b", value))
    return has_issue and has_year


def _has_visual_event(candidate: Mapping[str, Any]) -> bool:
    for key in ("visible_event", "what_visibly_happens", "moment"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _candidate_urls(candidate: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("evidence_urls", "source_urls"):
        urls = candidate.get(key, [])
        if isinstance(urls, str):
            urls = [urls]
        if isinstance(urls, list):
            values.extend(str(url).strip() for url in urls if str(url).strip())
    return values


def _returned_urls(raw_call: RawCall) -> tuple[set[str], bool]:
    payload = raw_call.payload
    if not isinstance(payload, Mapping):
        return set(), True
    if raw_call.api == "research":
        output = payload.get("output")
        if not isinstance(output, Mapping):
            return set(), True
        sources = output.get("sources")
        if not isinstance(sources, list):
            return set(), True
        urls: set[str] = set()
        malformed = False
        for source in sources:
            if not isinstance(source, Mapping):
                malformed = True
                continue
            url = source.get("url")
            if url is not None:
                if not isinstance(url, str):
                    malformed = True
                elif url.strip():
                    urls.add(url.strip())
        return urls, malformed
    if raw_call.api == "search":
        results = payload.get("results")
        if isinstance(results, Mapping):
            urls: set[str] = set()
            _collect_urls(results, urls)
            return urls, False
        if not isinstance(results, list):
            return set(), True
        urls: set[str] = set()
        malformed = False
        for result in results:
            if not isinstance(result, Mapping):
                malformed = True
                continue
            _collect_urls(result, urls)
        return urls, malformed
    return set(), True


def _collect_urls(value: Any, urls: set[str]) -> None:
    if isinstance(value, Mapping):
        url = value.get("url")
        if isinstance(url, str) and url.strip():
            urls.add(url.strip())
        for child in value.values():
            _collect_urls(child, urls)
    elif isinstance(value, list):
        for child in value:
            _collect_urls(child, urls)


def _is_duplicate(candidate: Mapping[str, Any]) -> bool:
    for key in ("duplicate", "is_duplicate", "_duplicate", "duplicate_of"):
        value = candidate.get(key)
        if value not in (None, False, "", [], {}):
            return True
    for key in ("evidence_urls", "source_urls"):
        urls = candidate.get(key)
        if isinstance(urls, list) and len(urls) != len(set(map(str, urls))):
            return True
    return False
