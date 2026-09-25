"""art_ui.open_path — the platform file/folder opener shared by a5_tts.play and
a6_video.open_folder. "open" only exists on macOS; on Windows the old
subprocess.run(["open", ...]) raised FileNotFoundError and the button looked dead."""
import subprocess

import pytest

from art_ui import open_path as op


def test_windows_uses_explorer(monkeypatch, tmp_path):
    monkeypatch.setattr(op.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    p = tmp_path / "audio.wav"
    p.write_bytes(b"x")

    op.open_path(p)

    assert calls == [["explorer", str(p)]]


def test_macos_uses_open(monkeypatch, tmp_path):
    monkeypatch.setattr(op.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    p = tmp_path / "audio.wav"
    p.write_bytes(b"x")

    op.open_path(p)

    assert calls == [["open", str(p)]]


def test_linux_uses_xdg_open(monkeypatch, tmp_path):
    monkeypatch.setattr(op.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    p = tmp_path / "audio.wav"
    p.write_bytes(b"x")

    op.open_path(p)

    assert calls == [["xdg-open", str(p)]]


def test_does_not_wait_for_the_child_process(monkeypatch, tmp_path):
    """A blocked opener (no desktop session on a Windows server) must never freeze
    the caller — Popen only, never .run()/.wait()/.communicate()."""
    monkeypatch.setattr(op.sys, "platform", "win32")

    class _NeverWait:
        def wait(self, *a, **k):
            raise AssertionError("open_path must not wait for the child process")

        def communicate(self, *a, **k):
            raise AssertionError("open_path must not wait for the child process")

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: _NeverWait())
    p = tmp_path / "audio.wav"
    p.write_bytes(b"x")

    op.open_path(p)  # must return without ever touching wait()/communicate()


def test_success_message_names_the_path_without_claiming_it_opened(monkeypatch, tmp_path):
    monkeypatch.setattr(op.sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: None)
    p = tmp_path / "audio.wav"
    p.write_bytes(b"x")

    msg = op.open_path(p)

    assert "Opening" in msg and str(p) in msg
    assert "opened" not in msg.lower()  # we can't know the window actually appeared


def test_popen_failure_is_caught_and_explained(monkeypatch, tmp_path):
    monkeypatch.setattr(op.sys, "platform", "darwin")

    def _boom(cmd, **k):
        raise FileNotFoundError("no such handler")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    p = tmp_path / "audio.wav"
    p.write_bytes(b"x")

    msg = op.open_path(p)

    assert "audio.wav" in msg
    assert "no such handler" in msg or "not" in msg.lower()


def test_exit_code_is_never_inspected(monkeypatch, tmp_path):
    """explorer.exe exits 1 even on success — a returncode check would misreport it
    as a failure."""
    monkeypatch.setattr(op.sys, "platform", "win32")

    class _NonZeroExit:
        returncode = 1

        def poll(self):
            return 1

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: _NonZeroExit())
    p = tmp_path / "audio.wav"
    p.write_bytes(b"x")

    msg = op.open_path(p)

    assert "Opening" in msg


def test_missing_path_is_reported_without_spawning_a_process(monkeypatch, tmp_path):
    monkeypatch.setattr(op.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: calls.append(cmd))

    msg = op.open_path(tmp_path / "nope.wav")

    assert calls == []
    assert "nope.wav" in msg
