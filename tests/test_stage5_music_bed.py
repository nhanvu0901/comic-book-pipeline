"""Guards for the background-music bed restored on 2026-08-12.

Context: the comic pipeline had NO music path at all — mix_audio only loudnormed the TTS, and
art_pipeline/assemble.py still imported a `_resolve_bgm` that no longer existed, so that module
could not even be imported. These cover the restored path, and above all that a project with no
music file behaves EXACTLY as it did before the bed existed.

Numbers under test are craft, not standards — see MUSIC_SCORING_RESEARCH_2026-08-12.md for why
no standards body specifies a music-vs-speech level.
"""
import subprocess

import pytest

from stages.stage_5.audio import _duck_expr, _require_ffmpeg, _silences, mix_audio
from stages.stage_5.pipeline import _resolve_bgm


def _tone(path, *, freq=220, dur=3.0, vol=0.4, gaps=False):
    """A stand-in narration/bed. `gaps=True` punches silence in so silencedetect has something
    to find — a continuous tone has no structure to duck against."""
    ff = _require_ffmpeg()
    af = f"volume={vol}"
    if gaps:
        # speak 0-1s, silent 1-2s, speak 2-3s
        af += ",volume=enable='between(t,1,2)':volume=0"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:duration={dur}",
                    "-af", af, "-ac", "2", "-ar", "48000", str(path)], check=True)
    return path


# ─── _resolve_bgm: resolution order ──────────────────────────────────────────

def test_resolve_bgm_returns_none_when_nothing_exists(tmp_path, monkeypatch):
    """The state every project ships in today. No music is a valid render, not an error."""
    monkeypatch.setattr("config.BG_MUSIC_PATH", str(tmp_path / "nope.mp3"))
    assert _resolve_bgm(log=lambda _m: None, root=tmp_path) is None


def test_resolve_bgm_prefers_explicit_then_project_then_config(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.mp3"
    project = tmp_path / "bgm.mp3"
    fallback = tmp_path / "config.mp3"
    for p in (explicit, project, fallback):
        p.write_bytes(b"x")
    monkeypatch.setattr("config.BG_MUSIC_PATH", str(fallback))

    assert _resolve_bgm(str(explicit), log=lambda _m: None, root=tmp_path) == explicit
    assert _resolve_bgm(log=lambda _m: None, root=tmp_path) == project
    assert _resolve_bgm(log=lambda _m: None) == fallback


def test_resolve_bgm_honours_the_off_switches(tmp_path, monkeypatch):
    f = tmp_path / "bgm.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr("config.BG_MUSIC_PATH", str(f))
    assert _resolve_bgm(enable_music=False, log=lambda _m: None) is None
    monkeypatch.setattr("config.ENABLE_BG_MUSIC", False)
    assert _resolve_bgm(log=lambda _m: None) is None


def test_resolve_bgm_keeps_the_three_positional_call_art_pipeline_makes():
    """art_pipeline/assemble.py:588 calls _resolve_bgm(path, enable, log) positionally.
    That call site is why this function lives in stage_5.pipeline at all."""
    assert _resolve_bgm(None, False, lambda _m: None) is None


# ─── duck curve ──────────────────────────────────────────────────────────────

def test_duck_expr_is_a_constant_when_no_gap_qualifies():
    """Nearly-continuous narration is the normal case — the bed then just sits ducked."""
    assert _duck_expr([], 0.5, 0.25) == "0.5000"


def test_duck_expr_skips_gaps_too_short_to_ramp_through():
    """A 0.3s gap with a 0.25s ramp each side never reaches full level before ducking again —
    that is the definition of pumping, so it must not produce a lift."""
    assert _duck_expr([(1.0, 1.3)], 0.5, 0.25) == "0.5000"


def test_duck_expr_lifts_inside_a_long_gap():
    expr = _duck_expr([(1.0, 3.0)], 0.5, 0.25)
    assert expr.startswith("0.5000+0.5000*min(1,")
    assert "between(t,1.000,1.250)" in expr and "between(t,2.750,3.000)" in expr


# ─── mix_audio ───────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_no_music_path_is_unchanged_and_leaves_no_premix(tmp_path):
    """The regression that matters most: every existing project must render as before."""
    src = _tone(tmp_path / "tts.wav")
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    mix_audio(src, a)
    mix_audio(src, b, bg_music_path=None)
    assert a.read_bytes() == b.read_bytes()
    assert not list(tmp_path.glob("*.premix.wav"))


@pytest.mark.integration
def test_a_missing_music_file_degrades_to_narration_only(tmp_path):
    """A bed must never be able to fail a render."""
    src = _tone(tmp_path / "tts.wav")
    plain, with_missing = tmp_path / "plain.wav", tmp_path / "missing.wav"
    mix_audio(src, plain)
    mix_audio(src, with_missing, bg_music_path=tmp_path / "does_not_exist.mp3")
    assert with_missing.read_bytes() == plain.read_bytes()


@pytest.mark.integration
def test_music_actually_reaches_the_output_and_cleans_up(tmp_path):
    src = _tone(tmp_path / "tts.wav", gaps=True)
    bed = _tone(tmp_path / "bed.wav", freq=110, dur=1.0)   # shorter than the narration → loops
    plain, mixed = tmp_path / "plain.wav", tmp_path / "mixed.wav"
    mix_audio(src, plain)
    mix_audio(src, mixed, bg_music_path=bed)
    assert mixed.exists() and mixed.read_bytes() != plain.read_bytes()
    assert not list(tmp_path.glob("*.premix.wav")), "premix scratch file was left behind"


@pytest.mark.integration
def test_silences_reads_the_audio_not_the_word_timestamps(tmp_path):
    """_silences must find real silence. word_timestamps.json cannot: Chatterbox spreads a
    sentence's words evenly across its measured duration, so every inter-word gap there is
    exactly 0.0 (measured on cap-shield-broken: 170/170 gaps == 0.0)."""
    src = _tone(tmp_path / "tts.wav", gaps=True)
    found = _silences(_require_ffmpeg(), src, -35)
    assert found, "silencedetect found nothing in audio with a 1s hole in it"
    assert any(s <= 1.15 and e >= 1.85 for s, e in found), found


# ─── regressions from the 2026-08-13 review ──────────────────────────────────

def test_duck_expr_is_capped_so_longform_can_render():
    """MEASURED: ffmpeg 8.1's expression evaluator parses 31 lifts and fails on 32
    ("Missing ')' or too many args"); a 19-minute long-form has ~380 qualifying gaps and
    built a 44k-char expression, which also trips Windows' 32767 command-line limit
    (WinError 206). Exceeding either is a FAILED RENDER, not a worse mix — so the cap is
    clamped in code and cannot be raised past it by config."""
    from stages.stage_5.audio import _DUCK_GAPS_HARD_MAX

    gaps = [(i * 3.0, i * 3.0 + 0.8) for i in range(380)]
    for asked in (0, 24, 500):                      # 0 = "no cap", 500 = a bad config value
        expr = _duck_expr(gaps, 0.5, 0.25, asked)
        assert expr.count("between(") <= _DUCK_GAPS_HARD_MAX * 3
        assert len(expr) < 8000, f"expression too long for ffmpeg at max_gaps={asked}"


def test_duck_expr_keeps_the_longest_pauses_when_capping():
    """A listener registers the long pauses; dropping those and keeping brief ones would
    lift the bed where nobody notices."""
    gaps = [(0.0, 0.9), (10.0, 11.9), (20.0, 20.9), (30.0, 32.9)]
    expr = _duck_expr(gaps, 0.5, 0.25, 2)
    assert "between(t,30.000" in expr and "between(t,10.000" in expr
    assert "between(t,0.000" not in expr


def test_duck_expr_survives_a_zero_ramp():
    """BG_MUSIC_DUCK_RAMP_S=0 is a plausible reading of "no fade" and used to divide by
    zero, producing NaN inside the filter — a bed that silently goes mute."""
    assert _duck_expr([(1.0, 3.0)], 0.5, 0.0) == "0.5000"


@pytest.mark.integration
def test_premix_is_removed_even_when_the_final_encode_fails(tmp_path, monkeypatch):
    """The cleanup used to sit after the encode, so a failure left a scratch .premix.wav in
    the project folder — where _resolve_bgm globs for bgm.* and a human reads the file list."""
    from stages.stage_5 import audio as A

    src = _tone(tmp_path / "tts.wav", gaps=True)
    bed = _tone(tmp_path / "bed.wav", freq=110, dur=1.0)
    orig, calls = A._run, {"n": 0}

    def _boom(cmd):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("ffmpeg died on the final encode")
        return orig(cmd)

    monkeypatch.setattr(A, "_run", _boom)
    with pytest.raises(RuntimeError):
        A.mix_audio(src, tmp_path / "out.wav", bg_music_path=bed)
    assert not list(tmp_path.glob("*.premix.wav"))
