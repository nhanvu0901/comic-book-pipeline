import json

import pytest

from art_pipeline import grounding
from stages.user_errors import InsufficientGroundingError, UserFacingError

MET = {"objectID": 1, "title": "T", "artistDisplayName": "A", "objectDate": "1880",
       "medium": "Oil on canvas", "creditLine": "Gift", "department": "D",
       "objectURL": "http://met/1", "isPublicDomain": True, "primaryImage": "http://x.jpg",
       "culture": "", "period": ""}


def test_merge_grounding_concats_and_picks_primary_url():
    text, url = grounding.merge_grounding(
        MET, {"text": "About the painting. " * 20, "url": "http://wiki/T"},
        {"text": "About the artist. " * 20, "url": "http://wiki/A"})
    assert "About the painting." in text and "About the artist." in text
    assert "Oil on canvas" in text          # met meta facts included
    assert url == "http://wiki/T"           # artwork article wins over artist


def test_merge_grounding_artist_url_when_no_artwork_article():
    text, url = grounding.merge_grounding(MET, None, {"text": "x" * 700, "url": "http://wiki/A"})
    assert url == "http://wiki/A"


def test_needs_sdk_fallback_threshold():
    assert grounding.needs_sdk_fallback("short text") is True
    assert grounding.needs_sdk_fallback("x" * 2000) is False


def test_build_summary_block_lists_artist_as_character():
    s = grounding.build_summary_block([MET])
    names = [c["name"] for c in s["characters"]]
    assert "A" in names


def test_build_art_context_too_thin_is_user_facing(tmp_path, monkeypatch):
    """Grounding below ART_GROUNDING_MIN_CHARS with no wiki hit and no SDK
    fallback must reach the app as copy telling the user to pick another
    artwork, not a traceback."""
    monkeypatch.setattr(grounding, "get_art_project_path", lambda n: tmp_path)
    monkeypatch.setattr(grounding, "fetch_wikipedia_extract", lambda title, **k: None)
    monkeypatch.setattr(grounding, "gather_art_story_sdk", lambda *a, **k: None)
    (tmp_path / "selection.json").write_text(json.dumps({"object_ids": [1], "theme": ""}))
    (tmp_path / "met_meta_1.json").write_text(json.dumps(MET))

    with pytest.raises(ValueError) as caught:
        grounding.build_art_context("p")

    assert isinstance(caught.value, (InsufficientGroundingError, UserFacingError))
