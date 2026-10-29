"""The lightweight Windows-only loader policy used by the isolated Chatterbox worker."""
from types import SimpleNamespace

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
