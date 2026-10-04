"""Windows-owned, append-only memory for scout and production decisions.

This module deliberately has no workflow hooks. Mac clients can open an existing
database for reads or consume the exported JSONL snapshot; all database writes,
exports, imports, and backups require the Windows-server writer guard.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterable, Mapping


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "ledger" / "ledger.db"
EVENT_KINDS = frozenset({"proposed", "in_progress", "produced", "published", "rejected", "banned", "unbanned"})
MODES = frozenset({"micro", "qa", "recap"})
STATE_PRECEDENCE = {
    "proposed": 1,
    "rejected": 2,
    "in_progress": 3,
    "produced": 4,
    "published": 4,
    "banned": 5,
    "unbanned": 0,
}
_EVENT_FIELDS = ("mode", "kind", "key", "keys", "label", "text", "entities", "scope", "reason_code", "refs")
_DB_COLUMNS = ("id", "ts", *_EVENT_FIELDS)
_WRITE_LOCK = threading.RLock()
_ISSUE_RE = re.compile(r"^\s*(?P<series>.+?)\s+#(?P<number>\d+(?:\.\d+)?[a-z]?)\s*(?:\((?P<year>(?:19|20)\d{2})\))?\s*$", re.I)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


class LedgerWriteDisabledError(RuntimeError):
    """Raised when a non-Windows process attempts a ledger mutation."""


class LedgerSchemaError(ValueError):
    """Raised when a proposed ledger event does not match the event schema."""


@dataclass(frozen=True)
class ImportResult:
    scanned: int
    inserted: int
    skipped: int


def _is_windows_server() -> bool:
    """Require an explicit server deployment marker as well as Windows."""
    return os.name == "nt" and os.environ.get("SCOUT_LEDGER_WRITER", "").strip().casefold() == "windows-server"


def _ensure_not_google_drive(path: Path) -> None:
    blocked = {"google drive", "google_drive", "googledrive", "my drive"}
    components = (*path.parts, *path.resolve().parts)
    if any(part.casefold() in blocked for part in components):
        raise LedgerWriteDisabledError("ledger files cannot be stored in Google Drive")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "mode": str(event.get("mode", "")).strip().lower(),
        "kind": str(event.get("kind", "")).strip().lower(),
        "key": str(event.get("key", "") or "").strip(),
        "keys": sorted({str(item).strip() for item in (event.get("keys") or []) if str(item).strip()}),
        "label": str(event.get("label", "") or "").strip(),
        "text": str(event.get("text", "") or "").strip(),
        "entities": event.get("entities") or [],
        "scope": str(event.get("scope", "item") or "item").strip().lower(),
        "reason_code": str(event.get("reason_code", "") or "").strip(),
        "refs": event.get("refs") or {},
    }
    if normalized["mode"] not in MODES:
        raise LedgerSchemaError(f"mode must be one of {sorted(MODES)}")
    if normalized["kind"] not in EVENT_KINDS:
        raise LedgerSchemaError(f"kind must be one of {sorted(EVENT_KINDS)}")
    if normalized["scope"] not in {"item", "lane"}:
        raise LedgerSchemaError("scope must be 'item' or 'lane'")
    if not isinstance(normalized["entities"], (list, dict)) or not isinstance(normalized["refs"], (list, dict)):
        raise LedgerSchemaError("entities and refs must be JSON objects or arrays")
    if not any((normalized["key"], normalized["keys"], normalized["label"], normalized["text"])):
        raise LedgerSchemaError("event must identify an issue, question, label, or text")
    return normalized


def event_id(event: Mapping[str, Any]) -> str:
    """Stable SHA-256 identity for an event, including its event time."""
    normalized = _normalize_event(event)
    identity = {"event": normalized, "ts": str(event.get("ts") or "")}
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _issue_key(value: str) -> str | None:
    match = _ISSUE_RE.fullmatch(value or "")
    if not match:
        return None
    series = re.sub(r"\s+", " ", match.group("series")).strip()
    # The key's year is the series start year. A year after #N is the issue's
    # publication year and must not be substituted for that distinct value.
    start_match = re.search(r"\s*\(((?:19|20)\d{2})\)\s*$", series)
    start_year = start_match.group(1) if start_match else ""
    if start_match:
        series = series[:start_match.start()].strip()
    slug = re.sub(r"[^a-z0-9]+", "-", series.casefold()).strip("-")
    return f"{slug}|{start_year}|{match.group('number').lower()}"


def _event_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    event = _normalize_event(raw)
    timestamp = str(raw.get("ts") or _now())
    complete = {**event, "ts": timestamp}
    return {"id": event_id(complete), "ts": timestamp, **event}


def answer_sets_are_same_video(left: Iterable[str], right: Iterable[str]) -> bool:
    """Apply the ledger's conservative Q&A overlap signal to issue-key sets."""
    a, b = set(left), set(right)
    if not a or not b:
        return False
    shared = len(a & b)
    return shared / max(len(a), len(b)) >= (2 / 3) or shared / min(len(a), len(b)) >= 0.5


class Ledger:
    """SQLite ledger with a single process-wide, locked transaction writer."""

    def __init__(self, db_path: str | Path | None = None, *, _writer_guard=None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._writer_guard = _writer_guard or _is_windows_server
        self._allow_writes = _writer_guard is not None

    def _require_writer(self) -> None:
        # The injectable guard exists only for isolated tests. Production writes
        # use the module-level Windows gate and the fixed, non-Drive DB location.
        if not self._writer_guard():
            raise LedgerWriteDisabledError("ledger writes are enabled only on the Windows server")
        if not self._allow_writes and self.db_path.resolve() != DEFAULT_DB_PATH.resolve():
            raise LedgerWriteDisabledError(f"production ledger writes must use {DEFAULT_DB_PATH}")
        _ensure_not_google_drive(self.db_path)

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        if write:
            self._require_writer()
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            return sqlite3.connect(self.db_path, timeout=30)
        if self._writer_guard():
            if self.db_path.exists():
                return sqlite3.connect(self.db_path, timeout=30)
            return sqlite3.connect(self.db_path, timeout=30)
        if not self.db_path.exists():
            raise FileNotFoundError(f"ledger snapshot is not present: {self.db_path}")
        uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=10)

    def _write(self, operation):
        self._require_writer()
        with _WRITE_LOCK:
            with self._connect(write=True) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("BEGIN IMMEDIATE")
                self._create_schema(connection)
                return operation(connection)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('micro','qa','recap')),
                kind TEXT NOT NULL CHECK (kind IN ('proposed','in_progress','produced','published','rejected','banned','unbanned')),
                key TEXT NOT NULL DEFAULT '', keys TEXT NOT NULL DEFAULT '[]',
                label TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
                entities TEXT NOT NULL DEFAULT '[]', scope TEXT NOT NULL DEFAULT 'item',
                reason_code TEXT NOT NULL DEFAULT '', refs TEXT NOT NULL DEFAULT '{}'
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS events_mode_key ON events(mode, key)")
        connection.execute("CREATE INDEX IF NOT EXISTS events_kind ON events(kind)")

    def initialize(self) -> None:
        self._write(lambda _connection: None)

    def append_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        row = _event_row(event)

        def insert(connection: sqlite3.Connection):
            values = [row[name] for name in _DB_COLUMNS]
            connection.execute(
                f"INSERT OR IGNORE INTO events ({','.join(_DB_COLUMNS)}) VALUES ({','.join('?' for _ in _DB_COLUMNS)})",
                [self._db_value(name, value) for name, value in zip(_DB_COLUMNS, values)],
            )
            return self._decode_row(connection.execute("SELECT * FROM events WHERE id=?", (row["id"],)).fetchone())

        return self._write(insert)

    def append_events(self, events: Iterable[Mapping[str, Any]]) -> ImportResult:
        rows = [_event_row(event) for event in events]

        def insert(connection: sqlite3.Connection):
            inserted = 0
            for row in rows:
                cursor = connection.execute(
                    f"INSERT OR IGNORE INTO events ({','.join(_DB_COLUMNS)}) VALUES ({','.join('?' for _ in _DB_COLUMNS)})",
                    [self._db_value(name, row[name]) for name in _DB_COLUMNS],
                )
                inserted += cursor.rowcount
            return ImportResult(len(rows), inserted, len(rows) - inserted)

        return self._write(insert)

    @staticmethod
    def _db_value(name: str, value: Any) -> Any:
        return _canonical_json(value) if name in {"keys", "entities", "refs"} else value

    @staticmethod
    def _decode_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        names = _DB_COLUMNS
        result = dict(zip(names, row))
        for name in ("keys", "entities", "refs"):
            result[name] = json.loads(result[name])
        return result

    def events_for_key(self, mode: str, key: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM events WHERE mode=? AND key=? ORDER BY ts,id", (mode, key)).fetchall()
        return [self._decode_row(row) for row in rows]

    def events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY ts,id").fetchall()
        return [self._decode_row(row) for row in rows]

    def effective_state(self, mode: str, key: str) -> str | None:
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        kinds = [event["kind"] for event in self.events_for_key(mode, key)]
        if not kinds:
            return None
        latest_unban = max((i for i, kind in enumerate(kinds) if kind == "unbanned"), default=-1)
        active = [kind for index, kind in enumerate(kinds)
                  if kind != "unbanned" and not (kind == "banned" and index < latest_unban)]
        if not active:
            return None
        return max(active, key=lambda kind: STATE_PRECEDENCE[kind])

    def is_hard_duplicate(self, mode: str, key: str) -> bool:
        # A repeated issue is a hard stop for micro only. Q&A issue reuse remains
        # a ranking hint and recap is an independent production lane.
        return mode == "micro" and self.effective_state(mode, key) in {"in_progress", "produced", "published", "banned"}

    def count_events(self) -> int:
        try:
            with self._connect() as connection:
                return int(connection.execute("SELECT count(*) FROM events").fetchone()[0])
        except sqlite3.OperationalError:
            return 0

    def journal_mode(self) -> str:
        with self._connect() as connection:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def export_jsonl(self, destination: str | Path) -> Path:
        """Write the server snapshot; Mac clients read this file without mutation."""
        self._require_writer()
        target = Path(destination)
        _ensure_not_google_drive(target)
        temp = target.with_name(target.name + ".tmp")
        database = self.db_path.resolve()
        if target.resolve() == database or temp.resolve() == database:
            raise ValueError("export destination or temporary file cannot replace the ledger database")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(_canonical_json(row) + "\n" for row in self.events())
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
        return target

    def import_jsonl(self, source: str | Path) -> ImportResult:
        self._require_writer()
        rows = []
        with Path(source).open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    _normalize_event(row)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise LedgerSchemaError(f"invalid JSONL ledger row {line_number}") from exc
                rows.append(row)
        return self.append_events(rows)

    def backup(self, destination: str | Path) -> Path:
        """Create a consistent snapshot with SQLite's VACUUM INTO command."""
        self._require_writer()
        target = Path(destination)
        _ensure_not_google_drive(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"backup already exists: {target}")
        self.initialize()
        with _WRITE_LOCK, self._connect(write=True) as connection:
            connection.execute("VACUUM INTO ?", (str(target),))
        return target

    def _import_records(self, records: list[dict[str, Any]]) -> ImportResult:
        # Legacy files do not carry event times. A fixed migration time makes
        # repeated imports stable; refs retain the original source/row location.
        for record in records:
            record.setdefault("ts", "1970-01-01T00:00:00Z")
        return self.append_events(records)

    def import_comic_candidates(self, source: str | Path) -> ImportResult:
        records = []
        path = Path(source)
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for line_number, row in enumerate(csv.DictReader(stream), 2):
                title = (row.get("title") or "").strip()
                if not title:
                    continue
                year = (row.get("year") or "").strip()
                slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
                status = (row.get("status") or "").casefold()
                kind = "banned" if "banned" in status else "produced" if "produced" in status or "shipped" in status else "proposed"
                records.append({
                    "mode": "recap", "kind": kind, "key": f"{slug}|{year}|*", "label": title,
                    "scope": "item", "refs": {"source": path.name, "line": line_number, "reader_url": row.get("reader_url", "")},
                })
        return self._import_records(records)

    def import_banlist(self, source: str | Path) -> ImportResult:
        path = Path(source)
        records = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            text = line.strip().strip("| ")
            if not text or text.startswith(("#", ">", "---")) or text.casefold().startswith(("question /", "scouted ", "date |", "question |", "series |")):
                continue
            issue_key = None
            for cell in (text, *text.split("|")):
                candidate = re.sub(r"^\s*\[[^\]]+\]\s*", "", cell.strip())
                issue_key = _issue_key(candidate)
                if issue_key:
                    break
            mode = "micro" if issue_key or "micro" in text.casefold() else "qa"
            records.append({"mode": mode, "kind": "banned", "key": issue_key or "", "text": text,
                            "scope": "lane", "refs": {"source": path.name, "line": line_number}})
        return self._import_records(records)

    def import_question_bank(self, source: str | Path) -> ImportResult:
        path = Path(source)
        records = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            cells = [part.strip() for part in line.strip().strip("|").split("|")]
            if len(cells) < 2 or cells[0].casefold() in {"status", "---", "question"} or set("-:") >= set(cells[0]):
                continue
            if "?" not in " ".join(cells):
                continue
            status, question = cells[0].casefold(), cells[1]
            if not question:
                continue
            kind = "banned" if any(word in status for word in ("ban", "reject", "done", "produced")) else "proposed"
            records.append({"mode": "qa", "kind": kind, "key": "", "label": question,
                            "scope": "lane", "refs": {"source": path.name, "line": line_number}})
        return self._import_records(records)

    def import_projects(self, projects_root: str | Path) -> ImportResult:
        """Conservatively snapshot existing project contexts; never edits projects."""
        root = Path(projects_root)
        records = []
        for context_path in sorted(root.glob("*/answer_context.json")):
            try:
                context = json.loads(context_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            issue_keys = []
            for item in context.get("items", []) if isinstance(context, dict) else []:
                if not isinstance(item, dict):
                    continue
                value = item.get("series_issue_year") or item.get("comic") or item.get("issue") or ""
                key = _issue_key(str(value))
                if key and item.get("series_start_year") and not key.split("|")[1]:
                    key = f"{key.split('|')[0]}|{item['series_start_year']}|{key.split('|')[2]}"
                if key:
                    issue_keys.append(key)
            question = str(context.get("question") or context.get("title") or context_path.parent.name)
            records.append({"mode": "qa", "kind": "produced", "key": "", "keys": issue_keys,
                            "label": question, "text": question, "scope": "item",
                            "refs": {"project": context_path.parent.name, "source": "answer_context.json"}})
        for context_path in sorted(root.glob("*/comic_context.json")):
            try:
                context = json.loads(context_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(context, dict) or (context_path.parent / "answer_context.json").exists():
                continue
            micro = context.get("scout_candidate")
            if not micro and (context_path.parent / "scout_candidate.json").exists():
                try:
                    micro = json.loads((context_path.parent / "scout_candidate.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    micro = None
            value = ""
            if isinstance(micro, dict):
                value = str(micro.get("series_issue_year") or "")
            if not value:
                value = str(context.get("series_issue_year") or context.get("title") or "")
            key = _issue_key(value)
            start_year = context.get("series_start_year")
            if key and start_year and not key.split("|")[1]:
                key = f"{key.split('|')[0]}|{start_year}|{key.split('|')[2]}"
            if not key:
                continue
            records.append({"mode": "micro" if micro else "recap", "kind": "produced", "key": key,
                            "label": value, "scope": "item", "refs": {"project": context_path.parent.name, "source": "comic_context.json"}})
        return self._import_records(records)


def shadow_state(ledger: Ledger, mode: str, key: str) -> dict[str, Any]:
    """Read-only decision primitive for later shadow comparison with legacy logic."""
    return {"mode": mode, "key": key, "state": ledger.effective_state(mode, key),
            "hard_duplicate": ledger.is_hard_duplicate(mode, key)}


def read_export_jsonl(source: str | Path) -> list[dict[str, Any]]:
    """Read and validate a server snapshot without opening a writable store."""
    rows = []
    with Path(source).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                _normalize_event(row)
                if not row.get("id") or not row.get("ts"):
                    raise LedgerSchemaError("snapshot row is missing id or ts")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise LedgerSchemaError(f"invalid JSONL ledger row {line_number}") from exc
            rows.append(row)
    return rows
