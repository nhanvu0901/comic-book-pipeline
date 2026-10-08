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


# ── ping_pages on REAL discover_issues dicts + series-level check (M9) ─────────
# utils.comic_scraper.discover_issues returns {"title","url","chapter_id","number","date"}
# — no "news_id" and no "id". The ping must derive both ids from what is really there.

def _real_issue(news_id, chapter_id, number, title):
    return {"title": title, "url": f"https://batcave.biz/reader/{news_id}/{chapter_id}",
            "chapter_id": chapter_id, "number": float(number), "date": "01.01.2020"}


def _civil_war_mocks():
    hits = [("777", "civil-war-2006", "https://batcave.biz/777-civil-war-2006.html")]
    issues = [_real_issue(777, 9001, 1, "Civil War (2006) #1"),
              _real_issue(777, 9002, 2, "Civil War (2006) #2")]
    return hits, issues


def test_ping_pages_uses_ids_from_real_discover_issues_shape():
    hits, issues = _civil_war_mocks()
    calls = []

    def fake_ping(reader_url, news_id, chapter_id):
        calls.append((news_id, chapter_id))
        return ["p1.jpg"]

    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=hits), \
         patch("stages.stage_1.batcave_verifier.discover_issues", return_value=issues), \
         patch("utils.comic_scraper.readcomiconline._ajax_chapter_images", side_effect=fake_ping):
        assert verify_batcave_issue("Civil War", 1, year=2006, ping_pages=True) is True
    assert calls == [("777", 9001)]


def test_ping_pages_failure_means_not_verified():
    hits, issues = _civil_war_mocks()
    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=hits), \
         patch("stages.stage_1.batcave_verifier.discover_issues", return_value=issues), \
         patch("utils.comic_scraper.readcomiconline._ajax_chapter_images", return_value=[]):
        assert verify_batcave_issue("Civil War", 1, year=2006, ping_pages=True) is False


def test_check_with_issue_reports_issue_level():
    from stages.stage_1.batcave_verifier import check_batcave_comic
    hits, issues = _civil_war_mocks()
    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=hits), \
         patch("stages.stage_1.batcave_verifier.discover_issues", return_value=issues), \
         patch("utils.comic_scraper.readcomiconline._ajax_chapter_images", return_value=["p1.jpg"]):
        res = check_batcave_comic("Civil War", 1, 2006)
    assert res.found is True and res.level == "issue"
    assert (res.series, res.issue, res.year) == ("Civil War", 1, 2006)


def test_check_without_issue_verifies_series_and_year_and_says_so():
    from stages.stage_1.batcave_verifier import check_batcave_comic
    hits, issues = _civil_war_mocks()
    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=hits), \
         patch("stages.stage_1.batcave_verifier.discover_issues", return_value=issues):
        res = check_batcave_comic("Civil War", None, 2006)
    assert res.found is True
    assert res.level == "series"
    assert res.issue is None            # never invented
    assert "issue number unknown" in res.note.lower()
    assert "series" in res.describe().lower() and "unknown" in res.describe().lower()


def test_series_level_check_rejects_a_different_volume_year():
    from stages.stage_1.batcave_verifier import check_batcave_comic
    hits = [("456", "civil-war-2018", "https://batcave.biz/456-civil-war-2018.html")]
    issues = [_real_issue(456, 10, 1, "Civil War (2018) #1")]
    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=hits), \
         patch("stages.stage_1.batcave_verifier.discover_issues", return_value=issues):
        assert check_batcave_comic("Civil War", None, 2006).found is False


def test_series_level_check_without_any_hit_is_not_found():
    from stages.stage_1.batcave_verifier import check_batcave_comic
    with patch("stages.stage_1.batcave_verifier._batcave_search", return_value=[]):
        assert check_batcave_comic("No Such Series", None, None).found is False


def test_check_never_raises_on_network_errors():
    from stages.stage_1.batcave_verifier import check_batcave_comic
    with patch("stages.stage_1.batcave_verifier._batcave_search", side_effect=RuntimeError("boom")):
        res = check_batcave_comic("Civil War", None, 2006)
    assert res.found is False


def test_check_with_blank_series_is_not_found_without_searching():
    from stages.stage_1.batcave_verifier import check_batcave_comic
    with patch("stages.stage_1.batcave_verifier._batcave_search") as search:
        assert check_batcave_comic("  ", 1, 2006).found is False
    search.assert_not_called()
