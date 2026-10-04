"""Build a small, deterministic list of relevant issue identities to avoid."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import config
from utils.lexical_sim import token_jaccard

from . import issue_identity
from . import ledger_inventory
from .micro_recency import issue_publication_year
from .models import ScoutMode

_ISSUE = re.compile(
    r"(?P<series>[^#|\n;]+?)\s*#(?P<first>\d+)(?:\s*[-–—]\s*(?P<last>\d+))?"
    r"\s*(?:\((?P<year>\d{4})\)|,\s*(?P<year2>\d{4}))"
)


def _labels(text: str) -> list[str]:
    labels: list[str] = []
    for match in _ISSUE.finditer(text or ""):
        series = match.group("series").strip(" -*\t")
        # Ban-list rows prefix a mode tag and title; if the row has a parenthesized
        # canonical comic label, use the inner series instead of that prose.
        if "(" in series:
            series = series.rsplit("(", 1)[-1].strip()
        series = re.sub(r"^\[[^]]+\]\s*", "", series).strip()
        year = match.group("year") or match.group("year2")
        first, last = int(match.group("first")), int(match.group("last") or match.group("first"))
        for number in range(first, min(last, first + 100) + 1):
            labels.append(f"{series} #{number} ({year})")
    return [label for label in labels if label.split(" #", 1)[0].strip()]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _walk_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _walk_text(child)]
    if isinstance(value, list):
        return [text for child in value for text in _walk_text(child)]
    return []


def _inventory(mode: ScoutMode) -> list[str]:
    root = Path(config.PROJECTS_ROOT)
    mode_value = str(getattr(mode, "value", mode))
    try:
        found, _keys, _questions, _status = ledger_inventory.load_production_inventory(mode)
    except (OSError, ValueError, TypeError):
        found = []
    if str(getattr(mode, "value", mode)) == "recap":
        csv_path = Path(getattr(config, "COMIC_CANDIDATES_CSV", root.parent / "comic_candidates.csv"))
        if csv_path.exists():
            try:
                with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        found.extend(label for value in row.values() for label in _labels(str(value or "")))
            except OSError:
                pass
    if mode_value == ScoutMode.QA.value:
        banlist = Path(__file__).resolve().parents[2] / "qa_question_banlist.md"
        if banlist.exists():
            found.extend(label for label in _labels(banlist.read_text(encoding="utf-8", errors="ignore")))
    return found


def relevant_question_avoid_lines(
    mode: ScoutMode | str, user_intent: str = "", extra_held=(), limit: int = 50,
) -> list[str]:
    """Question text for discover's question-level burn filter."""
    result = [str(item).strip() for item in extra_held if str(item).strip()]
    limit = max(0, min(int(limit), 50))
    mode_value = str(getattr(mode, "value", mode))
    if mode_value != ScoutMode.QA.value:
        return list(dict.fromkeys(result))[:limit]
    path = Path(__file__).resolve().parents[2] / "qa_question_banlist.md"
    try:
        _labels, _keys, questions, _status = ledger_inventory.load_production_inventory(ScoutMode.QA)
        result.extend(questions)
    except (OSError, ValueError, TypeError):
        pass
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 2 or not re.match(r"(?:19|20)\d{2}-\d{2}-\d{2}", cells[0]):
                continue
            question = re.sub(r"\s+", " ", cells[1])
            if question:
                result.append(question)
    return list(dict.fromkeys(result))[:limit]


def _key(label: str) -> tuple[str, str, str] | None:
    try:
        parsed = ledger_inventory.micro_identity(label)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    publication_year = issue_publication_year(label)
    if publication_year is None:
        return None
    return issue_identity._normal_series(parsed.series), parsed.number, str(publication_year)


def inventory_issue_keys(mode: ScoutMode | str) -> set[tuple[str, str, str]]:
    """Return every issue key in the local inventory, without the prompt cap.

    ``relevant_avoid_lines`` is intentionally limited to 50 entries for prompt
    size. Micro's one-video-per-issue rule is a code gate, so it must consult
    the full inventory independently of which labels rank into that prompt.
    """
    mode_value = str(getattr(mode, "value", mode))
    keys = {key for label in _inventory(mode) if (key := _key(label))}
    if mode_value == ScoutMode.MICRO.value:
        try:
            _labels, ledger_keys, _questions, _status = ledger_inventory.load_production_inventory(mode)
            keys.update(ledger_keys)
        except (OSError, ValueError, TypeError):
            pass
    return keys


def relevant_avoid_lines(
    mode: ScoutMode | str, user_intent: str, plan: Any = None,
    extra_held=(), limit: int = 50,
) -> list[str]:
    """Return up to ``limit`` issue labels, with current held items first."""
    limit = max(0, min(int(limit), 50))
    held = [str(item).strip() for item in extra_held if str(item).strip()]
    candidates = held + _inventory(mode)
    intent = str(user_intent or "")
    plan_text = str(getattr(plan, "research_prompt", "") or "")
    context = f"{intent} {plan_text}"
    unique: dict[tuple[str, str, str] | str, str] = {}
    for label in candidates:
        key = _key(label) or label.casefold()
        unique.setdefault(key, label)
    held_keys = {_key(label) or label.casefold() for label in held}
    scored = []
    for key, label in unique.items():
        score = 2 if key in held_keys else token_jaccard(context, label)
        scored.append((key not in held_keys, -score, label.casefold(), label))
    scored.sort()
    return [row[3] for row in scored[:limit]]
