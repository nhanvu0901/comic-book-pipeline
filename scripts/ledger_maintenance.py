"""Maintenance commands for the Windows-owned scout ledger.

On other hosts every command is a visible dry run. The database and export
locations are fixed under data/ledger so this tool cannot write elsewhere.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Callable

# Running ``python scripts/ledger_maintenance.py`` puts only ``scripts/`` on
# sys.path. Add the repository root so direct CLI use matches ``python -m``.
_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from stages.research_scout.ledger import Ledger, _is_windows_server


REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_RELATIVE = Path("data/ledger/ledger.db")
EXPORT_RELATIVE = Path("data/ledger/export.jsonl")


def _backup_destination(root: Path, _now: datetime | None = None) -> Path:
    stamp = (_now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S.%fZ")
    return root / "data" / "ledger" / "backups" / f"ledger-{stamp}.db"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain the scout decision ledger (Windows server only).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="atomically write data/ledger/export.jsonl")
    sub.add_parser("backup", help="snapshot ledger.db with SQLite VACUUM INTO")
    imp = sub.add_parser("import-legacy", help="idempotently import legacy project records")
    imp.add_argument("--banlist", type=Path)
    imp.add_argument("--candidates", type=Path)
    imp.add_argument("--question-bank", type=Path)
    imp.add_argument("--projects", type=Path)
    return parser


def main(argv: list[str] | None = None, *, root: str | Path = REPO_ROOT,
         writer_check: Callable[[], bool] = _is_windows_server,
         ledger_factory: Callable[[Path], Ledger] = Ledger) -> int:
    args = _parser().parse_args(argv)
    root = Path(root)
    db_path = root / LEDGER_RELATIVE
    if not writer_check():
        print(f"DRY-RUN: {args.command} chỉ được ghi trên Windows server có SCOUT_LEDGER_WRITER=windows-server; không tạo DB và không thay đổi file.")
        return 0

    ledger = ledger_factory(db_path)
    if args.command == "backup":
        if not db_path.exists():
            raise FileNotFoundError(f"Không có ledger để sao lưu: {db_path}")
        target = _backup_destination(root)
        ledger.backup(target)
        print(f"Đã sao lưu ledger: {target}")
        return 0

    if args.command == "export":
        if not db_path.exists():
            raise FileNotFoundError(f"Không có ledger để xuất: {db_path}")
        ledger.export_jsonl(root / EXPORT_RELATIVE)
        print(f"Đã xuất ledger: {root / EXPORT_RELATIVE}")
        return 0

    # A first import has no prior database to preserve. Every subsequent
    # import snapshots the current ledger before touching it.
    if db_path.exists():
        target = _backup_destination(root)
        ledger.backup(target)
        print(f"Đã sao lưu trước import: {target}")
    else:
        print("Chưa có DB cũ; import sẽ khởi tạo sổ mới.")

    sources = [
        ("banlist", args.banlist or root / "qa_question_banlist.md", ledger.import_banlist),
        ("comic candidates", args.candidates or root / "comic_candidates.csv", ledger.import_comic_candidates),
        ("question bank", args.question_bank or root / "qa_question_bank.md", ledger.import_question_bank),
        ("projects", args.projects or root / "projects", ledger.import_projects),
    ]
    for label, source, importer in sources:
        if not source.exists():
            print(f"Bỏ qua {label}: không tìm thấy {source}")
            continue
        result = importer(source)
        print(f"{label}: đọc {result.scanned}, thêm {result.inserted}, bỏ qua {result.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
