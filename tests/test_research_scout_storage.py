from pathlib import Path

import pytest

from stages.research_scout.models import ScoutMode
from stages.research_scout.storage import SessionStore


def test_save_revision_writes_audit_and_preserves_old_artifact(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(mode=ScoutMode.QA, user_intent="Hulk questions")
    store.write_artifact(session.id, "general/candidates.v1.json", {"ids": ["a"]})
    session.revision = 2
    store.save(session, event="general_research_requested", detail={"feedback": "newer"})
    assert store.load(session.id).revision == 2
    assert (store.session_dir(session.id) / "general/candidates.v1.json").exists()
    assert "general_research_requested" in (store.session_dir(session.id) / "audit.jsonl").read_text()


def test_append_audit_rejects_audit_path_resolving_outside_store(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "store")
    session = store.create(mode=ScoutMode.MICRO, user_intent="Hulk moment")
    audit_path = store.session_dir(session.id) / "audit.jsonl"
    outside_path = tmp_path / "outside-audit.jsonl"
    real_resolve = Path.resolve

    def resolve(path, strict=False):
        if path == audit_path:
            return outside_path
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="artifact name must stay"):
        store.append_audit(session.id, "should_not_write")
    assert not outside_path.exists()


def test_append_audit_rejects_symlinked_audit_entry_without_symlink_privilege(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    session = store.create(mode=ScoutMode.MICRO, user_intent="Hulk moment")
    audit_path = store.session_dir(session.id) / "audit.jsonl"
    real_is_symlink = Path.is_symlink

    def is_symlink(path):
        return path == audit_path or real_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    with pytest.raises(ValueError, match="must not be a symlink"):
        store.append_audit(session.id, "should_not_write")
