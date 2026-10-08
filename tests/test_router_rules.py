import json
from pathlib import Path
import pytest

from stages.research_scout.router_schema import PrimaryMedium, VisualSource, RoutedItem, QuestionRouteResponse
from stages.research_scout.router_rules import resolve_media_route, route_question_to_pipeline


def test_resolve_media_route_rules():
    # Case 1: Has comic item -> always comic_qa
    items_comic = [
        RoutedItem(
            event="Death of Gwen Stacy in comics",
            primary_medium=PrimaryMedium.COMIC,
            visual_source=VisualSource.COMIC,
            confidence=0.9,
            reason="Comic canon"
        )
    ]
    assert resolve_media_route(items_comic, batcave_available=True, clips_found=True) == "comic_qa"
    assert resolve_media_route(items_comic, batcave_available=False, clips_found=True) == "comic_qa"

    # Case 2: Has mixed item -> always comic_qa (Safety Net)
    items_mixed = [
        RoutedItem(
            event="Civil War conflict",
            primary_medium=PrimaryMedium.MIXED,
            visual_source=VisualSource.BOTH,
            confidence=0.9,
            reason="Both in comics and film"
        )
    ]
    assert resolve_media_route(items_mixed, batcave_available=True, clips_found=True) == "comic_qa"
    assert resolve_media_route(items_mixed, batcave_available=False, clips_found=True) == "comic_qa"

    # Case 3: 100% Screen (film), Batcave fails, clips found -> screen_qa
    items_screen = [
        RoutedItem(
            event="Time heist in Endgame",
            primary_medium=PrimaryMedium.FILM,
            visual_source=VisualSource.YOUTUBE,
            confidence=0.95,
            reason="MCU film canon"
        )
    ]
    assert resolve_media_route(items_screen, batcave_available=False, clips_found=True) == "screen_qa"

    # Case 4: 100% Screen, but Batcave has comic issue -> comic_qa
    assert resolve_media_route(items_screen, batcave_available=True, clips_found=True) == "comic_qa"

    # Case 5: 100% Screen, Batcave fails, but NO clips found -> comic_qa (safe fallback)
    assert resolve_media_route(items_screen, batcave_available=False, clips_found=False) == "comic_qa"


def test_civil_war_safety_net():
    """
    Safety-net case: 'Why did Captain America and Iron Man fight?'
    Even if search mentions the movie, ground truth is both/mixed and comics exist -> comic_qa.
    """
    items = [
        RoutedItem(
            event="Stamford incident vs Sokovia Accords",
            primary_medium=PrimaryMedium.MIXED,
            visual_source=VisualSource.BOTH,
            adaptation_title="Civil War (2006) / Captain America: Civil War (2016)",
            confidence=0.95,
            reason="Major comic event adapted into movie"
        )
    ]
    route = resolve_media_route(items, batcave_available=True, clips_found=True)
    assert route == "comic_qa"


def test_neutral_dataset_offline_evaluation():
    """Evaluate against the neutral followup_definitions.json rules."""
    def_file = Path("/tmp/source_router/followup_definitions.json")
    if not def_file.exists():
        pytest.skip("/tmp/source_router/followup_definitions.json not found")

    data = json.loads(def_file.read_text())
    set_a = data.get("set_a", [])

    for q in set_a:
        exp_med = q["expected_primary_medium"]
        cat = q["ground_truth_category"]

        # Synthesize routed items according to expected ground truth
        if exp_med == "mixed" or cat == "both":
            routed_items = [
                RoutedItem(
                    event=q["question"],
                    primary_medium=PrimaryMedium.MIXED,
                    visual_source=VisualSource.BOTH,
                    confidence=0.9,
                    reason=q.get("notes", "")
                )
            ]
            route = resolve_media_route(routed_items, batcave_available=True, clips_found=True)
            assert route == "comic_qa", f"Expected comic_qa for both/mixed question {q['id']}"

        elif exp_med in ("film", "tv_animation") or cat == "screen_only":
            routed_items = [
                RoutedItem(
                    event=q["question"],
                    primary_medium=PrimaryMedium(exp_med),
                    visual_source=VisualSource.YOUTUBE,
                    confidence=0.95,
                    reason=q.get("notes", "")
                )
            ]
            # When batcave check fails and clips found:
            route = resolve_media_route(routed_items, batcave_available=False, clips_found=True)
            assert route == "screen_qa", f"Expected screen_qa for screen-only question {q['id']}"

        elif exp_med == "comic" or cat == "comic_only":
            routed_items = [
                RoutedItem(
                    event=q["question"],
                    primary_medium=PrimaryMedium.COMIC,
                    visual_source=VisualSource.COMIC,
                    confidence=0.95,
                    reason=q.get("notes", "")
                )
            ]
            route = resolve_media_route(routed_items, batcave_available=True, clips_found=False)
            assert route == "comic_qa", f"Expected comic_qa for comic-only question {q['id']}"
