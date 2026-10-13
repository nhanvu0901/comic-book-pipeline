"""Durable discovery batches for Stage 1 Q&A and Micro.

This is separate from the production/rejection ledger. Historical reroll
rejections remain rejections; they are never imported into the saved library.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .ledger_inventory import question_identity


_TEXT_FIELD = {"qa": "question", "micro": "moment"}


def _mode(value: Any) -> str:
    mode = str(getattr(value, "value", value)).strip().lower()
    if mode not in _TEXT_FIELD:
        raise ValueError(f"Saved Scout supports Q&A and Micro, not {mode!r}")
    return mode


class SavedScoutStore:
    """SQLite keeps batches and full candidate payloads atomic across UI clients."""

    def __init__(self, root: Path):
        self.path = Path(root) / "saved_scout.sqlite3"

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if write:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('saved', 'deferred')),
                    context_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id),
                    mode TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('saved', 'rejected')),
                    UNIQUE(mode, identity)
                );
                CREATE INDEX IF NOT EXISTS saved_scout_batch_entries ON entries(batch_id);
            """)
        return connection

    @contextmanager
    def _connection(self, *, write: bool = False):
        connection = self._connect(write=write)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "batch_id": row["batch_id"], "mode": row["mode"],
            "created_at": row["created_at"],
            "status": "rejected" if row["entry_status"] == "rejected" else row["batch_status"],
            "entry": json.loads(row["payload_json"]),
            "context": json.loads(row["context_json"]),
        }

    def save_batch(
        self, mode: Any, entries: Sequence[Mapping[str, Any]], *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        mode = _mode(mode)
        field = _TEXT_FIELD[mode]
        candidates = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, Mapping) or entry.get("fallback"):
                continue
            identity = question_identity(entry.get(field))
            if identity and identity not in seen:
                candidates.append((identity, dict(entry)))
                seen.add(identity)
        if not candidates:
            return None

        batch_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO batches VALUES (?, ?, ?, 'saved', ?)",
                (batch_id, mode, created_at, json.dumps(dict(context or {}), ensure_ascii=False)),
            )
            inserted = []
            for position, (identity, entry) in enumerate(candidates):
                item_id = uuid4().hex
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO entries VALUES (?, ?, ?, ?, ?, ?, 'saved')",
                    (item_id, batch_id, mode, identity, position,
                     json.dumps(entry, ensure_ascii=False)),
                )
                if cursor.rowcount:
                    inserted.append({"id": item_id, "entry": entry})
            if not inserted:
                connection.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
                return None
        return {"id": batch_id, "mode": mode, "entries": inserted}

    def defer_batch(self, batch_id: str) -> None:
        if not batch_id or not self.path.exists():
            return
        with self._connection(write=True) as connection:
            connection.execute(
                "UPDATE batches SET status = 'deferred' WHERE id = ? AND status = 'saved'",
                (batch_id,),
            )

    def list_entries(
        self, mode: Any | None = None, *, include_discarded: bool = False,
    ) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        args: list[Any] = []
        conditions = []
        if mode is not None:
            conditions.append("e.mode = ?")
            args.append(_mode(mode))
        if not include_discarded:
            conditions.append("e.status != 'rejected'")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = (
            "SELECT e.*, e.status AS entry_status, b.created_at, "
            "b.status AS batch_status, b.context_json FROM entries e "
            "JOIN batches b ON b.id = e.batch_id" + where +
            " ORDER BY b.created_at DESC, e.position ASC"
        )
        with self._connection() as connection:
            return [self._entry(row) for row in connection.execute(query, args)]

    def avoid_texts(self, mode: Any) -> list[str]:
        field = _TEXT_FIELD[_mode(mode)]
        return [row["entry"][field] for row in self.list_entries(mode, include_discarded=True)]

    def discard_entry(self, entry_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT e.*, e.status AS entry_status, b.created_at, "
                "b.status AS batch_status, b.context_json FROM entries e "
                "JOIN batches b ON b.id = e.batch_id WHERE e.id = ?", (entry_id,),
            ).fetchone()
            if row is None or row["entry_status"] == "rejected":
                return None
            connection.execute(
                "UPDATE entries SET status = 'rejected' WHERE id = ?", (entry_id,),
            )
            return self._entry({**dict(row), "entry_status": "rejected"})
