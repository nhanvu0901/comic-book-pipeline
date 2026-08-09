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


def test_chunk_cap_stays_under_the_measured_generation_timeout():
    """The server caps GENERATION TIME near 60s, not character count. Measured 2026-07-29:
    1491 chars took 39.8s and 2189 chars 504'd at 62.7s, i.e. ≈0.025 s/char + ~5s overhead.
    A cap that projects past ~55s would 504 and then burn another 60s on the retry."""
    projected_seconds = rt._MAX_SYNTH_CHARS * 0.025 + 5
    assert projected_seconds < 55, (
        f"_MAX_SYNTH_CHARS={rt._MAX_SYNTH_CHARS} projects to {projected_seconds:.0f}s "
        f"generation — too close to the ~60s server cap")


def test_a_typical_short_now_fits_in_one_chunk():
    """Our Shorts run 1000-1300 chars (5 shipped projects: 1010, 1080, 1104, 1110, 1278). At the
    old 700-char cap every one of them split in two; at 1200 most are a single generation."""
    for chars in (1010, 1080, 1104, 1110):
        text = " ".join(["word"] * (chars // 5)) + "."
        assert len(rt._split_chunks(text, rt._MAX_SYNTH_CHARS)) == 1, chars


def test_split_chunks_never_exceeds_the_cap_and_loses_no_words():
    text = " ".join(f"Sentence {i} says something here." for i in range(400))
    chunks = rt._split_chunks(text, rt._MAX_SYNTH_CHARS)
    assert len(chunks) > 1
    assert all(len(c) <= rt._MAX_SYNTH_CHARS for c in chunks)
    assert " ".join(chunks).split() == text.split()


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


# ─── RMS chunk normalization (voice consistency across batches) ──────────────

def test_rms_normalize_equalizes_chunk_loudness():
    import numpy as np
    from stages.stage_4.resemble_tts import _rms_normalize_chunks
    def chunk(amp, n=4000):
        return (np.sin(np.linspace(0, 50, n)) * amp).astype(np.int16).tobytes()
    parts = [chunk(3000), chunk(12000), chunk(1500)]   # quiet / loud / quieter
    joined = _rms_normalize_chunks(parts, 2)
    a = np.frombuffer(joined, dtype=np.int16).astype(np.float32)
    sizes = [len(p) // 2 for p in parts]
    off, after = 0, []
    for s in sizes:
        seg = a[off:off + s]; after.append(float(np.sqrt(np.mean(seg ** 2)))); off += s
    assert max(after) / min(after) < 1.15, f"chunks not equalized: {after}"
    assert np.max(np.abs(a)) <= 32767, "normalization must never clip"


def test_rms_normalize_passthrough_non_int16_and_silence():
    from stages.stage_4.resemble_tts import _rms_normalize_chunks
    # non-int16 width → untouched join
    parts = [b"\x01\x02\x03", b"\x04\x05\x06"]
    assert _rms_normalize_chunks(parts, 3) == b"".join(parts)
    # all-silence chunks (no voiced RMS) → untouched join, no divide-by-zero
    sil = (b"\x00\x00" * 100)
    assert _rms_normalize_chunks([sil, sil], 2) == sil + sil
