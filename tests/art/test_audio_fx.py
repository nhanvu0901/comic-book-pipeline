"""art_pipeline/audio_fx.py — calm frequency-shaping must PRESERVE length."""
import math
import wave
from pathlib import Path

import pytest

from art_pipeline import audio_fx

SR = 44100


def _sine_wav(path: Path, seconds: float, freq: float = 220.0):
    n = int(SR * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * freq * (i / SR)))
            frames += int(v & 0xFFFF).to_bytes(2, "little", signed=False) \
                if v >= 0 else int((v + 65536) & 0xFFFF).to_bytes(2, "little")
        w.writeframes(bytes(frames))


def _dur(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def test_build_calm_filter_has_expected_stages():
    af = audio_fx.build_calm_filter(lowpass_hz=4000, bass_gain_db=5,
                                    deess_gain_db=-6, lufs=-18)
    assert "lowpass=f=4000" in af
    assert "bass=g=5" in af
    assert "equalizer=f=3000" in af and "g=-6" in af
    assert "loudnorm=I=-18" in af
    assert "highpass=f=80" in af


def test_apply_calm_filters_preserves_length(tmp_path):
    ff = audio_fx._resolve_ffmpeg()
    import shutil
    if ff == "ffmpeg" and not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    wav = tmp_path / "audio.wav"
    _sine_wav(wav, 2.0)
    before = _dur(wav)
    out = audio_fx.apply_calm_filters(wav, log=lambda *_: None)
    after = _dur(out)
    assert out == wav
    assert after == pytest.approx(before, abs=0.05)   # length-preserving → no drift


def test_apply_calm_filters_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        audio_fx.apply_calm_filters(tmp_path / "nope.wav", log=lambda *_: None)
