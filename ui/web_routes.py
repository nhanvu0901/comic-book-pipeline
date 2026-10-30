"""FastAPI routes for picking a video clip per review beat (ENABLE_VIDEO_CLIPS=1).

  GET  /moments_review?project=&beat=[&q=]   the picking page: YouTube candidates, each in a real
                                              YT.Player; "Dùng từ đây" sends player.getCurrentTime()
  POST /api/pick_moment                      {project, beat, video_id, start} → starts the pick job
  GET  /api/pick_status?project=&beat=       state of that job (waiting_tts / downloading /
                                              rendering / ready / error) — the page polls it
  GET  /api/clip_preview?project=&beat=      the 9:16 preview mp4 of the beat's picked clip
  GET  /api/beat_tts_status?project=         the background-TTS status (beat durations)
  POST /api/bump_beat_tts                    move a beat's scene to the front of the TTS queue

A PICK is a background job, never an inline request: wait (bounded) until the cached TTS has the
beat's EXACT duration, download only the section yt-dlp needs
(start .. start + beat_duration x CLIP_SPEED_MAX + margin), render the 9:16 preview through the same
fit Stage 5 will use, and only THEN write review/clips/clips.json — rebased so t=0 of the section
file is the picked moment, open-ended so the fit may extend into the footage margin. Nothing is
written to the manifest before the file exists, and a clip that cannot fill its beat is refused
with the reason, not stored.

Everything that reaches a path is validated: the project must be an existing directory under
PROJECTS_ROOT, the beat a plain review beat key, the video id an 11-char YouTube id (the URL is
rebuilt from it; a client-supplied URL is never fetched).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from html import escape as e
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import config
from stages import clip_fetch
from stages.stage_4 import tts_status
from stages.stage_5 import clips

logger = logging.getLogger(__name__)

router = APIRouter()

_BEAT_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-\.:]+$")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_MAX_START = 24 * 3600.0
# how long a pick waits for the background TTS to reach its beat before giving up
WAIT_TTS_SECONDS = float(os.getenv("PICK_WAIT_TTS_SECONDS", "900"))


# ─── validation ───────────────────────────────────────────────────────────────────────────────

def get_safe_project_dir(project: str, allow_missing: bool = False) -> Path:
    """The project's directory, which must lie inside PROJECTS_ROOT (and, unless allow_missing,
    exist). Anything else is a 400/404 — a LAN client can never make this write outside projects/."""
    if not project or not project.strip():
        raise HTTPException(status_code=400, detail="Missing project name")
    try:
        root_resolved = config.PROJECTS_ROOT.resolve()
        target = (config.PROJECTS_ROOT / project).resolve()
        if not target.is_relative_to(root_resolved) or target == root_resolved:
            raise HTTPException(status_code=400, detail="Path traversal detected")
        if not allow_missing and not target.is_dir():
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
        return target
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid project path: {exc}")


def validate_beat_key(beat: str) -> str:
    """A review beat key ("intro", "outro", "<sid>", "<sid>:<frag>") — never a path."""
    if not beat or not _BEAT_KEY_RE.match(beat) or len(beat) > 64:
        raise HTTPException(status_code=400, detail=f"Invalid beat key '{beat}'")
    return beat


def validate_video_id(video_id: str) -> str:
    if not _VIDEO_ID_RE.match(video_id or ""):
        raise HTTPException(status_code=400, detail=f"Invalid YouTube video id '{video_id}'")
    return video_id


def _js(value: Any) -> str:
    """A JSON literal that is safe inside an inline <script> (no </script>, no HTML comment)."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


# ─── "a clip was picked" listeners (the review gate repaints its card) ───────────────────────

_listeners: dict[Any, Callable[[dict[str, Any]], None]] = {}
_listeners_lock = threading.Lock()


def add_moment_picked_listener(cb: Callable[[dict[str, Any]], None], key: Any = None) -> Any:
    """Register `cb` for pick events. With a `key` (e.g. the page+project it repaints) a new
    registration REPLACES the previous one of that key — a screen that rebuilds itself must not leave
    one more listener behind each time. Returns the key (pass it to remove_moment_picked_listener)."""
    k = key if key is not None else cb
    with _listeners_lock:
        _listeners[k] = cb
    return k


def remove_moment_picked_listener(key_or_cb: Any) -> None:
    with _listeners_lock:
        if key_or_cb in _listeners:
            del _listeners[key_or_cb]
            return
        for k, cb in list(_listeners.items()):
            if cb is key_or_cb:
                del _listeners[k]


def clear_moment_picked_listeners() -> None:
    with _listeners_lock:
        _listeners.clear()


def listener_count() -> int:
    with _listeners_lock:
        return len(_listeners)


def _broadcast_moment_picked(payload: dict[str, Any]) -> None:
    with _listeners_lock:
        current = list(_listeners.items())
    for key, listener in current:
        try:
            listener(payload)
        except Exception:                      # a closed page's repaint raises: forget that listener
            logger.debug("dropping a moment-picked listener that raised", exc_info=True)
            remove_moment_picked_listener(key)


# ─── the pick job ─────────────────────────────────────────────────────────────────────────────

from stages.clip_fetch import ClipPickError, process_clip_pick


_PICKS: dict[tuple[str, str], dict[str, Any]] = {}
_PICKS_LOCK = threading.Lock()


def _set_pick(project: str, beat: str, gen: int | None = None, **fields: Any) -> int:
    with _PICKS_LOCK:
        cur = _PICKS.get((project, beat))
        if gen is not None and cur is not None and cur.get("gen") != gen:
            return -1                                           # superseded by a newer pick
        new_gen = (cur.get("gen", 0) + 1) if gen is None and cur else (gen or 1)
        _PICKS[(project, beat)] = {**(cur or {}), **fields, "gen": new_gen, "t": time.time()}
        return new_gen


def get_pick_state(project: str, beat: str) -> dict[str, Any]:
    """The pick job's state, or — when no job ran in this process — what the manifest says."""
    with _PICKS_LOCK:
        cur = _PICKS.get((project, beat))
        if cur is not None:
            return {k: v for k, v in cur.items() if k != "gen"}
    try:
        root = get_safe_project_dir(project)
    except HTTPException:
        return {"state": "none"}
    for c in clips.read_manifest_doc(root)["clips"]:
        if isinstance(c, dict) and str(c.get("beat")) == beat:
            return {"state": "ready", "entry": c}
    return {"state": "none"}


class PickMomentRequest(BaseModel):
    project: str
    beat: str
    video_id: str
    start: float
    end: float | None = None


def _run_pick_job(
    project: str,
    project_root: Path,
    beat: str,
    video_id: str,
    start: float,
    gen: int,
    end: float | None = None,
) -> dict | None:
    def state(s: str, **kw: Any) -> bool:
        return _set_pick(project, beat, gen, state=s, **kw) != -1

    def commit(entry: dict[str, Any], staged_preview: Path, canonical_preview: Path) -> bool:
        """Check this job's generation and persist as one short operation.

        `_set_pick()` takes the same lock, so a newer pick cannot arrive between the generation
        check and manifest upsert. Network/download/render work remains outside this lock.
        """
        with _PICKS_LOCK:
            current = _PICKS.get((project, beat))
            if current is None or current.get("gen") != gen:
                return False
            os.replace(staged_preview, canonical_preview)
            clips.upsert_beat_clip(project_root, entry)
            return True

    try:
        from stages.stage_4.background_tts import bump_priority_beat
        beat_dur = tts_status.beat_duration(project_root, beat)
        if beat_dur is None:
            state("waiting_tts", message="Đang chờ TTS tính thời lượng chính xác của beat…")
            try:
                bump_priority_beat(project, beat)
            except Exception as exc:
                logger.warning("could not bump the TTS queue for %s/%s: %s", project, beat, exc)
            deadline = time.time() + WAIT_TTS_SECONDS
            while beat_dur is None and time.time() < deadline:
                time.sleep(1.0)
                beat_dur = tts_status.beat_duration(project_root, beat)
                if _PICKS.get((project, beat), {}).get("gen") != gen:
                    return None
            if beat_dur is None:
                raise ClipPickError("TTS chưa tính xong thời lượng beat này (quá thời gian chờ). "
                                    "Mở lại sau khi giọng đọc chạy tới đoạn này.")
        state("downloading", beat_duration=beat_dur, message="Đang tải đoạn clip…")
        entry = process_clip_pick(project_root, beat, video_id, start, beat_dur, end=end,
                                  on_state=lambda s: state(s, message={"downloading": "Đang tải đoạn clip…",
                                                                      "rendering": "Đang dựng preview 9:16…"}.get(s, s)),
                                  preview_token=str(gen), commit=commit)
        if entry is None:
            return None
        if not state("ready", entry=entry, message="✓ Đã chọn clip cho beat."):
            return None
        _broadcast_moment_picked({"project": project, "state": "ready", **entry})
        return entry
    except ClipPickError as exc:
        if not state("error", message=str(exc)):
            return None
    except Exception as exc:
        logger.warning("clip pick failed for %s/%s: %s", project, beat, exc, exc_info=True)
        if not state("error", message=f"Lỗi khi tải/dựng clip: {exc}"):
            return None
    _broadcast_moment_picked({"project": project, "beat": beat, "state": "error",
                              "message": get_pick_state(project, beat).get("message", "")})
    return None


# ─── the picking page ─────────────────────────────────────────────────────────────────────────

def _beat_query(project_root: Path | None, beat: str) -> str:
    """The narration words of review beat `beat` — the default search text."""
    if not project_root or not beat:
        return ""
    try:
        from stages.stage_4.beat_timing import beat_rows
        narr = json.loads((project_root / "narration.json").read_text("utf-8"))
        return next((r.text.strip() for r in beat_rows(narr) if r.beat_key == beat), "")
    except Exception:
        return ""


@router.get("/moments_review", response_class=HTMLResponse)
async def moments_review(project: str = "", beat: str = "", q: str = "") -> str:
    """Web review tab embedding YouTube players for picking a clip moment."""
    project_root = get_safe_project_dir(project) if project else None
    if beat:
        validate_beat_key(beat)

    query = q.strip() or _beat_query(project_root, beat)
    if not query:
        query = f"Action scene {beat}" if beat else "Action clip"

    # Reuse the shortlist this (beat, query) already fetched
    candidates: list[dict[str, Any]] = []
    cache_file = None
    search_error = ""
    if project_root is not None:
        import hashlib
        clips_dir = project_root / "review" / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        q_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
        cache_file = clips_dir / f"search_{(beat or 'default').replace(':', '_')}_{q_hash}.json"
        if cache_file.exists():
            try:
                candidates = json.loads(cache_file.read_text("utf-8"))
            except Exception:
                candidates = []

    if not candidates:
        try:
            # moment_search blocks on the network — a worker thread, never the event loop that also serves Flet
            candidates = await run_in_threadpool(clip_fetch.moment_search, query, limit=8)
            if cache_file and candidates:
                cache_file.write_text(json.dumps(candidates, indent=2, ensure_ascii=False), "utf-8")
        except Exception as exc:
            search_error = str(exc)
            candidates = []

    usable = [c for c in candidates if isinstance(c, dict) and "error" not in c]
    cards_html = []
    for i, c in enumerate(usable, 1):
        vid = str(c.get("id") or "")
        if not _VIDEO_ID_RE.match(vid):
            continue
        title = c.get("title") or "Video Clip"
        channel = c.get("channel") or ""
        url = f"https://www.youtube.com/watch?v={vid}"
        embeddable = c.get("embeddable", True)
        moments = c.get("moments") or [{"start": 0.0, "end": 5.0, "label": "Start"}]
        first_start = float(moments[0].get("start", 0.0))

        buttons_html = []
        for m in moments:
            mst = float(m.get("start", 0.0))
            lbl = m.get("why") or m.get("label") or f"Mốc {mst:.1f}s"
            buttons_html.append(
                f'<button class="time-btn" onclick="seekTo({e(_js(vid))}, {mst})">{e(str(lbl))} ({mst:.1f}s)</button>')

        if embeddable:
            player_elem = (
                f'<div class="video-container">'
                f'<iframe id="player_{e(vid)}" src="https://www.youtube.com/embed/{e(vid)}?enablejsapi=1" '
                f'allow="autoplay; encrypted-media" allowfullscreen></iframe></div>')
            time_ctrl = (
                f'<span>Mốc hiện tại của player: <b id="cur_{e(vid)}">0.0</b>s '
                f'(<span id="cur_fmt_{e(vid)}">0:00.0</span>)</span>')
            player_range_btns = (
                f'<button type="button" class="mini-btn" onclick="setInFromPlayer({e(_js(vid))})">⏱ Lấy điểm đầu</button>'
                f'<button type="button" class="mini-btn" onclick="setOutFromPlayer({e(_js(vid))})">⏱ Lấy điểm cuối</button>'
                f'<button type="button" class="mini-btn" onclick="previewRange({e(_js(vid))})">▶ Xem thử đoạn</button>'
            )
        else:
            player_elem = (
                f'<div class="no-embed"><p>Video này chặn nhúng trực tiếp.</p>'
                f'<a href="{e(url)}&t={int(first_start)}s" target="_blank" rel="noopener noreferrer">'
                f'Xem trên YouTube tại {first_start:.1f}s ↗</a></div>')
            time_ctrl = (f'<label>Mốc bắt đầu (giây, xem trên YouTube rồi nhập): '
                         f'<input type="number" step="0.5" min="0" id="start_{e(vid)}" value="{first_start}"></label>')
            player_range_btns = ""

        range_ctrl = f"""
          <div class="range-bar">
            <div class="range-inputs">
              <label>Từ (Bắt đầu):
                <input type="text" class="time-text" id="in_{e(vid)}" placeholder="VD: 0:05 hoặc 5" oninput="updateRangeInfo({e(_js(vid))})">
              </label>
              <label>Đến (Kết thúc):
                <input type="text" class="time-text" id="out_{e(vid)}" placeholder="VD: 0:15 (trống = tự động)" oninput="updateRangeInfo({e(_js(vid))})">
              </label>
              {player_range_btns}
            </div>
            <div class="range-hint" id="range_info_{e(vid)}">
              Mẹo: Nhập khoảng <b>Từ</b> → <b>Đến</b> (VD: <code>0:05</code> đến <code>0:15</code>) để lấy trọn đoạn đó và tự động co giãn tốc độ vừa khít với Beat {e(beat)}. Để trống ô <b>Đến</b> nếu chỉ muốn lấy từ mốc bắt đầu.
            </div>
          </div>"""

        cards_html.append(f"""
        <div class="card" id="card_{e(vid)}">
          <h3>{i}. {e(title)}</h3>
          <p class="channel">{e(channel)} | <a href="{e(url)}&t={int(first_start)}s" target="_blank" rel="noopener noreferrer">Xem trực tiếp trên YouTube tại mốc t ↗</a></p>
          {player_elem}
          <div class="timestamps">Gợi ý: {' '.join(buttons_html)}</div>
          {range_ctrl}
          <div class="controls">
            {time_ctrl}
            <button class="btn pick-btn" onclick="pickMoment({e(_js(vid))})">✓ Dùng từ đây cho Beat {e(beat)}</button>
          </div>
        </div>""")
    if not cards_html:
        err_msg = f" ({e(search_error)})" if search_error else ""
        cards_html.append(f"""
        <div class="empty-state">
          <p>Không tìm thấy video nào phù hợp với từ khóa "{e(query)}"{err_msg}.</p>
          <p>Vui lòng thử lại với từ khóa chi tiết hơn.</p>
        </div>""")

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Chọn khoảnh khắc cho Beat {e(beat)} - {e(project)}</title>
  <style>
    body {{ background: #0f1115; color: #e1e4e8; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 24px; max-width: 900px; margin: 0 auto; }}
    h1 {{ color: #58a6ff; font-size: 24px; margin-bottom: 8px; }}
    .query-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px 16px; margin-bottom: 16px; }}
    .query-box input[type=text] {{ background: #0d1117; color: white; border: 1px solid #30363d; padding: 6px 10px; border-radius: 4px; width: 60%; font-size: 14px; }}
    .dur {{ margin-bottom: 16px; color: #8b949e; }} .dur b {{ color: #58a6ff; }}
    .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
    .card h3 {{ margin: 0 0 6px 0; font-size: 18px; }}
    .channel {{ color: #8b949e; font-size: 13px; margin: 0 0 12px 0; }}
    .channel a {{ color: #58a6ff; text-decoration: none; }}
    .channel a:hover {{ text-decoration: underline; }}
    .video-container {{ position: relative; width: 100%; aspect-ratio: 16/9; margin-bottom: 12px; }}
    iframe {{ width: 100%; height: 100%; border: 0; border-radius: 6px; }}
    .no-embed {{ background: #21262d; padding: 20px; text-align: center; border-radius: 6px; margin-bottom: 12px; }}
    .no-embed a {{ color: #58a6ff; font-weight: 500; text-decoration: none; }}
    .timestamps {{ margin-bottom: 12px; }}
    .time-btn {{ background: #21262d; color: #58a6ff; border: 1px solid #30363d; padding: 6px 10px; border-radius: 4px; cursor: pointer; margin-right: 6px; margin-bottom: 6px; font-size: 13px; }}
    .time-btn:hover {{ background: #30363d; }}
    .range-bar {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px 12px; margin-bottom: 12px; }}
    .range-inputs {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 13px; }}
    .time-text {{ background: #161b22; color: #f0f6fc; border: 1px solid #30363d; padding: 5px 8px; border-radius: 4px; width: 135px; font-size: 13px; margin-left: 4px; }}
    .mini-btn {{ background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 5px 9px; border-radius: 4px; cursor: pointer; font-size: 12px; }}
    .mini-btn:hover {{ background: #30363d; color: #58a6ff; }}
    .range-hint {{ color: #8b949e; font-size: 12px; line-height: 1.4; }}
    .range-hint b.ok {{ color: #3fb950; }}
    .range-hint b.warn {{ color: #f85149; }}
    .controls {{ display: flex; align-items: center; justify-content: space-between; border-top: 1px solid #21262d; padding-top: 12px; }}
    input[type=number] {{ background: #0d1117; color: white; border: 1px solid #30363d; padding: 6px 10px; border-radius: 4px; width: 80px; font-size: 14px; }}
    .btn {{ background: #238636; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }}
    .btn:hover {{ background: #2ea043; }}
    .empty-state {{ background: #161b22; border: 1px dashed #30363d; border-radius: 8px; padding: 32px; text-align: center; color: #8b949e; }}
    #pick-box {{ position: sticky; bottom: 12px; background: #161b22; border: 1px solid #238636; border-radius: 8px; padding: 12px 16px; display: none; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}
    #pick-box.err {{ border-color: #da3633; }}
    #pick-preview {{ height: 320px; border-radius: 6px; margin-top: 8px; display: none; }}
  </style>
</head>
<body>
  <h1>Chọn khoảnh khắc cho Beat {e(beat)}</h1>
  <form class="query-box" method="get" action="/moments_review">
    <input type="hidden" name="project" value="{e(project)}"><input type="hidden" name="beat" value="{e(beat)}">
    <strong>Từ khóa tìm kiếm:</strong> <input type="text" name="q" value="{e(query)}"> <button class="btn" type="submit">Tìm lại</button>
  </form>
  <div class="dur">Thời lượng beat (TTS): <b id="beat-dur">đang tính…</b></div>

  <div id="cards">{''.join(cards_html)}</div>
  <div id="pick-box"><div id="pick-msg"></div><video id="pick-preview" controls muted playsinline></video></div>

  <script>
    const PROJECT = {_js(project)};
    const BEAT = {_js(beat)};
    window.players = {{}};
    window.beatDurationSec = null;
    window.previewStopTimers = {{}};

    function fmtTime(sec) {{
      if (typeof sec !== 'number' || isNaN(sec) || sec < 0) return '0:00.0';
      const m = Math.floor(sec / 60);
      const s = (sec - m * 60).toFixed(1).padStart(4, '0');
      return m + ':' + s;
    }}

    function parseTimeStr(raw) {{
      if (raw === null || raw === undefined) return null;
      let s = String(raw).trim();
      if (!s) return null;
      s = s.replace(/s$/i, '').trim();
      // Normalize accidental 'o'/'O' typed instead of '0' (e.g. "o:15" -> "0:15", "0:o5" -> "0:05")
      s = s.replace(/[oO](?=[:\\d])/g, '0').replace(/(?<=[:\\d])[oO]/g, '0');
      if (s.includes(':')) {{
        const parts = s.split(':').map(p => p.trim());
        if (parts.some(p => p === '' || isNaN(Number(p)))) return NaN;
        let total = 0;
        for (const p of parts) total = total * 60 + parseFloat(p);
        return Math.round(total * 10) / 10;
      }}
      const v = parseFloat(s);
      return isNaN(v) ? NaN : Math.round(v * 10) / 10;
    }}

    // The API calls this global the moment it has loaded — it must already exist (so this script comes
    // BEFORE the iframe_api tag below) and the iframes must already be in the DOM.
    function initPlayers() {{
      document.querySelectorAll('iframe[id^="player_"]').forEach(iframe => {{
        if (window.players[iframe.id.replace('player_', '')]) return;
        const vid = iframe.id.replace('player_', '');
        window.players[vid] = new YT.Player(iframe.id, {{ events: {{ 'onReady': () => console.log('YT Player ready: ' + vid) }} }});
      }});
    }}
    function onYouTubeIframeAPIReady() {{
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initPlayers);
      else initPlayers();
    }}
    setInterval(() => {{
        for (const [vid, p] of Object.entries(window.players)) {{
          const el = document.getElementById('cur_' + vid);
          const fmtEl = document.getElementById('cur_fmt_' + vid);
          if (el && p && typeof p.getCurrentTime === 'function') {{
            try {{
              const cur = Math.round(p.getCurrentTime() * 10) / 10;
              el.textContent = cur.toFixed(1);
              if (fmtEl) fmtEl.textContent = fmtTime(cur);
            }} catch (err) {{}}
          }}
        }}
      }}, 400);

    function getPlayerTime(vid) {{
      const p = window.players[vid];
      if (p && typeof p.getCurrentTime === 'function') {{
        try {{
          const cur = p.getCurrentTime();
          if (typeof cur === 'number' && !isNaN(cur)) return Math.round(cur * 10) / 10;
        }} catch (err) {{}}
      }}
      return null;
    }}

    function updateRangeInfo(vid) {{
      const infoEl = document.getElementById('range_info_' + vid);
      if (!infoEl) return;
      const inRaw = (document.getElementById('in_' + vid) || {{}}).value || '';
      const outRaw = (document.getElementById('out_' + vid) || {{}}).value || '';
      const inVal = parseTimeStr(inRaw);
      const outVal = parseTimeStr(outRaw);
      if (inVal !== null && isNaN(inVal)) {{
        infoEl.innerHTML = '<b class="warn">⚠ Mốc bắt đầu không hợp lệ (hãy nhập như 0:05 hoặc 5).</b>';
        return;
      }}
      if (outVal !== null && isNaN(outVal)) {{
        infoEl.innerHTML = '<b class="warn">⚠ Mốc kết thúc không hợp lệ (hãy nhập như 0:15 hoặc 15).</b>';
        return;
      }}
      if (outVal !== null) {{
        const st = (inVal !== null) ? inVal : (getPlayerTime(vid) || 0.0);
        if (outVal <= st) {{
          infoEl.innerHTML = '<b class="warn">⚠ Mốc kết thúc (' + fmtTime(outVal) + ') phải lớn hơn mốc bắt đầu (' + fmtTime(st) + ').</b>';
          return;
        }}
        const span = Math.round((outVal - st) * 10) / 10;
        let speedTxt = 'sẽ tự co giãn tốc độ để vừa khít thời lượng beat';
        if (window.beatDurationSec && window.beatDurationSec > 0) {{
          const spd = (span / window.beatDurationSec).toFixed(2);
          speedTxt = 'tốc độ ~' + spd + 'x để khớp chính xác beat ' + window.beatDurationSec.toFixed(2) + 's';
        }}
        infoEl.innerHTML = '<b class="ok">✓ Đoạn chọn: ' + fmtTime(st) + ' → ' + fmtTime(outVal) + ' (dài ' + span.toFixed(1) + 's — ' + speedTxt + ')</b>';
      }} else if (inVal !== null) {{
        infoEl.innerHTML = 'Bắt đầu từ <b>' + fmtTime(inVal) + '</b> (' + inVal.toFixed(1) + 's) — để trống ô Đến sẽ tự lấy độ dài theo thời lượng beat.';
      }} else {{
        infoEl.innerHTML = 'Mẹo: Nhập khoảng <b>Từ</b> → <b>Đến</b> (VD: <code>0:05</code> đến <code>0:15</code>) để lấy trọn đoạn đó và tự động co giãn tốc độ vừa khít với Beat ' + BEAT + '.';
      }}
    }}

    function setInFromPlayer(vid) {{
      const cur = getPlayerTime(vid);
      if (cur !== null) {{
        const inEl = document.getElementById('in_' + vid);
        if (inEl) inEl.value = fmtTime(cur);
        updateRangeInfo(vid);
      }}
    }}

    function setOutFromPlayer(vid) {{
      const cur = getPlayerTime(vid);
      if (cur !== null) {{
        const inEl = document.getElementById('in_' + vid);
        if (inEl && !inEl.value.trim()) inEl.value = '0:00.0';
        const outEl = document.getElementById('out_' + vid);
        if (outEl) outEl.value = fmtTime(cur);
        updateRangeInfo(vid);
      }}
    }}

    function previewRange(vid) {{
      const p = window.players[vid];
      if (!p || typeof p.seekTo !== 'function') return;
      const inVal = parseTimeStr((document.getElementById('in_' + vid) || {{}}).value);
      const outVal = parseTimeStr((document.getElementById('out_' + vid) || {{}}).value);
      const st = (inVal !== null && !isNaN(inVal)) ? inVal : (getPlayerTime(vid) || 0.0);
      if (window.previewStopTimers[vid]) clearInterval(window.previewStopTimers[vid]);
      p.seekTo(st, true);
      if (typeof p.playVideo === 'function') p.playVideo();
      if (outVal !== null && !isNaN(outVal) && outVal > st) {{
        window.previewStopTimers[vid] = setInterval(() => {{
          const cur = getPlayerTime(vid);
          if (cur !== null && cur >= outVal) {{
            if (typeof p.pauseVideo === 'function') p.pauseVideo();
            clearInterval(window.previewStopTimers[vid]);
          }}
        }}, 150);
      }}
    }}

    function seekTo(vid, seconds) {{
      const input = document.getElementById('start_' + vid);
      if (input) input.value = seconds;
      const inEl = document.getElementById('in_' + vid);
      if (inEl) {{ inEl.value = fmtTime(seconds); updateRangeInfo(vid); }}
      const p = window.players[vid];
      if (p && typeof p.seekTo === 'function') {{
        p.seekTo(seconds, true);
        if (typeof p.playVideo === 'function') p.playVideo();
      }}
    }}

    function showPick(text, isErr) {{
      const box = document.getElementById('pick-box');
      box.style.display = 'block';
      box.className = isErr ? 'err' : '';
      document.getElementById('pick-msg').textContent = text;
    }}

    let pollTimer = null;
    async function pollPick() {{
      try {{
        const r = await fetch('/api/pick_status?project=' + encodeURIComponent(PROJECT) + '&beat=' + encodeURIComponent(BEAT));
        const j = await r.json();
        if (j.state === 'ready') {{
          showPick(j.message || '✓ Đã chọn clip cho beat.', false);
          const v = document.getElementById('pick-preview');
          v.src = '/api/clip_preview?project=' + encodeURIComponent(PROJECT) + '&beat=' + encodeURIComponent(BEAT) + '&t=' + Date.now();
          v.style.display = 'block';
          return;
        }}
        if (j.state === 'error') {{ showPick(j.message || 'Lỗi', true); return; }}
        showPick(j.message || j.state, false);
      }} catch (err) {{ showPick('Mất kết nối khi hỏi trạng thái: ' + err, true); return; }}
      pollTimer = setTimeout(pollPick, 1000);
    }}

    async function pickMoment(vid) {{
      const inEl = document.getElementById('in_' + vid);
      const outEl = document.getElementById('out_' + vid);
      const inParsed = parseTimeStr(inEl ? inEl.value : '');
      const outParsed = parseTimeStr(outEl ? outEl.value : '');
      if (inParsed !== null && isNaN(inParsed)) {{
        showPick('Mốc bắt đầu (Từ) không hợp lệ. Hãy nhập dạng 0:05 hoặc số giây như 5.', true);
        return;
      }}
      if (outParsed !== null && isNaN(outParsed)) {{
        showPick('Mốc kết thúc (Đến) không hợp lệ. Hãy nhập dạng 0:15 hoặc số giây như 15.', true);
        return;
      }}

      let startVal = inParsed;
      if (startVal === null) {{
        startVal = getPlayerTime(vid);
      }}
      if (startVal === null) {{
        const input = document.getElementById('start_' + vid);
        startVal = parseFloat((input && input.value) || "0");
      }}

      const payload = {{ project: PROJECT, beat: BEAT, video_id: vid, start: startVal }};
      if (outParsed !== null) {{
        if (outParsed <= startVal) {{
          showPick('Mốc kết thúc (' + fmtTime(outParsed) + ') phải lớn hơn mốc bắt đầu (' + fmtTime(startVal) + ')!', true);
          return;
        }}
        payload.end = outParsed;
        showPick('Đang gửi đoạn ' + fmtTime(startVal) + ' → ' + fmtTime(outParsed) + ' (' + (outParsed - startVal).toFixed(1) + 's, tự động co giãn khớp beat)…', false);
      }} else {{
        showPick('Đang gửi lựa chọn (mốc ' + startVal.toFixed(1) + 's)…', false);
      }}

      try {{
        const resp = await fetch('/api/pick_moment', {{
          method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload)
        }});
        if (!resp.ok) {{ showPick('Lỗi gửi lựa chọn: ' + resp.status + ' ' + (await resp.text()), true); return; }}
        clearTimeout(pollTimer);
        pollPick();
      }} catch (err) {{ showPick('Lỗi kết nối: ' + err, true); }}
    }}

    async function pollDuration() {{
      try {{
        const r = await fetch('/api/beat_tts_status?project=' + encodeURIComponent(PROJECT));
        const j = await r.json();
        const d = (j.beat_durations || {{}})[BEAT];
        if (d) window.beatDurationSec = d;
        document.getElementById('beat-dur').textContent = d ? d.toFixed(2) + ' s' : 'đang tính… (TTS chưa tới đoạn này)';
        if (d) return;
      }} catch (err) {{}}
      setTimeout(pollDuration, 2000);
    }}
    fetch('/api/bump_beat_tts', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ project: PROJECT, beat: BEAT }}) }}).catch(() => {{}});
    pollDuration();
  </script>
  <script src="https://www.youtube.com/iframe_api"></script>
</body>
</html>"""


# ─── API ──────────────────────────────────────────────────────────────────────────────────────

@router.post("/api/pick_moment")
async def pick_moment(req: PickMomentRequest, sync: bool = False) -> dict[str, Any]:
    """Start the pick job for (project, beat). Returns at once with the job's state; the page polls
    /api/pick_status. `sync=true` runs the whole job before answering (tests, scripts)."""
    project_root = get_safe_project_dir(req.project)
    beat = validate_beat_key(req.beat)
    video_id = validate_video_id(req.video_id)
    if not math.isfinite(req.start) or not 0 <= req.start <= _MAX_START:
        raise HTTPException(status_code=400, detail="Invalid start time")
    end_val: float | None = None
    if req.end is not None:
        if not math.isfinite(req.end) or req.end <= req.start or req.end > _MAX_START:
            raise HTTPException(status_code=400, detail="Invalid end time (must be greater than start)")
        end_val = float(req.end)

    gen = _set_pick(req.project, beat, state="queued", message="Đang xếp hàng…",
                    video_id=video_id, start=req.start, end=end_val)
    args = (req.project, project_root, beat, video_id, float(req.start), gen, end_val)
    if sync:
        entry = await run_in_threadpool(_run_pick_job, *args)
        st = get_pick_state(req.project, beat)
        return {"status": "ok" if entry else "error", "state": st.get("state"), "data": entry or st}
    threading.Thread(target=_run_pick_job, args=args, daemon=True,
                     name=f"pick-{req.project}-{beat}").start()
    return {"status": "accepted", "state": "queued", "beat": beat,
            "beat_duration": tts_status.beat_duration(project_root, beat)}


@router.get("/api/pick_status")
async def pick_status(project: str = "", beat: str = "") -> dict[str, Any]:
    get_safe_project_dir(project)
    return get_pick_state(project, validate_beat_key(beat))


@router.get("/api/clip_preview")
async def clip_preview(project: str = "", beat: str = ""):
    """The 9:16 preview mp4 of the beat's picked clip."""
    root = get_safe_project_dir(project)
    beat = validate_beat_key(beat)
    manifest_clips = clips.read_manifest_doc(root)["clips"]
    for c in manifest_clips:
        if isinstance(c, dict) and str(c.get("beat")) == beat:
            rel = c.get("preview_file") or c.get("file")
            if rel:
                target = (root / str(rel)).resolve()
                if target.is_relative_to(root) and target.is_file():
                    return FileResponse(target, media_type="video/mp4")
    # Legacy fallback: if beat is a bookend fragment ("<sid>:0"), check for "intro"/"outro" entry
    if beat.endswith(":0"):
        narr_p = root / "narration.json"
        if narr_p.is_file():
            try:
                narr = json.loads(narr_p.read_text(encoding="utf-8"))
                scenes = narr.get("scenes") or []
                sid_str = beat.partition(":")[0]
                intro = next((s for s in scenes if s.get("is_intro")), None)
                if intro and str(intro.get("scene_id")) == sid_str:
                    for c in manifest_clips:
                        if isinstance(c, dict) and str(c.get("beat")) == "intro":
                            rel = c.get("preview_file") or c.get("file")
                            if rel:
                                target = (root / str(rel)).resolve()
                                if target.is_relative_to(root) and target.is_file():
                                    return FileResponse(target, media_type="video/mp4")
                outro = next((s for s in scenes if s.get("is_outro")), None)
                if outro and str(outro.get("scene_id")) == sid_str:
                    for c in manifest_clips:
                        if isinstance(c, dict) and str(c.get("beat")) == "outro":
                            rel = c.get("preview_file") or c.get("file")
                            if rel:
                                target = (root / str(rel)).resolve()
                                if target.is_relative_to(root) and target.is_file():
                                    return FileResponse(target, media_type="video/mp4")
            except Exception:
                pass
    raise HTTPException(status_code=404, detail="No preview for this beat")


@router.get("/api/beat_tts_status")
async def beat_tts_status(project: str = "") -> dict[str, Any]:
    """Background TTS progress and beat durations (review/tts_status.json — the one status path)."""
    if not project:
        return tts_status.empty_status()
    try:
        return tts_status.read_status(get_safe_project_dir(project))
    except HTTPException:
        return tts_status.empty_status()


@router.post("/api/bump_beat_tts")
async def bump_beat_tts(payload: dict[str, str]) -> dict[str, Any]:
    """Bump background TTS priority for a beat."""
    project = payload.get("project", "")
    beat = payload.get("beat", "")
    if project and beat:
        try:
            get_safe_project_dir(project)
            validate_beat_key(beat)
            from stages.stage_4.background_tts import bump_priority_beat
            await run_in_threadpool(bump_priority_beat, project, beat)
        except Exception as exc:
            logger.warning("bump_priority_beat failed: %s", exc)
    return {"status": "ok"}
