"""Return a downloaded Scout project to its existing Stage 1 research session."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import re
from pathlib import Path
from uuid import uuid4

import config

from .models import ResearchSession, ScoutMode, SessionState
from .storage import SessionStore


_STAGE2_CACHE_DIRS = ("raw_comic", "preprocessed")


def return_project_to_research(
    project_name: str,
    *,
    projects_root: Path | None = None,
    sessions_root: Path | None = None,
) -> ResearchSession:
    """Restore the one Stage 1 session that materialised ``project_name``.

    Only Stage 2's downloaded input and preprocessing cache are removed. Cache
    directories are first renamed aside, making a failed session save reversible.
    """
    project_root = _project_root(project_name, projects_root or config.PROJECTS_ROOT)
    store = SessionStore(sessions_root or config.RESEARCH_SESSIONS_ROOT)
    session, pending = _linked_session(store, project_name, project_root)
    _validate_restorable_research(store, session)

    if pending is not None:
        _delete_staged(project_root, pending)
        return session

    session_path = store.session_dir(session.id) / "session.json"
    audit_path = store.artifact_path(session.id, "audit.jsonl")
    if session_path.is_symlink() or audit_path.is_symlink():
        raise ValueError("research session files must not be symlinks")
    snapshots = {session_path: _read_snapshot(session_path), audit_path: _read_snapshot(audit_path)}

    staged = _stage_caches(project_root)
    original_state, original_project = session.state, session.created_project
    session.state = SessionState.CANDIDATE_REVIEW
    session.created_project = None
    try:
        store.save(
            session,
            event="project_returned_to_research",
                    detail={"project": project_name, "cleared": list(_STAGE2_CACHE_DIRS),
                    "staged": [path.name for _name, path in staged],
                    "project_identity": _project_identity(project_root)},
        )
    except Exception:
        # save() writes session.json before appending audit.jsonl; rollback both.
        _restore_snapshot(session_path, snapshots[session_path])
        _restore_snapshot(audit_path, snapshots[audit_path])
        _restore_staged(staged)
        session.state, session.created_project = original_state, original_project
        raise

    try:
        _delete_staged(project_root, [path.name for _name, path in staged])
    except Exception as exc:
        # A partial rmtree cannot be truthfully rolled back. The durable audit
        # marker records only our generated staging names, so a later retry can
        # finish safely without another state transition or audit event.
        raise RuntimeError("Stage 2 cache cleanup failed; retry to finish the reset") from exc
    return session


def _project_root(project_name: str, projects_root: Path) -> Path:
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValueError("project name must be a non-empty path component")
    name = Path(project_name)
    if name.is_absolute() or len(name.parts) != 1:
        raise ValueError("project name must be a single path component")
    root = Path(projects_root).resolve()
    project = Path(projects_root) / project_name
    if project.is_symlink() or not project.is_dir():
        raise ValueError("project must be a real directory")
    resolved = project.resolve()
    if resolved.parent != root:
        raise ValueError("project must stay within PROJECTS_ROOT")
    return resolved


def _linked_session(
    store: SessionStore, project_name: str, project_root: Path,
) -> tuple[ResearchSession, list[str] | None]:
    matches: list[tuple[ResearchSession, list[str] | None]] = []
    for entry in store.root.iterdir():
        if entry.is_symlink() or not entry.is_dir() or not (entry / "session.json").is_file():
            continue
        try:
            session = store.load(entry.name)
        except Exception:
            continue
        if session.created_project == project_name:
            matches.append((session, None))
        elif session.created_project is None and session.state is SessionState.CANDIDATE_REVIEW:
            marker = _pending_return_marker(
                entry / "audit.jsonl", project_name, _project_identity(project_root),
            )
            if marker is not None:
                matches.append((session, marker))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one research session linked to {project_name!r}")
    session, pending = matches[0]
    if pending is None and session.state is not SessionState.PRODUCTION_GATES:
        raise ValueError("linked session must be in PRODUCTION_GATES")
    return session, pending


def _pending_return_marker(
    audit_path: Path, project_name: str, project_identity: dict[str, int],
) -> list[str] | None:
    if audit_path.is_symlink() or not audit_path.is_file():
        return None
    try:
        events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError):
        return None
    for event in reversed(events):
        detail = event.get("detail") if isinstance(event, dict) else None
        if not isinstance(detail, dict) or detail.get("project") != project_name:
            continue
        if event.get("event") == "project_created":
            return None
        if event.get("event") == "project_returned_to_research":
            staged = detail.get("staged", [])
            if detail.get("project_identity") != project_identity:
                return None
            return staged if isinstance(staged, list) and all(isinstance(x, str) for x in staged) else None
    return None


def _validate_restorable_research(store: SessionStore, session: ResearchSession) -> None:
    selected = session.selected_specific_candidate_ids
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("linked session has an invalid candidate selection")
    if session.mode is ScoutMode.QA and not 3 <= len(selected) <= 5:
        raise ValueError("linked QA session must retain three to five selections")
    if session.mode is ScoutMode.MICRO and len(selected) != 1:
        raise ValueError("linked MICRO session must retain exactly one selection")
    candidates_path = store.artifact_path(session.id, "general/candidates.v1.json")
    gates_path = store.artifact_path(session.id, "specific/evidence_gate.v1.json")
    if any(path.is_symlink() or not path.is_file() for path in (candidates_path, gates_path)):
        raise ValueError("linked session is missing restorable research artifacts")
    try:
        candidates_doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        gates_doc = json.loads(gates_path.read_text(encoding="utf-8"))
        candidates = candidates_doc["candidates"]
        gates = gates_doc.get("gates", [gates_doc]) if isinstance(gates_doc, dict) else gates_doc
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("linked session has malformed research artifacts") from exc
    candidate_ids = {item.get("id") for item in candidates if isinstance(item, dict)}
    gate_ids = {
        item.get("candidate_id")
        for item in gates
        if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
        and item["candidate_id"].strip()
    }
    # The factory deliberately supports its original Micro artifact: one bare
    # gate, without a candidate id. It necessarily belongs to its one selected
    # candidate, so returning that session must preserve the same compatibility.
    if session.mode is ScoutMode.MICRO and len(gates) == 1 and not gate_ids:
        gate_ids = {selected[0]}
    if not set(selected).issubset(candidate_ids) or not set(selected).issubset(gate_ids):
        raise ValueError("linked session cannot restore its selected candidates and gates")


def _stage_caches(project_root: Path) -> list[tuple[str, Path]]:
    staged: list[tuple[str, Path]] = []
    try:
        for name in _STAGE2_CACHE_DIRS:
            source = project_root / name
            if not source.exists():
                continue
            if source.is_symlink() or not source.is_dir() or source.resolve().parent != project_root:
                raise ValueError(f"Stage 2 cache {name!r} must be a real project subdirectory")
            destination = project_root / f".{name}.returning-{uuid4().hex}"
            os.replace(source, destination)
            staged.append((name, destination))
    except Exception:
        _restore_staged(staged)
        raise
    return staged


def _restore_staged(staged: list[tuple[str, Path]]) -> None:
    for name, staged_path in reversed(staged):
        destination = staged_path.parent / name
        if staged_path.exists() and not destination.exists():
            os.replace(staged_path, destination)


def _delete_staged(project_root: Path, names: list[str]) -> None:
    expected = {name: re.compile(rf"^\.{re.escape(name)}\.returning-[0-9a-f]+$") for name in _STAGE2_CACHE_DIRS}
    for staged_name in names:
        if not isinstance(staged_name, str) or not any(pattern.fullmatch(staged_name) for pattern in expected.values()):
            raise ValueError("invalid pending Stage 2 cache path")
        staged_path = project_root / staged_name
        if staged_path.exists():
            if staged_path.is_symlink() or staged_path.resolve().parent != project_root:
                raise ValueError("pending Stage 2 cache must stay within the project")
            shutil.rmtree(staged_path)


def _project_identity(project_root: Path) -> dict[str, int]:
    """Stable filesystem identity prevents an old retry marker claiming a new slug."""
    stat = project_root.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino}


def _read_snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _restore_snapshot(path: Path, content: bytes | None) -> None:
    if content is None:
        if path.exists():
            path.unlink()
        return
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
