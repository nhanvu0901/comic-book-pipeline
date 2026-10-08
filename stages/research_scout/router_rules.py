"""Deterministic Decision Rules for Media Source Routing."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .router_schema import PrimaryMedium, VisualSource, RoutedItem, QuestionRouteResponse
from .media_router import route_media_source
from .youcom import YouComClient
from stages.stage_1.batcave_verifier import BatcaveCheck, check_batcave_comic
from stages.clip_fetch import youtube_search

logger = logging.getLogger(__name__)


def _wants_comic(item: RoutedItem) -> bool:
    """An item that lives in comics, in both media, or still asks for comic visuals."""
    return (
        item.primary_medium in (PrimaryMedium.COMIC, PrimaryMedium.MIXED)
        or item.visual_source in (VisualSource.COMIC, VisualSource.BOTH)
    )


def resolve_media_route(
    routed_items: list[RoutedItem],
    batcave_available: bool,
    clips_found: bool,
) -> str:
    """Deterministic routing decision rule.

    screen_qa ONLY IF:
      1. No item is comic or mixed (all items are screen: film / tv_animation / game)
      2. Batcave issue check fails (batcave_available is False)
      3. YouTube video clips are found (clips_found is True)

    OTHERWISE:
      comic_qa (default safe route)
    """
    if not routed_items:
        return "comic_qa"

    if any(_wants_comic(item) for item in routed_items):
        return "comic_qa"

    if not batcave_available and clips_found:
        return "screen_qa"

    return "comic_qa"


@dataclass
class RouteDecision:
    """The route plus everything that led to it (written to media_route.json)."""

    route: str
    response: QuestionRouteResponse | None = None
    batcave_checks: list[BatcaveCheck] = field(default_factory=list)
    clips_found: bool | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "clips_found": self.clips_found,
            "reasons": list(self.reasons),
            "batcave_checks": [
                {"found": c.found, "level": c.level, "series": c.series, "issue": c.issue,
                 "year": c.year, "note": c.note}
                for c in self.batcave_checks
            ],
            "items": [i.model_dump(mode="json") for i in (self.response.items if self.response else [])],
        }


def web_search_evidence(question: str, *, log: Callable[[str], None] = logger.info) -> list[dict]:
    """You.com web results for the router prompt ([{url,title,snippets}]). Never raises:
    no key / no network / an error response all mean "no evidence" (the LLM still runs)."""
    try:
        res = YouComClient(timeout=30).search(question, None)
    except Exception as exc:  # noqa: BLE001
        log(f"[router] Web search failed: {exc}")
        return []
    if not getattr(res, "ok", False):
        log(f"[router] Web search returned no evidence: {getattr(res, 'error', None)}")
        return []
    payload = res.payload if isinstance(res.payload, dict) else {}
    results = payload.get("results")
    web = results.get("web") if isinstance(results, dict) else results
    return [w for w in (web or []) if isinstance(w, dict)]


def _comic_refs(items: list[RoutedItem]) -> list[tuple[str, int | None, int | None]]:
    """Distinct (series, issue, year) comic references the LLM typed for the items. Items with
    no `comic_series` have no comic counterpart as far as the router knows — nothing is
    parsed out of titles or guessed (no default issue)."""
    seen: list[tuple[str, int | None, int | None]] = []
    for item in items:
        if not item.comic_series:
            continue
        ref = (item.comic_series, item.comic_issue, item.comic_year)
        if ref not in seen:
            seen.append(ref)
    return seen


def decide_route(
    question: str,
    *,
    search_results: list[dict] | None = None,
    client: Any = None,
    log: Callable[[str], None] = logger.info,
) -> RouteDecision:
    """Route a question to 'comic_qa' or 'screen_qa': LLM classification, then the
    deterministic rule (comic first; screen_qa only when no comic counterpart is on
    Batcave and clips exist)."""
    if search_results is None:
        search_results = web_search_evidence(question, log=log)

    try:
        route_resp = route_media_source(question, search_results, client=client)
    except Exception as exc:  # noqa: BLE001
        reason = f"LLM routing failed: {exc}"
        log(f"[router] {reason} — defaulting to comic_qa")
        return RouteDecision("comic_qa", None, reasons=[reason + " — default comic_qa"])

    items = route_resp.items
    if not items:
        reason = "router returned no items — default comic_qa"
        log(f"[router] {reason}")
        return RouteDecision("comic_qa", route_resp, reasons=[reason])

    if any(_wants_comic(item) for item in items):
        reason = "comic/mixed element present -> comic_qa"
        log(f"[router] Question '{question}': {reason}")
        return RouteDecision("comic_qa", route_resp, reasons=[reason])

    decision = RouteDecision("comic_qa", route_resp)
    refs = _comic_refs(items)
    if not refs:
        decision.reasons.append("no comic counterpart named by the router (comic_series null on every item)")

    for series, issue, year in refs:
        check = check_batcave_comic(series, issue, year, ping_pages=True, log=log)
        decision.batcave_checks.append(check)
        log(f"[router] batcave: {check.describe()}")
        if check.found:
            break

    if any(c.found for c in decision.batcave_checks):
        found = next(c for c in decision.batcave_checks if c.found)
        reason = f"comic counterpart on batcave ({found.describe()}) -> comic_qa"
        decision.reasons.append(reason)
        log(f"[router] Question '{question}': {reason}")
        return decision

    inconclusive = [c for c in decision.batcave_checks if c.inconclusive]
    if inconclusive:
        # A lookup that could not finish says nothing about the comic: never let it read as
        # "not on batcave" (that would send a comic-canon question to screen_qa).
        reason = f"batcave lookup inconclusive ({inconclusive[0].describe()}) -> comic_qa"
        decision.reasons.append(reason)
        log(f"[router] Question '{question}': {reason}")
        return decision

    clips_found = False
    try:
        clips_found = bool(youtube_search(f"{question} clip"))
    except Exception as exc:  # noqa: BLE001
        log(f"[router] YouTube search failed: {exc}")
        decision.reasons.append(f"clip search failed: {exc}")
    decision.clips_found = clips_found

    decision.route = resolve_media_route(items, batcave_available=False, clips_found=clips_found)
    decision.reasons.append(f"no comic on batcave, clips_found={clips_found} -> {decision.route}")
    log(f"[router] Resolved route for '{question}': {decision.route} "
        f"(checks={len(decision.batcave_checks)}, clips={clips_found})")
    return decision


def route_question_to_pipeline(
    question: str,
    *,
    search_results: list[dict] | None = None,
    client: Any = None,
    log: Callable[[str], None] = logger.info,
) -> tuple[str, QuestionRouteResponse | None]:
    """Route a question to either 'comic_qa' or 'screen_qa' using LLM + deterministic checks.

    Returns (pipeline_name, route_response). Use `decide_route` for the full audit trail.
    """
    decision = decide_route(question, search_results=search_results, client=client, log=log)
    return decision.route, decision.response
