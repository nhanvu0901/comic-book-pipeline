import subprocess
from pathlib import Path
import pytest
from stages.stage_5.pipeline import _build_outro_card, _pad_audio_tail, _require_ffmpeg, _ass_drawtext_escape


def _ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float((out.stdout or "0").strip() or 0.0)


@pytest.mark.integration
def test_build_outro_card_makes_clip_of_right_duration(tmp_path):
    out = tmp_path / "card.mp4"
    res = _build_outro_card(out, duration=3.5, logo=None,
                            channel_name="Grimframe", handle="@grimframe")
    assert res == out and out.exists()
    assert abs(_ffprobe_duration(out) - 3.5) < 0.3


@pytest.mark.integration
def test_pad_audio_tail_extends_duration(tmp_path):
    ff = _require_ffmpeg()
    src = tmp_path / "a.wav"
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(src)], capture_output=True)
    out = tmp_path / "a_pad.wav"
    _pad_audio_tail(src, 3.5, out)
    assert abs(_ffprobe_duration(out) - 5.5) < 0.2


def test_drawtext_escape_handles_apostrophe():
    # A channel name with an apostrophe must not leave a bare straight quote
    # that would break ffmpeg drawtext’s single-quoted text=’...’ filter.
    out = _ass_drawtext_escape("Master’s Channel")
    straight_apos = chr(0x27)  # U+0027 = ‘
    curly_apos = chr(0x2019)   # U+2019 = ‘
    assert straight_apos not in out  # no bare straight apostrophe survives
    assert curly_apos in out         # converted to a curly apostrophe


def test_drawtext_escape_colon_and_backslash():
    assert _ass_drawtext_escape("a:b") == "a\\:b"
    assert _ass_drawtext_escape("a\\b") == "a\\\\b"
