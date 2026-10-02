from datetime import date

from stages.research_scout.micro_recency import (
    explicit_historical_scope,
    issue_publication_year,
    micro_release_rejection_reason,
    recent_micro_instruction,
)


TODAY = date(2026, 9, 30)


def test_publication_year_is_after_issue_number_not_series_launch_year():
    assert issue_publication_year("Hero (2022) #67 (2026)") == 2026
    assert issue_publication_year("Hero (2022) #67") is None
    assert issue_publication_year("Hero #7 (2025/2026)") is None
    assert issue_publication_year("Hero #7 (2025)") == 2025


def test_broad_micro_scout_only_accepts_recent_published_issues():
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2024)"}, "Find a micro moment", TODAY
    ) == "outside_recent_micro_window"
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2027)"}, "Find a micro moment", TODAY
    ) == "future_issue_year"
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero (2022) #2"}, "Find a micro moment", TODAY
    ) == "issue_publication_year_unresolved"
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero (2022) #2 (2026)"}, "Find a micro moment", TODAY
    ) is None


def test_explicit_older_issue_or_era_keeps_historical_requests_working():
    assert explicit_historical_scope("Find a moment in Hero #2 (2014)", TODAY)
    assert explicit_historical_scope("Find a Golden Age Batman moment", TODAY)
    assert not explicit_historical_scope("Find a recent Batman moment", TODAY)
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2014)"},
        "Find a moment in Hero #2 (2014)", TODAY,
    ) is None
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2027)"},
        "Find a moment in Hero #2 (2027)", TODAY,
    ) == "future_issue_year"


def test_instruction_is_dynamic_and_requires_an_actual_story_turn():
    instruction = recent_micro_instruction(TODAY)
    assert "2026" in instruction and "2025" in instruction
    assert "publication year of the exact issue" in instruction
    assert "setup" in instruction and "consequence" in instruction
