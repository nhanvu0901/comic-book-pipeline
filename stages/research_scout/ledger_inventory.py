"""Read production reservations from the shared ledger and approved projects."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

import config

from . import issue_identity, ledger_shadow
from .micro_recency import issue_publication_year
from .ledger import STATE_PRECEDENCE
from .models import ScoutMode

_ACTIVE = {"in_progress", "produced", "published", "banned"}
_ISSUE_MARKER = re.compile(r"#\s*\d+(?:\.\d+)?[A-Za-z]?(?!\w)")
_TRAILING_START_YEAR = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")


def micro_identity(label: str):
    """Parse series and issue while keeping a pre-# launch year out of pub-year parsing."""
    try:
        identity = issue_identity._candidate_identity(label)
    except (TypeError, ValueError):
        identity = None
    if identity is not None:
        return identity
    marker = _ISSUE_MARKER.search(label or "")
    if marker is None:
        return None
    prefix = _TRAILING_START_YEAR.sub("", str(label)[:marker.start()])
    cleaned = prefix + str(label)[marker.start():]
    try:
        return issue_identity._candidate_identity(cleaned)
    except (TypeError, ValueError):
        return None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_approved_project(project: Path) -> bool:
    state = _json(project / "state.json")
    narration_path = project / "narration.json"
    narration = _json(narration_path)
    final = project / "final.mp4"
    from .production_ledger import is_verified_final
    if is_verified_final(project):
        return True
    if not isinstance(state, dict) or not isinstance(narration, (dict, list)):
        return False
    approved = state.get("approved")
    if not isinstance(approved, dict) or not approved.get("2"):
        return False
    expected = str(state.get("approved_narration_sha256") or "").strip()
    if not expected:  # legacy Stage 2 approval predating the hash field
        return True
    try:
        actual = hashlib.sha256(narration_path.read_bytes()).hexdigest()
    except OSError:
        return False
    return actual == expected


def _local_events(mode: str) -> list[dict[str, Any]]:
    root = Path(config.PROJECTS_ROOT)
    rows: list[dict[str, Any]] = []
    for project in sorted(root.iterdir()) if root.exists() else ():
        if not project.is_dir() or not _is_approved_project(project):
            continue
        answer = _json(project / "answer_context.json")
        if isinstance(answer, dict):
            project_mode = "qa"
            if project_mode == mode:
                question = str(answer.get("question") or answer.get("title") or "").strip()
                issue_labels = []
                for item in answer.get("items", []) if isinstance(answer.get("items"), list) else []:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("source_comic") or item.get("series_issue_year")
                                or item.get("comic") or item.get("issue") or "").strip()
                    source_year = str(item.get("source_year") or "").strip()
                    if label and source_year.isdigit() and len(source_year) == 4 and issue_publication_year(label) is None:
                        label = f"{label} ({source_year})"
                    if label:
                        issue_labels.append(label)
                rows.append({"mode": "qa", "kind": "in_progress", "label": question,
                             "text": question, "keys": [], "issue_labels": issue_labels, "key": ""})
            continue
        context = _json(project / "comic_context.json")
        if not isinstance(context, dict):
            continue
        candidate = _json(project / "scout_candidate.json")
        if candidate is None and isinstance(context.get("scout_candidate"), dict):
            candidate = context["scout_candidate"]
        is_micro = isinstance(candidate, dict) or context.get("pipeline_mode") == "micro_moment"
        project_mode = "micro" if is_micro else "recap"
        if project_mode != mode:
            continue
        source = candidate if isinstance(candidate, dict) else context
        label = str(source.get("series_issue_year") or source.get("comic") or "").strip()
        if not label:
            label = str(context.get("series_issue_year") or "").strip()
        if not label:
            series = str(source.get("series") or context.get("series") or "").strip()
            issue = str(source.get("issue") or context.get("issue") or "").strip().lstrip("#")
            if series and issue:
                label = f"{series} #{issue}"
        if not label:
            label = str(context.get("title") or "").strip()
        source_year = str(source.get("source_year") or context.get("source_year")
                          or context.get("year") or "").strip()
        if (label and source_year.isdigit() and len(source_year) == 4
                and issue_publication_year(label) is None):
            label = f"{label} ({source_year})"
        if label:
            rows.append({"mode": project_mode, "kind": "in_progress", "label": label,
                         "text": "", "keys": [], "key": ""})
    return rows


def _rows_active(rows: list[dict[str, Any]]) -> bool:
    ordered = sorted(rows, key=lambda e: (str(e.get("ts", "")), str(e.get("id", ""))))
    last_unban = max((i for i, e in enumerate(ordered) if e.get("kind") == "unbanned"), default=-1)
    kinds = [str(e.get("kind")) for i, e in enumerate(ordered)
             if e.get("kind") != "unbanned" and not (e.get("kind") == "banned" and i < last_unban)]
    return bool(kinds) and max(kinds, key=lambda k: STATE_PRECEDENCE.get(k, 0)) in _ACTIVE


def _active_key_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda e: (str(e.get("ts", "")), str(e.get("id", ""))))
    last_unban = max((i for i, e in enumerate(ordered) if e.get("kind") == "unbanned"), default=-1)
    return [event for i, event in enumerate(ordered)
            if event.get("kind") in _ACTIVE
            and not (event.get("kind") == "banned" and i < last_unban)]


def load_production_inventory(
    mode: ScoutMode | str,
) -> tuple[list[str], set[tuple[str, str, str]], list[str], str]:
    """Return labels, Micro (series, issue, publication-year) keys, QA questions and snapshot status."""
    mode_value = str(getattr(mode, "value", mode))
    if mode_value not in {"qa", "micro", "recap"}:
        raise ValueError(f"unsupported scout inventory mode: {mode_value}")
    events, source, status, _error = ledger_shadow._read_events(
        db_path=ledger_shadow.DEFAULT_DB_PATH,
        export_path=ledger_shadow.DEFAULT_EXPORT_PATH,
        windows_server=ledger_shadow.ledger._is_windows_server(),
    )
    labels: list[str] = []
    questions: list[str] = []
    keys: set[tuple[str, str, str]] = set()
    local = _local_events(mode_value)
    all_events = (events or []) + local
    if mode_value == "qa":
        # QA reservations are grouped by their stable question key when present;
        # legacy/context rows have an empty key and remain useful as local holds.
        groups = sorted({str(e.get("key") or e.get("text") or e.get("label") or "") for e in all_events
                         if e.get("mode") == "qa"}, key=str.casefold)
        for group in groups:
            members = [e for e in all_events if e.get("mode") == "qa"
                       and (str(e.get("key") or e.get("text") or e.get("label") or "") == group)]
            if not any(e in local for e in members) and not _rows_active(members):
                continue
            text = next((str(e.get("text") or e.get("label") or "").strip() for e in members
                         if str(e.get("text") or e.get("label") or "").strip()), "")
            if text:
                questions.append(text)
            for e in members:
                labels.extend(str(label).strip() for label in e.get("issue_labels", []) if str(label).strip())
            for e in members:
                for item in e.get("keys", []) if isinstance(e.get("keys"), list) else []:
                    parts = str(item).split("|")
                    if len(parts) == 3:
                        labels.append(f"{parts[0]} #{parts[2]}")
        stable = lambda value: (value.casefold(), value)
        return sorted(set(labels), key=stable), keys, sorted(set(questions), key=stable), f"{source or 'ledger'}:{status}"

    labels.extend(str(event.get("label") or "").strip() for event in local
                  if event.get("mode") == mode_value and str(event.get("label") or "").strip())
    if mode_value == "micro":
        for event in local:
            label = str(event.get("label") or "").strip()
            if event.get("mode") != "micro" or not label:
                continue
            try:
                identity = micro_identity(label)
            except (TypeError, ValueError):
                identity = None
            publication_year = issue_publication_year(label)
            if identity is not None and publication_year is not None:
                keys.add((issue_identity._normal_series(identity.series), identity.number,
                          str(publication_year)))
    ledger_rows = [event for event in (events or []) if event.get("mode") == mode_value]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in ledger_rows:
        grouped.setdefault(str(event.get("key") or ""), []).append(event)
    for key, members in grouped.items():
        if not key:
            continue
        for event in _active_key_rows(members):
            label = str(event.get("label") or "").strip()
            if not label:
                continue
            labels.append(label)
        if mode_value == "micro":
            for event in _active_key_rows(members):
                label = str(event.get("label") or "").strip()
                if not label:
                    continue
                try:
                    identity = micro_identity(label)
                except (TypeError, ValueError):
                    identity = None
                publication_year = issue_publication_year(label)
                # The label's year after #N is publication year. Never infer it from
                # the ledger key, whose middle component is series-start year.
                if identity is not None and publication_year is not None:
                    keys.add((issue_identity._normal_series(identity.series), identity.number,
                              str(publication_year)))
    return sorted(set(labels), key=lambda value: (value.casefold(), value)), keys, questions, f"{source or 'ledger'}:{status}"
