import json

from stages.research_scout.ledger import Ledger
from stages.research_scout.ledger_shadow import build_shadow_report


def _candidate(label="Batman #77 (2019)", candidate_id="c1"):
    return {"id": candidate_id, "series_issue_year": label}


def test_shadow_reads_database_on_windows_server_even_if_snapshot_is_stale(tmp_path):
    db = tmp_path / "ledger.db"
    export = tmp_path / "export.jsonl"
    writer = Ledger(db, _writer_guard=lambda: True)
    writer.append_event({"mode": "micro", "kind": "produced", "key": "batman||77"})
    export.write_text("", encoding="utf-8")

    report = build_shadow_report(
        [_candidate()], [], mode="micro", db_path=db, export_path=export,
        windows_server=True,
    )

    assert report["source"] == "database"
    assert report["ledger_hard_duplicate_count"] == 1
    assert report["disagreement_count"] == 1


def test_shadow_reads_export_snapshot_off_server_without_opening_database(tmp_path):
    db = tmp_path / "ledger.db"
    stale_writer = Ledger(db, _writer_guard=lambda: True)
    stale_writer.append_event({"mode": "micro", "kind": "proposed", "key": "batman||77"})
    export = tmp_path / "export.jsonl"
    row = {
        "id": "event-id", "ts": "2026-10-03T12:00:00Z", "mode": "micro",
        "kind": "produced", "key": "batman||77", "keys": [], "label": "",
        "text": "", "entities": [], "scope": "item", "reason_code": "", "refs": {},
    }
    export.write_text(json.dumps(row) + "\n", encoding="utf-8")

    report = build_shadow_report(
        [_candidate()], [], mode="micro", db_path=db, export_path=export,
        windows_server=False,
    )

    assert report["source"] == "export"
    assert report["ledger_hard_duplicate_count"] == 1
    assert report["disagreements"][0]["ledger_state"] == "produced"


def test_shadow_missing_snapshot_is_nonfatal_and_marks_unavailable(tmp_path):
    report = build_shadow_report(
        [_candidate()], [], mode="micro", db_path=tmp_path / "missing.db",
        export_path=tmp_path / "missing.jsonl", windows_server=False,
    )
    assert report["status"] == "unavailable"
    assert report["source"] is None
    assert report["candidate_count"] == 1
    assert report["comparable_count"] == 0


def test_shadow_corrupt_database_or_snapshot_is_nonfatal(tmp_path):
    db = tmp_path / "ledger.db"
    db.write_bytes(b"not a sqlite database")
    report = build_shadow_report(
        [_candidate()], [], mode="micro", db_path=db,
        export_path=tmp_path / "missing.jsonl", windows_server=True,
    )
    assert report["status"] == "corrupted"
    assert report["source"] == "database"

    export = tmp_path / "bad.jsonl"
    export.write_text("{broken\n", encoding="utf-8")
    report = build_shadow_report(
        [_candidate()], [], mode="micro", db_path=tmp_path / "missing.db",
        export_path=export, windows_server=False,
    )
    assert report["status"] == "corrupted"
    assert report["source"] == "export"


def test_shadow_treats_qa_issue_reuse_as_soft_signal(tmp_path):
    writer = Ledger(tmp_path / "ledger.db", _writer_guard=lambda: True)
    writer.append_event({"mode": "qa", "kind": "produced", "key": "batman||77"})
    report = build_shadow_report(
        [_candidate()], [], mode="qa", db_path=writer.db_path,
        export_path=tmp_path / "missing.jsonl", windows_server=True,
    )
    assert report["hard_duplicate_policy"] == "micro_only"
    assert report["ledger_hard_duplicate_count"] == 0
    assert report["status"] == "skipped_mode"


def test_unknown_start_year_resolves_when_ledger_has_one_series_identity(tmp_path):
    writer = Ledger(tmp_path / "ledger.db", _writer_guard=lambda: True)
    writer.append_event({"mode": "micro", "kind": "produced", "key": "batman|2016|77"})
    report = build_shadow_report(
        [_candidate("Batman #77 (2019)")],
        [{"candidate_id": "c1", "reason": "duplicate_issue_key"}],
        mode="micro", db_path=writer.db_path,
        export_path=tmp_path / "missing.jsonl", windows_server=True,
    )
    assert report["unique_identity_match_count"] == 1
    assert report["comparable_count"] == 1
    assert report["ambiguous_count"] == 0
    assert report["disagreement_count"] == 0


def test_unknown_start_year_is_uncomparable_across_relaunches(tmp_path):
    writer = Ledger(tmp_path / "ledger.db", _writer_guard=lambda: True)
    writer.append_events([
        {"mode": "micro", "kind": "produced", "key": "batman|2016|77"},
        {"mode": "micro", "kind": "produced", "key": "batman|2024|77"},
    ])
    report = build_shadow_report(
        [_candidate("Batman #77 (2025)")], [], mode="micro",
        db_path=writer.db_path, export_path=tmp_path / "missing.jsonl",
        windows_server=True,
    )
    assert report["ambiguous_count"] == 1
    assert report["uncomparable_count"] == 1
    assert report["comparable_count"] == 0
    assert report["disagreement_count"] == 0


def test_run_general_records_shadow_difference_without_changing_acceptance(tmp_path, monkeypatch):
    import config
    from stages.research_scout import ledger_shadow
    from stages.research_scout.models import ScoutMode
    from stages.research_scout.storage import SessionStore
    from stages.research_scout.workflow import ScoutWorkflow
    from tests.test_research_scout_workflow import _FakeYouCom

    monkeypatch.setattr(config, "SCOUT_TOPUP_ROUNDS", 0)
    db = tmp_path / "ledger.db"
    writer = Ledger(db, _writer_guard=lambda: True)
    writer.append_event({"mode": "micro", "kind": "produced", "key": "hero-a||1"})
    monkeypatch.setattr(ledger_shadow, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ledger_shadow, "DEFAULT_EXPORT_PATH", tmp_path / "missing.jsonl")
    monkeypatch.setattr(ledger_shadow.ledger, "_is_windows_server", lambda: True)

    workflow = ScoutWorkflow(store=SessionStore(tmp_path / "sessions"), client=_FakeYouCom(), planner=lambda *_: None)
    session = workflow.start(ScoutMode.MICRO, "new Hulk moment")
    workflow.run_general(session.id)

    session_dir = workflow.store.session_dir(session.id)
    candidates = json.loads((session_dir / "general/candidates.v1.json").read_text())["candidates"]
    report = json.loads((session_dir / "general/ledger_shadow.rev1.v1.json").read_text())
    assert [candidate["id"] for candidate in candidates] == ["a", "b", "c"]
    assert report["source"] == "database"
    assert report["disagreements"][0]["candidate_id"] == "a"
    assert report["disagreements"][0]["ledger_hard_duplicate"] is True
