"""Guards for `youcom_scout micro` — scouting single MOMENTS, not questions.

The module's other stages hunt a QUESTION whose answer spans 3+ different comics. A micro
moment is the opposite shape: one drawn beat inside one issue. Reusing discover's prompt
returns listicles, which is exactly what a one-off run outside the repo produced before this
subcommand existed.
"""
import json

import pytest

from stages import youcom_scout as Y
from tests import micro_detail_rules as rules


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
            "turning_point": "z", "evidence_urls": ["https://aiptcomics.com/x"]}


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


def test_the_schema_demands_a_concrete_turning_point(tmp_path, monkeypatch):
    """Reference moments turn on an action or reveal; breaking a famous rule is optional."""
    got = {}
    monkeypatch.setattr(Y, "build_scouted_digest", lambda: "")
    monkeypatch.setattr(Y, "_call_logged",
                        lambda k, p, e, schema, o, t: got.update(schema=schema) or {})
    Y.run_micro("k", tmp_path, "deep", "2010 or later")
    props = got["schema"]["properties"]["candidates"]["items"]["properties"]
    assert "turning_point" in props and "series_issue_year" in props
    assert "constant_broken" not in props


def test_default_micro_cli_window_filters_old_candidates(tmp_path, monkeypatch):
    from datetime import date

    year = date.today().year
    monkeypatch.setattr(Y, "build_scouted_digest", lambda: "")
    monkeypatch.setattr(Y, "_call_logged", lambda *a, **k: _resp(
        _cand("Hero #1 (2014)"), _cand(f"Hero #2 ({year})")
    ))
    Y.run_micro("k", tmp_path, "deep")
    report = (tmp_path / "micro_report.md").read_text(encoding="utf-8")
    assert f"## Hulk — Hero #2 ({year})" in report
    assert "## Hulk — Hero #1 (2014)" not in report
    assert "outside_recent_micro_window" in report


# ─── aftermath and context: the scene alone left the writer a teaser to end on ─

def _run_capturing(tmp_path, monkeypatch, *, response=None):
    """Run the micro CLI with the network cut; hand back (prompts, schemas, report)."""
    prompts, schemas = [], []

    def fake_call(key, prompt, effort, schema, outdir, tag):
        prompts.append(prompt)
        schemas.append(schema)
        return response if response is not None else {}

    monkeypatch.setattr(Y, "build_scouted_digest", lambda: "")
    monkeypatch.setattr(Y, "_call_logged", fake_call)
    Y.run_micro("k", tmp_path, "deep", "in 2025 or 2026")
    return prompts, schemas, (tmp_path / "micro_report.md").read_text(encoding="utf-8")


def test_micro_cli_prompt_carries_the_three_rules_and_keeps_its_single_issue_shape(
    tmp_path, monkeypatch
):
    prompts, _schemas, _report = _run_capturing(tmp_path, monkeypatch)

    assert len(prompts) == len(Y.MICRO_ANGLES)
    for prompt in prompts:
        assert rules.missing_rules(prompt) == []
        assert rules.in_order(prompt)
        assert "SINGLE issue" in prompt
        assert "3 or more separate moments" not in prompt
        assert "in 2025 or 2026" in prompt


def test_micro_cli_schema_asks_for_the_aftermath_fields_in_strict_form(tmp_path, monkeypatch):
    _prompts, schemas, _report = _run_capturing(tmp_path, monkeypatch)

    items = schemas[0]["properties"]["candidates"]["items"]
    props = items["properties"]
    for name in rules.DETAIL_FIELDS:
        assert name in props
    assert set(items["required"]) == set(props)
    assert items["additionalProperties"] is False
    assert props["detail_citations"]["items"]["required"] == ["supports", "url", "quote"]
    # what the earlier schema pinned stays pinned
    assert "turning_point" in props and "series_issue_year" in props
    assert "constant_broken" not in props


def test_discovery_does_not_require_the_aftermath_fields():
    """workflow.discover_questions shares _MICRO_PROPS and its candidates (and every
    fixture) carry none of them; only the CLI's full scout asks."""
    assert not set(rules.DETAIL_FIELDS) & set(Y._MICRO_PROPS)


def test_the_cli_and_the_workflow_ask_for_the_same_detail_fields():
    from stages.research_scout.planner import MICRO_DETAIL_PROPS

    assert Y._MICRO_DETAIL_PROPS == MICRO_DETAIL_PROPS


def test_the_report_prints_the_aftermath_the_context_and_what_is_not_revealed(
    tmp_path, monkeypatch
):
    candidate = _cand("Hero #2 (2026)")
    candidate.update(
        aftermath="Steve loses the duel and keeps the blade.",
        context_behind="He took the blade from the vault to settle a debt.",
        unrevealed="The review never says what the twist is.",
        detail_citations=[
            {"supports": "aftermath", "url": "https://aiptcomics.com/duel",
             "quote": "Steve loses the duel."},
            {"supports": "context_behind", "url": "https://cbr.com/vault",
             "quote": "The blade came from the vault."},
        ],
    )
    _prompts, _schemas, report = _run_capturing(
        tmp_path, monkeypatch, response=_resp(candidate),
    )

    assert "- what happens next: Steve loses the duel and keeps the blade." in report
    assert "- context: He took the blade from the vault to settle a debt." in report
    assert "- not revealed by sources: The review never says what the twist is." in report
    assert 'aftermath: https://aiptcomics.com/duel — "Steve loses the duel."' in report
    assert 'context_behind: https://cbr.com/vault — "The blade came from the vault."' in report


def test_the_report_says_so_when_no_source_states_the_aftermath(tmp_path, monkeypatch):
    """"" is the answer when no source states it. Saying nothing would read as the
    scout never having looked."""
    candidate = _cand("Hero #2 (2026)")
    candidate.update(aftermath="", context_behind="", unrevealed="", detail_citations=[])
    _prompts, _schemas, report = _run_capturing(tmp_path, monkeypatch, response=_resp(candidate))

    assert "- what happens next: (none found in sources)" in report
    assert "- context: (none found in sources)" in report
    assert "not revealed by sources" not in report
    assert "detail sources" not in report


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
