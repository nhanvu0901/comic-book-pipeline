from art_pipeline import grounding

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
