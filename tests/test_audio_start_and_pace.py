"""Audible from frame 0 + adaptive pace (2026-11-12).

Measured on 8 shipped Shorts: first sound at 0.21 s and the first 100 ms at -58 dBFS (TTS model
silence + a 1 s music fade-in), and a pace of 2.80-3.28 wps under a fixed atempo. These cover the
general mechanisms that fix both, on synthetic audio built with ffmpeg lavfi (tones + silence):

* Stage 4 trims the narration's leading silence and shifts every timing by exactly that amount;
  a narration that already starts at 0 is untouched.
* TTS_TARGET_WPS (unset by default) picks the atempo that reaches a target pace, clamped; unset
  leaves the output exactly as before.
* The music (CapCut reference track or generated bed) is at its working level in the first 100 ms.
"""
import hashlib
import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

import config
from stages.stage_4 import narration_audio as na

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
FF = shutil.which("ffmpeg") or "ffmpeg"
SR = 24000


# ─── helpers ─────────────────────────────────────────────────────────────────────────────────

def _lavfi(path: Path, src: str, af: str | None = None, *, sr: int = SR, ch: int = 1) -> Path:
    cmd = [FF, "-y", "-v", "error", "-f", "lavfi", "-i", src]
    if af:
        cmd += ["-af", af]
    cmd += ["-ac", str(ch), "-ar", str(sr), "-c:a", "pcm_s16le", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _speech(path: Path, *, lead: float, body: float, tail: float = 0.0, vol: float = 0.4) -> Path:
    """`lead` s of silence, then a `body` s tone (a stand-in for speech), then `tail` silence."""
    return _lavfi(path, f"sine=frequency=300:sample_rate={SR}:duration={body}",
                  f"volume={vol},adelay={int(round(lead * 1000))}:all=1,apad=pad_dur={tail}")


def _frames(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wf:
        return wf.readframes(wf.getnframes())


def _dur(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _decode(path: Path, sr: int = 48000) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4")


def _db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt((x.astype(np.float64) ** 2).mean()) + 1e-12))


def _even(n: int, start: float, end: float) -> list[dict]:
    step = (end - start) / n
    return [{"word": f"w{i}", "start": round(start + i * step, 4),
             "end": round(start + (i + 1) * step, 4)} for i in range(n)]


TRIM = dict(threshold_db=-50.0, keep_ms=20.0, max_trim_s=2.0)


# ─── narration_audio: the trim ───────────────────────────────────────────────────────────────

def test_trim_removes_the_lead_silence_minus_the_pre_roll(tmp_path):
    wav = _speech(tmp_path / "a.wav", lead=0.25, body=1.0)
    before = _frames(wav)
    cut = na.trim_leading_silence(wav, **TRIM)
    assert cut == pytest.approx(0.25 - 0.020, abs=0.006)
    # frame-exact: the result is the original minus exactly `cut` seconds of frames, no re-encode
    after = _frames(wav)
    assert before.endswith(after)
    assert (len(before) - len(after)) / 2 / SR == pytest.approx(cut, abs=1e-9)
    span = na.sound_span(na.read_wav_mono(wav), -50.0)
    assert span[0] == pytest.approx(0.020, abs=0.006)


def test_trim_shifts_every_word_by_exactly_the_cut():
    words = [{"word": "a", "start": 0.0, "end": 0.40}, {"word": "b", "start": 0.40, "end": 0.9},
             {"word": "c", "start": 0.9, "end": 1.25}]
    out = na.shift_words(words, 0.23)
    assert [w["start"] for w in out] == [0.0, pytest.approx(0.17), pytest.approx(0.67)]
    assert [w["end"] for w in out] == [pytest.approx(0.17), pytest.approx(0.67), pytest.approx(1.02)]
    assert na.shift_words(words, 0.0) is words


def test_narration_already_starting_at_zero_is_unchanged(tmp_path):
    wav = _speech(tmp_path / "a.wav", lead=0.0, body=1.0)
    sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    assert na.trim_leading_silence(wav, **TRIM) == 0.0
    assert hashlib.sha256(wav.read_bytes()).hexdigest() == sha


def test_a_click_in_the_model_silence_is_not_speech(tmp_path):
    wav = _lavfi(tmp_path / "a.wav",
                 f"aevalsrc='if(between(t,0.05,0.052),0.5,0)+if(gte(t,0.3),0.4*sin(2*PI*300*t),0)'"
                 f":s={SR}:d=1.3")
    cut = na.trim_leading_silence(wav, **TRIM)
    assert cut == pytest.approx(0.30 - 0.020, abs=0.006)


def test_all_silent_or_capped_input_is_safe(tmp_path):
    silent = _lavfi(tmp_path / "s.wav", f"anullsrc=r={SR}:cl=mono:d=1")
    assert na.trim_leading_silence(silent, **TRIM) == 0.0
    late = _speech(tmp_path / "late.wav", lead=3.0, body=0.5)
    assert na.trim_leading_silence(late, threshold_db=-50, keep_ms=20, max_trim_s=1.0) == \
        pytest.approx(1.0, abs=1e-3)


# ─── narration_audio: the pace maths ─────────────────────────────────────────────────────────

def test_choose_atempo_reaches_the_target_and_respects_the_clamp():
    assert na.choose_atempo(3.0, 3.6, 1.0, 1.35) == pytest.approx(1.2)
    assert na.choose_atempo(2.0, 3.6, 1.0, 1.35) == 1.35          # never chipmunked
    assert na.choose_atempo(4.0, 3.6, 1.0, 1.35) == 1.0           # never slowed below the floor
    assert na.tempo_bounds(5.0, 0.1) == (0.5, 2.0)                # ffmpeg-legal, ordered
    assert na.resolve_target_wps(None) is None
    assert na.resolve_target_wps("") is None
    assert na.resolve_target_wps("0") is None
    assert na.resolve_target_wps("abc") is None
    assert na.resolve_target_wps("3.5") == 3.5


# ─── Stage 4 end to end, every provider ──────────────────────────────────────────────────────

SCENES = [{"id": 1, "text": "One two three four five six."},
          {"id": 2, "text": "Seven eight nine ten eleven twelve."}]


def _stage4(tmp_path, monkeypatch, provider: str, raw_wav: Path, raw_words: list[dict]):
    """A project + a fake `provider` whose synthesize() returns `raw_wav`/`raw_words`."""
    import stages.review_gate as rg
    import stages.stage_4.pipeline as p4
    monkeypatch.setattr(p4, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    proj = tmp_path / "p"
    (proj / "review").mkdir(parents=True)
    (proj / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": {}}))
    (proj / "narration.json").write_text(json.dumps({"mode": "explore_answer", "scenes": SCENES}))
    payload = raw_wav.read_bytes()

    class R:
        wav_bytes = payload
        sample_rate = SR
        word_timestamps = [dict(w) for w in raw_words]

    fake = lambda *a, **k: R()  # noqa: E731
    if provider == "chatterbox":
        from stages.stage_4 import chatterbox_tts as mod
    elif provider == "resemble":
        from stages.stage_4 import resemble_tts as mod
    else:
        from stages.stage_4 import cartesia_tts as mod
    monkeypatch.setattr(mod, "synthesize", fake)
    return p4, proj


def _run(p4, provider, **kw):
    return p4.synthesize_project("p", provider=provider, skip_review=True, force=True, **kw)


@pytest.mark.parametrize("provider", ["chatterbox", "resemble", "cartesia"])
def test_stage4_voice_starts_at_zero_and_all_timings_shift(tmp_path, monkeypatch, provider):
    raw = _speech(tmp_path / "raw.wav", lead=0.20, body=3.0, tail=0.2)
    raw_words = _even(12, 0.20, 3.20)
    p4, proj = _stage4(tmp_path, monkeypatch, provider, raw, raw_words)
    monkeypatch.setattr(config, "TTS_TARGET_WPS", None)
    res = _run(p4, provider, post_atempo=1.0)

    audio = proj / "audio.wav"
    cut = _dur(raw) - _dur(audio)
    assert cut == pytest.approx(0.18, abs=0.006)
    assert _frames(raw).endswith(_frames(audio))
    x = _decode(audio)
    assert _db(x[:4800]) > _db(x[24000:48000]) - 2.5, "the first 100 ms carry the voice"
    words = json.loads((proj / "word_timestamps.json").read_text())
    for w0, w1 in zip(raw_words, words):
        assert w1["start"] == pytest.approx(max(0.0, w0["start"] - cut), abs=1e-4)
        assert w1["end"] == pytest.approx(w0["end"] - cut, abs=1e-4)
    scenes = json.loads((proj / "scene_timings.json").read_text())
    assert scenes[1]["start"] == pytest.approx(raw_words[6]["start"] - cut, abs=1e-3)
    chunks = json.loads((proj / "caption_chunks.json").read_text())
    assert chunks[-1]["end"] == pytest.approx(raw_words[-1]["end"] - cut, abs=1e-3)
    assert res.audio_duration_seconds == pytest.approx(_dur(audio), abs=1e-3)


def test_stage4_reuse_path_trims_an_old_audio_once(tmp_path, monkeypatch):
    raw = _speech(tmp_path / "raw.wav", lead=0.20, body=3.0)
    p4, proj = _stage4(tmp_path, monkeypatch, "chatterbox", raw, _even(12, 0.2, 3.2))
    shutil.copyfile(raw, proj / "audio.wav")                    # an audio.wav from before the trim
    (proj / "word_timestamps.json").write_text(json.dumps(_even(12, 0.2, 3.2)))
    p4.synthesize_project("p", provider="chatterbox", skip_review=True, post_atempo=1.0)
    first = (proj / "audio.wav").read_bytes()
    words = json.loads((proj / "word_timestamps.json").read_text())
    assert words[1]["start"] == pytest.approx(0.45 - 0.18, abs=0.006)
    p4.synthesize_project("p", provider="chatterbox", skip_review=True, post_atempo=1.0)
    assert (proj / "audio.wav").read_bytes() == first, "idempotent: no second trim"
    assert json.loads((proj / "word_timestamps.json").read_text()) == words


def test_unset_target_and_no_trim_is_byte_for_byte_the_old_output(tmp_path, monkeypatch):
    """TTS_TARGET_WPS unset = the fixed POST_ATEMPO path exactly; the trim only removes frames."""
    raw = _speech(tmp_path / "raw.wav", lead=0.20, body=3.0)
    raw_words = _even(12, 0.2, 3.2)
    p4, proj = _stage4(tmp_path, monkeypatch, "chatterbox", raw, raw_words)
    monkeypatch.setattr(p4, "POST_ATEMPO", 1.15)
    monkeypatch.setattr(config, "TTS_TARGET_WPS", None)

    old = tmp_path / "old.wav"
    shutil.copyfile(raw, old)
    p4._apply_atempo(old, 1.15)                                 # what Stage 4 did before
    old_words = [{"word": w["word"], "start": w["start"] / 1.15, "end": w["end"] / 1.15}
                 for w in raw_words]

    monkeypatch.setattr(config, "TTS_TRIM_LEADING_SILENCE", False)
    _run(p4, "chatterbox")                                      # post_atempo=None → mode default
    assert (proj / "audio.wav").read_bytes() == old.read_bytes()
    assert json.loads((proj / "word_timestamps.json").read_text()) == old_words

    monkeypatch.setattr(config, "TTS_TRIM_LEADING_SILENCE", True)
    _run(p4, "chatterbox")
    assert _frames(old).endswith(_frames(proj / "audio.wav"))  # sample-for-sample, minus the lead


@pytest.mark.parametrize("provider", ["chatterbox", "resemble", "cartesia"])
def test_adaptive_pace_hits_the_target(tmp_path, monkeypatch, capsys, provider):
    raw = _speech(tmp_path / "raw.wav", lead=0.2, body=3.0, tail=0.3)   # 9 words / 3 s = 3.0 wps
    raw_words = _even(9, 0.2, 3.2)
    p4, proj = _stage4(tmp_path, monkeypatch, provider, raw, raw_words)
    monkeypatch.setattr(config, "TTS_TARGET_WPS", "3.6")
    monkeypatch.setattr(config, "TTS_TEMPO_MIN", 1.0)
    monkeypatch.setattr(config, "TTS_TEMPO_MAX", 1.35)
    _run(p4, provider)
    logs = capsys.readouterr().out.splitlines()
    audio = proj / "audio.wav"
    span = na.sound_span(na.read_wav_mono(audio), -50.0)
    assert na.measured_wps(9, span) == pytest.approx(3.6, abs=0.06)
    assert any("atempo 1.2" in l and "target 3.60" in l for l in logs), logs
    words = json.loads((proj / "word_timestamps.json").read_text())
    # word timings follow the audio: the last word ends where the sound ends
    assert words[-1]["end"] == pytest.approx(span[1], abs=0.03)


def test_adaptive_pace_is_clamped(tmp_path, monkeypatch):
    raw = _speech(tmp_path / "raw.wav", lead=0.0, body=3.0)            # 6 words / 3 s = 2.0 wps
    p4, proj = _stage4(tmp_path, monkeypatch, "chatterbox", raw, _even(6, 0.0, 3.0))
    monkeypatch.setattr(config, "TTS_TARGET_WPS", "3.6")
    monkeypatch.setattr(config, "TTS_TEMPO_MIN", 1.0)
    monkeypatch.setattr(config, "TTS_TEMPO_MAX", 1.35)
    _run(p4, "chatterbox")
    assert _dur(proj / "audio.wav") == pytest.approx(3.0 / 1.35, abs=0.02)


def test_explicit_atempo_and_longform_ignore_the_target(tmp_path, monkeypatch):
    raw = _speech(tmp_path / "raw.wav", lead=0.0, body=3.0)
    p4, proj = _stage4(tmp_path, monkeypatch, "chatterbox", raw, _even(9, 0.0, 3.0))
    monkeypatch.setattr(config, "TTS_TARGET_WPS", "3.6")
    _run(p4, "chatterbox", post_atempo=1.1)                             # manual override wins
    assert _dur(proj / "audio.wav") == pytest.approx(3.0 / 1.1, abs=0.02)
    assert p4.target_wps_for_mode("panel_walk") is None
    assert p4.target_wps_for_mode("explore_answer") == 3.6


def test_cached_chunks_reach_the_target_with_the_remainder(tmp_path):
    """The clip-flow cache holds chunks already at the fixed atempo; the total pace is measured
    relative to that, so the remainder is target / measured."""
    import stages.stage_4.pipeline as p4
    wav = _speech(tmp_path / "c.wav", lead=0.0, body=3.0)               # 9 words: 3.0 wps measured
    total = p4._pace_for_target(wav, 9, 3.45, applied=1.15, fallback=1.15, log=lambda _m: None)
    assert total == pytest.approx(1.15 * 3.45 / 3.0, abs=1e-3)
    silent = _lavfi(tmp_path / "s.wav", f"anullsrc=r={SR}:cl=mono:d=1")
    assert p4._pace_for_target(silent, 9, 3.45, applied=1.15, fallback=1.15,
                               log=lambda _m: None) == 1.15


# ─── music at its working level from frame 0 ─────────────────────────────────────────────────

def test_capcut_music_is_at_working_level_in_the_first_100ms(tmp_path):
    from stages.music_sync import build_capcut_music_mix_filter
    music = _lavfi(tmp_path / "m.wav", "sine=frequency=440:sample_rate=48000:duration=8",
                   "volume=0.5", sr=48000, ch=2)

    def render(fade_in: float) -> np.ndarray:
        out = tmp_path / f"o_{fade_in}.wav"
        f = build_capcut_music_mix_filter(-10.0, 6.0, fade_in=fade_in, fade_out=2.0)
        subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo:d=6", "-i", str(music),
                        "-filter_complex", f, "-map", "[aout]", str(out)], check=True)
        return _decode(out)

    x = render(config.CAPCUT_MUSIC_FADE_IN_S)
    working = _db(x[48000:96000])
    assert _db(x[:4800]) > working - 1.5, "first 100 ms at the working level"
    assert _db(render(1.0)[:4800]) < working - 15, "the old 1 s fade left it near-silent"
    assert config.CAPCUT_MUSIC_FADE_IN_S <= 0.05
    f = build_capcut_music_mix_filter(-10.0, 6.0, fade_in=0.0, fade_out=2.0)
    assert "afade=t=in" not in f and "afade=t=out" in f, "fade-out kept"


def test_generated_bed_skips_a_soft_intro(tmp_path, monkeypatch):
    from stages.stage_5 import audio as A
    ff = A._require_ffmpeg()
    # bed: 2 s soft intro (-46 dB), then the body at working level
    bed = _lavfi(tmp_path / "bed.wav",
                 "aevalsrc='if(lt(t,2),0.005,0.3)*sin(2*PI*220*t)':s=48000:d=12", sr=48000, ch=2)
    assert A.bed_intro_offset(ff, bed, within_db=12, max_s=4) == pytest.approx(2.0, abs=0.06)
    assert A.bed_intro_offset(ff, bed, within_db=12, max_s=1) == pytest.approx(1.0, abs=1e-3)
    flat = _lavfi(tmp_path / "flat.wav", "sine=frequency=220:sample_rate=48000:duration=6",
                  "volume=0.3", sr=48000, ch=2)
    assert A.bed_intro_offset(ff, flat, within_db=12, max_s=4) == 0.0

    # narration: silent for 1 s, then a tone — so the first 100 ms of the premix are bed only
    nar = _lavfi(tmp_path / "nar.wav",
                 "aevalsrc='if(gte(t,1),0.3*sin(2*PI*600*t),0)':s=48000:d=6", sr=48000, ch=2)
    monkeypatch.setattr(config, "BG_MUSIC_DUCK_DB", 0.0)               # flat bed, isolate the start

    def premix(skip: bool) -> np.ndarray:
        monkeypatch.setattr(config, "BG_MUSIC_SKIP_INTRO", skip)
        out = tmp_path / f"pm_{skip}.wav"
        assert A._build_bed_mix(ff, nar, bed, out, "loudnorm=I=-14:TP=-1:LRA=9", lambda _m: None)
        return _decode(out)

    x = premix(True)
    assert _db(x[:4800]) > _db(x[24000:43200]) - 1.5, "bed at its working level in the first 100 ms"
    assert _db(premix(False)[:4800]) < _db(x[:4800]) - 20
