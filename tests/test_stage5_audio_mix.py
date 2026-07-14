import re
import subprocess
import pytest
from stages.stage_5.audio import mix_audio, _require_ffmpeg


def _integrated_lufs(path) -> float:
    """One-pass loudnorm analysis of `path`; returns its Input Integrated LUFS."""
    ff = _require_ffmpeg()
    res = subprocess.run(
        [ff, "-i", str(path), "-af", "loudnorm=print_format=summary", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"Input Integrated:\s*(-?\d+\.?\d*)\s*LUFS", res.stderr)
    assert m, f"loudnorm summary not found in stderr:\n{res.stderr[-1000:]}"
    return float(m.group(1))


@pytest.mark.integration
def test_mix_audio_loudnorms_quiet_tts_into_target_range(tmp_path):
    ff = _require_ffmpeg()
    src = tmp_path / "tts.wav"
    # A quiet sine tone stands in for a soft TTS take — real narration.wav levels
    # vary a lot take to take, which is exactly why loudnorm exists.
    subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
         "-af", "volume=0.05", str(src)],
        capture_output=True,
    )
    out = tmp_path / "mixed.wav"
    result = mix_audio(src, out)
    assert result == out and out.exists()
    lufs = _integrated_lufs(out)
    # One-pass loudnorm on a 3s sample has real error vs the -14 LUFS target;
    # a wide band confirms it normalized (source was far quieter) without being flaky.
    assert -17.0 <= lufs <= -11.0, f"expected roughly -14 LUFS, got {lufs}"
