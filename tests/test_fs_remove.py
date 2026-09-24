"""utils.fs_remove.remove_tree — the folder delete behind the project and research-session
Delete buttons. The app runs on Windows, where a plain shutil.rmtree stops at a read-only
file or at a file some other program still has open for a moment (a player, an Explorer
preview, antivirus scanning something just written)."""
import shutil
import stat

import pytest

import utils.fs_remove as fs_remove
from stages.user_errors import UserFacingError


def test_removes_a_tree_with_read_only_files(tmp_path):
    target = tmp_path / "proj"
    (target / "sub").mkdir(parents=True)
    for name in ("a.json", "sub/b.jpg"):
        (target / name).write_bytes(b"x")
        (target / name).chmod(stat.S_IREAD)

    fs_remove.remove_tree(target)

    assert not target.exists()


def test_a_missing_folder_is_already_removed(tmp_path):
    fs_remove.remove_tree(tmp_path / "never-existed")


def test_a_briefly_locked_file_is_retried(tmp_path, monkeypatch):
    target = tmp_path / "proj"
    target.mkdir()
    real_rmtree = shutil.rmtree
    calls = []

    def _locked_twice(path, **kwargs):
        calls.append(path)
        if len(calls) <= 2:
            raise PermissionError(32, "in use", str(path / "audio.wav"))
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(fs_remove.shutil, "rmtree", _locked_twice)

    fs_remove.remove_tree(target, delay=0)

    assert len(calls) == 3
    assert not target.exists()


def test_a_file_that_stays_locked_is_named_in_a_readable_error(tmp_path, monkeypatch):
    target = tmp_path / "proj"
    target.mkdir()

    def _always_locked(path, **kwargs):
        raise PermissionError(32, "in use", str(path / "final.mp4"))

    monkeypatch.setattr(fs_remove.shutil, "rmtree", _always_locked)

    with pytest.raises(fs_remove.FileInUseError) as caught:
        fs_remove.remove_tree(target, attempts=3, delay=0)

    assert isinstance(caught.value, UserFacingError)
    assert "final.mp4" in str(caught.value)
    assert target.exists()


def test_a_file_that_vanishes_mid_delete_is_not_an_error(tmp_path):
    gone = tmp_path / "gone.txt"
    fs_remove._clear_readonly_and_retry(lambda p: None, str(gone), FileNotFoundError())
