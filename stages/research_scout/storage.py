"""Durable session and artifact storage for the Stage 1 research scout."""

from datetime import datetime, timezone
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from utils.atomic_json import write_json_atomic

from .models import ResearchSession, ScoutMode


class SessionStore:
    """Persist each research session in its own directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, mode: ScoutMode, user_intent: str) -> ResearchSession:
        session = ResearchSession(
            id=uuid4().hex,
            mode=mode,
            user_intent=user_intent,
        )
        self.save(session, event="session_created")
        return session

    def session_dir(self, session_id: str) -> Path:
        """Return a session directory, rejecting path traversal in its id."""
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session id must be a non-empty path component")
        session_component = Path(session_id)
        if session_component.is_absolute() or len(session_component.parts) != 1:
            raise ValueError("session id must be a single path component")

        session_dir = self.root / session_id
        try:
            session_dir.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("session id must stay within the store root") from exc
        return session_dir

    def load(self, session_id: str) -> ResearchSession:
        path = self.session_dir(session_id) / "session.json"
        return ResearchSession.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, session_id: str) -> None:
        """Permanently remove a session directory. Reuses session_dir's traversal guard
        (single path component, must resolve inside the store root) — a bad session_id
        must never let rmtree touch anything outside root."""
        target = self.session_dir(session_id)
        if not target.is_dir():
            raise ValueError(f"session directory does not exist: {session_id!r}")
        shutil.rmtree(target)

    def save(
        self,
        session: ResearchSession,
        *,
        event: str | None = None,
        detail: dict | None = None,
    ) -> ResearchSession:
        session_dir = self.session_dir(session.id)
        session_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(session_dir / "session.json", session.model_dump(mode="json"))
        if event is not None:
            self.append_audit(session.id, event, detail=detail)
        return session

    def append_audit(
        self,
        session_id: str,
        event: str,
        detail: dict | None = None,
    ) -> Path:
        path = self.artifact_path(session_id, "audit.jsonl")
        if path.is_symlink():
            raise ValueError("audit path must not be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "detail": detail if detail is not None else {},
        }
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o644)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
        return path

    def artifact_path(self, session_id: str, name: str) -> Path:
        """Return an artifact path while keeping it inside the session directory."""
        session_dir = self.session_dir(session_id)
        artifact = Path(name)
        if not name or artifact == Path("."):
            raise ValueError("artifact name must not be empty")

        candidate = session_dir / artifact
        try:
            candidate.resolve().relative_to(session_dir.resolve())
        except ValueError as exc:
            raise ValueError("artifact name must stay within the session directory") from exc
        return candidate

    def write_artifact(self, session_id: str, name: str, artifact: object) -> Path:
        path = self.artifact_path(session_id, name)
        return write_json_atomic(path, artifact)
