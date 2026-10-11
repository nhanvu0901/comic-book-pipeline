"""Discover's question avoid list: what it holds, in what order, and its two sizes.

* ``question_avoid_lines`` is the FULL list (every active ledger question, every
  active rejection, every banlist row, plus what the session already showed). It
  feeds the hard ``is_burned`` filter and has no cap — the way micro's code gate
  uses the uncapped ``inventory_issue_keys``.
* ``relevant_question_avoid_lines`` is that list cut to the prompt's 50, session
  first, then the newest ledger rows.

A ``rejected`` row counts here and nowhere else.
"""
import pytest

from stages.research_scout import avoid_list, ledger_inventory, ledger_shadow
from stages.research_scout.models import ScoutMode

_BANLIST_ROWS = (
    "| Date | Question | Reason |\n|------|----------|--------|\n"
    "| 2026-08-05 | Which banlisted lane stays dead? | produced |\n"
)


def _event(mode, kind, text, ts, *, key="", label=None):
    return {
        "id": f"{mode}-{kind}-{text}-{ts}", "ts": ts, "mode": mode, "kind": kind, "key": key,
        "keys": [], "label": label or text, "text": text, "entities": [], "scope": "item",
        "reason_code": "", "refs": {},
    }


@pytest.fixture
def ledger_rows(monkeypatch, tmp_path):
    """Scripted ledger snapshot, no local projects, a one-row banlist."""
    rows: list[dict] = []
    monkeypatch.setattr(avoid_list.config, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(
        ledger_shadow, "_read_events", lambda **kwargs: (list(rows), "export", "ready", None),
    )
    banlist = tmp_path / "qa_question_banlist.md"
    banlist.write_text(_BANLIST_ROWS, encoding="utf-8")
    monkeypatch.setattr(avoid_list, "_banlist_path", lambda: banlist)
    return rows


def test_rejected_questions_join_the_qa_discover_avoid(ledger_rows):
    ledger_rows.append(_event("qa", "rejected", "Who has lifted Mjolnir?", "2026-10-01T00:00:00Z"))

    assert "Who has lifted Mjolnir?" in avoid_list.question_avoid_lines(ScoutMode.QA)
    assert "Who has lifted Mjolnir?" in avoid_list.relevant_question_avoid_lines(ScoutMode.QA)


def test_a_rejection_is_avoided_in_its_own_mode_only(ledger_rows):
    ledger_rows.append(_event("qa", "rejected", "Who has lifted Mjolnir?", "2026-10-01T00:00:00Z"))
    ledger_rows.append(_event(
        "micro", "rejected", "Hero opens the sealed door.", "2026-10-01T00:00:01Z",
        label="Hero #2 (2025)",
    ))

    qa = avoid_list.question_avoid_lines(ScoutMode.QA)
    micro = avoid_list.question_avoid_lines(ScoutMode.MICRO)

    assert "Who has lifted Mjolnir?" in qa and "Hero opens the sealed door." not in qa
    assert "Hero opens the sealed door." in micro and "Who has lifted Mjolnir?" not in micro
    assert avoid_list.question_avoid_lines("recap") == []


def test_rejected_never_reaches_the_production_inventory_or_the_micro_issue_gate(ledger_rows):
    ledger_rows.append(_event("qa", "rejected", "Who has lifted Mjolnir?", "2026-10-01T00:00:00Z"))
    ledger_rows.append(_event(
        "micro", "rejected", "Hero opens the sealed door.", "2026-10-01T00:00:01Z",
        label="Hero #2 (2025)",
    ))

    qa_labels, _, qa_questions, _ = ledger_inventory.load_production_inventory(ScoutMode.QA)
    micro_labels, micro_keys, _, _ = ledger_inventory.load_production_inventory(ScoutMode.MICRO)

    assert qa_questions == [] and qa_labels == []
    assert micro_labels == [] and micro_keys == set()
    assert avoid_list.inventory_issue_keys(ScoutMode.MICRO) == set()


def test_a_lifted_rejection_stops_being_avoided(ledger_rows):
    ledger_rows.append(_event("qa", "rejected", "Who has lifted Mjolnir?", "2026-10-01T00:00:00Z"))
    ledger_rows.append(_event("qa", "proposed", "who has  lifted mjolnir?", "2026-10-02T00:00:00Z"))

    assert "Who has lifted Mjolnir?" not in avoid_list.question_avoid_lines(ScoutMode.QA)


def test_rejecting_again_after_a_lift_avoids_it_again(ledger_rows):
    ledger_rows.append(_event("qa", "rejected", "Who has lifted Mjolnir?", "2026-10-01T00:00:00Z"))
    ledger_rows.append(_event("qa", "proposed", "Who has lifted Mjolnir?", "2026-10-02T00:00:00Z"))
    ledger_rows.append(_event("qa", "rejected", "Who has lifted Mjolnir?", "2026-10-03T00:00:00Z"))

    assert "Who has lifted Mjolnir?" in avoid_list.question_avoid_lines(ScoutMode.QA)


def test_a_question_that_is_both_produced_and_rejected_is_listed_once(ledger_rows):
    ledger_rows.append(_event("qa", "produced", "Who has lifted Mjolnir?", "2026-09-01T00:00:00Z"))
    ledger_rows.append(_event("qa", "rejected", "Who has lifted Mjolnir?", "2026-10-01T00:00:00Z"))

    lines = avoid_list.question_avoid_lines(ScoutMode.QA)

    assert [line for line in lines if "mjolnir" in line.casefold()] == ["Who has lifted Mjolnir?"]


def test_banlist_rows_are_part_of_the_full_list(ledger_rows):
    assert "Which banlisted lane stays dead?" in avoid_list.question_avoid_lines(ScoutMode.QA)


def test_the_session_comes_first_then_the_newest_ledger_rows(ledger_rows):
    ledger_rows.append(_event("qa", "produced", "Oldest produced?", "2026-01-01T00:00:00Z"))
    ledger_rows.append(_event("qa", "rejected", "Newest rejected?", "2026-10-05T00:00:00Z"))
    ledger_rows.append(_event("qa", "produced", "Middle produced?", "2026-06-01T00:00:00Z"))

    lines = avoid_list.question_avoid_lines(
        ScoutMode.QA, extra_held=["Shown first?", "Shown second?"],
    )

    assert lines[:2] == ["Shown second?", "Shown first?"]  # newest session batch first
    ledger_part = [line for line in lines[2:] if line.endswith("?") and "banlisted" not in line]
    assert ledger_part == ["Newest rejected?", "Middle produced?", "Oldest produced?"]


def test_local_holds_missing_from_the_snapshot_rank_as_newest(ledger_rows, monkeypatch):
    """A project approved on this machine but not yet in the exported snapshot is
    newer than anything in it."""
    ledger_rows.append(_event("qa", "produced", "Old produced?", "2026-10-09T00:00:00Z"))
    monkeypatch.setattr(ledger_inventory, "_local_events", lambda mode: [{
        "mode": "qa", "kind": "in_progress", "label": "Local approval?", "text": "Local approval?",
        "keys": [], "issue_labels": [], "key": "",
    }] if mode == "qa" else [])

    lines = avoid_list.question_avoid_lines(ScoutMode.QA)

    assert lines.index("Local approval?") < lines.index("Old produced?")


def _sixty_produced(rows):
    # Question 01 is the OLDEST row, question 60 the newest.
    for number in range(1, 61):
        rows.append(_event(
            "qa", "produced", f"Distinct lane number {number:02d}?",
            f"2026-03-{number % 28 + 1:02d}T{number // 28:02d}:00:00Z",
        ))


def test_the_full_list_has_no_cap_but_the_prompt_view_stops_at_fifty(ledger_rows):
    _sixty_produced(ledger_rows)

    full = avoid_list.question_avoid_lines(ScoutMode.QA)
    prompt = avoid_list.relevant_question_avoid_lines(ScoutMode.QA, limit=50)

    assert len([line for line in full if line.startswith("Distinct lane")]) == 60
    assert len(prompt) == 50
    assert prompt == full[:50]


def test_the_prompt_cut_keeps_the_newest_ledger_rows(ledger_rows):
    _sixty_produced(ledger_rows)
    by_age = sorted(ledger_rows, key=lambda event: event["ts"])
    oldest, newest = by_age[0]["text"], by_age[-1]["text"]

    prompt = avoid_list.relevant_question_avoid_lines(ScoutMode.QA)

    assert newest in prompt
    assert oldest not in prompt
    assert oldest in avoid_list.question_avoid_lines(ScoutMode.QA)


def test_session_shown_questions_survive_the_prompt_cut(ledger_rows):
    _sixty_produced(ledger_rows)

    prompt = avoid_list.relevant_question_avoid_lines(
        ScoutMode.QA, extra_held=["Just turned this down?"], limit=50,
    )

    assert prompt[0] == "Just turned this down?"
    assert len(prompt) == 50


def test_lines_are_single_line_so_the_burn_digest_cannot_lose_a_row(ledger_rows):
    ledger_rows.append(_event("qa", "rejected", "Split\nacross   lines?", "2026-10-01T00:00:00Z"))

    lines = avoid_list.question_avoid_lines(ScoutMode.QA, extra_held=["Held\n  twice?"])

    assert "Split across lines?" in lines and "Held twice?" in lines
    assert all("\n" not in line for line in lines)


def test_an_unreadable_snapshot_still_returns_the_session_and_the_banlist(monkeypatch, tmp_path):
    monkeypatch.setattr(avoid_list.config, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(
        ledger_shadow, "_read_events", lambda **kwargs: (None, "export", "corrupted", "bad json"),
    )
    banlist = tmp_path / "qa_question_banlist.md"
    banlist.write_text(_BANLIST_ROWS, encoding="utf-8")
    monkeypatch.setattr(avoid_list, "_banlist_path", lambda: banlist)

    lines = avoid_list.question_avoid_lines(ScoutMode.QA, extra_held=["Shown?"])

    assert lines == ["Shown?", "Which banlisted lane stays dead?"]
