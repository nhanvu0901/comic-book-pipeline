"""The scout's 'None of these — find 5 more' press is Master turning a batch down.

That decision is recorded as one append-only ``rejected`` ledger event per
question, so a later session (even after a reload, even on another machine
reading the export) will not offer the same lane again.

Writes exist only on the Windows server (the ledger's writer guard); a read-only
host must skip cleanly. Undo is a later ``proposed`` event for the same text.
"""
import json

import pytest

from stages.research_scout import ledger_shadow, scout_rejections
from stages.research_scout.ledger import Ledger


def _server(tmp_path) -> Ledger:
    return Ledger(tmp_path / "ledger" / "ledger.db", _writer_guard=lambda: True)


def _read_only(tmp_path) -> Ledger:
    return Ledger(tmp_path / "ledger" / "ledger.db", _writer_guard=lambda: False)


def _qa(*questions, angle="times a famous power or rule failed"):
    return [{"question": q, "angle": angle} for q in questions]


def _rejected(store: Ledger):
    return [e for e in store.events() if e["kind"] == "rejected"]


def test_every_shown_question_becomes_one_rejected_item_event(tmp_path):
    store = _server(tmp_path)
    shown = _qa("Who has lifted Mjolnir?", "Whose healing factor failed?")

    result = scout_rejections.record_rejected("qa", shown, ledger=store)

    assert result == "recorded"
    rows = _rejected(store)
    assert sorted(row["text"] for row in rows) == [
        "Who has lifted Mjolnir?", "Whose healing factor failed?",
    ]
    for row in rows:
        assert row["mode"] == "qa"
        assert row["kind"] == "rejected"
        assert row["scope"] == "item"
        assert row["reason_code"] == "scout_reroll_rejected"
        assert row["label"] == row["text"]
        assert row["refs"]["angle"] == "times a famous power or rule failed"
        assert row["refs"]["batch_size"] == 2
        assert row["refs"]["batch_id"]


def test_pressing_again_does_not_duplicate_a_question(tmp_path):
    store = _server(tmp_path)
    shown = _qa("Who has lifted Mjolnir?", "Whose healing factor failed?")
    scout_rejections.record_rejected("qa", shown, ledger=store)

    again = scout_rejections.record_rejected("qa", shown, ledger=store)

    assert again == "already_recorded"
    assert len(_rejected(store)) == 2


def test_same_question_reworded_in_case_or_spacing_is_still_the_same_row(tmp_path):
    store = _server(tmp_path)
    scout_rejections.record_rejected("qa", _qa("Who has lifted Mjolnir?"), ledger=store)

    scout_rejections.record_rejected(
        "qa", _qa("  who has   LIFTED mjolnir?  "), ledger=store,
    )

    assert len(_rejected(store)) == 1


def test_a_new_question_in_the_same_press_is_still_recorded(tmp_path):
    store = _server(tmp_path)
    scout_rejections.record_rejected("qa", _qa("Who has lifted Mjolnir?"), ledger=store)

    result = scout_rejections.record_rejected(
        "qa", _qa("Who has lifted Mjolnir?", "Whose healing factor failed?"), ledger=store,
    )

    assert result == "recorded"
    assert len(_rejected(store)) == 2


def test_the_angle_fallback_and_blank_entries_are_never_recorded(tmp_path):
    """A dead You.com leaves the raw angle on screen flagged ``fallback``; it is
    not a question Master read and turned down."""
    store = _server(tmp_path)
    shown = [
        {"question": "times a famous power or rule failed", "angle": "x", "fallback": True},
        {"question": "   ", "angle": "x"},
        {"angle": "no text at all"},
    ]

    result = scout_rejections.record_rejected("qa", shown, ledger=store)

    assert result == "nothing_to_record"
    assert not (tmp_path / "ledger").exists()


def test_micro_records_the_moment_and_carries_the_issue_without_a_ledger_key(tmp_path):
    """The issue label rides along for audit, but never as the ledger ``key``:
    a keyed rejected row would change how the micro hard-duplicate gate and the
    shadow report resolve that issue's real production state."""
    store = _server(tmp_path)
    shown = [{
        "moment": "Hero opens the sealed door and frees a prisoner.",
        "series_issue_year": "Hero #2 (2025)", "angle": "a small act", "character": "Hero",
    }]

    scout_rejections.record_rejected("micro", shown, ledger=store)

    (row,) = _rejected(store)
    assert row["mode"] == "micro"
    assert row["text"] == "Hero opens the sealed door and frees a prisoner."
    assert row["label"] == "Hero #2 (2025)"
    assert row["key"] == ""
    assert row["refs"]["series_issue_year"] == "Hero #2 (2025)"
    assert row["entities"] == ["Hero"]
    assert store.effective_state("micro", "hero||2") is None
    assert store.is_hard_duplicate("micro", "hero||2") is False


def test_the_snapshot_is_refreshed_once_after_the_batch(tmp_path):
    store = _server(tmp_path)

    scout_rejections.record_rejected(
        "qa", _qa("Who has lifted Mjolnir?", "Whose healing factor failed?"), ledger=store,
    )

    export = tmp_path / "ledger" / "export.jsonl"
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    assert sorted(row["text"] for row in rows) == [
        "Who has lifted Mjolnir?", "Whose healing factor failed?",
    ]


def test_a_failed_export_is_reported_not_raised_and_the_rows_are_kept(tmp_path, monkeypatch):
    store = _server(tmp_path)

    def broken_export(_destination):
        raise OSError("disk full")

    monkeypatch.setattr(store, "export_jsonl", broken_export)

    result = scout_rejections.record_rejected("qa", _qa("Who has lifted Mjolnir?"), ledger=store)

    assert result == "export_pending"
    assert len(_rejected(store)) == 1


def test_a_read_only_host_skips_without_touching_disk(tmp_path):
    store = _read_only(tmp_path)

    result = scout_rejections.record_rejected("qa", _qa("Who has lifted Mjolnir?"), ledger=store)

    assert result == "read_only"
    assert not (tmp_path / "ledger").exists()


def test_the_default_ledger_is_read_only_off_the_windows_server(monkeypatch, tmp_path):
    """No injected ledger: this Mac (or any non-server host) must report
    read_only and never create data/ledger/ledger.db."""
    monkeypatch.setattr(
        "stages.research_scout.ledger.DEFAULT_DB_PATH", tmp_path / "ledger" / "ledger.db",
    )
    monkeypatch.delenv("SCOUT_LEDGER_WRITER", raising=False)

    result = scout_rejections.record_rejected("qa", _qa("Who has lifted Mjolnir?"))

    assert result == "read_only"
    assert not (tmp_path / "ledger").exists()


def test_unknown_mode_is_refused_with_a_schema_error_not_written(tmp_path):
    store = _server(tmp_path)

    with pytest.raises(ValueError):
        scout_rejections.record_rejected("recap", _qa("Anything?"), ledger=store)


# ─── undo ────────────────────────────────────────────────────────────────────


def test_lifting_a_rejection_appends_a_proposed_event_and_nothing_is_deleted(tmp_path):
    store = _server(tmp_path)
    scout_rejections.record_rejected("qa", _qa("Who has lifted Mjolnir?"), ledger=store)

    result = scout_rejections.lift_rejection("qa", ["WHO has lifted  Mjolnir?"], ledger=store)

    assert result == "lifted"
    kinds = [event["kind"] for event in store.events()]
    assert kinds == ["rejected", "proposed"]  # append-only: the rejection is still there
    lift = store.events()[-1]
    assert lift["reason_code"] == "scout_reroll_unrejected"
    assert lift["text"] == "Who has lifted Mjolnir?"  # the text as first recorded


def test_a_lift_is_a_noop_for_a_question_that_was_never_rejected(tmp_path):
    store = _server(tmp_path)

    result = scout_rejections.lift_rejection("qa", ["Never offered?"], ledger=store)

    assert result == "nothing_to_lift"
    assert store.count_events() == 0


def test_lifting_on_a_read_only_host_is_refused_cleanly(tmp_path):
    result = scout_rejections.lift_rejection(
        "qa", ["Who has lifted Mjolnir?"], ledger=_read_only(tmp_path),
    )

    assert result == "read_only"


def test_rejecting_again_after_a_lift_is_recorded_not_swallowed_as_a_duplicate(tmp_path):
    store = _server(tmp_path)
    shown = _qa("Who has lifted Mjolnir?")
    scout_rejections.record_rejected("qa", shown, ledger=store)
    scout_rejections.lift_rejection("qa", ["Who has lifted Mjolnir?"], ledger=store)

    result = scout_rejections.record_rejected("qa", shown, ledger=store)

    assert result == "recorded"
    assert [e["kind"] for e in store.events()] == ["rejected", "proposed", "rejected"]
    # ...and the state the avoid list reads is "rejected again", not "lifted".
    events = store.events()
    assert max(events, key=lambda e: (e["ts"], e["id"]))["kind"] == "rejected"


def test_rejections_are_scoped_to_their_mode(tmp_path):
    store = _server(tmp_path)
    scout_rejections.record_rejected("qa", _qa("Same words?"), ledger=store)
    scout_rejections.record_rejected(
        "micro", [{"moment": "Same words?", "series_issue_year": "Hero #1 (2025)"}], ledger=store,
    )

    assert sorted(row["mode"] for row in _rejected(store)) == ["micro", "qa"]


def test_reads_back_through_the_shadow_reader_on_a_non_server_host(tmp_path):
    """The Mac reads the exported snapshot; the rows we write must survive the
    same validation (read_export_jsonl) that host applies to every row."""
    store = _server(tmp_path)
    scout_rejections.record_rejected("qa", _qa("Who has lifted Mjolnir?"), ledger=store)

    events, source, status, _error = ledger_shadow._read_events(
        db_path=tmp_path / "nope.db",
        export_path=tmp_path / "ledger" / "export.jsonl",
        windows_server=False,
    )

    assert (source, status) == ("export", "ready")
    assert [e["kind"] for e in events] == ["rejected"]
