import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import ledger_maintenance
from stages.research_scout.ledger import Ledger


def test_mac_commands_are_dry_run_and_do_not_create_database(tmp_path, capsys):
    root = tmp_path
    assert ledger_maintenance.main(["backup"], root=root, writer_check=lambda: False) == 0
    assert "dry-run" in capsys.readouterr().out.lower()
    assert not (root / "data/ledger/ledger.db").exists()


def test_direct_script_invocation_runs_mac_dry_run_from_repo_root():
    script = Path(ledger_maintenance.__file__).resolve()
    db_path = script.parents[1] / "data/ledger/ledger.db"
    existed_before = db_path.exists()
    result = subprocess.run(
        [sys.executable, str(script), "export"],
        cwd=script.parents[1],
        env={**os.environ, "SCOUT_LEDGER_WRITER": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout.lower()
    assert db_path.exists() is existed_before


def test_gitignore_excludes_server_database_but_tracks_export():
    repo = Path(ledger_maintenance.__file__).resolve().parents[1]
    ignored = [
        "data/ledger/ledger.db",
        "data/ledger/ledger.db-wal",
        "data/ledger/ledger.db-shm",
        "data/ledger/backups/ledger-test.db",
    ]
    for path in ignored:
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=repo)
        assert result.returncode == 0, path
    exported = subprocess.run(["git", "check-ignore", "-q", "data/ledger/export.jsonl"], cwd=repo)
    assert exported.returncode == 1


def test_import_legacy_is_idempotent_and_backups_before_import(tmp_path):
    root = tmp_path
    legacy = root / "legacy.csv"
    legacy.write_text("title,year,status\nBatman #1,2020,produced\n", encoding="utf-8")
    calls = []

    class RecordingLedger(Ledger):
        def backup(self, destination):
            calls.append(("backup", Path(destination)))
            return super().backup(destination)

        def import_comic_candidates(self, source):
            calls.append(("import", Path(source)))
            return super().import_comic_candidates(source)

    factory = lambda db_path: RecordingLedger(db_path, _writer_guard=lambda: True)
    args = ["import-legacy", "--candidates", str(legacy)]
    assert ledger_maintenance.main(args, root=root, writer_check=lambda: True, ledger_factory=factory) == 0
    assert calls[0][0] == "import"  # New DB has nothing to back up.
    calls.clear()
    assert ledger_maintenance.main(args, root=root, writer_check=lambda: True, ledger_factory=factory) == 0
    assert calls[0][0] == "backup"
    assert calls[1][0] == "import"
    assert Ledger(root / "data/ledger/ledger.db", _writer_guard=lambda: True).count_events() == 1


def test_export_uses_fixed_path_and_creates_valid_jsonl(tmp_path):
    root = tmp_path
    ledger = Ledger(root / "data/ledger/ledger.db", _writer_guard=lambda: True)
    ledger.append_event({"mode": "qa", "kind": "proposed", "text": "Who beat Batman?"})
    factory = lambda db_path: Ledger(db_path, _writer_guard=lambda: True)
    assert ledger_maintenance.main(["export"], root=root, writer_check=lambda: True, ledger_factory=factory) == 0
    export_path = root / "data/ledger/export.jsonl"
    row = json.loads(export_path.read_text(encoding="utf-8"))
    assert row["text"] == "Who beat Batman?"


def test_backup_refuses_existing_destination_without_overwriting(tmp_path):
    root = tmp_path
    db = root / "data/ledger/ledger.db"
    Ledger(db, _writer_guard=lambda: True).append_event({"mode": "micro", "kind": "produced", "key": "batman|2016|1"})
    backup_dir = root / "data/ledger/backups"
    backup_dir.mkdir(parents=True)
    existing = backup_dir / "ledger-20261003T000000Z.db"
    existing.write_bytes(b"preserve")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ledger_maintenance, "_backup_destination", lambda _root, _now=None: existing)
    try:
        factory = lambda db_path: Ledger(db_path, _writer_guard=lambda: True)
        with pytest.raises(FileExistsError):
            ledger_maintenance.main(["backup"], root=root, writer_check=lambda: True, ledger_factory=factory)
        assert existing.read_bytes() == b"preserve"
    finally:
        monkeypatch.undo()


def _write_project(project, *, approved=False, rendered=False):
    import hashlib
    import shutil
    import subprocess
    import pytest
    project.mkdir(parents=True)
    (project / "comic_context.json").write_text(json.dumps({
        "series": "A Series", "issue": 1, "year": 2024,
    }))
    narration = project / "narration.json"
    narration.write_text('{"scenes":[{"script":"saved"}]}')
    if approved:
        (project / "state.json").write_text(json.dumps({
            "approved": {"2": True},
            "approved_narration_sha256": hashlib.sha256(narration.read_bytes()).hexdigest(),
        }))
    if rendered:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not shutil.which("ffprobe"):
            pytest.skip("ffmpeg/ffprobe unavailable")
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.04",
                        "-c:v", "mpeg4", "-y", str(project / "final.mp4")], check=True, timeout=15)


def test_import_approved_vs_rendered_vs_bare(tmp_path):
    from stages.research_scout.production_ledger import record_milestone
    projects = tmp_path / "projects"
    _write_project(projects / "approved", approved=True)
    _write_project(projects / "rendered", rendered=True)
    bare = projects / "bare"
    bare.mkdir()
    (bare / "comic_context.json").write_text(json.dumps({"title": "No milestone"}))

    ledger = Ledger(tmp_path / "ledger.db", _writer_guard=lambda: True)
    result = ledger.import_projects(projects)
    rows = {event["refs"]["project"]: event["kind"] for event in ledger.events()}
    assert rows == {"approved": "in_progress", "rendered": "produced"}
    assert result.scanned == 3
    assert result.inserted == 2
    assert record_milestone(
        "approved", "in_progress", projects_root=projects, ledger=ledger,
    ) == "already_recorded"


def test_repeated_import_is_idempotent(tmp_path):
    projects = tmp_path / "projects"
    _write_project(projects / "approved", approved=True)
    _write_project(projects / "rendered", rendered=True)
    ledger = Ledger(tmp_path / "ledger.db", _writer_guard=lambda: True)
    first = ledger.import_projects(projects)
    second = ledger.import_projects(projects)
    assert first.inserted == 2
    assert second.inserted == 0
    assert second.skipped == 2
    assert ledger.count_events() == 2


def test_project_import_exports_once_after_batch(tmp_path):
    projects = tmp_path / "projects"
    _write_project(projects / "approved", approved=True)
    _write_project(projects / "rendered", rendered=True)

    class ExportCountingLedger(Ledger):
        export_calls = 0

        def export_jsonl(self, destination):
            self.export_calls += 1
            return super().export_jsonl(destination)

    ledger = ExportCountingLedger(tmp_path / "ledger.db", _writer_guard=lambda: True)
    ledger.import_projects(projects)
    assert ledger.export_calls == 1
