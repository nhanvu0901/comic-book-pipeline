from unittest.mock import patch, MagicMock
import pytest

from stages.stage_1.batcave_verifier import verify_batcave_issue


def test_batcave_verifier_mock_positive():
    mock_hits = [("123", "amazing-spider-man-1963", "https://batcave.biz/123-amazing-spider-man-1963.html")]
    mock_issues = [
        {"id": "1", "news_id": "123", "title": "Amazing Spider-Man (1963) Issue #121", "url": "https://batcave.biz/reader/123/1"},
        {"id": "2", "news_id": "123", "title": "Amazing Spider-Man (1963) Issue #122", "url": "https://batcave.biz/reader/123/2"},
    ]

    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=mock_hits), \
         patch("stages.stage_1.batcave_verifier.discover_issues", return_value=mock_issues):
        ok = verify_batcave_issue("Amazing Spider-Man", 121, year=1963)
        assert ok is True


def test_batcave_verifier_mock_false_positive_prevention_wrong_year():
    # Candidate slug year is 2018, requested year is 1963
    mock_hits = [("456", "amazing-spider-man-2018", "https://batcave.biz/456-amazing-spider-man-2018.html")]
    mock_issues = [
        {"id": "10", "news_id": "456", "title": "Amazing Spider-Man (2018) Issue #121", "url": "https://batcave.biz/reader/456/10"}
    ]

    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=mock_hits), \
         patch("stages.stage_1.batcave_verifier.discover_issues", return_value=mock_issues):
        # Even if issue #121 somehow exists in 2018, year mismatch (1963 vs 2018) rejects it
        ok = verify_batcave_issue("Amazing Spider-Man", 121, year=1963)
        assert ok is False


def test_batcave_verifier_mock_missing_issue():
    mock_hits = [("123", "amazing-spider-man-1963", "https://batcave.biz/123-amazing-spider-man-1963.html")]
    mock_issues = [
        {"id": "1", "news_id": "123", "title": "Amazing Spider-Man (1963) Issue #1", "url": "https://batcave.biz/reader/123/1"}
    ]

    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=mock_hits), \
         patch("stages.stage_1.batcave_verifier.discover_issues", return_value=mock_issues):
        ok = verify_batcave_issue("Amazing Spider-Man", 999, year=1963)
        assert ok is False


@pytest.mark.integration
def test_batcave_verifier_live_network():
    # Live network test for true positive
    ok = verify_batcave_issue("Amazing Spider-Man", 121, year=1963)
    assert ok is True

    # Live network test for false positive prevention: ASM 2018 only ran ~93 issues, #121 does not exist
    ok_false = verify_batcave_issue("Amazing Spider-Man", 121, year=2018)
    assert ok_false is False
