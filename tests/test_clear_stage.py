"""utils.clear_stage — the per-stage Clear buttons. A clear must report only what it
really removed: rmtree(ignore_errors=True) used to report raw_comic/ or _stage5/ as
cleared while a file held open on Windows kept part of it on disk."""
import os
from pathlib import Path

import pytest

import utils.clear_stage as clear_stage
import utils.fs_remove as fs_remove


def _project(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(clear_stage, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(fs_remove.time, "sleep", lambda _seconds: None)
    proj = tmp_path / "some-comic"
    (proj / "raw_comic").mkdir(parents=True)
    (proj / "raw_comic" / "ch01_page_01.jpg").write_bytes(b"x")
    (proj / "_stage5" / "shots").mkdir(parents=True)
    (proj / "_stage5" / "shots" / "shot_01.mp4").write_bytes(b"x")
    (proj / "audio.wav").write_bytes(b"x")
    (proj / "scene_timings.json").write_text("{}")
    return proj


def test_clear_stage_5_removes_the_render_scratch_and_reports_it(tmp_path, monkeypatch):
    proj = _project(tmp_path, monkeypatch)

    assert clear_stage.clear_stage_5("some-comic") == [proj / "_stage5"]
    assert not (proj / "_stage5").exists()
    assert not (proj / ".deleting").exists()


def test_a_clear_blocked_by_an_open_page_says_so_and_keeps_every_page(tmp_path, monkeypatch):
    proj = _project(tmp_path, monkeypatch)
    raw = proj / "raw_comic"
    real_replace = os.replace

    def _windows_like(src, dst):
        if Path(src) in (raw, raw / "ch01_page_01.jpg"):
            raise PermissionError(32, "The process cannot access the file", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(fs_remove.os, "replace", _windows_like)

    with pytest.raises(fs_remove.FileInUseError, match="ch01_page_01.jpg"):
        clear_stage.clear_stage_2("some-comic", raw=True)

    assert (raw / "ch01_page_01.jpg").exists()


def test_clear_stage_4_names_an_audio_file_that_is_still_playing(tmp_path, monkeypatch):
    proj = _project(tmp_path, monkeypatch)
    audio = proj / "audio.wav"
    real_unlink, real_replace = os.unlink, os.replace

    def _held(fn):
        def _call(src, *rest, **kwargs):
            if Path(src) == audio:
                raise PermissionError(32, "The process cannot access the file", str(src))
            return fn(src, *rest, **kwargs)
        return _call

    monkeypatch.setattr(fs_remove.os, "unlink", _held(real_unlink))
    monkeypatch.setattr(fs_remove.os, "replace", _held(real_replace))

    with pytest.raises(fs_remove.FileInUseError, match="audio.wav: it is still open"):
        clear_stage.clear_stage_4("some-comic")

    assert audio.exists()
