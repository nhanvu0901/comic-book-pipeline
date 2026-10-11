import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from stages.research_scout import ledger as ledger_module
from stages.research_scout.ledger import (
    Ledger,
    LedgerWriteDisabledError,
    answer_sets_are_same_video,
    event_id,
    read_export_jsonl,
)


def windows_ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.db", _writer_guard=lambda: True)


def test_event_id_is_deterministic_for_the_same_event_time():
    base = {"mode": "micro", "kind": "produced", "key": "batman|2016|77"}
    timestamped = {**base, "ts": "2026-10-03T12:00:00Z"}
    assert event_id(timestamped) == event_id(timestamped)
    assert event_id(timestamped) != event_id({**base, "ts": "2026-10-04T12:00:00Z"})


def test_event_insert_is_idempotent_and_wal_enabled(tmp_path):
    ledger = windows_ledger(tmp_path)
    event = {"mode": "micro", "kind": "produced", "key": "batman|2016|77", "ts": "2026-10-03T12:00:00Z"}
    first = ledger.append_event(event)
    second = ledger.append_event(event)
    assert first["id"] == second["id"]
    assert len(ledger.events_for_key("micro", "batman|2016|77")) == 1
    assert ledger.journal_mode() == "wal"


def test_concurrent_writers_preserve_all_events(tmp_path):
    ledgers = [windows_ledger(tmp_path) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: ledgers[i].append_event({
            "mode": "micro", "kind": "produced", "key": f"batman|2016|{i}",
            "ts": "2026-10-03T12:00:00Z",
        }), range(8)))
    assert ledgers[0].count_events() == 8


def test_non_windows_writer_refuses_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "_is_windows_server", lambda: False)
    ledger = Ledger(tmp_path / "ledger.db")
    with pytest.raises(LedgerWriteDisabledError):
        ledger.append_event({"mode": "micro", "kind": "produced", "key": "x|2020|1"})
    assert not (tmp_path / "ledger.db").exists()


def test_windows_host_also_needs_explicit_server_marker(monkeypatch):
    monkeypatch.setenv("SCOUT_LEDGER_WRITER", "windows-server")
    # The marker alone must never enable it on a non-Windows host (pinned, so this also holds
    # when the suite itself runs on the Windows server).
    monkeypatch.setattr(ledger_module.os, "name", "posix")
    assert ledger_module._is_windows_server() is False


def test_windows_writer_requires_both_windows_and_explicit_marker(monkeypatch):
    monkeypatch.setattr(ledger_module.os, "name", "nt")
    monkeypatch.delenv("SCOUT_LEDGER_WRITER", raising=False)
    assert not ledger_module._is_windows_server()
    monkeypatch.setenv("SCOUT_LEDGER_WRITER", "windows-server")
    assert ledger_module._is_windows_server()


def test_status_precedence_and_mode_independence(tmp_path):
    ledger = windows_ledger(tmp_path)
    key = "batman|2016|77"
    for kind in ("proposed", "rejected", "in_progress", "produced", "banned"):
        ledger.append_event({"mode": "micro", "kind": kind, "key": key})
    assert ledger.effective_state("micro", key) == "banned"
    assert ledger.effective_state("recap", key) is None


def test_unbanned_event_clears_prior_ban_and_later_ban_restores_it(tmp_path):
    ledger = windows_ledger(tmp_path)
    key = "batman|2016|77"
    ledger.append_event({"mode": "micro", "kind": "banned", "key": key, "ts": "2026-10-01T00:00:00Z"})
    ledger.append_event({"mode": "micro", "kind": "unbanned", "key": key, "ts": "2026-10-02T00:00:00Z"})
    assert ledger.effective_state("micro", key) is None
    ledger.append_event({"mode": "micro", "kind": "banned", "key": key, "ts": "2026-10-03T00:00:00Z"})
    assert ledger.effective_state("micro", key) == "banned"


def test_micro_duplicate_is_hard_but_qa_issue_overlap_is_soft(tmp_path):
    ledger = windows_ledger(tmp_path)
    ledger.append_event({"mode": "micro", "kind": "produced", "key": "batman|2016|77"})
    assert ledger.is_hard_duplicate("micro", "batman|2016|77")
    assert not ledger.is_hard_duplicate("recap", "batman|2016|77")
    assert not ledger.is_hard_duplicate("qa", "batman|2016|77")
    assert answer_sets_are_same_video({"a", "b", "c"}, {"a", "b", "d"})
    assert not answer_sets_are_same_video({"a", "b", "c", "d"}, {"a", "e", "f", "g"})


def test_import_csv_is_safe_repeatable_and_export_round_trips(tmp_path):
    ledger = windows_ledger(tmp_path / "db")
    csv_path = tmp_path / "comic_candidates.csv"
    csv_path.write_text(
        "title,year,status\nBatman: The Button,2017,produced-banned\n",
        encoding="utf-8",
    )
    first = ledger.import_comic_candidates(csv_path)
    again = ledger.import_comic_candidates(csv_path)
    assert first.inserted == 1
    assert again.inserted == 0
    assert ledger.events_for_key("recap", "batman-the-button|2017|*")[0]["kind"] == "banned"
    export_path = tmp_path / "export.jsonl"
    ledger.export_jsonl(export_path)
    rows = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert read_export_jsonl(export_path) == rows
    copy = windows_ledger(tmp_path / "copy")
    assert copy.import_jsonl(export_path).inserted == 1
    assert copy.import_jsonl(export_path).inserted == 0


def test_banlist_and_question_bank_imports_are_repeatable_and_typed(tmp_path):
    ledger = windows_ledger(tmp_path / "db")
    banlist = tmp_path / "qa_question_banlist.md"
    banlist.write_text(
        "# Banlist\n| 2026-10-03 | [micro] Batman #77 (2019) | held |\n",
        encoding="utf-8",
    )
    bank = tmp_path / "qa_question_bank.md"
    bank.write_text(
        "| Status | Question | Notes |\n|---|---|---|\n| REJECTED | Who beat Batman? | held |\n",
        encoding="utf-8",
    )
    assert ledger.import_banlist(banlist).inserted == 1
    assert ledger.import_banlist(banlist).inserted == 0
    assert ledger.import_question_bank(bank).inserted == 1
    assert ledger.import_question_bank(bank).inserted == 0
    assert ledger.events_for_key("micro", "batman||77")[0]["kind"] == "banned"
    assert any(event["mode"] == "qa" and event["kind"] == "banned" for event in ledger.events())


def test_backup_uses_vacuum_into(tmp_path):
    ledger = windows_ledger(tmp_path)
    ledger.append_event({"mode": "qa", "kind": "proposed", "text": "Who beat X?"})
    backup = ledger.backup(tmp_path / "backup.db")
    assert backup.exists()
    assert Ledger(backup, _writer_guard=lambda: True).count_events() == 1


def test_backup_refuses_to_overwrite_existing_file(tmp_path):
    ledger = windows_ledger(tmp_path)
    destination = tmp_path / "backup.db"
    destination.write_bytes(b"keep this file")
    with pytest.raises(FileExistsError):
        ledger.backup(destination)
    assert destination.read_bytes() == b"keep this file"


def test_project_import_is_repeatable_and_keeps_qa_issue_sets(tmp_path):
    projects = tmp_path / "projects"
    project = projects / "qa-example"
    project.mkdir(parents=True)
    (project / "answer_context.json").write_text(json.dumps({
        "question": "Who beat Batman?",
        "items": [
            {"series_issue_year": "Batman #77 (2019)"},
            {"series_issue_year": "Detective Comics #1000 (2019)"},
        ],
    }), encoding="utf-8")
    narration = project / "narration.json"
    narration.write_text('{"script":"approved"}', encoding="utf-8")
    (project / "state.json").write_text(json.dumps({
        "approved": {"2": True},
        "approved_narration_sha256": hashlib.sha256(narration.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    ledger = windows_ledger(tmp_path / "ledger")
    first = ledger.import_projects(projects)
    again = ledger.import_projects(projects)
    event = ledger.events()[0]
    assert first.inserted == 1 and again.inserted == 0
    assert event["mode"] == "qa"
    assert event["keys"] == ["batman||77", "detective-comics||1000"]


def test_issue_key_uses_series_start_year_instead_of_publication_year():
    from stages.research_scout.ledger import _issue_key

    assert _issue_key("Batman (2016) #77 (2019)") == "batman|2016|77"
    assert _issue_key("Batman #77 (2019)") == "batman||77"


def test_export_refuses_to_replace_the_database_file(tmp_path):
    ledger = windows_ledger(tmp_path)
    ledger.append_event({"mode": "qa", "kind": "proposed", "text": "Who beat Batman?"})
    before = ledger.events()

    with pytest.raises(ValueError, match="database"):
        ledger.export_jsonl(ledger.db_path)

    assert ledger.events() == before


def test_export_refuses_temp_file_collision_with_database(tmp_path):
    ledger = Ledger(tmp_path / "export.jsonl.tmp", _writer_guard=lambda: True)
    ledger.append_event({"mode": "qa", "kind": "proposed", "text": "Who beat Batman?"})
    before = ledger.events()
    with pytest.raises(ValueError, match="temporary file"):
        ledger.export_jsonl(tmp_path / "export.jsonl")
    assert ledger.events() == before


def test_read_only_client_reads_database_and_cannot_mutate(tmp_path, monkeypatch):
    writer = windows_ledger(tmp_path)
    writer.append_event({"mode": "micro", "kind": "produced", "key": "batman|2016|77"})
    monkeypatch.setattr(ledger_module, "_is_windows_server", lambda: False)
    client = Ledger(writer.db_path)
    assert client.count_events() == 1
    with pytest.raises(LedgerWriteDisabledError):
        client.append_event({"mode": "micro", "kind": "produced", "key": "batman|2016|78"})
    assert client.count_events() == 1


def test_append_milestone_uses_stable_id_real_timestamp_and_reports_insert(tmp_path):
    ledger = windows_ledger(tmp_path)
    event = {"mode": "micro", "kind": "in_progress", "key": "batman|2016|77"}
    first, inserted = ledger.append_milestone(event, "stable-milestone-id")
    again, inserted_again = ledger.append_milestone(event, "stable-milestone-id")
    assert inserted is True and inserted_again is False
    assert first["id"] == again["id"] == "stable-milestone-id"
    assert first["ts"] != ""
    assert ledger.count_events() == 1
