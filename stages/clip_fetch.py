"""Find, fetch and pick video clips for Stage 5 clip shots (see stages/stage_5/clips.py).

    python -m stages.clip_fetch search "green lantern animated series mogo"
    python -m stages.clip_fetch fetch "https://www.youtube.com/watch?v=gKiT1ekWIAA" --project my-proj
    python -m stages.clip_fetch sheet projects/my-proj/review/clips/gKiT1ekWIAA.mp4 --start 60 --end 80 --every 0.5
    python -m stages.clip_fetch beats --project my-proj
    python -m stages.clip_fetch add --project my-proj --file review/clips/gKiT1ekWIAA.mp4 \\
        --start 64.5 --end 67.0 --beat 3:1 --crop-cx 0.55
    python -m stages.clip_fetch sync --project my-proj      # fetch entries that only have a URL

search → You.com Web Search (stages.research_scout.youcom), restricted to youtube.com.
fetch  → yt-dlp (node JS runtime, merged to mp4) into <project>/review/clips/src/, then ALWAYS
         transcoded to <project>/review/clips/<id>.mp4 (H.264 yuv420p, constant 30 fps, short
         side <= 1080, AAC kept for previewing) — sources arrive as AV1/VP9 at any frame rate.
         Writes <id>_sheet.jpg (a timestamped contact sheet) and <id>.json (title/channel/url).
sheet  → contact sheet of any span, to read in/out points off; start/end in the manifest are
         seconds in the cached <id>.mp4 (the same timeline as the source video).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLIP_DIR_REL = Path("review") / "clips"

_VIDEO_URL = re.compile(r"(youtube\.com/(watch\?|shorts/|embed/|live/)|youtu\.be/)", re.I)
_YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


# ─── search ─────────────────────────────────────────────────────────────────────

def _api_key() -> str:
    key = os.environ.get("YDC_API_KEY", "").strip()
    if not key:
        env = REPO / ".env"
        if env.exists():
            m = re.search(r"^YDC_API_KEY=(.+)$", env.read_text(encoding="utf-8"), re.M)
            if m:
                key = m.group(1).strip().strip('"').strip("'")
    return key


def parse_search_results(payload) -> list[dict]:
    """You.com Search payload → [{"title","url","snippet","is_video"}], deduped by URL, in
    response order (web results, then news)."""
    results = payload.get("results") if isinstance(payload, dict) else None
    rows: list = []
    if isinstance(results, dict):
        for key in ("web", "news"):
            if isinstance(results.get(key), list):
                rows.extend(results[key])
    elif isinstance(results, list):
        rows = results
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        snips = r.get("snippets") or []
        snippet = str(r.get("description") or (snips[0] if snips else "") or "")
        out.append({"title": str(r.get("title") or ""), "url": url,
                    "snippet": " ".join(snippet.split())[:200],
                    "is_video": bool(_VIDEO_URL.search(url))})
    return out


def youtube_search(query: str, *, client=None) -> list[dict]:
    """Web Search restricted to youtube.com. Raises RuntimeError on an API error."""
    from .research_scout.youcom import YouComClient
    client = client or YouComClient(api_key=_api_key(), timeout=120)
    call = client.search(query, {"include_domains": ["youtube.com"]})
    if not call.ok:
        raise RuntimeError(f"You.com search failed: {call.error}")
    return parse_search_results(call.payload)


# ─── fetch + normalise ──────────────────────────────────────────────────────────

def _ffmpeg() -> str:
    from .stage_5.shots import _require_ffmpeg
    return _require_ffmpeg()


def _ytdlp_cmd() -> list[str]:
    exe = os.environ.get("YTDLP_BIN", "").strip() or shutil.which("yt-dlp")
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]


def ytdlp_args(url: str, out_dir: Path, *, max_height: int = 1080) -> list[str]:
    """yt-dlp command for one video: node JS runtime (some videos need it for the signature
    challenge), best video <= max_height + best audio, merged into an mp4 container."""
    fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
    return [*_ytdlp_cmd(), "--no-playlist", "--js-runtimes", "node", "-f", fmt,
            "--merge-output-format", "mp4", "--write-info-json", "--no-progress",
            "-o", str(Path(out_dir) / "%(id)s.%(ext)s"),
            "--print", "after_move:filepath", url]


def download(url: str, out_dir: Path, *, max_height: int = 1080, log=print) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"[clip] downloading {url}")
    res = subprocess.run(ytdlp_args(url, out_dir, max_height=max_height),
                         capture_output=True, text=True)
    lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
    path = Path(lines[-1]) if lines else None
    if res.returncode != 0 or path is None or not path.is_file():
        tail = " ".join((res.stderr or res.stdout or "").split())[-500:]
        raise RuntimeError(f"yt-dlp failed for {url} (exit {res.returncode}): {tail}")
    return path


def normalize(src: Path, out: Path, *, max_short_side: int = 1080, log=print) -> Path:
    """Transcode ANY input to H.264 yuv420p, constant 30 fps, short side <= max_short_side,
    AAC audio if there is any. Written to a temp name first so a crash never leaves a
    half-file that later looks cached."""
    from .stage_5.clips import probe_video
    info = probe_video(src)
    w, h = info["width"], info["height"]
    k = min(1.0, max_short_side / min(w, h))
    tw, th = max(2, int(round(w * k / 2)) * 2), max(2, int(round(h * k / 2)) * 2)
    tmp = out.with_name(out.stem + ".part.mp4")
    cmd = [_ffmpeg(), "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", f"fps=30,scale={tw}:{th}:flags=lanczos,setsar=1,format=yuv420p",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)]
    log(f"[clip] transcoding {src.name} ({w}x{h}) → {out.name} ({tw}x{th}, h264, 30 fps)")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"transcode failed: {(res.stderr or '')[-600:]}")
    tmp.replace(out)
    return out


def fetch_clip(url: str, clip_dir: Path, *, max_height: int = 1080, sheet: bool = True,
               every: float = 2.0, log=print) -> dict:
    """Download + transcode one video into clip_dir (cached by video id). Returns
    {"id","file","raw","sheet","meta"}; "file" is the normalised mp4 to put in the manifest."""
    clip_dir = Path(clip_dir)
    m = _YT_ID.search(url)
    vid = m.group(1) if m else None
    out = clip_dir / f"{vid}.mp4" if vid else None
    raw = None
    if out is None or not out.is_file():
        raw = download(url, clip_dir / "src", max_height=max_height, log=log)
        vid = vid or raw.stem
        out = clip_dir / f"{vid}.mp4"
        normalize(raw, out, log=log)
    else:
        log(f"[clip] cached: {out}")
    meta_path = clip_dir / f"{vid}.json"
    meta = _write_meta(meta_path, url, clip_dir / "src" / f"{vid}.info.json", out)
    sheet_path = None
    if sheet:
        sheet_path = contact_sheet(out, clip_dir / f"{vid}_sheet.jpg", every=every, log=log)
    return {"id": vid, "file": str(out), "raw": str(raw) if raw else "",
            "sheet": str(sheet_path) if sheet_path else "", "meta": meta}


def _write_meta(path: Path, url: str, info_json: Path, out: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    info = {}
    if info_json.exists():
        try:
            info = json.loads(info_json.read_text(encoding="utf-8"))
        except ValueError:
            info = {}
    from .stage_5.clips import probe_video
    p = probe_video(out)
    meta = {"source_url": info.get("webpage_url") or url, "title": info.get("title", ""),
            "channel": info.get("channel") or info.get("uploader", ""),
            "duration": round(p["duration"], 3), "width": p["width"], "height": p["height"]}
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


# ─── contact sheet ──────────────────────────────────────────────────────────────

def _fmt_t(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}"


def sheet_times(duration: float, *, every: float, start: float = 0.0,
                end: float | None = None, max_frames: int = 120) -> tuple[list[float], float]:
    """Sample times for a contact sheet and the step actually used (widened when the span
    would need more than max_frames tiles)."""
    end = duration if end is None else min(end, duration)
    span = max(0.0, end - start)
    step = max(every, 0.04)
    if span / step + 1 > max_frames:
        step = span / max_frames
    n = int(math.floor(span / step + 1e-9)) + 1
    return [round(start + k * step, 3) for k in range(n) if start + k * step < duration], step


def contact_sheet(video: Path, out: Path, *, every: float = 2.0, start: float = 0.0,
                  end: float | None = None, cols: int = 6, thumb_w: int = 320,
                  log=print) -> Path:
    """Grid of frames every `every` seconds over [start, end], each labelled with its time in
    the file — read in/out points straight off it. Labels are drawn with PIL (no drawtext)."""
    from PIL import Image, ImageDraw
    from .stage_5.clips import probe_video
    from .stage_5.panel_sheet import _font
    info = probe_video(video)
    times, step = sheet_times(info["duration"], every=every, start=start, end=end)
    if not times:
        raise ValueError(f"nothing to sample: start {start}s is past the end of {video}")
    span = times[-1] - times[0] + step
    with tempfile.TemporaryDirectory() as td:
        cmd = [_ffmpeg(), "-y", "-v", "error", "-ss", f"{times[0]:.3f}", "-t", f"{span:.3f}",
               "-i", str(video), "-vf", f"fps=1/{step:.4f},scale={thumb_w}:-2",
               "-frames:v", str(len(times)), str(Path(td) / "f_%04d.png")]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"contact sheet frames failed: {(res.stderr or '')[-400:]}")
        frames = sorted(Path(td).glob("f_*.png"))
        if not frames:
            raise RuntimeError("contact sheet: ffmpeg produced no frames")
        thumbs = [Image.open(f).convert("RGB") for f in frames]
    th = thumbs[0].height
    label_h, pad, head_h = 26, 6, 34
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (pad + cols * (thumb_w + pad), head_h + rows * (th + label_h + pad)),
                      (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 8), f"{video.name}  every {step:.2f}s  ({info['width']}x{info['height']}, "
              f"{info['duration']:.1f}s)", fill=(255, 255, 255), font=_font(18))
    font = _font(18)
    for k, (img, t) in enumerate(zip(thumbs, times)):
        r, c = divmod(k, cols)
        x, y = pad + c * (thumb_w + pad), head_h + r * (th + label_h + pad)
        sheet.paste(img, (x, y))
        draw.text((x + 2, y + th + 3), f"{t:.2f}s  ({_fmt_t(t)})", fill=(255, 220, 0), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    log(f"[clip] contact sheet: {out} ({len(thumbs)} frames, every {step:.2f}s)")
    return out


# ─── manifest editing ───────────────────────────────────────────────────────────

def _root(project: str) -> Path:
    from .review_gate import _project_root
    root = _project_root(project)
    if not root.is_dir():
        raise SystemExit(f"project not found: {project} ({root})")
    return root


def _rel(root: Path, f: str | Path) -> str:
    """A clip path as the manifest stores it: project-relative when it lives inside the project
    (the project folder can move), absolute otherwise. A relative input is read as
    project-relative unless only the current directory has it."""
    p = Path(f)
    if not p.is_absolute():
        p = Path.cwd() / p if (Path.cwd() / p).exists() and not (root / p).exists() else root / p
    p, r = p.resolve(), root.resolve()
    return (p.relative_to(r) if p.is_relative_to(r) else p).as_posix()


def add_entry(root: Path, entry: dict) -> dict:
    """Validate one manifest entry (same parser Stage 5 uses) and append it to clips.json."""
    from utils.atomic_json import write_json_atomic
    from .stage_5.clips import manifest_path, parse_manifest
    entry = {k: v for k, v in entry.items() if v not in (None, "", {})}
    if entry.get("file"):
        entry["file"] = _rel(root, entry["file"])
        meta = (root / entry["file"]).with_suffix(".json")
        if not entry.get("source_url") and meta.exists():
            try:
                entry["source_url"] = json.loads(meta.read_text(encoding="utf-8")).get("source_url", "")
            except ValueError:
                pass
    p = manifest_path(root)
    doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"clips": []}
    doc.setdefault("clips", [])
    parsed, problems = parse_manifest({"clips": [entry]}, root)
    if not parsed:
        raise ValueError("; ".join(problems) or "invalid entry")
    if problems:
        print("[clip] warning: " + "; ".join(problems))
    entry.setdefault("id", parsed[0].id)
    if any(isinstance(c, dict) and c.get("id") == entry["id"] for c in doc["clips"]):
        entry["id"] = f"{entry['id']}-{len(doc['clips']) + 1}"
    f = parsed[0].file
    if f and not Path(f).is_file():
        print(f"[clip] warning: {f} does not exist yet — Stage 5 will render the panel until it does")
    doc["clips"].append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(p, doc)
    return entry


def beat_rows(root: Path) -> list[tuple[str, str, list[str]]]:
    """[(beat_key, text, [clip ids targeting it])] for the project's narration."""
    from .stage_5.clips import load_manifest
    from .stage_5.shots import _beat_rows_for_custom
    np_ = root / "narration.json"
    if not np_.exists():
        raise SystemExit(f"{np_} missing — beats come from Stage 3's narration")
    narration = json.loads(np_.read_text(encoding="utf-8"))
    by_beat: dict[str, list[str]] = {}
    for e in load_manifest(str(root), log=lambda m: None):
        by_beat.setdefault(e.beat or "(desc)", []).append(e.id)
    rows = [(bk, txt, by_beat.pop(bk, [])) for bk, txt in _beat_rows_for_custom(narration)]
    # Keys that are not review rows still resolve at render (a bookend's plain scene id, or a
    # desc-placed entry) — or are stale. List them so nothing in the manifest is invisible.
    texts = {str(s.get("scene_id")): str(s.get("text") or "") for s in narration.get("scenes") or []}
    for bk, ids in by_beat.items():
        if bk == "(desc)":
            txt = "(placed by word overlap at render)"
        else:
            txt = texts.get(bk.partition(":")[0], "(no such scene — stale key)")
        rows.append((bk, txt, ids))
    return rows


# ─── CLI ────────────────────────────────────────────────────────────────────────

def _parse_crop_args(a) -> dict | None:
    if a.crop:
        parts = [float(x) for x in a.crop.split(",")]
        if len(parts) != 4:
            raise SystemExit("--crop takes x,y,w,h as fractions 0-1")
        return dict(zip(("x", "y", "w", "h"), parts))
    if a.crop_cx is not None or a.crop_cy is not None:
        return {"cx": 0.5 if a.crop_cx is None else a.crop_cx,
                "cy": 0.5 if a.crop_cy is None else a.crop_cy}
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stages.clip_fetch",
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="You.com Web Search on youtube.com")
    s.add_argument("query")
    s.add_argument("--all", action="store_true", help="also list non-video pages")
    s.add_argument("--json", action="store_true")

    f = sub.add_parser("fetch", help="download + transcode + contact sheet")
    f.add_argument("url")
    f.add_argument("--project", required=True)
    f.add_argument("--max-height", type=int, default=1080)
    f.add_argument("--every", type=float, default=2.0, help="contact-sheet step, seconds")

    sh = sub.add_parser("sheet", help="contact sheet of a span, to pick in/out points")
    sh.add_argument("file")
    sh.add_argument("--every", type=float, default=1.0)
    sh.add_argument("--start", type=float, default=0.0)
    sh.add_argument("--end", type=float, default=None)
    sh.add_argument("--cols", type=int, default=6)
    sh.add_argument("--out", default=None)

    ad = sub.add_parser("add", help="append a clip entry to review/clips/clips.json")
    ad.add_argument("--project", required=True)
    ad.add_argument("--file", help="clip file (project-relative or absolute)")
    ad.add_argument("--url", dest="source_url", help="source URL (credit; fetched by sync if no --file)")
    ad.add_argument("--start", type=float, required=True)
    ad.add_argument("--end", type=float, default=None)
    ad.add_argument("--beat", help='beat key: "intro", "outro", "<scene_id>" or "<scene_id>:<frag>"')
    ad.add_argument("--desc", help="place by word overlap instead of --beat")
    ad.add_argument("--id")
    ad.add_argument("--crop", help="x,y,w,h fractions 0-1")
    ad.add_argument("--crop-cx", type=float, default=None, help="9:16 window centre x, 0-1")
    ad.add_argument("--crop-cy", type=float, default=None, help="9:16 window centre y, 0-1")

    b = sub.add_parser("beats", help="list the project's beat keys and their clips")
    b.add_argument("--project", required=True)

    sy = sub.add_parser("sync", help="fetch every entry that has a source_url but no file")
    sy.add_argument("--project", required=True)

    a = ap.parse_args(argv)

    if a.cmd == "search":
        rows = youtube_search(a.query)
        if not a.all:
            rows = [r for r in rows if r["is_video"]]
        if a.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for i, r in enumerate(rows, 1):
                print(f"{i:2d}. {r['title']}\n    {r['url']}\n    {r['snippet']}")
            if not rows:
                print("no video results (try --all, or a different query)")
        return 0
    if a.cmd == "fetch":
        root = _root(a.project)
        res = fetch_clip(a.url, root / CLIP_DIR_REL, max_height=a.max_height, every=a.every)
        rel = _rel(root, res["file"])
        print(json.dumps({**res, "manifest_file": rel}, indent=2, ensure_ascii=False))
        print(f"\nnext: open {res['sheet']}, pick in/out, then\n"
              f"  python -m stages.clip_fetch add --project {a.project} --file {rel} "
              f"--start <s> --end <s> --beat <key>")
        return 0
    if a.cmd == "sheet":
        src = Path(a.file)
        out = Path(a.out) if a.out else src.with_name(
            f"{src.stem}_sheet_{a.start:g}-{'end' if a.end is None else f'{a.end:g}'}.jpg")
        contact_sheet(src, out, every=a.every, start=a.start, end=a.end, cols=a.cols)
        return 0
    if a.cmd == "add":
        root = _root(a.project)
        if not (a.file or a.source_url):
            raise SystemExit("add needs --file or --url")
        if not (a.beat or a.desc):
            raise SystemExit("add needs --beat (or --desc)")
        entry = add_entry(root, {"id": a.id, "file": a.file, "source_url": a.source_url,
                                 "start": a.start, "end": a.end, "beat": a.beat,
                                 "desc": a.desc, "crop": _parse_crop_args(a)})
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0
    if a.cmd == "beats":
        for bk, txt, ids in beat_rows(_root(a.project)):
            clip = f"  ← {', '.join(ids)}" if ids else ""
            print(f"{bk:>8}  {' '.join(txt.split())[:90]}{clip}")
        return 0
    if a.cmd == "sync":
        from .stage_5.clips import prefetch_clips
        root = _root(a.project)
        n = prefetch_clips(str(root))
        print(f"fetched {n} clip(s)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
