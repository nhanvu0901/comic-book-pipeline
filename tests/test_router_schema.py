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
