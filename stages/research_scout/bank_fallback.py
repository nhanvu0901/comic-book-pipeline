"""Tier A of the empty-intent fallback: still-open qa_question_bank.md rows.

Master 2026-08-22: an empty research intent used to raise ValueError two layers
up (ui/bridge.py + the UI's own guard). The two-tier fix lives in two places —
this module is Tier A (bank-first, zero API cost); Tier B (angle rotation) is
`ScoutWorkflow.next_angle` in workflow.py. Both are wired together in
ui/bridge.py::start_scout_session.

Tier A only applies to QA: qa_question_bank.md is a QUESTION backlog (the
`explore_answer` shape). micro_moment has no equivalent backlog file, so it
always falls straight through to Tier B.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import ScoutMode

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Leading token of a Status cell, case-insensitively, ignoring any trailing
# parenthetical/date text ("PRODUCED 2026-07-14 (projects/... )" -> PRODUCED).
_STATUS_TOKEN_RE = re.compile(r"\s*([A-Za-z][A-Za-z-]*)")
_OPEN_TOKENS = frozenset({"SAVE-FOR-LATER", "CANDIDATE-BLOCKED"})

# First cell of a markdown table's header row, for either table shape this
# fallback reads (qa_question_bank.md: "Status | Question | ..."; the reject
# table in qa_question_banlist.md: "Date | Question | Reason").
_HEADER_FIRST_CELLS = frozenset({"status", "date", "date produced"})


def normalize_question(text: str) -> str:
    """Lowercase, punctuation- and whitespace-stripped form used to compare a
    bank question against a banlist question ('Who Has...?' == 'who has...')."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _status_token(status: str) -> str:
    match = _STATUS_TOKEN_RE.match(status or "")
    return match.group(1).upper() if match else ""


def is_open_status(status: str) -> bool:
    """OPEN = SAVE-FOR-LATER or CANDIDATE-BLOCKED. Everything else (REJECTED,
    PRODUCED, or anything unrecognized) is treated as closed — an unknown
    status is not a safe thing to resurface as a suggestion."""
    return _status_token(status) in _OPEN_TOKENS


def _table_rows(text: str) -> list[list[str]]:
    """Cell lists for every markdown-table row in `text`.

    Skips LESSON blockquotes ('>' lines — qa_question_bank.md's trailing notes,
    not table rows) and separator rows ('|---|...'). Does NOT skip header rows
    — callers decide that, since the two files this reads use different first
    header cells ("Status" vs "Date").
    """
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith(">"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or not cells[0]:
            continue
        if re.fullmatch(r":?-+:?", cells[0]):
            continue  # "|---|---|" separator row
        rows.append(cells)
    return rows


def _is_header_row(cells: list[str]) -> bool:
    return bool(cells) and cells[0].strip().lower() in _HEADER_FIRST_CELLS


def parse_bank_rows(text: str) -> list[dict]:
    """[{"status", "question"}, ...] for every real row of qa_question_bank.md,
    in file order. Column 0 is Status, column 1 is Question — the other two
    columns (answer items, notes) aren't needed to decide what to surface."""
    rows = []
    for cells in _table_rows(text):
        if _is_header_row(cells) or len(cells) < 2:
            continue
        rows.append({"status": cells[0], "question": cells[1]})
    return rows


def banlist_questions(text: str) -> set[str]:
    """Normalized question text for every row of qa_question_banlist.md — both
    the rejection table and the "Produced" table, since a bank question can
    collide with either and banlist always wins either way."""
    out: set[str] = set()
    for cells in _table_rows(text):
        if _is_header_row(cells) or len(cells) < 2:
            continue
        question = cells[1]
        if question:
            out.add(normalize_question(question))
    return out


def bank_suggestions_for_mode(
    mode: ScoutMode,
    *,
    bank_path: Path | None = None,
    banlist_path: Path | None = None,
) -> list[dict]:
    """Still-open bank candidates for `mode`, banlist-filtered, in file order.

    Empty for MICRO (no bank file exists for that mode — see module docstring)
    and empty whenever the bank file itself is missing or has nothing open.
    """
    if mode is not ScoutMode.QA:
        return []
    bank_path = bank_path or (_REPO_ROOT / "qa_question_bank.md")
    if not bank_path.exists():
        return []
    banlist_path = banlist_path or (_REPO_ROOT / "qa_question_banlist.md")
    banned = (
        banlist_questions(banlist_path.read_text(encoding="utf-8"))
        if banlist_path.exists()
        else set()
    )
    open_rows = [
        row for row in parse_bank_rows(bank_path.read_text(encoding="utf-8"))
        if is_open_status(row["status"])
    ]
    return [row for row in open_rows if normalize_question(row["question"]) not in banned]
