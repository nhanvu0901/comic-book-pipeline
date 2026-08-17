"""Guards for `youcom_scout micro` — scouting single MOMENTS, not questions.

The module's other stages hunt a QUESTION whose answer spans 3+ different comics. A micro
moment is the opposite shape: one drawn beat inside one issue. Reusing discover's prompt
returns listicles, which is exactly what a one-off run outside the repo produced before this
subcommand existed.
"""
import json

import pytest

from stages import youcom_scout as Y


def test_micro_asks_for_one_issue_not_a_list(tmp_path, monkeypatch):
    """The single most important difference from discover: never ask for 3+ comics."""
    sent = []
    monkeypatch.setattr(Y, "build_scouted_digest", lambda: "DIGEST")
    monkeypatch.setattr(Y, "_call_logged",
                        lambda k, prompt, e, s, o, t: sent.append(prompt) or {})

    Y.run_micro("k", tmp_path, "deep", "in 2025 or 2026")

    assert len(sent) == len(Y.MICRO_ANGLES)
    for p in sent:
        assert "SINGLE issue" in p
        assert "3 or more separate moments" not in p, "that is discover's Q&A shape"
        assert "in 2025 or 2026" in p, "the --years window must reach the model"
        assert "DIGEST" in p, "the already-done digest must be carried in"


def test_every_angle_is_searched_separately(tmp_path, monkeypatch):
    """You.com only finds what its own plan step thinks to search for — the module's own
    recall lesson — so the fan-out has to be ours, one query per angle."""
    monkeypatch.setattr(Y, "build_scouted_digest", lambda: "")
    seen = []
    monkeypatch.setattr(Y, "_call_logged",
                        lambda k, p, e, s, o, tag: seen.append(tag) or {})
    Y.run_micro("k", tmp_path, "deep", "2010 or later")
    assert seen == [f"micro{i}" for i in range(1, len(Y.MICRO_ANGLES) + 1)]


def _resp(*cands):
    """Mirrors _cands(): the payload hangs off output.content, not output.parsed."""
    return {"output": {"content": {"candidates": list(cands)}}}


def _cand(series, moment="a thing happens", char="Hulk"):
    return {"moment": moment, "character": char, "series_issue_year": series,
            "what_visibly_happens": "x", "why_it_lands": "y",
            "constant_broken": "z", "evidence_urls": ["https://aiptcomics.com/x"]}


def test_a_burned_series_is_dropped(tmp_path, monkeypatch):
    """The real failure from the first run: 3 of 7 candidates were Absolute Batman — a
    series already produced from, and flagged in the ban list as having 8 breakdowns in one
    week. You.com cannot know that; our digest does."""
    monkeypatch.setattr(Y, "build_scouted_digest",
                        lambda: "- Absolute Batman things regular Batman cannot do "
                                "(project: absolute-batman-feats)")
    monkeypatch.setattr(Y, "_call_logged", lambda *a, **k: _resp(
        _cand("Absolute Batman #11 (2025)"), _cand("Hulk: Smash Everything #2 (2026)")))

    Y.run_micro("k", tmp_path, "deep", "2010 or later")
    report = (tmp_path / "micro_report.md").read_text(encoding="utf-8")
    assert "Hulk: Smash Everything #2" in report
    assert "Dropped as burned" in report


def test_the_report_names_what_is_still_unverified(tmp_path, monkeypatch):
    """A scouted candidate is not a producible one. batcave availability cannot be checked
    from here (Cloudflare 403s plain HTTP) and one clean narration search is not enough —
    both have burned past runs, so the report has to say so out loud."""
    monkeypatch.setattr(Y, "build_scouted_digest", lambda: "")
    monkeypatch.setattr(Y, "_call_logged", lambda *a, **k: _resp(_cand("Hulk #1 (2026)")))
    Y.run_micro("k", tmp_path, "deep", "2010 or later")
    report = (tmp_path / "micro_report.md").read_text(encoding="utf-8")
    assert "batcave" in report.lower()
    assert "coverage" in report.lower()


def test_the_schema_demands_the_broken_constant(tmp_path, monkeypatch):
    """A micro moment without a broken constant is just a nice panel — that gate is what
    separates this mode from 'find me a cool page'."""
    got = {}
    monkeypatch.setattr(Y, "build_scouted_digest", lambda: "")
    monkeypatch.setattr(Y, "_call_logged",
                        lambda k, p, e, schema, o, t: got.update(schema=schema) or {})
    Y.run_micro("k", tmp_path, "deep", "2010 or later")
    props = got["schema"]["properties"]["candidates"]["items"]["properties"]
    assert "constant_broken" in props and "series_issue_year" in props


# ─── series-level burn check ─────────────────────────────────────────────────

def test_series_burn_check_catches_a_sibling_issue():
    """is_burned cannot do this job: it wants >=60% token containment and >=2 non-format
    shared tokens — thresholds tuned for whole questions. "Absolute Batman #11 (2025)" has
    four tokens, two of them format words, so it scores 50% and slips through. A micro
    candidate identifies itself by SERIES, so match on that."""
    digest = ("- 3 things Absolute Batman can do that regular Batman can't "
              "(project: absolute-batman-feats)")
    assert Y._series_burned("Absolute Batman #11 (2025)", digest)
    assert Y._series_burned("Absolute Batman 2025 Annual #1 (2025)", digest)
    assert Y._series_burned("Hulk: Smash Everything #2 (2026)", digest) is None


def test_series_burn_check_ignores_a_one_word_series():
    """"Hulk #1" against a digest mentioning Hulk anywhere would burn every Hulk comic
    ever — too generic to act on."""
    assert Y._series_burned("Hulk #1 (2026)", "- who has beaten the Hulk barehanded") is None


@pytest.mark.parametrize("raw,want", [
    ("Absolute Batman #11 (2025)", "absolute batman"),
    ("Hulk: Smash Everything #2, 2026", "hulk smash everything"),
    ("Avengers #26", "avengers"),
    ("", ""),
])
def test_series_name_extraction(raw, want):
    assert Y._series_of(raw) == want
