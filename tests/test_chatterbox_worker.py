"""The lightweight Windows-only loader policy used by the isolated Chatterbox worker."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from stages.stage_4 import background_tts as bt
from stages.stage_4 import _chatterbox_worker as worker
from stages.stage_4 import chatterbox_tts


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


@pytest.mark.parametrize("emit_progress", [False, True])
def test_direct_synthesis_deadline_covers_a_worker_that_stops_writing_stdout(tmp_path, monkeypatch, emit_progress):
    """The caller's timeout must apply while stdout is open, not only after the worker exits."""
    fake_worker = tmp_path / "stalled_worker.py"
    progress = "print('diagnostic before hang', flush=True)\n" if emit_progress else ""
    fake_worker.write_text(
        "import time\n" + progress + "time.sleep(1.5)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(chatterbox_tts, "_venv_python", lambda _venv=None: Path(sys.executable))
    monkeypatch.setattr(chatterbox_tts, "_WORKER", fake_worker)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out") as exc_info:
        chatterbox_tts.synthesize("One sentence.", timeout=0.1)
    assert time.monotonic() - started < 0.8
    if emit_progress:
        assert "diagnostic before hang" in str(exc_info.value)


def test_direct_synthesis_keeps_normal_worker_wav_parsing(tmp_path, monkeypatch):
    """The deadline reader must preserve the normal worker protocol and synthesized WAV result."""
    fake_worker = tmp_path / "successful_worker.py"
    fake_worker.write_text(
        "import json, sys, wave\n"
        "from pathlib import Path\n"
        "job = json.loads(Path(sys.argv[1]).read_text())\n"
        "out = Path(job['out_dir']); out.mkdir(parents=True, exist_ok=True)\n"
        "with wave.open(str(out / 'chunk_00000.wav'), 'wb') as wav:\n"
        "    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b'\\x00\\x00' * 800)\n"
        "print(json.dumps({'i': 0, 'sec': 0.1, 'sr': 8000}), flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(chatterbox_tts, "_venv_python", lambda _venv=None: Path(sys.executable))
    monkeypatch.setattr(chatterbox_tts, "_WORKER", fake_worker)

    result = chatterbox_tts.synthesize("One sentence.", timeout=1)

    assert result.sample_rate == 8000
    assert [word["word"] for word in result.word_timestamps] == ["One", "sentence."]
    assert result.word_timestamps[-1]["end"] == pytest.approx(0.1)
