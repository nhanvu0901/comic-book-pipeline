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


def test_alias_umbrella_prefix_matches_intent_series():
    assert micro_issue_rejection_reason(
        "Darkseid vs The World #1",
        {"series_issue_year": "DC All In Saga: Darkseid vs. the World #1 (2026)"},
    ) is None


def test_alias_umbrella_prefix_matches_with_year_and_trailing_intent():
    assert micro_issue_rejection_reason(
        "Darkseid vs. The World #1 (2026) Uxas origin",
        {"series_issue_year": "DC All In Saga: Darkseid vs. the World #1 (2026)"},
    ) is None


def test_alias_umbrella_prefix_with_ongoing_year_format():
    assert micro_issue_rejection_reason(
        "Darkseid vs The World #1 (2026)",
        {"series_issue_year": "DC All In Saga: Darkseid vs. the World (2026-) #1"},
    ) is None


def test_intent_with_umbrella_prefix_matches_bare_candidate_series():
    assert micro_issue_rejection_reason(
        "DC All In Saga: Darkseid vs. The World #1 (2026)",
        {"series_issue_year": "Darkseid vs. the World #1 (2026)"},
    ) is None


def test_repeated_bare_issue_in_long_prompt_does_not_cause_ambiguity():
    long_prompt = (
        "Find one evidence-grounded, visually specific moment in "
        "DC All In Saga: Darkseid vs. the World #1 (2026). "
        "In issue #1, Darkseid meets Pandora and outlines his plan to collapse reality. "
        "Make sure the moment is from issue #1."
    )
    assert micro_issue_rejection_reason(
        long_prompt,
        {"series_issue_year": "DC All In Saga: Darkseid vs. the World #1 (2026)"},
    ) is None


def test_repeated_bare_hash_mention_agrees_with_named_target():
    prompt = "Darkseid vs The World #1 (2026). In #1, Uxas origin is explored."
    assert micro_issue_rejection_reason(
        prompt,
        {"series_issue_year": "DC All In Saga: Darkseid vs. the World #1 (2026)"},
    ) is None


def test_candidate_title_with_umbrella_alias_matches_intent():
    assert micro_issue_rejection_reason(
        "Darkseid vs The World #1 (2026)",
        {
            "series_issue_year": "DC All In Saga: Darkseid vs. the World #1 (2026)",
            "title": "DC All In Saga: Darkseid vs. the World #1 (2026) — Omega Point",
        },
    ) is None


def test_conflicting_bare_issue_fails_closed():
    prompt = "Darkseid vs The World #1 (2026). But in issue #2, the battle continues."
    reason = micro_issue_rejection_reason(
        prompt,
        {"series_issue_year": "DC All In Saga: Darkseid vs. the World #1 (2026)"},
    )
    assert reason is not None
    assert "ambiguous" in reason.lower()


def test_bare_issue_without_named_series_fails_closed():
    prompt = "Find a moment in issue #1."
    reason = micro_issue_rejection_reason(
        prompt,
        {"series_issue_year": "Batman #1 (2026)"},
    )
    assert reason is not None
    assert "ambiguous" in reason.lower()


@pytest.mark.parametrize("intent,candidate", [
    ("Spider-Man #1", "Amazing Spider-Man #1 (2022)"),
    ("Amazing Spider-Man #1", "Spider-Man #1 (2022)"),
    ("Batman #1", "Batman: Knightfight #1 (2026)"),
    ("Avengers #1", "Avengers: Twilight #1 (2024)"),
    ("Justice League #1", "Justice League of America #1 (2024)"),
    ("Batman: White Knight #1", "Batman: Curse of the White Knight #1 (2020)"),
])
def test_negative_cases_different_series_with_shared_words_fail(intent, candidate):
    reason = micro_issue_rejection_reason(intent, {"series_issue_year": candidate})
    assert reason is not None


@pytest.mark.parametrize("series_issue", [
    "Detective Comics #1000 (2019)",
    "Action Comics #1 (1938)",
    "The Comic Book #1 (1990)",
])
def test_series_name_ending_with_comics_or_book_is_preserved(series_issue):
    assert micro_issue_rejection_reason(
        series_issue, {"series_issue_year": series_issue}
    ) is None


def test_candidate_title_with_series_ending_with_comics_is_preserved():
    assert micro_issue_rejection_reason(
        "Detective Comics #1000 (2019)",
        {
            "series_issue_year": "Detective Comics #1000 (2019)",
            "title": "Detective Comics #1000 (2019) — 80th Anniversary",
        },
    ) is None


@pytest.mark.parametrize("intent,candidate", [
    ("Batman: Year One #1", "Green Arrow: Year One #1 (1987)"),
    ("Spider-Man: Reign #1", "X-Men: Reign #1 (2006)"),
    ("Batman: Hush #1", "Superman: Hush #1 (2003)"),
])
def test_different_series_sharing_subtitle_are_rejected(intent, candidate):
    reason = micro_issue_rejection_reason(intent, {"series_issue_year": candidate})
    assert reason is not None
    assert "Batman" in reason or "Spider-Man" in reason or "Hush" in reason


def test_intent_with_two_series_sharing_subtitle_fails_closed_as_ambiguous():
    prompt = "Compare Batman: Year One #1 with Green Arrow: Year One #1."
    reason = micro_issue_rejection_reason(
        prompt, {"series_issue_year": "Batman: Year One #1 (1987)"}
    )
    assert reason is not None
    assert "ambiguous" in reason.lower()


def test_year_in_bare_mention_is_merged_and_enforced():
    prompt = "Batman #1. In #1 (2016), he investigates the murder."
    # Mismatched candidate year
    reason_wrong = micro_issue_rejection_reason(
        prompt, {"series_issue_year": "Batman #1 (2020)"}
    )
    assert reason_wrong is not None
    assert "year missing or different" in reason_wrong or "2016" in reason_wrong

    # Matching candidate year
    reason_ok = micro_issue_rejection_reason(
        prompt, {"series_issue_year": "Batman #1 (2016)"}
    )
    assert reason_ok is None


def test_conflicting_years_in_bare_mentions_fail_closed():
    prompt = "Batman #1. In #1 (2016). And in #1 (2020)."
    reason = micro_issue_rejection_reason(
        prompt, {"series_issue_year": "Batman #1 (2016)"}
    )
    assert reason is not None
    assert "ambiguous" in reason.lower()


@pytest.mark.parametrize("intent", [
    "Batman (2016-) #1",
    "Batman (2016-present) #1",
    "Batman #1 (2016-)",
    "Batman #1 (2016-present)",
])
def test_intent_with_ongoing_year_qualifier_enforces_year_constraint(intent):
    # Candidate from a different volume/year must be rejected
    reason_wrong = micro_issue_rejection_reason(
        intent, {"series_issue_year": "Batman #1 (2011)"}
    )
    assert reason_wrong is not None
    assert "2016" in reason_wrong or "year missing or different" in reason_wrong

    # Candidate matching the publication/volume start year must be accepted
    assert micro_issue_rejection_reason(
        intent, {"series_issue_year": "Batman #1 (2016)"}
    ) is None



