"""Art-side calm/sleep audio shaping (2026-06-13, research/reports/2026-06-13-*).

A length-PRESERVING ffmpeg filter pass applied to the finished narration WAV:
it shapes FREQUENCY (de-ess harsh presence, warm low-mid, low-pass the airy
top) and normalises to a low "sleep" loudness. Because every filter here keeps
the sample count identical, the `scene_timings` / `word_timestamps` already
computed by TTS stay valid — NO A/V drift (the one hard rule: never change the
audio's length after timings are computed).

Comic Stage 4/5 are NOT touched; this is an art-only post-step run on the
project's `audio.wav` after synthesis."""
import shutil
import subprocess
from pathlib import Path


def _resolve_ffmpeg(override: str | None = None) -> str:
    if override:
        return override
    try:
        from config import FFMPEG_BIN  # comic config (read-only import)
        if FFMPEG_BIN and Path(FFMPEG_BIN).exists():
            return FFMPEG_BIN
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def build_calm_filter(*, lowpass_hz: int, bass_gain_db: float,
                      deess_gain_db: float, lufs: float) -> str:
    """The length-preserving ffmpeg -af chain for a soothing voice:
      highpass 80 Hz   — drop sub rumble
      equalizer 3 kHz  — cut sibilance / harsh presence (deess_gain_db, negative)
      bass +g          — warmth in the low-mids
      lowpass          — remove the airy top that keeps the brain alert
      loudnorm I=lufs  — gentle, low sleep-level loudness (single-pass, in-length)
    """
    return (
        "highpass=f=80,"
        f"equalizer=f=3000:width_type=q:w=1.5:g={deess_gain_db},"
        f"bass=g={bass_gain_db}:f=200,"
        f"lowpass=f={lowpass_hz},"
        f"loudnorm=I={lufs}:TP=-2:LRA=11"
    )


def apply_calm_filters(wav_path, *, lowpass_hz: int = 4000,
                       bass_gain_db: float = 5.0, deess_gain_db: float = -6.0,
                       lufs: float = -18.0, ffmpeg: str | None = None,
                       log=print) -> Path:
    """Apply the calm filter chain in-place to `wav_path`. Length-preserving.
    Returns the path. Raises RuntimeError if ffmpeg fails."""
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise FileNotFoundError(f"audio not found: {wav_path}")
    ff = _resolve_ffmpeg(ffmpeg)
    af = build_calm_filter(lowpass_hz=lowpass_hz, bass_gain_db=bass_gain_db,
                           deess_gain_db=deess_gain_db, lufs=lufs)
    tmp = wav_path.with_suffix(".calm.wav")
    res = subprocess.run(
        [ff, "-y", "-i", str(wav_path), "-af", af, "-c:a", "pcm_s16le", str(tmp)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"calm-audio ffmpeg failed: {res.stderr[-500:]}")
    tmp.replace(wav_path)
    log(f"[calm-audio] shaped {wav_path.name} "
        f"(lowpass {lowpass_hz}Hz, bass +{bass_gain_db}dB, "
        f"deess {deess_gain_db}dB, {lufs} LUFS)")
    return wav_path
