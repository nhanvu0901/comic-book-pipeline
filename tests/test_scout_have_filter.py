"""The --have list must bind (2026-08-09).

You.com IGNORES "do NOT repeat these" in the prompt: the 2026-08-05 micro run handed
back Immortal Hulk #1 and Venom #13 despite both being listed as already-found. Soft
constraints don't bind, so enumerate now hard-filters on our side. These tests pin the
issue matcher — both that it CATCHES repeats and, just as importantly, that it does not
silently eat a legitimately different issue.
"""
import pytest

from stages.youcom_scout import _issue_key, _same_issue


# ── the actual regression: same issue written two ways ───────────────────────

@pytest.mark.parametrize("a,b", [
    ("Immortal Hulk #1 (2018)", "Immortal Hulk #1"),
    ("Venom #13 (2019)", "Venom #13"),
    ("Venom (2018) #13", "Venom #13 (2019)"),
    ("The Amazing Spider-Man #798 (2018)", "Amazing Spider-Man #798"),
    ("Batman Annual #1 (2016)", "Batman Annual (Volume 3) #1"),
])
def test_same_issue_is_caught(a, b):
    assert _same_issue(a, b), f"{a!r} should match {b!r}"
    assert _same_issue(b, a), "matcher must be symmetric"


# ── must NOT collide: dropping these would hide real picks ───────────────────

@pytest.mark.parametrize("a,b", [
    ("Venom #13 (2019)", "Venom #1 (2018)"),          # different number
    ("Immortal Hulk #1", "Immortal Hulk #50"),
    ("Detective Comics #1000", "Action Comics #1000"),  # different series, same number
])
def test_different_issues_survive(a, b):
    assert not _same_issue(a, b)


def test_annual_is_not_the_main_series():
    """An Annual is a different comic from the ongoing. Containment-against-the-shorter
    scored this 1.0 (the shorter title is a strict subset), which would have dropped the
    Ace the Bat-Hound pick — Batman Annual #1 — if Batman #1 were in the have-list."""
    assert not _same_issue("Batman Annual #1 (2016)", "Batman #1 (2016)")
    assert not _same_issue("Batman #1 (2016)", "Batman Annual #1 (2016)")


def test_empty_never_matches():
    assert not _same_issue("", "Venom #13")
    assert not _same_issue("Venom #13", "")
    assert not _same_issue("", "")


# ── _issue_key ───────────────────────────────────────────────────────────────

def test_issue_key_pulls_the_number_and_drops_bare_years():
    toks, num = _issue_key("Immortal Hulk #1 (2018)")
    assert num == "1"
    assert "immortal" in toks and "hulk" in toks
    assert "2018" not in toks, "a bare year must not act as a series token"


def test_issue_key_without_a_number():
    toks, num = _issue_key("Batman: Damned")
    assert num == ""
    assert "batman" in toks and "damned" in toks
