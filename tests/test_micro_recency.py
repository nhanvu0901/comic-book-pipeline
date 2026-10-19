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
    assert issue_publication_year("Hero (2022) #67", allow_series_year=True) == 2022
    assert issue_publication_year("Action Comics (1938) #1064 (2024)") == 2024
    assert issue_publication_year("Action Comics (1938) #1") is None
    assert issue_publication_year("Action Comics (1938) #1", allow_series_year=True) == 1938
    assert issue_publication_year("Hero #7 (2025/2026)") is None
    assert issue_publication_year("Hero #7 (2025)") == 2025


def test_broad_micro_scout_enforces_2010_floor_and_rejects_pre_2010():
    # Pre-2010 comics (<= 2009) MUST be rejected in broad scout
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (1985)"}, "Find a micro moment", TODAY
    ) == "outside_recent_micro_window"
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2009)"}, "Find a micro moment", TODAY
    ) == "outside_recent_micro_window"
    assert micro_release_rejection_reason(
        {"series_issue_year": "World's Finest #71 (1954)"}, "Find a micro moment", TODAY
    ) == "outside_recent_micro_window"
    assert micro_release_rejection_reason(
        {"series_issue_year": "Batman #321 (1980)"}, "Find a micro moment", TODAY
    ) == "outside_recent_micro_window"
    assert micro_release_rejection_reason(
        {"series_issue_year": "DC Special Series #27 (1977)"}, "Find a micro moment", TODAY
    ) == "outside_recent_micro_window"

    # Modern comics (>= 2010 up to current year) MUST pass in broad scout
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2010)"}, "Find a micro moment", TODAY
    ) is None
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2014)"}, "Find a micro moment", TODAY
    ) is None
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2024)"}, "Find a micro moment", TODAY
    ) is None
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero (2022) #2 (2026)"}, "Find a micro moment", TODAY
    ) is None

    # Future comics (> today.year) must be rejected
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2027)"}, "Find a micro moment", TODAY
    ) == "future_issue_year"

    # Unresolved issue years must be rejected
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero (2022) #2"}, "Find a micro moment", TODAY
    ) == "issue_publication_year_unresolved"
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2"}, "Find a micro moment", TODAY
    ) == "issue_publication_year_unresolved"

    # Explicit recent requests narrow to current year (2026)
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2026)"}, "Find a recent micro moment", TODAY
    ) is None
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2024)"}, "Find a recent micro moment", TODAY
    ) == "outside_recent_micro_window"


def test_selected_year_is_exact_and_overrides_any_year_in_the_request():
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2024)"}, "Find 2025 Hero stories", TODAY,
        publication_year=2024,
    ) is None
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2025)"}, "Find 2025 Hero stories", TODAY,
        publication_year=2024,
    ) == "outside_recent_micro_window"
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2024)"}, "Find 2024 Hero stories", TODAY,
    ) is None
    # Pre-2010 year cannot pass even if requested
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2009)"}, "Find 2009 Hero stories", TODAY,
        publication_year=2009,
    ) == "outside_recent_micro_window"


def test_explicit_older_issue_or_era_keeps_historical_requests_working():
    assert explicit_historical_scope("Find a moment in Hero #2 (2014)", TODAY)
    assert explicit_historical_scope("Find a Golden Age Batman moment", TODAY)
    assert not explicit_historical_scope("Find a recent Batman moment", TODAY)
    # 2014 is >= 2010, so it qualifies
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2014)"},
        "Find a moment in Hero #2 (2014)", TODAY,
    ) is None
    # 2027 is in the future
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (2027)"},
        "Find a moment in Hero #2 (2027)", TODAY,
    ) == "future_issue_year"
    # Pre-2010 issue is rejected even if named in request
    assert micro_release_rejection_reason(
        {"series_issue_year": "Hero #2 (1985)"},
        "Find a moment in Hero #2 (1985)", TODAY,
    ) == "outside_recent_micro_window"


def test_instruction_is_dynamic_and_requires_an_actual_story_turn():
    instruction = recent_micro_instruction(TODAY)
    assert "2010" in instruction and "2026" in instruction
    assert "Search published issues from 2024 only" in recent_micro_instruction(
        TODAY, publication_year=2024
    )
    assert "Search published issues from 2026 only" in recent_micro_instruction(
        TODAY, user_intent="Find recent moments"
    )
    assert "publication year of the exact issue" in instruction
    assert "setup" in instruction and "consequence" in instruction

