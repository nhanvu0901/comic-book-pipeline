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

class ClipPickError(RuntimeError):
    """A pick that cannot succeed, with the reason shown to Master."""


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


def process_clip_pick(
    project_root: Path,
    beat: str,
    video_id: str,
    start: float,
    beat_duration: float,
    url: str | None = None,
    *,
    on_state: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Download the section for the picked moment, render its 9:16 preview, and put it in the
    manifest with a rebased timeline. Raises ClipPickError (with the reason) when the footage
    after `start` cannot fill the beat. Returns the manifest entry."""
    say = on_state or (lambda _s: None)
    clips_dir = project_root / "review" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    video_url = f"https://www.youtube.com/watch?v={video_id}"     # rebuilt: a given url is never fetched

    say("downloading")
    section_path = clip_fetch.fetch_clip_section(video_url, clips_dir, start=start, beat_duration=beat_duration)

    say("rendering")
    preview_path = clips_dir / f"preview_{beat.replace(':', '_')}.mp4"
    try:
        clips.render_clip_preview(section_path, start=0.0, beat_duration=beat_duration, out_path=preview_path)
    except clips.ClipTooShort as exc:
        raise ClipPickError(
            f"Từ mốc {start:.1f}s video chỉ còn quá ít cảnh để lấp beat {beat} ({beat_duration:.1f}s): {exc}. "
            f"Hãy chọn mốc sớm hơn.") from exc
    except Exception as exc:
        raise ClipPickError(f"Không dựng được preview 9:16: {exc}") from exc

    def rel(p: Path) -> str:
        return p.relative_to(project_root).as_posix()

    entry = {
        "id": video_id,
        "file": rel(section_path),
        "beat": beat,
        "start": 0.0,                 # t=0 of the section file IS the picked moment
        "source_url": video_url,      # (no "end": open-ended, Stage 5's fit may extend into the margin)
        "source_start": start,
        "beat_duration": beat_duration,
        "preview_file": rel(preview_path),
    }
    clips.upsert_beat_clip(project_root, entry)
    return entry


def _run_pick_job(project: str, project_root: Path, beat: str, video_id: str, start: float, gen: int) -> dict | None:
    def state(s: str, **kw: Any) -> bool:
        return _set_pick(project, beat, gen, state=s, **kw) != -1

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
        entry = process_clip_pick(project_root, beat, video_id, start, beat_dur,
                                  on_state=lambda s: state(s, message={"downloading": "Đang tải đoạn clip…",
                                                                      "rendering": "Đang dựng preview 9:16…"}.get(s, s)))
        if not state("ready", entry=entry, message="✓ Đã chọn clip cho beat."):
            return None
        _broadcast_moment_picked({"project": project, "state": "ready", **entry})
        return entry
    except ClipPickError as exc:
        state("error", message=str(exc))
    except Exception as exc:
        logger.warning("clip pick failed for %s/%s: %s", project, beat, exc, exc_info=True)
        state("error", message=f"Lỗi khi tải/dựng clip: {exc}")
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
            time_ctrl = (f'<span>Mốc hiện tại của player: <b id="cur_{e(vid)}">0.0</b>s</span>')
        else:
            player_elem = (
                f'<div class="no-embed"><p>Video này chặn nhúng trực tiếp.</p>'
                f'<a href="{e(url)}&t={int(first_start)}s" target="_blank" rel="noopener noreferrer">'
                f'Xem trên YouTube tại {first_start:.1f}s ↗</a></div>')
            time_ctrl = (f'<label>Mốc bắt đầu (giây, xem trên YouTube rồi nhập): '
                         f'<input type="number" step="0.5" min="0" id="start_{e(vid)}" value="{first_start}"></label>')

        cards_html.append(f"""
        <div class="card" id="card_{e(vid)}">
          <h3>{i}. {e(title)}</h3>
          <p class="channel">{e(channel)} | <a href="{e(url)}&t={int(first_start)}s" target="_blank" rel="noopener noreferrer">Xem trực tiếp trên YouTube tại mốc t ↗</a></p>
          {player_elem}
          <div class="timestamps">Gợi ý: {' '.join(buttons_html)}</div>
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
          if (el && p && typeof p.getCurrentTime === 'function') {{
            try {{ el.textContent = (Math.round(p.getCurrentTime() * 10) / 10).toFixed(1); }} catch (err) {{}}
          }}
        }}
      }}, 400);

    function seekTo(vid, seconds) {{
      const input = document.getElementById('start_' + vid);
      if (input) input.value = seconds;
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
      // The player's own clock is the truth (spec 4.2); the number box only exists for videos that cannot be embedded.
      let startVal = null;
      const p = window.players[vid];
      if (p && typeof p.getCurrentTime === 'function') {{
        try {{
          const cur = p.getCurrentTime();
          if (typeof cur === 'number' && !isNaN(cur)) startVal = Math.round(cur * 10) / 10;
        }} catch (err) {{ console.warn('Could not read getCurrentTime():', err); }}
      }}
      if (startVal === null) {{
        const input = document.getElementById('start_' + vid);
        startVal = parseFloat((input && input.value) || "0");
      }}
      showPick('Đang gửi lựa chọn (mốc ' + startVal.toFixed(1) + 's)…', false);
      try {{
        const resp = await fetch('/api/pick_moment', {{
          method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ project: PROJECT, beat: BEAT, video_id: vid, start: startVal }})
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

    gen = _set_pick(req.project, beat, state="queued", message="Đang xếp hàng…", video_id=video_id, start=req.start)
    args = (req.project, project_root, beat, video_id, float(req.start), gen)
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
    for c in clips.read_manifest_doc(root)["clips"]:
        if isinstance(c, dict) and str(c.get("beat")) == beat and c.get("preview_file"):
            target = (root / str(c["preview_file"])).resolve()
            if target.is_relative_to(root) and target.is_file():
                return FileResponse(target, media_type="video/mp4")
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
