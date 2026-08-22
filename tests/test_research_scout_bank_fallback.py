"""Tier A of the empty-intent fallback — see stages/research_scout/bank_fallback.py.

qa_question_bank.md mixes real table rows with '> LESSON ...' blockquote lines that
must never be read as a table row, and its Status cells carry trailing dates/notes
("PRODUCED 2026-07-14 (projects/...)") that must not defeat the leading-token
OPEN/CLOSED check. qa_question_banlist.md always wins over a bank row, even when
the two files spell the same question with different punctuation or casing.
"""
from stages.research_scout.bank_fallback import (
    bank_suggestions_for_mode,
    banlist_questions,
    is_open_status,
    normalize_question,
    parse_bank_rows,
)
from stages.research_scout.models import ScoutMode

_BANK = """\
# Q&A question BANK

| Status | Question | Answer items (comic, year) | Notes |
|--------|----------|----------------------------|-------|
| SAVE-FOR-LATER (do-later backlog) | Open question one? | item one | note |
| CANDIDATE-BLOCKED | Open question two? | item two | note |
| REJECTED | Rejected question? | item | note |
| PRODUCED 2026-07-14 (projects/foo — DO NOT re-suggest) | Produced question? | item | note |
> LESSON 2026-07-21 (scout-qa): this line starts with '>' and must never parse as a
> row, even though it reads like prose that mentions a Question-shaped sentence?
"""

_BANLIST = """\
# Q&A question BAN LIST

| Date | Question | Reason |
|------|----------|--------|
| 2026-07-22 | Open question two? | already covered |

## Produced — đã làm video

| Date produced | Question | Project |
|---|---|---|
| 2026-07-10 | Some other produced question | projects/x |
"""


def test_open_rows_are_kept_and_rejected_or_produced_rows_are_dropped():
    rows = parse_bank_rows(_BANK)
    assert {r["question"] for r in rows} == {
        "Open question one?", "Open question two?",
        "Rejected question?", "Produced question?",
    }
    open_rows = [r for r in rows if is_open_status(r["status"])]
    assert {r["question"] for r in open_rows} == {"Open question one?", "Open question two?"}


def test_blockquote_lesson_lines_are_never_treated_as_table_rows():
    rows = parse_bank_rows(_BANK)
    assert all("LESSON" not in r["question"] for r in rows)
    assert len(rows) == 4  # not 5 — the '>' block must not become a 5th row


def test_status_leading_token_matches_regardless_of_trailing_parenthetical_or_date():
    assert is_open_status("SAVE-FOR-LATER (do-later backlog)") is True
    assert is_open_status("CANDIDATE-BLOCKED") is True
    assert is_open_status("REJECTED") is False
    assert is_open_status("PRODUCED 2026-07-14 (projects/foo — DO NOT re-suggest)") is False
    assert is_open_status("") is False


def test_banlist_question_wins_even_with_punctuation_and_case_differences():
    banned = banlist_questions(_BANLIST)
    assert normalize_question("open QUESTION two??") in banned
    assert normalize_question("Some other produced question") in banned


def test_bank_suggestions_drop_a_question_that_also_appears_in_the_banlist(tmp_path):
    bank_path = tmp_path / "qa_question_bank.md"
    banlist_path = tmp_path / "qa_question_banlist.md"
    bank_path.write_text(_BANK, encoding="utf-8")
    banlist_path.write_text(_BANLIST, encoding="utf-8")

    suggestions = bank_suggestions_for_mode(
        ScoutMode.QA, bank_path=bank_path, banlist_path=banlist_path,
    )
    # "Open question two?" is OPEN in the bank but ALSO in the banlist -> banlist wins,
    # leaving only "Open question one?" (the REJECTED/PRODUCED rows were never open).
    assert [s["question"] for s in suggestions] == ["Open question one?"]


def test_micro_mode_never_reads_the_bank_file_even_if_one_exists(tmp_path):
    bank_path = tmp_path / "qa_question_bank.md"
    bank_path.write_text(_BANK, encoding="utf-8")
    # micro_moment has no equivalent backlog file by design — this must be a hard
    # skip, not "read it and find nothing to filter on".
    assert bank_suggestions_for_mode(ScoutMode.MICRO, bank_path=bank_path) == []


def test_missing_bank_file_yields_no_suggestions(tmp_path):
    missing = tmp_path / "does_not_exist.md"
    assert bank_suggestions_for_mode(ScoutMode.QA, bank_path=missing) == []


def test_missing_banlist_file_does_not_crash_and_bans_nothing(tmp_path):
    bank_path = tmp_path / "qa_question_bank.md"
    bank_path.write_text(_BANK, encoding="utf-8")
    missing_banlist = tmp_path / "does_not_exist.md"

    suggestions = bank_suggestions_for_mode(
        ScoutMode.QA, bank_path=bank_path, banlist_path=missing_banlist,
    )
    assert {s["question"] for s in suggestions} == {"Open question one?", "Open question two?"}
