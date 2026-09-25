import pytest

from art_pipeline.sources import met
from stages.user_errors import UserFacingError

FIXTURE = {
    "objectID": 436535,
    "isPublicDomain": True,
    "primaryImage": "https://images.metmuseum.org/CRDImages/ep/original/DT1567.jpg",
    "title": "Wheat Field with Cypresses",
    "artistDisplayName": "Vincent van Gogh",
    "objectDate": "1889",
    "department": "European Paintings",
    "creditLine": "Purchase, The Annenberg Foundation Gift, 1993",
    "objectURL": "https://www.metmuseum.org/art/collection/search/436535",
    "medium": "Oil on canvas",
}


def test_validate_cc0_passes_pd_with_image():
    ok, why = met.validate_cc0(FIXTURE)
    assert ok and why == ""


def test_validate_cc0_refuses_non_pd():
    bad = dict(FIXTURE, isPublicDomain=False)
    ok, why = met.validate_cc0(bad)
    assert not ok and "NOT public domain" in why


def test_validate_cc0_refuses_missing_image():
    bad = dict(FIXTURE, primaryImage="")
    ok, why = met.validate_cc0(bad)
    assert not ok and "no primaryImage" in why


def test_parse_candidate_extracts_fields():
    c = met.parse_candidate(FIXTURE)
    assert c["object_id"] == 436535
    assert c["artist"] == "Vincent van Gogh"
    assert c["object_url"].endswith("/436535")
    assert c["credit_line"].startswith("Purchase")


def test_fetch_image_no_primary_image_is_user_facing(tmp_path):
    meta = dict(FIXTURE, primaryImage="")
    with pytest.raises(ValueError) as caught:
        met.fetch_image(meta, tmp_path / "out.jpg")
    assert isinstance(caught.value, UserFacingError)
