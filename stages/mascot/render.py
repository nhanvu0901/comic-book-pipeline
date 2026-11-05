"""Render the mascot onto the finished Short — the last step of the pipeline (UI step 9).

final.mp4 handling (Master's decision, design doc section 8, decision 4):
  * The mascot output is written straight to ``projects/<slug>/final.mp4``.
  * The first render moves the clean Stage-5 video to ``projects/<slug>/mascot/final_clean.mp4``
    (NOT under _stage5/, which "Delete _stage5 scratch" wipes). Every mascot render reads the
    clean copy, so mascots never stack.
  * ``mascot/render_state.json`` records the signature (size + mtime_ns) of the final.mp4 the
    mascot step wrote. If final.mp4 no longer matches it, Stage 5 re-rendered: that final.mp4
    is the new clean copy.
  * Disabling the mascot restores the clean copy to final.mp4.
  * final.mp4 is replaced atomically (render to a temp file in the same folder, verify with
    ffprobe, then os.replace). A failed render never leaves a broken final.mp4.

Output contract = Stage 5's: same W×H and fps as the source (1080×1920, 30 fps), H.264
yuv420p, CRF 18, audio stream copied untouched. Talk bounce reads the narration-only
``audio.wav`` when present (so music does not bounce it), else the source video's audio.
Frames are decoded with an ffmpeg rawvideo pipe, composited with PIL, piped to the encoder.

W, H, fps and duration are always read from the source with ffprobe (never hard-coded).
The clean copy is a COPY (final.mp4 stays untouched until the verified replacement), which
keeps a failed first render harmless.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageFilter

from .config import MascotConfig, _read_json, _write_json_atomic, save_config
from .kit import compose, load_kit, sprite_height_px, sprite_metrics
from .motion import SpriteState, Track, build_track, state_at

Progress = Callable[[float], None]      # 0..1
Log = Callable[[str], None]

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
# Stage 5's output contract; tests patch this to simulate an encoder failure.
VIDEO_CODEC_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
PREVIEW_CODEC_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
DURATION_TOLERANCE = 0.15
AUDIO_WAV_TOLERANCE = 0.5
ENV_SR = 16000


def mascot_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "mascot"


def _final(project_dir: Path) -> Path:
    return Path(project_dir) / "final.mp4"


def _clean(project_dir: Path) -> Path:
    return mascot_dir(project_dir) / "final_clean.mp4"


def _state_path(project_dir: Path) -> Path:
    return mascot_dir(project_dir) / "render_state.json"


MISSING_CLEAN_MSG = ("final.mp4 đã có mascot nhưng không thấy bản sạch (mascot/final_clean.mp4) "
                     "— hãy render lại bước 8")
TAG_PREFIX = "mascot_render:"
_FP_CHUNK = 1 << 20


def _sig(path: Path) -> list:
    """Content fingerprint [size, sha1(first MiB + last MiB)]: survives copies and syncs
    (mtime does not)."""
    path = Path(path)
    size = path.stat().st_size
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        h.update(fh.read(_FP_CHUNK))
        if size > _FP_CHUNK:
            fh.seek(max(_FP_CHUNK, size - _FP_CHUNK))
            h.update(fh.read(_FP_CHUNK))
    return [size, h.hexdigest()]


_TAG_CACHE: dict = {}


def _has_tag(path: Path) -> bool:
    """Container tag check, memoised per (path, size, mtime) so lookups stay cheap."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return False
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _TAG_CACHE:
        if len(_TAG_CACHE) > 64:
            _TAG_CACHE.clear()
        _TAG_CACHE[key] = _read_tag(path)
    return _TAG_CACHE[key]


def _read_tag(path: Path) -> bool:
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format_tags", "-of", "json",
                              str(path)], capture_output=True, text=True, check=True).stdout
        tags = (json.loads(out).get("format") or {}).get("tags") or {}
    except (subprocess.CalledProcessError, ValueError, OSError):
        return False
    return any(str(v).startswith(TAG_PREFIX) for v in tags.values())


def _missing_clean(project_dir: Path) -> Exception:
    if _final(project_dir).is_file() and has_mascot_output(project_dir):
        return RuntimeError(MISSING_CLEAN_MSG)
    return FileNotFoundError(f"no video in {project_dir}")


# ── final.mp4 / clean copy bookkeeping ───────────────────────────────────────────────────
def has_mascot_output(project_dir: Path) -> bool:
    """True when final.mp4 is the file the mascot step last wrote."""
    final = _final(project_dir)
    if not final.is_file():
        return False
    state = _read_json(_state_path(project_dir))
    if isinstance(state, dict) and isinstance(state.get("final"), list):
        try:
            if state["final"] == _sig(final):       # old [size, mtime_ns] states never match
                return True
        except OSError:
            return False
    return _has_tag(final)                          # container tag: belt and braces


def source_video(project_dir: Path) -> Path | None:
    """The CLEAN video to draw on: mascot/final_clean.mp4 when final.mp4 is our own mascot
    output, otherwise final.mp4 itself (fresh from Stage 5). None when neither exists.
    Pure lookup — never copies or moves anything."""
    final, clean = _final(project_dir), _clean(project_dir)
    if has_mascot_output(project_dir):
        return clean if clean.is_file() else None
    if final.is_file():
        return final
    return clean if clean.is_file() else None


def _copy_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dst.name + ".", suffix=".tmp", dir=str(dst.parent))
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_clean_copy(project_dir: Path) -> Path:
    """Make mascot/final_clean.mp4 current (copy final.mp4 when it is a fresh Stage-5 render)
    and return it. Raises FileNotFoundError when there is no video at all."""
    final, clean = _final(project_dir), _clean(project_dir)
    if has_mascot_output(project_dir):
        if not clean.is_file():
            raise RuntimeError(MISSING_CLEAN_MSG)
        return clean
    if final.is_file():
        _copy_atomic(final, clean)          # fresh Stage-5 render → the new clean copy
        return clean
    if clean.is_file():
        return clean
    raise FileNotFoundError(f"no final.mp4 in {project_dir}")


def restore_clean(project_dir: Path) -> bool:
    """Put the clean copy back as final.mp4 (mascot off). True when something was restored."""
    final, clean = _final(project_dir), _clean(project_dir)
    if not clean.is_file():
        return False
    if final.is_file() and not has_mascot_output(project_dir):
        return False                        # final.mp4 is already a clean Stage-5 video
    _copy_atomic(clean, final)
    try:
        _state_path(project_dir).unlink()
    except OSError:
        pass
    return True


# ── ffmpeg helpers ───────────────────────────────────────────────────────────────────────
class _Procs:
    """Every ffmpeg subprocess of one job: killed together on any failure (no zombies)."""

    def __init__(self) -> None:
        self.items: list[tuple[subprocess.Popen, object]] = []

    def popen(self, cmd: list[str], **kw) -> tuple[subprocess.Popen, object]:
        err = tempfile.TemporaryFile()
        p = subprocess.Popen(cmd, stderr=err, **kw)
        self.items.append((p, err))
        return p, err

    def kill_all(self) -> None:
        for p, err in self.items:
            try:
                if p.poll() is None:
                    p.kill()
                for f in (p.stdin, p.stdout):
                    try:
                        if f:
                            f.close()
                    except Exception:
                        pass
                p.wait()
            except Exception:
                pass
            try:
                err.close()
            except Exception:
                pass


def _err_text(err) -> str:
    try:
        err.seek(0)
        return err.read().decode("utf-8", "replace").strip()[-800:]
    except Exception:
        return ""


def _probe(path: Path) -> dict:
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json",
                              str(path)], capture_output=True, text=True, check=True).stdout
        data = json.loads(out)
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        raise RuntimeError(f"ffprobe failed on {path}: {exc}") from None
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = 0.0
    for src in ((data.get("format") or {}).get("duration"), (v or {}).get("duration")):
        try:
            dur = float(src)
            break
        except (TypeError, ValueError):
            continue
    fps = Fraction(0)
    if v:
        for k in ("avg_frame_rate", "r_frame_rate"):
            try:
                fr = Fraction(v.get(k) or "0")
            except (ValueError, ZeroDivisionError):
                fr = Fraction(0)
            if fr > 0:
                fps = fr
                break
    return {"has_video": v is not None, "has_audio": a is not None, "audio_codec": (a or {}).get("codec_name"),
            "w": int((v or {}).get("width") or 0), "h": int((v or {}).get("height") or 0),
            "fps": fps, "duration": dur, "colorspace": (v or {}).get("color_space")}


def _fps_arg(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


def _envelope(audio_src: Path, n_frames: int, fps: float) -> np.ndarray:
    """Voice level 0..1 per video frame (RMS, normalised by the 98th percentile, 3-tap smooth)."""
    zeros = np.zeros(n_frames, np.float32)
    try:
        raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(audio_src), "-vn", "-ac", "1", "-ar",
                              str(ENV_SR), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError):
        return zeros
    x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float64) / 32768.0
    if x.size < ENV_SR // 10:
        return zeros
    cs = np.concatenate([[0.0], np.cumsum(x * x)])
    edges = np.clip((np.arange(n_frames + 1) * ENV_SR / fps).astype(np.int64), 0, x.size)
    cnt = np.maximum(1, edges[1:] - edges[:-1])
    rms = np.sqrt(np.maximum(0.0, cs[edges[1:]] - cs[edges[:-1]]) / cnt)
    ref = float(np.percentile(rms, 98))
    if ref < 1e-4:
        return zeros
    env = np.clip(rms / ref, 0.0, 1.0)
    env = np.convolve(env, np.ones(3) / 3, mode="same")
    return env.astype(np.float32)


def _envelope_for(project_dir: Path, src: Path, info: dict) -> np.ndarray:
    n = int(math.ceil(info["duration"] * float(info["fps"]))) + 2
    wav = Path(project_dir) / "audio.wav"
    if wav.is_file():
        try:
            wi = _probe(wav)
            if wi["has_audio"] and abs(wi["duration"] - info["duration"]) <= AUDIO_WAV_TOLERANCE:
                return _envelope(wav, n, float(info["fps"]))
        except RuntimeError:
            pass
    if info["has_audio"]:
        return _envelope(src, n, float(info["fps"]))
    return np.zeros(n, np.float32)


# ── compositing ──────────────────────────────────────────────────────────────────────────
class _Painter:
    """Draws one SpriteState onto a frame, touching only the sprite's bounding box."""

    def __init__(self, kit, cfg: MascotConfig, frame_w: int, frame_h: int) -> None:
        self.kit, self.cfg, self.W, self.H = kit, cfg, frame_w, frame_h
        self.h = sprite_height_px(cfg.size_pct, frame_h)
        self._cache: dict[tuple[str, str], tuple] = {}

    def sprite(self, body: str, face: str) -> tuple:
        hit = self._cache.get((body, face))
        if hit is None:
            img = compose(self.kit, body, face, tuple(self.cfg.accessories), outline=self.cfg.outline,
                          shadow=self.cfg.shadow, height_px=self.h)
            m = sprite_metrics(self.kit, body, self.h)
            hit = (img.convert("RGBa"), img.width, img.height, m["feet_x"], m["feet_y"])
            self._cache[(body, face)] = hit
        return hit

    def paint(self, arr: np.ndarray, st: SpriteState) -> None:
        spr, sw, sh, fx, fy = self.sprite(st.body, st.face)
        th = math.radians(st.rot)
        c, s = math.cos(th), math.sin(th)
        xs, ys = [], []
        for x, y in ((0, 0), (sw, 0), (sw, sh), (0, sh)):
            dx, dy = (x - fx) * st.sx, (y - fy) * st.sy
            xs.append(dx * c + dy * s)
            ys.append(-dx * s + dy * c)
        m = int(math.ceil(3 * st.blur)) + 2 if st.blur > 0.6 else 2
        X0 = max(0, int(math.floor(st.cx + min(xs))) - m)
        Y0 = max(0, int(math.floor(st.feet_y + min(ys))) - m)
        X1 = min(self.W, int(math.ceil(st.cx + max(xs))) + m)
        Y1 = min(self.H, int(math.ceil(st.feet_y + max(ys))) + m)
        cw, ch = X1 - X0, Y1 - Y0
        if cw <= 0 or ch <= 0:
            return
        ox, oy = st.cx - X0, st.feet_y - Y0
        a, b = c / st.sx, -s / st.sx
        d, e = s / st.sy, c / st.sy
        data = (a, b, fx - (a * ox + b * oy), d, e, fy - (d * ox + e * oy))
        img = spr.transform((cw, ch), Image.AFFINE, data, resample=Image.BILINEAR).convert("RGBA")
        if st.blur > 0.6:
            img = img.filter(ImageFilter.GaussianBlur(st.blur))
        if st.alpha < 0.999:
            img.putalpha(img.getchannel("A").point(lambda v: int(v * st.alpha)))
        region = Image.fromarray(arr[Y0:Y1, X0:X1])
        region.paste(img, (0, 0), img)
        arr[Y0:Y1, X0:X1] = np.asarray(region)


def _readinto(stream, mv: memoryview) -> int:
    got = 0
    while got < len(mv):
        n = stream.readinto(mv[got:])
        if not n:
            break
        got += n
    return got


def _overlay_pass(src: Path, dst: Path, *, w: int, h: int, fps: Fraction, t_start: float,
                  t_len: float | None, cfg: MascotConfig, kit, track: Track, env: np.ndarray,
                  info: dict, video_args: list[str], audio_args: list[str], scale_to: bool,
                  est_frames: int, progress: Progress | None, log: Log | None, p_hi: float,
                  metadata: str | None = None) -> int:
    """decode → composite → encode. Kills every subprocess on any exception."""
    window = [] if t_start <= 0 and t_len is None else ["-ss", f"{t_start:.3f}"] + (
        ["-t", f"{t_len:.3f}"] if t_len is not None else [])
    dec_cmd = [FFMPEG, "-v", "error"] + window + ["-i", str(src), "-an"]
    if scale_to:
        dec_cmd += ["-vf", f"scale={w}:{h}"]
    dec_cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    enc_cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
               "-r", _fps_arg(fps), "-i", "-"] + window + ["-i", str(src), "-map", "0:v:0", "-map", "1:a:0?"]
    if info.get("colorspace") == "bt709":
        enc_cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv", "-colorspace", "bt709",
                    "-color_primaries", "bt709", "-color_trc", "bt709"]
    if metadata:
        enc_cmd += ["-metadata", f"comment={metadata}"]
    enc_cmd += video_args + audio_args + ["-movflags", "+faststart", str(dst)]

    painter = _Painter(kit, cfg, w, h)
    fsize = w * h * 3
    buf = bytearray(fsize)
    mv = memoryview(buf)
    arr = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    fps_f = float(fps)
    off = int(round(t_start * fps_f))
    procs = _Procs()
    frames = 0
    try:
        dec, dec_err = procs.popen(dec_cmd, stdout=subprocess.PIPE, bufsize=fsize * 2)
        enc, enc_err = procs.popen(enc_cmd, stdin=subprocess.PIPE)
        while True:
            if _readinto(dec.stdout, mv) < fsize:
                break
            t = t_start + frames / fps_f
            e = float(env[min(off + frames, len(env) - 1)]) if len(env) else 0.0
            st = state_at(track, t, e)
            if st is not None:
                painter.paint(arr, st)
            try:
                enc.stdin.write(mv)
            except (BrokenPipeError, OSError):
                raise RuntimeError(f"encoder died: {_err_text(enc_err)}") from None
            frames += 1
            if progress and frames % 8 == 0:
                progress(min(p_hi, frames / max(1, est_frames) * p_hi))
        try:
            enc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        enc.wait()
        dec.wait()
        if dec.returncode not in (0, None) and frames == 0:
            raise RuntimeError(f"decoder failed: {_err_text(dec_err)}")
        if enc.returncode != 0:
            raise RuntimeError(f"encoder failed ({enc.returncode}): {_err_text(enc_err)}")
        if frames == 0:
            raise RuntimeError("no frames decoded from " + str(src))
    except BaseException:
        procs.kill_all()
        try:
            Path(dst).unlink()
        except OSError:
            pass
        raise
    finally:
        for _p, err in procs.items:
            try:
                err.close()
            except Exception:
                pass
    if log:
        log(f"mascot: {frames} frames -> {Path(dst).name}")
    return frames


def _verify(tmp: Path, src_info: dict) -> None:
    info = _probe(tmp)
    if not info["has_video"]:
        raise RuntimeError("verification failed: no video stream in the new file")
    if abs(info["duration"] - src_info["duration"]) > DURATION_TOLERANCE:
        raise RuntimeError(f"verification failed: duration {info['duration']:.2f}s vs source "
                           f"{src_info['duration']:.2f}s")
    if src_info["has_audio"] and not info["has_audio"]:
        raise RuntimeError("verification failed: audio stream missing")


# ── public API ───────────────────────────────────────────────────────────────────────────
def frame_at(project_dir: Path, t: float, *, width: int = 540) -> Path:
    """JPEG of the CLEAN video at time t, scaled to `width` (aspect kept), cached under
    mascot/cache/frames/ keyed by (source signature, t rounded to 1/30 s, width)."""
    src = source_video(project_dir)
    if src is None:
        raise _missing_clean(project_dir)
    t = max(0.0, float(t))
    key = json.dumps([_sig(src), round(t * 30), int(width)])
    out = mascot_dir(project_dir) / "cache" / "frames" / f"{hashlib.sha1(key.encode()).hexdigest()[:20]}.jpg"
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.jpg")

    def grab(at: float) -> bool:
        tmp.unlink(missing_ok=True)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1",
                        "-vf", f"scale={int(width)}:-2", "-q:v", "3", str(tmp)],
                       capture_output=True)
        return tmp.is_file() and tmp.stat().st_size > 0

    try:
        if not grab(t):
            dur = _probe(src)["duration"]          # t past the end → the last frame
            if not grab(max(0.0, dur - 0.1)):
                raise RuntimeError(f"could not extract a frame at {t:.2f}s")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def render_preview(project_dir: Path, cfg: MascotConfig, t_center: float, *, span: float = 3.0,
                   width: int = 540, progress: Progress | None = None, log: Log | None = None) -> Path:
    """A short low-res clip (span seconds around t_center, clamped to the video) WITH audio,
    written to mascot/preview.mp4 (overwritten each time). Same motion maths as the full
    render, scaled to the preview size, so what Master sees is what renders."""
    project_dir = Path(project_dir)
    src = source_video(project_dir)
    if src is None:
        raise _missing_clean(project_dir)
    info = _probe(src)
    if not info["has_video"] or info["w"] <= 0:
        raise RuntimeError(f"{src} has no video stream")
    pw = max(2, int(width) // 2 * 2)
    ph = max(2, round(pw * info["h"] / info["w"] / 2) * 2)
    dur = info["duration"]
    length = min(float(span), dur)
    t0 = max(0.0, min(float(t_center) - length / 2, dur - length))
    kit = load_kit(cfg.kit)
    track = build_track(cfg, kit, dur, pw, ph)
    env = _envelope_for(project_dir, src, info)
    out = mascot_dir(project_dir) / "preview.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name("preview.tmp.mp4")
    if progress:
        progress(0.0)
    try:
        _overlay_pass(src, tmp, w=pw, h=ph, fps=info["fps"], t_start=t0, t_len=length, cfg=cfg, kit=kit,
                      track=track, env=env, info=info, video_args=PREVIEW_CODEC_ARGS,
                      audio_args=["-c:a", "aac", "-b:a", "128k"], scale_to=True,
                      est_frames=max(1, int(length * float(info["fps"]))), progress=progress, log=log,
                      p_hi=0.97)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    if progress:
        progress(1.0)
    return out


def render_final(project_dir: Path, cfg: MascotConfig, *, progress: Progress | None = None,
                 log: Log | None = None) -> Path:
    """Full render to final.mp4 (see module docstring). cfg.enabled False → restore_clean()
    and return final.mp4. Also saves cfg as mascot.json so the file on disk matches the video."""
    project_dir = Path(project_dir)
    final = _final(project_dir)
    if progress:
        progress(0.0)
    if not cfg.enabled:
        restore_clean(project_dir)
        save_config(project_dir, cfg)
        if progress:
            progress(1.0)
        return final
    clean = ensure_clean_copy(project_dir)
    info = _probe(clean)
    if not info["has_video"] or info["w"] <= 0 or info["fps"] <= 0:
        raise RuntimeError(f"{clean} has no usable video stream")
    kit = load_kit(cfg.kit)
    track = build_track(cfg, kit, info["duration"], info["w"], info["h"])
    if not track.segments:                   # no cue ever shows the mascot: the clean video IS the result
        restore_clean(project_dir)
        save_config(project_dir, cfg)
        if log:
            log("mascot: no visible cue, final.mp4 left clean")
        if progress:
            progress(1.0)
        return final
    env = _envelope_for(project_dir, clean, info)
    tmp = project_dir / "final.mascot.tmp.mp4"
    tmp.unlink(missing_ok=True)
    try:
        _overlay_pass(clean, tmp, w=info["w"], h=info["h"], fps=info["fps"], t_start=0.0, t_len=None,
                      cfg=cfg, kit=kit, track=track, env=env, info=info, video_args=VIDEO_CODEC_ARGS,
                      audio_args=["-c:a", "copy"], scale_to=False,
                      est_frames=max(1, int(info["duration"] * float(info["fps"]))),
                      progress=progress, log=log, p_hi=0.97, metadata=TAG_PREFIX + cfg.kit)
        _verify(tmp, info)
        if not _has_tag(tmp):
            raise RuntimeError("verification failed: mascot tag missing in the new file")
        # os.replace keeps the bytes, so the fingerprint recorded now is final.mp4's afterwards
        _write_json_atomic(_state_path(project_dir), {"final": _sig(tmp), "clean": _sig(clean)})
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    save_config(project_dir, cfg)
    if progress:
        progress(1.0)
    return final
