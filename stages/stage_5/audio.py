"""Loudness-normalize TTS narration for video mux."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

# EBU R128 target. -14 LUFS is what YouTube normalises to, so hitting it exactly is what keeps
# our video as loud as the one the viewer just watched.
_TARGET_I = -14.0
_TARGET_TP = -1.0
_TARGET_LRA = 9.0


def mix_audio(
    tts_wav: Path,
    out_path: Path,
    *,
    bg_music_path: Path | str | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Mix the narration (optionally over a ducked music bed) and loudnorm it to out_path.

    `bg_music_path` None / missing / unreadable → narration-only, byte-identical to the
    no-music behaviour that shipped before the bed existed. The bed is folded in BEFORE
    normalisation on purpose: ATSC A/85 measures short-form (<~2-3 min) on the FULL MIX,
    not on an isolated anchor, so the two loudnorm passes below must see what ships.

    TWO PASSES (changed 2026-07-29, Master approved). Single-pass loudnorm has to normalise the
    opening of the file before it has heard the rest, so it guesses and converges — and lands
    short. Measured on four shipped projects, all of which asked for I=-14:

        loki -15.4 · psylocke -15.7 · wolverine -15.5 · wonder-woman -16.2 LUFS

    1.4-2.2 LU under target, by a different amount each time. YouTube normalises playback to
    ~-14 LUFS, so undershooting means our videos play quieter than whatever the viewer watched
    before — and since the miss varies per file, it is ALSO a loudness inconsistency between our
    own videos. Pass 1 measures and prints JSON; pass 2 feeds those measurements back so ffmpeg
    applies one correct gain instead of guessing. Costs one extra read of the file.

    Note this fixes loudness ACROSS videos, not the prosody wobble WITHIN one — that is the TTS
    model itself and normalisation cannot touch it. See
    project_audio_inconsistency_diagnosed_2026-07-29.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log = progress or (lambda _m: None)

    ff = _require_ffmpeg()
    base = f"loudnorm=I={_TARGET_I}:TP={_TARGET_TP}:LRA={_TARGET_LRA}"

    # Fold the bed in first, then normalise the RESULT. `src` stays tts_wav when there is no
    # music, so the whole path below is untouched in that case.
    src, premix = tts_wav, None
    if bg_music_path:
        premix = out_path.with_name(out_path.stem + ".premix.wav")
        if _build_bed_mix(ff, tts_wav, Path(bg_music_path), premix, base, log):
            src = premix
        else:
            premix = None

    try:
        measured = _measure(ff, src, base, log)
        af = base
        if measured:
            af = (f"{base}:measured_I={measured['input_i']}"
                  f":measured_TP={measured['input_tp']}"
                  f":measured_LRA={measured['input_lra']}"
                  f":measured_thresh={measured['input_thresh']}"
                  f":offset={measured['target_offset']}:linear=true")
            log(f"[stage5] loudnorm pass 1: measured I={measured['input_i']} LUFS, "
                f"LRA={measured['input_lra']} LU → applying a single corrective gain")
        else:
            # Measurement failed (odd ffmpeg build, unparseable output). One pass still
            # produces usable audio, just off target — better than failing the render for it.
            log("[stage5] loudnorm pass 1 gave no measurements — falling back to single-pass")

        log(f"[stage5] loudnorm {'MIX' if premix else 'TTS'} → {out_path.name}")
        _run([ff, "-y", "-i", str(src), "-af", af,
              "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(out_path)])
    finally:
        # finally, not a trailing statement: if the encode above raises (disk full, a bad
        # filter arg), the scratch premix would otherwise be left sitting in the project
        # folder — where _resolve_bgm globs for bgm.* and a human reads the file list.
        if premix is not None:
            premix.unlink(missing_ok=True)
    return out_path


_MEASURED_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def _measure(ff: str, src: Path, base_filter: str, log: Callable[[str], None]) -> dict | None:
    """Pass 1: analyse only. `-f null` writes no audio, so this is a read, not an encode.

    loudnorm prints its JSON block to stderr. Returns None (never raises) when anything about
    that is unexpected — a normalisation nicety must not be able to fail a render."""
    res = subprocess.run(
        [ff, "-hide_banner", "-nostdin", "-i", str(src),
         "-af", f"{base_filter}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    blob = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr or "", re.S)
    if not blob:
        return None
    try:
        data = json.loads(blob.group(0))
    except json.JSONDecodeError:
        return None
    out = {}
    for k in _MEASURED_KEYS:
        v = data.get(k)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        # A digital-silence input measures -inf and would poison the second pass.
        if f != f or abs(f) == float("inf"):
            return None
        out[k] = v
    return out


_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")

# Hard ceiling, enforced no matter what config asks for. MEASURED by binary search against the
# shipped ffmpeg (8.1): 31 lifts parse, 32 fail with "Missing ')' or too many args" — the limit
# is inside ffmpeg's own expression evaluator, and it bites long BEFORE the command-line length
# limit does (31 lifts is only ~3.4k chars). Exceeding it is not a degraded mix, it is a failed
# render, so the knob is clamped rather than trusted; 24 leaves headroom above the measured edge.
_DUCK_GAPS_HARD_MAX = 24


def _silences(ff: str, src: Path, floor_db: float) -> list[tuple[float, float]]:
    """(start, end) of every detected silence in `src`.

    Reads the RENDERED AUDIO, deliberately — not word_timestamps.json. Chatterbox spreads a
    sentence's words evenly across that sentence's measured duration, so every inter-word gap
    in that file is exactly 0.0 and it can locate no silence at all (measured 2026-08-12).
    """
    res = subprocess.run(
        [ff, "-hide_banner", "-nostdin", "-i", str(src),
         "-af", f"silencedetect=noise={floor_db}dB:d=0.2", "-f", "null", "-"],
        capture_output=True, text=True)
    out, start = [], None
    for kind, val in _SILENCE_RE.findall(res.stderr or ""):
        if kind == "start":
            start = float(val)
        elif start is not None:
            out.append((start, float(val)))
            start = None
    return out


def _duck_expr(gaps: list[tuple[float, float]], duck_lin: float, ramp: float,
               max_gaps: int = 0) -> str:
    """Gain curve for the bed: ducked by default, lifting only inside `gaps`.

    Written inverted (duck is the resting state, gaps are the exception) because the narration
    is nearly continuous — enumerating the speech instead would produce a far longer expression
    for the same curve. Each lift is a trapezoid so the bed ramps rather than steps.

    Consumed by ffmpeg's `volume` filter, which REQUIRES eval=frame here: its default eval=once
    collapses the expression to a constant at init, which sounds like a bed that is merely quiet
    and never moves — a silent failure.

    `max_gaps` caps how many lifts the curve carries, keeping the LONGEST pauses — the ones a
    listener actually registers. This is not tuning, it is a hard limit: a 19-minute long-form
    has ~380 qualifying gaps, which builds a 44k-character expression, and Windows' CreateProcess
    refuses any command line over 32767 (measured: WinError 206, "filename or extension is too
    long"). A 61s Short has 2 gaps and never comes near it, so the ceiling only ever bites
    long-form — which is exactly where it would otherwise be a hard render failure.
    """
    if ramp <= 0:
        # A zero ramp is a plausible reading of "no fade", but it divides by zero below and
        # yields NaN inside the filter — a bed that silently goes mute. Hold the duck instead.
        return f"{duck_lin:.4f}"
    usable = [(gs, ge) for gs, ge in gaps if ge - gs > 2 * ramp + 0.05]
    cap = min(max_gaps, _DUCK_GAPS_HARD_MAX) if max_gaps else _DUCK_GAPS_HARD_MAX
    if len(usable) > cap:
        usable = sorted(sorted(usable, key=lambda g: g[1] - g[0], reverse=True)[:cap])
    traps = [
        f"between(t,{gs:.3f},{gs + ramp:.3f})*(t-{gs:.3f})/{ramp:.3f}"
        f"+between(t,{gs + ramp:.3f},{ge - ramp:.3f})"
        f"+between(t,{ge - ramp:.3f},{ge:.3f})*({ge:.3f}-t)/{ramp:.3f}"
        for gs, ge in usable
    ]
    if not traps:
        return f"{duck_lin:.4f}"
    return f"{duck_lin:.4f}+{1 - duck_lin:.4f}*min(1,{'+'.join(traps)})"


def _build_bed_mix(ff: str, tts_wav: Path, bed_path: Path, out_path: Path,
                   base_filter: str, log: Callable[[str], None]) -> bool:
    """Lay `bed_path` under `tts_wav` at a self-calibrated level, ducked around speech.

    Returns False (and writes nothing) on ANY problem — a missing/short/unreadable bed must
    degrade to narration-only, never fail a render. The bed level is derived from the TTS's own
    measured loudness rather than a fixed dBFS, so it tracks whatever the voice delivered.
    """
    from config import (BG_MUSIC_DUCK_DB, BG_MUSIC_DUCK_MAX_GAPS, BG_MUSIC_DUCK_MIN_GAP_S,
                        BG_MUSIC_DUCK_RAMP_S, BG_MUSIC_OFFSET_LU, BG_MUSIC_SILENCE_DB)
    try:
        if not bed_path.exists():
            log(f"[stage5] music: {bed_path} not found — narration only")
            return False
        tts_m = _measure(ff, tts_wav, base_filter, log)
        bed_m = _measure(ff, bed_path, base_filter, log)
        if not tts_m or not bed_m:
            log("[stage5] music: could not measure narration/bed loudness — narration only")
            return False

        target = float(tts_m["input_i"]) - BG_MUSIC_OFFSET_LU
        gain_db = target - float(bed_m["input_i"])

        gaps = [(s, e) for s, e in _silences(ff, tts_wav, BG_MUSIC_SILENCE_DB)
                if e - s >= BG_MUSIC_DUCK_MIN_GAP_S]
        duck_lin = 10 ** (BG_MUSIC_DUCK_DB / 20)
        expr = _duck_expr(gaps, duck_lin, BG_MUSIC_DUCK_RAMP_S, BG_MUSIC_DUCK_MAX_GAPS)
        # Counted from the expression itself (3 `between()` per lift). Deriving it from the
        # config knob instead reported gaps the curve never got: it missed both the too-short
        # filter and the hard clamp, so a long-form logged "380 gap(s) lift the bed" while the
        # curve actually carried 24.
        lifted = expr.count("between(") // 3
        capped = f" (of {len(gaps)} found)" if lifted < len(gaps) else ""
        log(f"[stage5] music: bed → {target:.1f} LUFS ({gain_db:+.1f} dB), "
            f"duck {BG_MUSIC_DUCK_DB:+.0f} dB, {lifted} gap(s) lift the bed{capped}")

        _run([ff, "-y", "-i", str(tts_wav), "-stream_loop", "-1", "-i", str(bed_path),
              "-filter_complex",
              f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
              f"volume={gain_db:.2f}dB,volume=volume='{expr}':eval=frame[bed];"
              f"[0:a][bed]amix=inputs=2:duration=first:normalize=0[a]",
              # f32 for the SCRATCH premix, not s16: amix with normalize=0 sums the two inputs
              # with no headroom, so narration peaking near 0 dBFS plus any bed clips hard at
              # 16-bit — permanently, before the loudnorm pass below ever sees it. Float has
              # the headroom to carry the overshoot until the true-peak limiter pulls it back.
              # The file is deleted moments later, so the width costs nothing.
              "-map", "[a]", "-ac", "2", "-ar", "48000", "-c:a", "pcm_f32le", str(out_path)])
        return True
    except Exception as exc:  # noqa: BLE001 — a music bed must never be able to fail a render
        log(f"[stage5] music: bed mix failed ({type(exc).__name__}: {exc}) — narration only")
        out_path.unlink(missing_ok=True)
        return False


def _require_ffmpeg() -> str:
    from config import FFMPEG_BIN
    if os.path.isabs(FFMPEG_BIN) and os.path.isfile(FFMPEG_BIN):
        return FFMPEG_BIN
    p = shutil.which(FFMPEG_BIN) or shutil.which("ffmpeg")
    if not p:
        raise FileNotFoundError(f"ffmpeg not found (FFMPEG_BIN={FFMPEG_BIN}). Check .env or PATH.")
    return p


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = (res.stderr or "")[-2000:]
        raise RuntimeError(
            f"ffmpeg failed (exit {res.returncode})\ncmd: {' '.join(cmd)}\nstderr:\n{tail}"
        )
