"""Regression: a Resemble chunk that returns AUDIO but empty timestamps must not
leave a hole in word_timestamps (which blanks captions + freezes the panel)."""
import base64

import stages.stage_4.resemble_tts as rt
from stages.stage_4.resemble_tts import (
    _words_from_graphemes, _even_word_timestamps, _wrap_pcm_as_wav,
)


def _silence_wav_b64(dur: float = 2.0, sr: int = 44100) -> str:
    pcm = b"\x00\x00" * int(sr * dur)  # 16-bit mono silence
    return base64.b64encode(_wrap_pcm_as_wav(pcm, sample_rate=sr, sampwidth=2, channels=1)).decode()


def test_words_from_empty_graphemes_is_empty():
    assert _words_from_graphemes({}) == []
    assert _words_from_graphemes({"graph_chars": [], "graph_times": []}) == []


def test_even_word_timestamps_spreads_over_duration():
    out = _even_word_timestamps("one two three four", 4.0)
    assert [w["word"] for w in out] == ["one", "two", "three", "four"]
    assert out[0]["start"] == 0.0
    assert abs(out[-1]["end"] - 4.0) < 1e-6
    # monotonic non-overlapping
    for a, b in zip(out, out[1:]):
        assert a["end"] <= b["start"] + 1e-9
    assert _even_word_timestamps("", 4.0) == []
    assert _even_word_timestamps("x", 0.0) == []


def test_synthesize_fills_hole_when_chunk_has_no_timestamps(monkeypatch):
    monkeypatch.setattr(rt, "RESEMBLE_API_KEY", "test-key")
    b64 = _silence_wav_b64(2.0)
    # Every chunk returns valid audio but EMPTY audio_timestamps (the bug trigger).
    monkeypatch.setattr(rt, "_synth_chunk",
                        lambda *a, **k: {"success": True, "audio_content": b64, "audio_timestamps": {}})
    text = "Rogue stole the cosmic power. Galactus wanted it back."
    res = rt.synthesize(text, voice_id="x")
    words = res.word_timestamps
    assert words, "fallback must produce timestamps instead of a hole"
    assert len(words) == len(text.split())
    # no 20s-style hole — gaps between consecutive words stay small
    prev = 0.0
    for w in words:
        assert w["start"] >= prev - 1e-6
        assert w["start"] - prev < 1.0
        prev = w["end"]
