"""Remember the questions Master turned down with 'None of these — find 5 more'.

Each question of the batch on screen is appended to the ledger as one
``rejected`` item event (reason ``scout_reroll_rejected``). Discover's question
avoid list reads them back (``ledger_inventory.active_rejections``), in that
mode only, so a question turned down once stays turned down after a reload.

Nothing else reads these rows as a decision: ``rejected`` is outside the
ledger's active kinds, so micro's hard issue duplicate, recap and the Q&A
production inventory behave exactly as before. The micro issue label is kept in
``label``/``refs`` and deliberately NOT in ``key`` — a keyed row would be picked
up by the shadow report's key resolution.

Writes follow ``production_ledger.record_milestone``: Windows server only
(a read-only host returns ``"read_only"``), deterministic ids so a repeat press
is ``"already_recorded"``, and the export snapshot refreshed afterwards.

UNDO — the ledger is append-only, so a rejection is lifted by appending a later
``proposed`` event for the same text (``reason_code`` ``scout_reroll_unrejected``)::

    python -c "from stages.research_scout import scout_rejections as s; \\
        print(s.lift_rejection('qa', ['<the question text>']))"

(run on the Windows server with SCOUT_LEDGER_WRITER=windows-server). A manual
``unbanned`` event with the same text lifts it too. Rejecting the same text
again afterwards records a fresh rejection.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .ledger import Ledger, _canonical_json, _now
from .ledger_inventory import _REJECTION_LIFTS, active_rejections, question_identity

REASON_REJECTED = "scout_reroll_rejected"
REASON_LIFTED = "scout_reroll_unrejected"
SOURCE = "scout_reroll"

# The field of a discover entry that holds the rejected text, per mode.
_TEXT_FIELD = {"qa": "question", "micro": "moment"}
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_LOCK = threading.RLock()


def _mode(mode: Any) -> str:
    value = str(getattr(mode, "value", mode)).strip().lower()
    if value not in _TEXT_FIELD:
        raise ValueError(f"scout rejections exist for {sorted(_TEXT_FIELD)} only, not {value!r}")
    return value


def _entry_rows(mode: str, entries: Sequence[Mapping[str, Any]], context: Mapping[str, Any] | None) -> list[dict]:
    """Ledger events for the real, distinct questions among ``entries``."""
    field = _TEXT_FIELD[mode]
    picked: dict[str, Mapping[str, Any]] = {}
    texts: dict[str, str] = {}
    for entry in entries:
        # ``fallback`` is the raw angle shown when research found nothing: not a
        # question Master read, so not one he turned down.
        if not isinstance(entry, Mapping) or entry.get("fallback"):
            continue
        text = " ".join(str(entry.get(field) or "").split())
        identity = question_identity(text)
        if identity and identity not in picked:
            picked[identity] = entry
            texts[identity] = text
    batch_id = hashlib.sha256("\n".join(sorted(picked)).encode("utf-8")).hexdigest()[:10]
    rows = []
    for position, (identity, entry) in enumerate(picked.items(), 1):
        issue = str(entry.get("series_issue_year") or "").strip()
        character = str(entry.get("character") or "").strip()
        refs: dict[str, Any] = {
            "source": SOURCE, "angle": str(entry.get("angle") or "").strip(),
            "position": position, "batch_size": len(picked), "batch_id": batch_id,
        }
        if issue:
            refs["series_issue_year"] = issue
        if context:
            refs["context"] = dict(context)
        rows.append({
            "mode": mode, "kind": "rejected", "key": "", "text": texts[identity],
            "label": issue or texts[identity], "entities": [character] if character else [],
            "scope": "item", "reason_code": REASON_REJECTED, "refs": refs,
        })
    return rows


def _next_ts(previous: str | None) -> str:
    """Now, but strictly after ``previous`` so a lift and a re-reject made within
    one second still sort in the order they happened."""
    now = _now()
    if not previous or previous < now:
        return now
    try:
        return (datetime.strptime(previous, _TS_FORMAT) + timedelta(seconds=1)).strftime(_TS_FORMAT)
    except ValueError:
        return now


def _history(events: list[dict[str, Any]], mode: str) -> tuple[dict[str, int], dict[str, str]]:
    """Per question identity: how many lifts it has had, and its newest ts."""
    lifts: dict[str, int] = {}
    newest: dict[str, str] = {}
    for event in events:
        kind = event.get("kind")
        if event.get("mode") != mode or (kind != "rejected" and kind not in _REJECTION_LIFTS):
            continue
        identity = question_identity(event.get("text") or event.get("label"))
        if not identity:
            continue
        if kind in _REJECTION_LIFTS:
            lifts[identity] = lifts.get(identity, 0) + 1
        newest[identity] = max(newest.get(identity, ""), str(event.get("ts", "")))
    return lifts, newest


def _refresh_export(store: Ledger) -> bool:
    try:
        store.export_jsonl(store.db_path.with_name("export.jsonl"))
    except Exception:
        return False
    return True


def record_rejected(
    mode: Any, entries: Sequence[Mapping[str, Any]], *,
    ledger: Ledger | None = None, context: Mapping[str, Any] | None = None,
) -> str:
    """Append one ``rejected`` event per distinct question in ``entries``.

    Returns ``nothing_to_record`` | ``read_only`` | ``recorded`` |
    ``already_recorded`` | ``export_pending`` (rows saved, snapshot refresh
    failed). Raises only for a mode that has no rejections (recap).
    """
    mode_value = _mode(mode)
    rows = _entry_rows(mode_value, entries, context)
    if not rows:
        return "nothing_to_record"
    store = ledger or Ledger()
    if not store._writer_guard():
        return "read_only"
    with _LOCK:
        store.initialize()
        lifts, newest = _history(store.events(), mode_value)
        inserted = 0
        for row in rows:
            identity = question_identity(row["text"])
            # The id carries the lift count, so the same text can be rejected
            # again after an undo, while a repeat press (same count) is ignored.
            milestone_id = hashlib.sha256(_canonical_json({
                "mode": mode_value, "kind": "rejected", "text": identity,
                "lifts": lifts.get(identity, 0),
            }).encode("utf-8")).hexdigest()
            _stored, was_inserted = store.append_milestone(
                {**row, "ts": _next_ts(newest.get(identity))}, milestone_id,
            )
            inserted += int(was_inserted)
        exported = _refresh_export(store)
    if not exported:
        return "export_pending"
    return "recorded" if inserted else "already_recorded"


def lift_rejection(mode: Any, texts: Sequence[str], *, ledger: Ledger | None = None) -> str:
    """Undo rejections: append a ``proposed`` event for each text still rejected.

    Returns ``nothing_to_lift`` | ``read_only`` | ``lifted`` | ``export_pending``.
    """
    mode_value = _mode(mode)
    store = ledger or Ledger()
    if not store._writer_guard():
        return "read_only"
    with _LOCK:
        store.initialize()
        events = store.events()
        live = {question_identity(row["text"] or row["label"]): row
                for row in active_rejections(events, mode_value)}
        _lifts, newest = _history(events, mode_value)
        wanted = list(dict.fromkeys(question_identity(text) for text in texts))
        lifted = 0
        for identity in wanted:
            row = live.get(identity)
            if row is None:
                continue
            store.append_event({
                "mode": mode_value, "kind": "proposed", "key": "", "text": row["text"],
                "label": row["label"], "scope": "item", "reason_code": REASON_LIFTED,
                "refs": {"source": SOURCE, "lifts": row["id"]},
                "ts": _next_ts(newest.get(identity)),
            })
            lifted += 1
        if not lifted:
            return "nothing_to_lift"
        exported = _refresh_export(store)
    return "lifted" if exported else "export_pending"
