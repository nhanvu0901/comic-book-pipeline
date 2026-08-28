"""Hard-delete flows for projects and research-scout sessions.

Master's chosen safety model: a confirmation dialog in the UI, then a real rmtree — no
trash folder, no type-the-name. These tests exercise the STORAGE-layer guard that makes
the hard delete safe (it must never touch anything outside its root) and the plain
mechanics of removal. The confirm-dialog / state-clearing UI wiring lives in
test_ui_project_picker.py (projects) and test_s1_research_scout_ui.py (sessions).

No test here may delete anything real: every path is under tmp_path.
"""
from pathlib import Path

import pytest

import ui.bridge as bridge
from stages.research_scout.models import ScoutMode
from stages.research_scout.storage import SessionStore


def _make_project(root: Path, name: str, *, files: dict[str, bytes] | None = None) -> Path:
    proj = root / name
    proj.mkdir(parents=True)
    (proj / "comic_context.json").write_bytes(b"{}")
    for rel, data in (files or {}).items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return proj


# ─── ui.bridge.delete_project ───────────────────────────────────────────────

def test_delete_project_removes_the_project_directory_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    proj = _make_project(tmp_path, "some-comic")

    bridge.delete_project("some-comic")

    assert not proj.exists()


def test_delete_project_refuses_a_traversal_name_and_leaves_the_target_untouched(
    tmp_path, monkeypatch,
):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", root)
    # "../evil" from PROJECTS_ROOT resolves to a sibling directory outside it.
    victim = tmp_path / "evil"
    victim.mkdir()
    (victim / "keepme.txt").write_text("still here")

    with pytest.raises(ValueError):
        bridge.delete_project("../evil")

    assert victim.exists()
    assert (victim / "keepme.txt").exists()


def test_delete_project_refuses_an_absolute_path_and_leaves_the_target_untouched(
    tmp_path, monkeypatch,
):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", root)
    victim = tmp_path / "abs-victim"
    victim.mkdir()

    with pytest.raises(ValueError):
        bridge.delete_project(str(victim))

    assert victim.exists()


def test_delete_project_refuses_a_name_containing_a_path_separator(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", root)
    nested = root / "sub"
    nested.mkdir()
    victim = nested / "victim"
    victim.mkdir()

    with pytest.raises(ValueError):
        bridge.delete_project("sub/victim")

    assert victim.exists()


def test_delete_project_refuses_a_missing_project_without_touching_the_root(
    tmp_path, monkeypatch,
):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", root)

    with pytest.raises(ValueError):
        bridge.delete_project("does-not-exist")


# ─── ui.bridge.describe_project ─────────────────────────────────────────────

def test_describe_project_walks_once_and_reports_size_and_extension_counts(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "counted", files={
        "raw_comic/p1.jpg": b"x" * 100,
        "raw_comic/p2.jpg": b"y" * 50,
        "narration.json": b"{}",
    })

    inv = bridge.describe_project("counted")

    # comic_context.json + narration.json + 2 jpgs
    assert inv.file_count == 4
    assert inv.total_bytes == 2 + 2 + 100 + 50
    assert inv.extension_counts[".jpg"] == 2
    assert inv.extension_counts[".json"] == 2


def test_describe_project_reports_zeros_for_a_project_that_does_not_exist(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)

    inv = bridge.describe_project("ghost")

    assert inv.total_bytes == 0
    assert inv.file_count == 0
    assert inv.extension_counts == {}


def test_human_size_formats_bytes_kilobytes_and_megabytes_distinctly():
    assert bridge.human_size(0) == "0 B"
    assert bridge.human_size(2048) == "2.0 KB"
    assert bridge.human_size(5 * 1024 * 1024) == "5.0 MB"


# ─── SessionStore.delete / ui.bridge.delete_scout_session ──────────────────

def test_session_store_delete_removes_the_session_directory_from_disk(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(mode=ScoutMode.QA, user_intent="Hulk questions")
    session_dir = store.session_dir(session.id)
    assert session_dir.exists()

    store.delete(session.id)

    assert not session_dir.exists()


def test_session_store_delete_refuses_a_traversal_id_and_leaves_the_target_untouched(
    tmp_path,
):
    store = SessionStore(tmp_path / "store")
    victim = tmp_path / "evil"
    victim.mkdir()
    (victim / "keepme.txt").write_text("still here")

    with pytest.raises(ValueError):
        store.delete("../evil")

    assert victim.exists()
    assert (victim / "keepme.txt").exists()


def test_session_store_delete_refuses_an_absolute_id_and_leaves_the_target_untouched(
    tmp_path,
):
    store = SessionStore(tmp_path / "store")
    victim = tmp_path / "abs-victim"
    victim.mkdir()

    with pytest.raises(ValueError):
        store.delete(str(victim))

    assert victim.exists()


def test_session_store_delete_refuses_an_id_containing_a_path_separator(tmp_path):
    store_root = tmp_path / "store"
    store = SessionStore(store_root)
    nested = store_root / "sub"
    nested.mkdir(parents=True)
    victim = nested / "victim"
    victim.mkdir()

    with pytest.raises(ValueError):
        store.delete("sub/victim")

    assert victim.exists()


def test_delete_scout_session_bridge_wrapper_removes_the_session_directory(tmp_path):
    root = tmp_path / "research_sessions"
    store = SessionStore(root)
    session = store.create(ScoutMode.MICRO, "Hulk moment")
    session_dir = store.session_dir(session.id)

    bridge.delete_scout_session(session.id, root=root)

    assert not session_dir.exists()
