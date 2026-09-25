"""utils.fs_remove.remove_tree — the folder delete behind the project and research-session
Delete buttons. The app runs on Windows, where a plain shutil.rmtree stops at a read-only
file, and stops HALF WAY at a file some other program still has open (a player, an
Explorer preview, antivirus scanning something just written)."""
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

import utils.fs_remove as fs_remove
from stages.user_errors import UserFacingError


def _project(root: Path) -> Path:
    target = root / "proj"
    (target / "raw").mkdir(parents=True)
    (target / "comic_context.json").write_text("{}")
    (target / "final.mp4").write_bytes(b"x")
    (target / "raw" / "p01.jpg").write_bytes(b"x")
    return target


def test_removes_a_tree_with_read_only_files(tmp_path):
    target = _project(tmp_path)
    for p in target.rglob("*"):
        if p.is_file():
            p.chmod(stat.S_IREAD)

    fs_remove.remove_tree(target)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_missing_folder_is_already_removed(tmp_path):
    fs_remove.remove_tree(tmp_path / "never-existed")


def test_an_open_file_stops_the_delete_before_anything_is_removed(tmp_path, monkeypatch):
    """What Windows does when a player holds final.mp4: the folder cannot be moved.
    Nothing may be deleted then, least of all comic_context.json, or the project drops
    out of the picker with most of its files still on disk."""
    target = _project(tmp_path)
    real_replace = os.replace

    def _windows_like(src, dst):
        if Path(src) == target or Path(src).name == "final.mp4":
            raise PermissionError(32, "The process cannot access the file", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(fs_remove.os, "replace", _windows_like)

    with pytest.raises(fs_remove.FileInUseError) as caught:
        fs_remove.remove_tree(target, attempts=2, delay=0)

    assert isinstance(caught.value, UserFacingError)
    assert "'final.mp4' is still open" in str(caught.value)
    assert "Nothing was deleted" in str(caught.value)
    assert sorted(p.name for p in target.rglob("*")) == [
        "comic_context.json", "final.mp4", "p01.jpg", "raw"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["proj"]


def test_a_briefly_blocked_move_is_retried(tmp_path, monkeypatch):
    target = _project(tmp_path)
    real_replace = os.replace
    blocked = []

    def _blocked_once(src, dst):
        if Path(src) == target and not blocked:
            blocked.append(src)
            raise PermissionError(32, "in use", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(fs_remove.os, "replace", _blocked_once)

    fs_remove.remove_tree(target, delay=0)

    assert blocked and not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_briefly_locked_file_is_retried(tmp_path, monkeypatch):
    target = _project(tmp_path)
    real_rmtree = shutil.rmtree
    calls = []

    def _locked_twice(path, **kwargs):
        calls.append(path)
        if len(calls) <= 2:
            raise PermissionError(32, "in use", str(Path(path) / "final.mp4"))
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(fs_remove.shutil, "rmtree", _locked_twice)

    fs_remove.remove_tree(target, delay=0)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_straggler_stays_hidden_and_the_next_delete_sweeps_it(tmp_path, monkeypatch):
    target = _project(tmp_path)

    def _always_locked(path, **kwargs):
        raise PermissionError(32, "in use", str(Path(path) / "final.mp4"))

    monkeypatch.setattr(fs_remove.shutil, "rmtree", _always_locked)
    fs_remove.remove_tree(target, attempts=2, delay=0)

    assert not target.exists()
    assert [p.name for p in tmp_path.iterdir()] == [".deleting"]
    assert not (tmp_path / ".deleting" / "comic_context.json").exists(), \
        "nothing inside .deleting may look like a project to a listing"

    monkeypatch.undo()
    other = tmp_path / "other"
    other.mkdir()
    fs_remove.remove_tree(other)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32",
                    reason="only Windows refuses to move a folder with a file open inside")
def test_a_really_open_file_blocks_the_delete_and_is_named(tmp_path):
    target = _project(tmp_path)

    with open(target / "final.mp4", "rb"):
        with pytest.raises(fs_remove.FileInUseError) as caught:
            fs_remove.remove_tree(target, attempts=2, delay=0)

    assert "'final.mp4' is still open" in str(caught.value)
    assert (target / "comic_context.json").exists()
    fs_remove.remove_tree(target)
    assert list(tmp_path.iterdir()) == []


def test_a_file_that_vanishes_mid_delete_is_not_an_error(tmp_path):
    gone = tmp_path / "gone.txt"
    fs_remove._clear_readonly_and_retry(lambda p: None, str(gone), FileNotFoundError())


# ─── remove_file ────────────────────────────────────────────────────────────

def test_remove_file_deletes_a_read_only_file(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"x")
    f.chmod(stat.S_IREAD)

    fs_remove.remove_file(f)

    assert not f.exists()


def test_remove_file_of_a_missing_file_is_fine(tmp_path):
    fs_remove.remove_file(tmp_path / "never.wav")


def test_remove_file_names_a_file_that_stays_open(tmp_path, monkeypatch):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"x")
    real_unlink, real_replace = os.unlink, os.replace

    def _held(fn):
        def _call(src, *rest, **kwargs):
            if Path(src) == f:
                raise PermissionError(32, "The process cannot access the file", str(src))
            return fn(src, *rest, **kwargs)
        return _call

    monkeypatch.setattr(fs_remove.os, "unlink", _held(real_unlink))
    monkeypatch.setattr(fs_remove.os, "replace", _held(real_replace))

    with pytest.raises(fs_remove.FileInUseError) as caught:
        fs_remove.remove_file(f, attempts=2, delay=0)

    assert "audio.wav: it is still open in another program" in str(caught.value)
    assert f.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to delete an open file")
def test_remove_file_really_open_on_windows(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"x")

    with open(f, "rb"):
        with pytest.raises(fs_remove.FileInUseError, match="still open in another program"):
            fs_remove.remove_file(f, attempts=2, delay=0)

    fs_remove.remove_file(f)
    assert not f.exists()
