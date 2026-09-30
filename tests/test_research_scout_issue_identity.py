"""A micro request for one exact comic issue cannot drift to another issue."""

import pytest

from stages.research_scout.issue_identity import micro_issue_rejection_reason


@pytest.mark.parametrize("intent", [
    "Amazing X-Men #2: Cyclops confronts the team.",
    "Find a micro moment in Amazing X-Men #2, where Cyclops confronts the team.",
    "in Amazing X-Men #2, Cyclops faces Darkchild in Limbo.",
    "Cyclops faces Darkchild in Amazing X-Men #2.",
    "Amazing X-Men #2: Cyclops confronts the team.\nComic: Amazing X-Men #2 (2014)",
])
def test_matching_issue_survives_common_micro_intent_shapes(intent):
    assert micro_issue_rejection_reason(
        intent, {"series_issue_year": "Amazing X-Men #2 (2014)"}
    ) is None


@pytest.mark.parametrize("issue", [
    "X-Men #2 (2014)",
    "Amazing X-Men #3 (2014)",
    "Batman: Knightfight #1 (2026)",
    "Amazing X-Men",
])
def test_exact_issue_intent_rejects_other_or_missing_identity(issue):
    reason = micro_issue_rejection_reason(
        "Find a moment in Amazing X-Men #2 (2014).", {"series_issue_year": issue}
    )
    assert reason is not None
    assert "Amazing X-Men #2" in reason


def test_same_title_and_number_from_another_year_is_rejected():
    assert micro_issue_rejection_reason(
        "Amazing X-Men #2 (2014)", {"series_issue_year": "Amazing X-Men #2 (2026)"}
    ) is not None


def test_year_before_issue_number_is_part_of_the_requested_identity():
    assert micro_issue_rejection_reason(
        "Amazing X-Men (2014) #2", {"series_issue_year": "Amazing X-Men #2 (2026)"}
    ) is not None


def test_conflicting_issue_mentions_fail_closed():
    reason = micro_issue_rejection_reason(
        "Amazing X-Men #2: scene.\nComic: Thor #1 (2024)",
        {"series_issue_year": "Amazing X-Men #2 (2014)"},
    )
    assert reason is not None
    assert "ambiguous" in reason.lower() or "conflict" in reason.lower()


def test_dotted_series_name_is_not_split_as_a_sentence():
    assert micro_issue_rejection_reason(
        "Find a moment in DC K.O.: Knightfight #1.",
        {"series_issue_year": "DC K.O.: Knightfight #1 (2026)"},
    ) is None


def test_exact_intent_rejects_other_dotted_series_without_year_hint():
    reason = micro_issue_rejection_reason(
        "in Amazing X-Men #2, Cyclops faces Darkchild.",
        {"series_issue_year": "DC K.O.: Knightfight #1 (2025/2026)"},
    )
    assert reason is not None
    assert "Amazing X-Men #2" in reason


def test_period_after_issue_does_not_disable_identity_gate():
    reason = micro_issue_rejection_reason(
        "Cyclops faces Darkchild in Amazing X-Men #2.",
        {"series_issue_year": "Batman #1 (2026)"},
    )
    assert reason is not None
    assert "Amazing X-Men #2" in reason


def test_candidate_title_with_another_explicit_issue_overrides_matching_metadata():
    reason = micro_issue_rejection_reason(
        "Amazing X-Men #2: Cyclops confronts the team.",
        {
            "series_issue_year": "Amazing X-Men #2 (2014)",
            "title": "Batman: Knightfight #1 — a different scene",
        },
    )
    assert reason is not None
    assert "Batman" in reason


def test_descriptive_title_without_issue_does_not_conflict():
    assert micro_issue_rejection_reason(
        "Amazing X-Men #2: Cyclops confronts the team.",
        {
            "series_issue_year": "Amazing X-Men #2 (2014)",
            "title": "Cyclops confronts Darkchild",
        },
    ) is None


def test_broad_intent_without_exact_issue_does_not_filter_candidates():
    assert micro_issue_rejection_reason(
        "Find me a recent Hulk micro moment.", {"series_issue_year": "Hulk #10 (2026)"}
    ) is None
