"""Find, fetch and pick video clips for Stage 5 clip shots (see stages/stage_5/clips.py).

    python -m stages.clip_fetch search "green lantern animated series mogo"
    python -m stages.clip_fetch moment "Kilowog fights Hal Jordan" --html moments.html
    python -m stages.clip_fetch fetch "https://www.youtube.com/watch?v=gKiT1ekWIAA" --project my-proj
    python -m stages.clip_fetch sheet projects/my-proj/review/clips/gKiT1ekWIAA.mp4 --start 60 --end 80 --every 0.5
    python -m stages.clip_fetch beats --project my-proj
    python -m stages.clip_fetch add --project my-proj --file review/clips/gKiT1ekWIAA.mp4 \\
        --start 64.5 --end 67.0 --beat 3:1 --crop-cx 0.55
    python -m stages.clip_fetch sync --project my-proj      # fetch entries that only have a URL

search → You.com Web Search (stages.research_scout.youcom), restricted to youtube.com.
moment → several phrasings of a described moment → every video they find, ranked by how well it
         matches, each with candidate windows from chapters, subtitles and "most replayed"
         peaks (read from metadata; nothing is downloaded).
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
    """You.com Search payload → [{"title","url","snippet","is_video"}], deduped by video id
    (the same video comes back as watch?v=, m.youtube.com and ?pp= variants) or URL, in
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
        key = video_id(url) or url
        if not url or key in seen:
            continue
        seen.add(key)
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


def video_id(url: str) -> str | None:
    """The 11-character YouTube id of a watch/shorts/embed/youtu.be URL, else None."""
    if not _VIDEO_URL.search(url or ""):
        return None
    m = _YT_ID.search(url)
    return m.group(1) if m else None


# ─── moment search: several candidates, several timing signals ──────────────────
# One query + "most replayed" picks ONE spot in ONE video. A moment search instead asks a few
# phrasings, keeps every video they return, and reads three independent timing signals per
# video without downloading it: chapters and subtitle lines that share words with the
# description, and the "most replayed" peaks. Videos rank by how well their title/description
# match the description and how many phrasings found them; the replay graph is one signal
# among three, never the ranking.

def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _terms(text: str) -> set[str]:
    from utils.lexical_sim import content_words
    return {_stem(w) for w in content_words(text)}


def moment_queries(desc: str, extra: list[str] | tuple = ()) -> list[str]:
    """The phrasings a moment search sends: the description as given, as a scene clip, and as
    animation — then any caller-supplied ones. Deduped, order kept."""
    out: list[str] = []
    for q in (desc, f"{desc} scene clip", f"{desc} animated", *extra):
        q = " ".join(str(q).split())
        if q and q not in out:
            out.append(q)
    return out


def gather_candidates(queries: list[str], *, search=youtube_search, log=print) -> list[dict]:
    """Run every query, keep each VIDEO once (by id) with how many queries found it ("hits")
    and its best rank. Sorted by hits, then best rank. A failed query is logged and skipped."""
    by_id: dict[str, dict] = {}
    for q in queries:
        try:
            rows = search(q)
        except RuntimeError as exc:
            log(f"[moment] query failed ({exc}): {q}")
            continue
        for rank, r in enumerate(r for r in rows if r.get("is_video")):
            vid = video_id(r["url"])
            if not vid:
                continue
            c = by_id.setdefault(vid, {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                                       "title": r.get("title", ""), "snippet": r.get("snippet", ""),
                                       "hits": 0, "best_rank": rank})
            c["hits"] += 1
            c["best_rank"] = min(c["best_rank"], rank)
    return sorted(by_id.values(), key=lambda c: (-c["hits"], c["best_rank"]))


_CUE = re.compile(r"(?:(\d+):)?(\d\d):(\d\d)[.,](\d+)\s+-->\s+(?:(\d+):)?(\d\d):(\d\d)[.,](\d+)")


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    """WebVTT → [(start, end, text)], tags stripped, consecutive repeats (auto-caption
    roll-up) collapsed."""
    def t(h, m, s, frac):
        return int(h or 0) * 3600 + int(m) * 60 + int(s) + float(f"0.{frac}")
    cues: list[tuple[float, float, str]] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        m = _CUE.search(lines[i])
        if not m:
            i += 1
            continue
        a, b = t(*m.groups()[:4]), t(*m.groups()[4:])
        i += 1
        body = []
        while i < len(lines) and lines[i].strip():
            body.append(re.sub(r"<[^>]+>", "", lines[i]).strip())
            i += 1
        txt = " ".join(x for x in body if x)
        if txt and not (cues and cues[-1][2] == txt):
            cues.append((a, b, txt))
    return cues


def heatmap_peaks(heatmap: list | None, duration: float, *, k: int = 3,
                  min_value: float = 0.35, min_gap: float = 8.0) -> list[tuple[float, float]]:
    """[(centre_seconds, value)] of up to k local maxima of YouTube's "most replayed" graph,
    strongest first, at least min_gap apart. The opening seconds are skipped: every video's
    graph starts high because playback starts there, which says nothing about the content."""
    segs = [(float(h["start_time"]), float(h["end_time"]), float(h["value"]))
            for h in heatmap or [] if isinstance(h, dict) and "value" in h]
    head = max(3.0, 0.02 * (duration or 0))
    peaks = []
    for i, (a, b, v) in enumerate(segs):
        if a < head or v < min_value:
            continue
        prev = segs[i - 1][2] if i else -1.0
        nxt = segs[i + 1][2] if i + 1 < len(segs) else -1.0
        if v >= prev and v >= nxt:
            peaks.append(((a + b) / 2, v))
    chosen: list[tuple[float, float]] = []
    for c, v in sorted(peaks, key=lambda p: -p[1]):
        if all(abs(c - c2) >= min_gap for c2, _ in chosen):
            chosen.append((round(c, 1), round(v, 2)))
        if len(chosen) == k:
            break
    return chosen


def find_moments(desc: str, info: dict, cues: list | None = None, *, k: int = 3) -> list[dict]:
    """Up to k candidate windows in one video, strongest first, each with the reason it was
    picked: a chapter or a subtitle line sharing words with the description, or a most-replayed
    peak. [] when the video gives no timing signal at all."""
    q = _terms(desc)
    # Words the TITLE already has (usually the characters' names) say what the whole video is
    # about, not where in it the moment is — a line naming "Hal Jordan" proves nothing in a
    # video titled "Hal Jordan vs Kilowog". So a chapter or subtitle must share at least one
    # of the REMAINING description words (the action: "fights", "dies", "saves"...).
    locate = q - _terms(info.get("title") or "")
    need = 1 if len(q) <= 2 else 2

    def hit(text: str) -> set[str]:
        shared = q & _terms(text)
        ok = bool(shared & locate) if locate else len(shared) >= need
        return shared if ok else set()

    dur = float(info.get("duration") or 0)
    out: list[dict] = []
    for ch in info.get("chapters") or []:
        shared = hit(ch.get("title", ""))
        if shared:
            a = float(ch.get("start_time") or 0)
            b = min(float(ch.get("end_time") or a + 10), a + 10)
            out.append({"start": round(a, 1), "end": round(b, 1), "strength": 0.6 + 0.1 * len(shared),
                        "why": f"chapter \"{ch.get('title', '')}\""})
    for a, b, txt in cues or []:
        shared = hit(txt)
        if shared:
            out.append({"start": round(max(0.0, a - 1), 1), "end": round(min(dur or b + 2, b + 2), 1),
                        "strength": 0.5 + 0.1 * len(shared), "why": f"subtitle \"{txt[:70]}\""})
    for c, v in heatmap_peaks(info.get("heatmap"), dur):
        out.append({"start": round(max(0.0, c - 3), 1), "end": round(min(dur or c + 3, c + 3), 1),
                    "strength": v, "why": f"most replayed ({v:.2f})"})
    out.sort(key=lambda m: -m["strength"])
    picked: list[dict] = []
    for m in out:                                   # drop windows overlapping a stronger one
        if all(m["end"] <= p["start"] or m["start"] >= p["end"] for p in picked):
            picked.append(m)
        if len(picked) == k:
            break
    return sorted(picked, key=lambda m: m["start"])


def score_video(desc: str, cand: dict, info: dict, moments: list[dict], n_queries: int) -> dict:
    """The ranking, with its parts so the list can say WHY: description match (title counts
    more than description/tags), how many phrasings found the video, whether it has a timing
    signal, a scene-clip length (30s-10min) and >=720p. A heuristic for ordering a shortlist
    a human picks from, not a gate — nothing is dropped for a low score."""
    q = _terms(desc) or {""}
    title = _terms(info.get("title") or cand.get("title", ""))
    body = _terms(" ".join([info.get("description") or cand.get("snippet", ""),
                            " ".join(info.get("tags") or [])]))
    match = 0.7 * len(q & title) / len(q) + 0.3 * len(q & (title | body)) / len(q)
    consensus = cand.get("hits", 1) / max(1, n_queries)
    timing = min(1.0, max((m["strength"] for m in moments), default=0.0))
    dur = float(info.get("duration") or 0)
    length_fit = 1.0 if 30 <= dur <= 600 else (0.5 if dur and dur <= 1200 else 0.0)
    res = 1.0 if (info.get("height") or 0) >= 720 else 0.0
    score = 0.45 * match + 0.25 * consensus + 0.15 * timing + 0.1 * length_fit + 0.05 * res
    return {"score": round(score, 3), "match": round(match, 2), "consensus": f"{cand.get('hits', 1)}/{n_queries}",
            "timing": round(timing, 2)}


_SUB_LANGS = ("en", "en-US", "en-GB", "en-orig")


_META_CACHE_DIR = REPO / "cache" / "metadata"
_MOMENTS_CACHE_DIR = REPO / "cache" / "moments"


def video_metadata(url: str, work_dir: Path, *, timeout: int = 120, use_cache: bool = True) -> tuple[dict, list]:
    """(yt-dlp info dict, subtitle cues) for one video WITHOUT downloading it. Two calls on
    purpose: the info JSON comes from --dump-single-json, which does not depend on subtitles —
    with both in one call a subtitle HTTP 429 (YouTube rate-limits caption requests) aborted
    the whole video. Subtitles are then fetched only when the video has English ones, and any
    failure there just means no subtitle signal. Raises when the info itself can't be read
    (private / removed / blocked)."""
    m = _YT_ID.search(url)
    vid = m.group(1) if m else None
    if use_cache and vid:
        _META_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _META_CACHE_DIR / f"{vid}.json"
        if cache_file.is_file():
            try:
                cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
                return cached_data["info"], cached_data.get("cues", [])
            except Exception:
                pass

    base = [*_ytdlp_cmd(), "--skip-download", "--no-playlist", "--js-runtimes", "node"]
    res = subprocess.run([*base, "--dump-single-json", url],
                         capture_output=True, text=True, timeout=timeout)
    try:
        info = json.loads(res.stdout or "")
    except ValueError:
        tail = " ".join((res.stderr or "").split())[-160:]
        raise RuntimeError(f"no metadata ({tail or 'private, removed or blocked?'})") from None
    langs = [lg for lg in _SUB_LANGS
             if lg in (info.get("subtitles") or {}) or lg in (info.get("automatic_captions") or {})]
    cues: list = []
    if langs:
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([*base, "--write-subs", "--write-auto-subs", "--sub-langs", langs[0],
                            "--sub-format", "vtt", "-o", str(work_dir / "%(id)s.%(ext)s"), url],
                           capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        vtts = sorted(work_dir.glob("*.vtt"))
        if vtts:
            cues = parse_vtt(vtts[0].read_text(encoding="utf-8", errors="replace"))

    if use_cache and vid and info:
        try:
            _META_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file = _META_CACHE_DIR / f"{vid}.json"
            cache_file.write_text(json.dumps({"info": info, "cues": cues}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return info, cues


def moment_search(desc: str, *, limit: int = 8, extra_queries: list[str] | tuple = (),
                  search=youtube_search, metadata=video_metadata, log=print,
                  use_cache: bool = True) -> list[dict]:
    """The shortlist: up to `limit` videos, best first, each with its candidate windows."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    queries = moment_queries(desc, extra_queries)
    cache_key = hashlib.sha256(f"{desc}|{queries}|{limit}".encode("utf-8")).hexdigest()
    if use_cache:
        _MOMENTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cfile = _MOMENTS_CACHE_DIR / f"{cache_key}.json"
        if cfile.is_file():
            try:
                return json.loads(cfile.read_text(encoding="utf-8"))
            except Exception:
                pass

    cands = gather_candidates(queries, search=search, log=log)[:limit]
    log(f"[moment] {len(queries)} queries → {len(cands)} video(s); reading their metadata")

    def one(c):
        try:
            with tempfile.TemporaryDirectory() as td:
                info, cues = metadata(c["url"], Path(td))
        except Exception as exc:
            return {**c, "error": str(exc)[:200]}
        moments = find_moments(desc, info, cues)
        row = {**c, "title": info.get("title") or c["title"],
               "channel": info.get("channel") or info.get("uploader") or "",
               "duration": round(float(info.get("duration") or 0), 1),
               "height": info.get("height"), "subtitles": bool(cues),
               "vertical": bool(info.get("width") and info.get("height")
                                and info["height"] > info["width"]),
               "thumbnail": info.get("thumbnail") or "",
               "embeddable": info.get("playable_in_embed", True) is not False,
               "moments": moments}
        row.update(score_video(desc, c, info, moments, len(queries)))
        return row

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(one, cands))
    ok = sorted((r for r in rows if "error" not in r), key=lambda r: -r["score"])
    result = ok + [r for r in rows if "error" in r]
    if use_cache:
        try:
            _MOMENTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (_MOMENTS_CACHE_DIR / f"{cache_key}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
    return result


def moments_html(sections: list[tuple[str, list[dict]]]) -> str:
    """A self-contained review page: one section per searched description, one card per video
    with an embedded player; each candidate window is a button that plays exactly that span
    (embed start/end). A video whose owner disabled embedding shows its thumbnail and a link.
    Every card links to YouTube at the window's start, so nothing depends on the embed."""
    from html import escape as e

    def card(i: int, r: dict) -> str:
        if "error" in r:
            return (f'<div class="card bad"><b>{i}. unavailable</b><br>'
                    f'<a href="{e(r["url"])}" target="_blank">{e(r["url"])}</a><br>'
                    f'<small>{e(r["error"])}</small></div>')
        vid, d = r["id"], r.get("duration") or 0
        first = (r.get("moments") or [{"start": 0, "end": 0}])[0]
        start = int(first["start"])
        player = (f'<iframe id="p-{e(vid)}" src="https://www.youtube.com/embed/{e(vid)}?start={start}'
                  f'" allow="autoplay; encrypted-media" allowfullscreen '
                  f'referrerpolicy="strict-origin-when-cross-origin"></iframe>'
                  if r.get("embeddable", True) else
                  f'<a href="{e(r["url"])}&t={start}s" target="_blank"><img src="{e(r.get("thumbnail", ""))}" '
                  f'alt=""><span class="note">embedding disabled — opens on YouTube</span></a>')
        moments = "".join(
            f'<li><button onclick="play(\'{e(vid)}\',{int(m["start"])},{int(math.ceil(m["end"]))})">'
            f'▶ {m["start"]:.1f}–{m["end"]:.1f}s</button> {e(m["why"])} '
            f'<a href="https://www.youtube.com/watch?v={e(vid)}&t={int(m["start"])}s" target="_blank">↗</a></li>'
            for m in r.get("moments") or [])
        if not moments:
            moments = ("<li class=note>no timing signal — short clip, the whole video is the scene</li>"
                       if d and d <= 120 else
                       "<li class=note>no timing signal — fetch it and read the contact sheet</li>")
        tags = " · ".join(x for x in (
            r.get("channel", ""), f"{int(d // 60)}:{int(d % 60):02d}",
            f"{r.get('height') or '?'}p" + (" vertical" if r.get("vertical") else ""),
            f"match {r['match']:.2f}", f"found by {r['consensus']} queries",
            "subtitles" if r.get("subtitles") else "") if x)
        return (f'<div class="card"><div class="head"><span class="score">{r["score"]:.2f}</span> '
                f'<b>{i}. <a href="{e(r["url"])}" target="_blank">{e(r["title"])}</a></b>'
                f'<div class="meta">{e(tags)}</div></div>{player}<ul>{moments}</ul></div>')

    body = "".join(
        f'<h2>{e(desc)} <small>({len(rows)} videos)</small></h2><div class="grid">'
        + "".join(card(i, r) for i, r in enumerate(rows, 1)) + "</div>"
        for desc, rows in sections)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Moment search</title>
<style>
body{{background:#111;color:#ddd;font:14px -apple-system,Segoe UI,sans-serif;margin:16px}}
h2{{color:#fff;margin:28px 0 10px}} h2 small{{color:#888;font-weight:normal}}
a{{color:#8cf}} .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}}
.card{{background:#1c1c1c;border:1px solid #333;border-radius:8px;padding:10px}} .bad{{opacity:.6}}
.head b{{font-size:15px}} .meta{{color:#999;font-size:12px;margin:4px 0 8px}}
.score{{background:#2a6;color:#fff;border-radius:4px;padding:1px 6px;font-weight:bold}}
iframe,.card img{{width:100%;aspect-ratio:16/9;border:0;border-radius:6px;background:#000;display:block}}
ul{{list-style:none;padding:0;margin:8px 0 0}} li{{margin:4px 0}}
button{{background:#333;color:#fff;border:1px solid #555;border-radius:4px;padding:2px 8px;cursor:pointer}}
button:hover{{background:#2a6}} .note{{color:#999;font-style:italic}}
</style></head><body><h1>Moment search</h1>{body}
<script>
function play(id,a,b){{document.getElementById('p-'+id).src=
  'https://www.youtube.com/embed/'+id+'?autoplay=1&start='+a+'&end='+b;}}
</script></body></html>"""


def format_moments(rows: list[dict]) -> str:
    out = []
    for i, r in enumerate(rows, 1):
        if "error" in r:
            out.append(f"{i:2d}. (unavailable) {r['url']}  — {r['error']}")
            continue
        d = r["duration"]
        out.append(f"{i:2d}. [{r['score']:.2f}] {r['title']}\n"
                   f"    {r['url']}  {r['channel']} · {int(d // 60)}:{int(d % 60):02d} · "
                   f"{r.get('height') or '?'}p{' vertical' if r.get('vertical') else ''} · "
                   f"match {r['match']:.2f} · found by {r['consensus']} queries"
                   f"{' · subtitles' if r.get('subtitles') else ''}")
        if r["moments"]:
            for m in r["moments"]:
                span = f"{m['start']:.1f}–{m['end']:.1f}s"
                out.append(f"      {span:<16} {m['why']}")
        elif d and d <= 120:
            out.append("      (no timing signal — short clip, the whole video is the scene)")
        else:
            out.append("      (no timing signal — fetch it and read the contact sheet)")
    return "\n".join(out)


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


def ytdlp_section_args(
    url: str,
    out_dir: Path,
    *,
    start: float,
    beat_duration: float,
    margin: float = 2.0,
    max_height: int = 1080,
) -> list[str]:
    import config
    end = start + beat_duration * config.CLIP_SPEED_MAX + margin
    fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
    return [
        *_ytdlp_cmd(),
        "--no-playlist",
        "--js-runtimes", "node",
        "-f", fmt,
        "--download-sections", f"*{start:.2f}-{end:.2f}",
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--no-progress",
        "-o", str(Path(out_dir) / "%(id)s.%(ext)s"),
        "--print", "after_move:filepath",
        url,
    ]


def fetch_clip_section(
    url: str,
    clip_dir: Path,
    *,
    start: float,
    beat_duration: float,
    margin: float = 2.0,
    max_height: int = 1080,
    log=print,
) -> Path:
    clip_dir = Path(clip_dir)
    src_dir = clip_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    m = _YT_ID.search(url)
    vid = m.group(1) if m else "clip"
    out = clip_dir / f"{vid}_{start:.1f}_{beat_duration:.1f}.mp4"
    if out.is_file():
        log(f"[clip] cached section: {out}")
        return out

    cmd = ytdlp_section_args(url, src_dir, start=start, beat_duration=beat_duration,
                            margin=margin, max_height=max_height)
    res = subprocess.run(cmd, capture_output=True, text=True)
    lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
    raw = Path(lines[-1]) if lines else None
    if res.returncode != 0 or raw is None or not raw.is_file():
        tail = " ".join((res.stderr or res.stdout or "").split())[-500:]
        raise RuntimeError(f"yt-dlp section download failed for {url}: {tail}")

    normalize(raw, out, log=log)
    return out


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

    mo = sub.add_parser("moment", help="shortlist videos + candidate windows for a described moment")
    mo.add_argument("description", nargs="+",
                    help='the moment, e.g. "Kilowog fights Hal Jordan" (several = several searches)')
    mo.add_argument("--limit", type=int, default=8, help="videos to examine (default 8)")
    mo.add_argument("--query", action="append", default=[], help="extra phrasing (repeatable)")
    mo.add_argument("--json", action="store_true")
    mo.add_argument("--html", help="also write a review page with embedded players to this path")

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
    if a.cmd == "moment":
        sections = [(d, moment_search(d, limit=a.limit, extra_queries=a.query))
                    for d in a.description]
        for d, rows in sections:
            if len(sections) > 1 and not a.json:
                print(f"\n=== {d}")
            print(json.dumps(rows, indent=2, ensure_ascii=False) if a.json else format_moments(rows))
        if a.html:
            out = Path(a.html)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(moments_html(sections), encoding="utf-8")
            print(f"\nreview page: {out}")
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
