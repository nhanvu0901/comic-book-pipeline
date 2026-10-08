"""Deterministic Decision Rules for Media Source Routing."""

from __future__ import annotations

import logging
from typing import Any, Callable

from .router_schema import PrimaryMedium, VisualSource, RoutedItem, QuestionRouteResponse
from .media_router import route_media_source
from stages.stage_1.batcave_verifier import verify_batcave_issue
from stages.clip_fetch import youtube_search

logger = logging.getLogger(__name__)


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

    has_comic_or_mixed = any(
        item.primary_medium in (PrimaryMedium.COMIC, PrimaryMedium.MIXED)
        for item in routed_items
    )

    if not has_comic_or_mixed and not batcave_available and clips_found:
        return "screen_qa"

    return "comic_qa"


def route_question_to_pipeline(
    question: str,
    *,
    search_results: list[dict] | None = None,
    client: Any = None,
    log: Callable[[str], None] = logger.info,
) -> tuple[str, QuestionRouteResponse | None]:
    """Route a question to either 'comic_qa' or 'screen_qa' using LLM + deterministic checks.

    Returns (pipeline_name, route_response).
    """
    if not search_results:
        # If no search results provided, use clip_fetch / You.com search
        from stages.research_scout.youcom import YouComClient
        import config
        api_key = config.YDC_API_KEY
        if api_key:
            yc = YouComClient(api_key=api_key, timeout=30)
            res = yc.search(question)
            if res.ok and isinstance(res.payload, dict):
                search_results = res.payload.get("hits") or []
        if not search_results:
            search_results = []

    try:
        route_resp = route_media_source(question, search_results, client=client)
    except Exception as exc:
        log(f"[router] LLM routing failed: {exc} — defaulting to comic_qa")
        return "comic_qa", None

    items = route_resp.items
    has_comic_or_mixed = any(
        item.primary_medium in (PrimaryMedium.COMIC, PrimaryMedium.MIXED)
        for item in items
    ) if items else True

    if has_comic_or_mixed:
        log(f"[router] Question '{question}' has comic/mixed elements -> comic_qa")
        return "comic_qa", route_resp

    # Check Batcave availability for any title mentioned
    batcave_available = False
    for item in items:
        title = item.adaptation_title or item.event
        if title:
            # Check if title exists on Batcave
            if verify_batcave_issue(title, 1, log=log):
                batcave_available = True
                break

    if batcave_available:
        log(f"[router] Batcave issue found for '{question}' -> comic_qa")
        return "comic_qa", route_resp

    # Check YouTube clips availability
    clips_found = False
    try:
        yt_hits = youtube_search(f"{question} clip")
        if yt_hits:
            clips_found = True
    except Exception as exc:
        log(f"[router] YouTube search failed: {exc}")

    route = resolve_media_route(items, batcave_available=batcave_available, clips_found=clips_found)
    log(f"[router] Resolved route for '{question}': {route} (batcave={batcave_available}, clips={clips_found})")
    return route, route_resp
