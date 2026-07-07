"""Long-form seamless stitch: N finished 9:16 segment mp4s -> ONE continuous mp4.

Each segment is a normal Stage-5 `final.mp4` (already has burned captions + mixed
audio, same H.264/9:16 spec). Stitching only needs to join them:
  - dissolve<=0 -> ffmpeg concat demuxer (stream copy, hard-cut boundaries).
  - dissolve>0  -> a segment-level xfade (video) + acrossfade (audio) chain,
    mirroring stages/stage_5/pipeline.py's `_xfade_chain` offset trick but on
    FULL videos (video and audio both need to cross-fade, since each segment
    already has its own audio baked in — unlike stage_5's silent shot clips).

Reuses `_require_ffmpeg`/`_run` from stage_5.pipeline (same ffmpeg-availability
check + subprocess-with-stderr-on-failure pattern) rather than reimplementing
subprocess plumbing.
"""
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from stages.stage_5.pipeline import _require_ffmpeg, _run

FPS = 30  # matches stage_5 output fps; segments are already this fps


def stitch_segments(
    segment_mp4s: list[Path],
    out_path: Path,
    *,
    dissolve: float = 0.4,
    log: Callable[[str], None] | None = None,
) -> Path:
    """Seamless-concat finished segment mp4s into one long-form mp4.

    0 segments -> fail loud. 1 segment -> plain file copy (nothing to stitch).
    dissolve<=0 -> concat demuxer hard-cut. dissolve>0 -> xfade+acrossfade chain
    whose net duration == sum(segment durations), same as stage_5._xfade_chain.
    """
    log = log or (lambda m: print(m))
    segments = [Path(p) for p in segment_mp4s]
    if not segments:
        raise ValueError("stitch_segments: 0 segments given — nothing to stitch")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if len(segments) == 1:
        log(f"[longform] single segment — copying {segments[0].name} -> {out_path.name}")
        shutil.copy2(segments[0], out_path)
        return out_path

    if dissolve <= 0:
        log(f"[longform] hard-cut concat of {len(segments)} segments -> {out_path.name}")
        return _concat_demuxer(segments, out_path)

    log(f"[longform] xfade+acrossfade stitch of {len(segments)} segments "
        f"(dissolve={dissolve}s) -> {out_path.name}")
    durs = [_probe_duration(p) for p in segments]
    return _xfade_acrossfade_chain(segments, durs, out_path, dissolve)


def _concat_demuxer(paths: list[Path], out_path: Path) -> Path:
    """Hard-cut join via ffmpeg's concat demuxer (stream copy — no re-encode)."""
    ff = _require_ffmpeg()
    list_file = out_path.parent / "_longform_concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{Path(p).resolve()}'" for p in paths) + "\n"
    )
    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)]
    _run(cmd)
    return out_path


def _xfade_offsets(durs: list[float]) -> list[float]:
    """Cumulative offsets [d0, d0+d1, ...] (len = N-1) — same trick as
    stage_5._xfade_chain: each non-final clip's video/audio is tail-padded by
    `x` seconds so the chain's net duration == sum(durs)."""
    offs, acc = [], 0.0
    for d in durs[:-1]:
        acc += d
        offs.append(round(acc, 3))
    return offs


def _xfade_acrossfade_chain(
    clips: list[Path], durs: list[float], out_path: Path, x: float
) -> Path:
    """Chain `clips` (full video+audio segments) with a video xfade + audio
    acrossfade of `x` seconds at every boundary.

    Video side mirrors stage_5._xfade_chain exactly (tpad tail-clone + explicit
    cumulative `offset`). Audio side uses `apad` to tail-pad non-last clips with
    `x`s of silence, then chains `acrossfade` (which needs no offset — it always
    crossfades the tail of the accumulated stream with the head of the next
    input) — the padding keeps the audio chain's net duration in lockstep with
    the video chain's, so the two `-map`ped outputs stay in sync.
    """
    ff = _require_ffmpeg()
    offs = _xfade_offsets(durs)
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]

    last = len(clips) - 1
    vchains: list[str] = []
    achains: list[str] = []
    for i in range(len(clips)):
        if i == last:
            vchains.append(f"[{i}:v]settb=AVTB,fps={FPS}[v{i}]")
            achains.append(f"[{i}:a]anull[a{i}]")
        else:
            vchains.append(
                f"[{i}:v]settb=AVTB,fps={FPS},tpad=stop_mode=clone:stop_duration={x}[v{i}]")
            achains.append(f"[{i}:a]apad=pad_dur={x}[a{i}]")

    prev_v, prev_a = "v0", "a0"
    for k in range(1, len(clips)):
        outv, outa = f"x{k}", f"y{k}"
        vchains.append(
            f"[{prev_v}][v{k}]xfade=transition=dissolve:duration={x}:offset={offs[k-1]}[{outv}]")
        achains.append(f"[{prev_a}][a{k}]acrossfade=d={x}[{outa}]")
        prev_v, prev_a = outv, outa

    filter_complex = ";".join(vchains + achains)
    cmd = [ff, "-y", *inputs,
           "-filter_complex", filter_complex,
           "-map", f"[{prev_v}]", "-map", f"[{prev_a}]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart",
           str(out_path)]
    _run(cmd)
    return out_path


def _probe_duration(path: Path) -> float:
    """ffprobe duration in seconds — copied from stage_5.pipeline._probe_duration
    (kept local so tests can stub it without touching stage_5)."""
    ff = shutil.which("ffprobe")
    if not ff:
        return 0.0
    res = subprocess.run(
        [ff, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float((res.stdout or "0").strip())
    except ValueError:
        return 0.0
