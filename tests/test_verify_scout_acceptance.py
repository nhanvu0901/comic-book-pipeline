import json

import pytest

import config
from scripts import verify_scout_acceptance as harness
from stages.research_scout import planner as planner_module
from stages.research_scout.planner import ResearchPlan


def test_dry_run_uses_offline_fixture_and_writes_only_safe_aggregates(tmp_path, monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("dry-run attempted network access")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    report_path = tmp_path / "report.json"
    report = harness.run_acceptance(live=False, report_path=report_path)

    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved == report
    assert report["mode"] == "dry_run"
    assert report["acceptance_evaluated"] is False
    assert report["evaluation_status"] == "dry_run_smoke_only"
    assert report["no_candidates"] is True
    assert report["metrics"]["duplicate_rate"] is None
    assert report["metrics"]["new_valid_per_standard_call"] is None
    assert len(report["cases"]) == 7
    assert report["planner_mode"] == "fallback_only"
    assert all(row["plan_source"] == "fallback" for row in report["cases"])
    assert report["accepted_candidate_scope"] == "new candidates only; carried held cards are excluded"
    assert all(row["hard_rules_first"] and row["standard_effort"] for row in report["cases"])
    assert all(value == 0 for value in (
        report["metrics"]["pre_2010_accepted"],
        report["metrics"]["non_comic_accepted"],
        report["metrics"]["held_duplicate_accepted"],
    ))
    serialized = report_path.read_text(encoding="utf-8")
    for forbidden in (
        "HARD RULES", "Every time Batman broke", "Which comic issues", "YDC_API_KEY", "response_text",
    ):
        assert forbidden not in serialized


def test_issue_key_ignores_year_but_requires_one_issue():
    assert harness._issue_key("Batman #57 (2018)") == harness._issue_key("Batman #57 (2020)")
    assert harness._issue_key("Batman #57-60 (2018)") is None


def test_batman_seed_and_rescout_match_source_without_inverting_the_question():
    batman_seed = next(seed for seed in harness.SEEDS if seed[0] == "qa_batman_no_kill_breaks")
    rescout = next(seed for seed in harness._acceptance_runs() if seed[0] == "rescout_batman_held")
    assert batman_seed[2] == "Every time Batman broke his no-kill rule"
    assert rescout[2] == batman_seed[2]
    assert "choosing not to kill" not in batman_seed[2]


def test_accepted_candidate_checks_title_and_rejects_unparseable_or_missing_year():
    assert harness._accepted_candidate_violations({
        "series_issue_year": "Batman #57 (2018)", "title": "Batman: The Movie",
    }) == {"non_comic"}
    assert harness._accepted_candidate_violations({"series_issue_year": "Unknown story"}) == {
        "issue_label_unparseable_or_year_missing",
    }
    assert harness._accepted_candidate_violations({"series_issue_year": "Batman #57"}) == {
        "issue_label_unparseable_or_year_missing",
    }
    assert harness._accepted_candidate_violations({"series_issue_year": "Final Crisis #6 (2009)"}) == {"pre_2010"}


def test_live_path_uses_production_planner_by_default_but_makes_no_network(tmp_path, monkeypatch):
    class FakeYouCom:
        api_key = "fixture-key"

        def research(self, *_args, **_kwargs):
            return harness.RawCall(api="research", payload={"output": {"content": {"candidates": []}, "sources": []}})

    plan_calls = []
    monkeypatch.setattr(harness, "YouComClient", FakeYouCom)
    monkeypatch.setattr(config, "SCOUT_TOPUP_ROUNDS", 0)
    monkeypatch.setattr(planner_module, "make_plan", lambda *args: (
        plan_calls.append(args) or ResearchPlan(
            unit="one comic issue", cardinality="exhaustive", research_prompt="Find qualifying issues.",
        )
    ))

    report = harness.run_acceptance(live=True, report_path=tmp_path / "live-mock.json")

    assert report["planner_mode"] == "production_planner"
    assert len(plan_calls) == 7
    assert all(row["plan_source"] == "planner" for row in report["cases"])
    assert report["acceptance_evaluated"] is False
    assert report["evaluation_status"] == "no_candidates"
    assert report["no_candidates"] is True
    assert report["metrics"]["duplicate_rate"] is None


def test_cli_requires_explicit_mode_and_does_not_default_to_live(tmp_path):
    with pytest.raises(SystemExit) as error:
        harness.main(["--report", str(tmp_path / "unused.json")])
    assert error.value.code == 2
    assert not (tmp_path / "unused.json").exists()
