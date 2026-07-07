"""Tests for stages/stage_1/comicvine.py — the Q&A Comic Vine cross-check (FIX D).

No network: verify_issue's graceful path (empty key) short-circuits before any
HTTP call, and the pure helpers (_pick_volume / _name_matches) are exercised
directly. The real-API happy path is covered by a live smoke, not here.
"""
import stages.stage_1.comicvine as cv


def test_verify_issue_graceful_when_no_key(monkeypatch):
    # Empty key => never touches the network, returns a non-blocking "unverified".
    monkeypatch.setattr(cv, "COMIC_VINE_API_KEY", "")
    # If it tried to call the API, this would blow up loudly instead of sleeping.
    monkeypatch.setattr(cv, "_cv_get", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("_cv_get must not be called when the key is empty")))
    r = cv.verify_issue("Deadpool", "26", "2010", "Deadpool", log=lambda _m: None)
    assert r["ok"] is True
    assert r["note"].startswith("unverified")


def test_verify_issue_missing_series_is_unverified(monkeypatch):
    monkeypatch.setattr(cv, "COMIC_VINE_API_KEY", "fake-key")
    monkeypatch.setattr(cv, "_cv_get", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("_cv_get must not be called without a series/issue")))
    r = cv.verify_issue("", "26", "2010", "Deadpool", log=lambda _m: None)
    assert r["ok"] is True and r["note"].startswith("unverified")


def test_verify_issue_flags_absent_character(monkeypatch):
    """Issue found but the character isn't in credits => ok=False (flag, not raise)."""
    monkeypatch.setattr(cv, "COMIC_VINE_API_KEY", "fake-key")

    def fake_get(resource, params, key):
        if resource == "volumes/":
            return {"results": [{"id": 1, "name": "Deadpool", "start_year": "2008"}]}
        if resource == "issues/":
            return {"results": [{"id": 42, "cover_date": "2010-05-01", "issue_number": "26"}]}
        return {"results": {"cover_date": "2010-05-01",
                            "character_credits": [{"name": "Wolverine"}]}}

    monkeypatch.setattr(cv, "_cv_get", fake_get)
    r = cv.verify_issue("Deadpool", "26", "2010", "Deadpool", log=lambda _m: None)
    assert r["ok"] is False
    assert r["character_present"] is False
    assert r["matched_issue_id"] == 42


def test_verify_issue_network_error_is_unverified(monkeypatch):
    monkeypatch.setattr(cv, "COMIC_VINE_API_KEY", "fake-key")
    monkeypatch.setattr(cv, "_cv_get", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    r = cv.verify_issue("Deadpool", "26", "2010", "Deadpool", log=lambda _m: None)
    assert r["ok"] is True and r["note"].startswith("unverified")


def test_pick_volume_prefers_running_volume():
    vols = [
        {"id": 1, "name": "Deadpool", "start_year": "1997"},
        {"id": 2, "name": "Deadpool", "start_year": "2008"},
        {"id": 3, "name": "Deadpool", "start_year": "2012"},
    ]
    assert cv._pick_volume(vols, "Deadpool", "2010")["id"] == 2
    assert cv._pick_volume(vols, "Deadpool", "1990")["id"] == 1
    assert cv._pick_volume([], "Deadpool", "2010") is None


def test_name_matches_lenient():
    assert cv._name_matches("The Punisher", ["Punisher"]) is True
    assert cv._name_matches("Danny Ketch", ["Daniel Ketch"]) is True
    assert cv._name_matches("Deadpool", ["Wolverine", "Cable"]) is False
