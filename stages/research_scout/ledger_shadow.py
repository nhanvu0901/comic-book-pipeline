"""Read-only comparison between legacy scout filtering and ledger decisions."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence

from . import ledger

DEFAULT_DB_PATH = ledger.DEFAULT_DB_PATH
DEFAULT_EXPORT_PATH = ledger._REPO_ROOT / "data" / "ledger" / "export.jsonl"


def _read_events(
    *, db_path: str | Path, export_path: str | Path, windows_server: bool,
) -> tuple[list[dict[str, Any]] | None, str | None, str, str | None]:
    """Load a snapshot without creating a database or mutating either source."""
    database = Path(db_path)
    export = Path(export_path)
    if windows_server and database.exists():
        source = "database"
        try:
            # A false guard forces Ledger._connect into SQLite URI mode=ro,
            # including on the server process that normally owns writes.
            events = ledger.Ledger(database, _writer_guard=lambda: False).events()
            return events, source, "ready", None
        except (OSError, sqlite3.Error, ValueError) as exc:
            return None, source, "corrupted", str(exc)

    source = "export"
    if not export.exists():
        return None, None, "unavailable", "ledger snapshot is not present"
    try:
        return ledger.read_export_jsonl(export), source, "ready", None
    except (OSError, ValueError, TypeError) as exc:
        return None, source, "corrupted", str(exc)


def _effective_state(events: Sequence[Mapping[str, Any]], mode: str, key: str) -> str | None:
    matching = sorted(
        (event for event in events if event.get("mode") == mode and event.get("key") == key),
        key=lambda event: (str(event.get("ts", "")), str(event.get("id", ""))),
    )
    if not matching:
        return None
    latest_unban = max(
        (index for index, event in enumerate(matching) if event.get("kind") == "unbanned"),
        default=-1,
    )
    active = [
        str(event["kind"])
        for index, event in enumerate(matching)
        if event.get("kind") != "unbanned"
        and not (event.get("kind") == "banned" and index < latest_unban)
    ]
    if not active:
        return None
    return max(active, key=lambda kind: ledger.STATE_PRECEDENCE[kind])


def _resolve_candidate_key(
    events: Sequence[Mapping[str, Any]], mode: str, candidate_key: str,
) -> tuple[str | None, bool]:
    """Resolve an unknown series-start year only when the ledger has one identity.

    A title like ``Series #12 (2020)`` contains an issue publication year, not
    the series start year. If the ledger has multiple starts for the same
    normalized series and issue, choosing one would conflate relaunches.
    """
    slug, start_year, issue_number = candidate_key.split("|", 2)
    exact = any(
        event.get("mode") == mode and event.get("key") == candidate_key
        for event in events
    )
    if exact or start_year:
        return candidate_key, False

    matching_keys = {
        str(event.get("key", ""))
        for event in events
        if event.get("mode") == mode
        and len(str(event.get("key", "")).split("|")) == 3
        and str(event.get("key", "")).split("|")[0] == slug
        and str(event.get("key", "")).split("|")[2] == issue_number
    }
    if len(matching_keys) == 1:
        return next(iter(matching_keys)), False
    if len(matching_keys) > 1:
        return None, True
    return candidate_key, False


def build_shadow_report(
    candidates: Sequence[Mapping[str, Any]],
    legacy_rejections: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    db_path: str | Path | None = None,
    export_path: str | Path | None = None,
    windows_server: bool | None = None,
) -> dict[str, Any]:
    """Compare micro hard-duplicate outcomes without affecting acceptance."""
    report: dict[str, Any] = {
        "mode": mode,
        "hard_duplicate_policy": "micro_only",
        "status": "skipped_mode" if mode != "micro" else "unavailable",
        "source": None,
        "candidate_count": len(candidates),
        "comparable_count": 0,
        "unique_identity_match_count": 0,
        "ambiguous_count": 0,
        "uncomparable_count": 0,
        "legacy_hard_duplicate_count": 0,
        "ledger_hard_duplicate_count": 0,
        "disagreement_count": 0,
        "disagreements": [],
    }
    if mode != "micro":
        return report

    database_path = db_path or DEFAULT_DB_PATH
    snapshot_path = export_path or DEFAULT_EXPORT_PATH
    writer_host = ledger._is_windows_server() if windows_server is None else windows_server
    events, source, status, error = _read_events(
        db_path=database_path, export_path=snapshot_path, windows_server=writer_host,
    )
    report.update({"source": source, "status": status})
    if error:
        report["error"] = error
    if events is None:
        return report

    duplicate_ids = {
        str(item.get("candidate_id", ""))
        for item in legacy_rejections
        if item.get("reason") == "duplicate_issue_key"
    }
    disagreements = []
    for candidate in candidates:
        candidate_id = str(candidate.get("id", ""))
        label = str(candidate.get("series_issue_year", ""))
        key = ledger._issue_key(label)
        if key is None:
            report["uncomparable_count"] += 1
            continue
        resolved_key, ambiguous = _resolve_candidate_key(events, "micro", key)
        if ambiguous:
            report["ambiguous_count"] += 1
            report["uncomparable_count"] += 1
            continue
        if resolved_key is None:
            report["uncomparable_count"] += 1
            continue
        report["comparable_count"] += 1
        report["unique_identity_match_count"] += int(resolved_key != key)
        legacy_duplicate = candidate_id in duplicate_ids
        state = _effective_state(events, "micro", resolved_key)
        ledger_duplicate = state in {"in_progress", "produced", "published", "banned"}
        report["legacy_hard_duplicate_count"] += int(legacy_duplicate)
        report["ledger_hard_duplicate_count"] += int(ledger_duplicate)
        if legacy_duplicate != ledger_duplicate:
            disagreements.append({
                "candidate_id": candidate_id,
                "series_issue_year": label,
                "key": key,
                "ledger_key": resolved_key,
                "legacy_hard_duplicate": legacy_duplicate,
                "ledger_hard_duplicate": ledger_duplicate,
                "ledger_state": state,
            })
    report["disagreements"] = disagreements
    report["disagreement_count"] = len(disagreements)
    return report
