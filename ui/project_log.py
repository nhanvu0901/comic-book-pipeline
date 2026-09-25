"""Stage logs kept in the project's own folder: projects/<name>/logs/<stage>.log.

The on-screen log lives only in the browser tab and keeps its last 300 lines, so after a
reload — or when someone else asks why a download failed or a script was refused — the
lines that explain it were gone. Each line is appended with a timestamp.

Writing a log never creates a project folder (a missing folder means no project yet, or
one deleted meanwhile) and never raises: a log must not break the screen it records.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from config import PROJECTS_ROOT

LOG_DIR = "logs"
_LAST_SCRIPT = "stage4_last_script.txt"


def stage_log_path(project_name: str, stage: str) -> Path | None:
    """projects/<name>/logs/<stage>.log, or None while no project is open."""
    if not project_name:
        return None
    return PROJECTS_ROOT / project_name / LOG_DIR / f"{stage}.log"


def append_log(path: Path | None, text: str) -> None:
    if path is None or not path.parent.parent.is_dir():
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        path.parent.mkdir(exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for line in str(text).splitlines() or [""]:
                fh.write(f"[{stamp}] {line}\n")
    except OSError:
        pass


def save_last_script(project_name: str, text: str) -> None:
    """Keep the narration script last sent to Approve, so a refused script survives a
    reload (the text box is the only other copy)."""
    path = stage_log_path(project_name, "")
    if path is None or not path.parent.parent.is_dir():
        return
    try:
        path.parent.mkdir(exist_ok=True)
        (path.parent / _LAST_SCRIPT).write_text(text, encoding="utf-8")
    except OSError:
        pass


def load_last_script(project_name: str) -> str:
    path = stage_log_path(project_name, "")
    if path is None:
        return ""
    try:
        return (path.parent / _LAST_SCRIPT).read_text(encoding="utf-8")
    except OSError:
        return ""
