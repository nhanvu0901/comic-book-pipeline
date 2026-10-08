import json
import pytest
from pydantic import ValidationError

from stages.research_scout.router_schema import (
    PrimaryMedium,
    VisualSource,
    RoutedItem,
    QuestionRouteResponse,
)


def test_schema_validation_valid_json():
    valid_data = {
        "question": "How did Gwen Stacy die?",
        "items": [
            {
                "event": "Neck snap from Spider-Man web whiplash",
                "primary_medium": "comic",
                "visual_source": "comic",
                "adaptation_title": "The Amazing Spider-Man #121",
                "evidence_urls": ["https://marvel.fandom.com/wiki/Amazing_Spider-Man_Vol_1_121"],
                "confidence": 0.95,
                "reason": "Original comic event where Gwen Stacy dies by whiplash.",
            },
            {
                "event": "Clock tower fall in movie",
                "primary_medium": "film",
                "visual_source": "youtube",
                "adaptation_title": "The Amazing Spider-Man 2 (2014)",
                "evidence_urls": ["https://en.wikipedia.org/wiki/The_Amazing_Spider-Man_2"],
                "confidence": 0.90,
                "reason": "Film adaptation depiction.",
            }
        ]
    }
    resp = QuestionRouteResponse.model_validate(valid_data)
    assert resp.question == "How did Gwen Stacy die?"
    assert len(resp.items) == 2
    assert resp.items[0].primary_medium == PrimaryMedium.COMIC
    assert resp.items[0].visual_source == VisualSource.COMIC
    assert resp.items[1].primary_medium == PrimaryMedium.FILM
    assert resp.items[1].visual_source == VisualSource.YOUTUBE


def test_schema_rejects_invalid_enums():
    invalid_medium = {
        "question": "Invalid test",
        "items": [
            {
                "event": "Test event",
                "primary_medium": "novel",  # Not in enum
                "visual_source": "comic",
                "evidence_urls": [],
                "confidence": 0.8,
                "reason": "Invalid medium",
            }
        ]
    }
    with pytest.raises(ValidationError):
        QuestionRouteResponse.model_validate(invalid_medium)


def test_schema_rejects_invalid_confidence():
    invalid_conf = {
        "question": "Invalid test",
        "items": [
            {
                "event": "Test event",
                "primary_medium": "comic",
                "visual_source": "comic",
                "evidence_urls": [],
                "confidence": 1.5,  # > 1.0
                "reason": "Invalid confidence",
            }
        ]
    }
    with pytest.raises(ValidationError):
        QuestionRouteResponse.model_validate(invalid_conf)


# ── typed comic reference (M9 / M14): refs come from the LLM's typed output ──

def _item(**extra):
    base = {
        "event": "Some event",
        "primary_medium": "film",
        "visual_source": "youtube",
        "confidence": 0.9,
        "reason": "r",
    }
    base.update(extra)
    return RoutedItem.model_validate(base)


def test_comic_reference_fields_default_to_none():
    item = _item()
    assert item.comic_series is None
    assert item.comic_issue is None
    assert item.comic_year is None


def test_comic_reference_fields_roundtrip():
    item = _item(comic_series="Civil War", comic_issue=1, comic_year=2006)
    again = RoutedItem.model_validate_json(item.model_dump_json())
    assert (again.comic_series, again.comic_issue, again.comic_year) == ("Civil War", 1, 2006)


def test_comic_issue_and_year_are_coerced_from_strings():
    item = _item(comic_series="X", comic_issue="#121", comic_year="1973")
    assert item.comic_issue == 121
    assert item.comic_year == 1973


@pytest.mark.parametrize("junk", ["unknown", "N/A", "", "1-3", "TBD", None])
def test_unusable_comic_issue_becomes_none_instead_of_failing_validation(junk):
    # An unsure model must be able to say "I don't know the issue" without
    # costing a retry — and the router must never turn that into a made-up #1.
    item = _item(comic_series="X", comic_issue=junk)
    assert item.comic_issue is None


@pytest.mark.parametrize("junk", ["unknown", "", "c. 1990s", "20", None, 12345])
def test_unusable_comic_year_becomes_none(junk):
    assert _item(comic_series="X", comic_year=junk).comic_year is None


def test_blank_comic_series_becomes_none():
    assert _item(comic_series="   ").comic_series is None


def test_json_schema_given_to_the_llm_mentions_the_comic_reference_fields():
    props = QuestionRouteResponse.model_json_schema()["$defs"]["RoutedItem"]["properties"]
    assert {"comic_series", "comic_issue", "comic_year"} <= set(props)
