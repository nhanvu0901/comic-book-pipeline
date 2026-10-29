"""The lightweight Windows-only loader policy used by the isolated Chatterbox worker."""
from types import SimpleNamespace

import pytest

from stages.stage_4 import background_tts as bt
from stages.stage_4 import _chatterbox_worker as worker


def test_windows_weight_loads_use_pread_not_mmap(monkeypatch):
    """Removing the override would reintroduce the Windows safetensors mmap crash."""
    calls = []

    def load_file(filename, device="cpu", *, backend="mmap"):
        calls.append((filename, device, backend))
        return {"backend": backend}

    tts_module = SimpleNamespace(load_file=load_file)
    monkeypatch.setattr(worker.sys, "platform", "win32")

    worker.enable_windows_pread_loader(tts_module, load_file)

    assert tts_module.load_file("weights.safetensors", device="cpu") == {"backend": "pread"}
    assert calls == [("weights.safetensors", "cpu", "pread")]


def test_non_windows_keeps_chatterbox_loader_unchanged(monkeypatch):
    """The compatibility workaround is scoped to the Windows native failure."""
    original = object()
    tts_module = SimpleNamespace(load_file=original)

    def load_file(filename, device="cpu", *, backend="mmap"):
        return {}

    monkeypatch.setattr(worker.sys, "platform", "darwin")
    worker.enable_windows_pread_loader(tts_module, load_file)
    assert tts_module.load_file is original


@pytest.mark.parametrize("exit_code", [-1073741819, 3221225477])
def test_native_access_violation_exit_codes_have_one_readable_label(exit_code):
    """Both Windows signed and unsigned Popen forms must identify the same crash."""
    assert bt.worker_exit_label(exit_code) == f"{exit_code} (0xC0000005)"
