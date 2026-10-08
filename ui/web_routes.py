"""FastAPI web routes for Q&A moment review and selection."""
from __future__ import annotations

import json
import logging
import re
import threading
from html import escape as e
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import config
from stages import clip_fetch
from stages.stage_5 import clips

logger = logging.getLogger(__name__)

router = APIRouter()

_listeners: list[Callable[[dict[str, Any]], None]] = []
_listeners_lock = threading.Lock()

_BEAT_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-\.:]+$")


def get_safe_project_dir(project: str, allow_missing: bool = False) -> Path:
    """Validate project path to prevent path traversal and ensure it resides within PROJECTS_ROOT."""
    if not project or not project.strip():
        raise HTTPException(status_code=400, detail="Missing project name")
    try:
        root_resolved = config.PROJECTS_ROOT.resolve()
        target = (config.PROJECTS_ROOT / project).resolve()
        if not target.is_relative_to(root_resolved):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        if not allow_missing and not target.is_dir():
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
        return target
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid project path: {exc}")


def validate_beat_key(beat: str) -> str:
    """Validate beat key against regex to prevent arbitrary path injection."""
    if not beat or not _BEAT_KEY_RE.match(beat) or len(beat) > 64:
        raise HTTPException(status_code=400, detail=f"Invalid beat key '{beat}'")
    return beat


def add_moment_picked_listener(cb: Callable[[dict[str, Any]], None]) -> None:
    """Register a callback to be notified whenever a user picks a moment from the web review tab."""
    with _listeners_lock:
        if cb not in _listeners:
            _listeners.append(cb)


def remove_moment_picked_listener(cb: Callable[[dict[str, Any]], None]) -> None:
    """Remove a registered callback."""
    with _listeners_lock:
        if cb in _listeners:
            _listeners.remove(cb)


def clear_moment_picked_listeners() -> None:
    """Clear all registered callbacks."""
    with _listeners_lock:
        _listeners.clear()


def _broadcast_moment_picked(payload: dict[str, Any]) -> None:
    with _listeners_lock:
        current_listeners = list(_listeners)
    for listener in current_listeners:
        try:
            listener(payload)
        except Exception:
            pass


class PickMomentRequest(BaseModel):
    project: str
    beat: str
    video_id: str
    start: float
    duration: float | None = None
    url: str | None = None


def process_clip_pick(
    project_root: Path,
    beat: str,
    video_id: str,
    start: float,
    beat_duration: float,
    url: str | None = None,
) -> dict[str, Any]:
    """Download section for the picked moment, render vertical preview, and update manifest with rebased timeline."""
    clips_dir = project_root / "review" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    video_url = url or f"https://www.youtube.com/watch?v={video_id}"

    # 1. Fetch section
    section_path = clip_fetch.fetch_clip_section(
        video_url,
        clips_dir,
        start=start,
        beat_duration=beat_duration,
    )

    # 2. Render vertical 9:16 preview
    preview_path = clips_dir / f"preview_{beat.replace(':', '_')}.mp4"
    try:
        clips.render_clip_preview(
            section_path,
            start=0.0,
            beat_duration=beat_duration,
            out_path=preview_path,
        )
    except Exception as exc:
        logger.warning(f"Failed to render clip preview: {exc}")

    # 3. Update manifest with rebased start (0.0)
    manifest_path = clips_dir / "clips.json"
    manifest_data = {"clips": []}
    if manifest_path.exists():
        try:
            manifest_data = json.loads(manifest_path.read_text("utf-8"))
        except Exception:
            manifest_data = {"clips": []}

    rel_file = str(section_path.relative_to(project_root)).replace("\\", "/")
    clip_entry = {
        "id": video_id,
        "file": rel_file,
        "beat": beat,
        "start": 0.0,  # Rebased to 0.0 since section starts at picked source timestamp
        "end": round(beat_duration, 3),
        "source_url": video_url,
        "source_start": start,
        "preview_file": str(preview_path.relative_to(project_root)).replace("\\", "/") if preview_path.exists() else None,
    }

    updated = False
    for item in manifest_data.get("clips", []):
        if str(item.get("beat")) == beat:
            item.update(clip_entry)
            updated = True
            break
    if not updated:
        manifest_data.setdefault("clips", []).append(clip_entry)

    manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), "utf-8")
    _broadcast_moment_picked(clip_entry)
    return clip_entry


@router.get("/moments_review", response_class=HTMLResponse)
async def moments_review(project: str = "", beat: str = "", q: str = "") -> str:
    """Web review tab embedding YouTube players for picking a clip moment."""
    project_root = None
    if project:
        project_root = get_safe_project_dir(project, allow_missing=True)
    if beat:
        validate_beat_key(beat)

    query = q.strip()

    # If no query supplied, read drawable_moment / beat text from project narration
    if not query and project_root and project_root.exists():
        narration_path = project_root / "narration.json"
        if narration_path.exists():
            try:
                narr = json.loads(narration_path.read_text("utf-8"))
                for sc in narr.get("scenes", []):
                    for b in sc.get("visual_beats", []):
                        if str(b.get("beat_id")) == beat:
                            query = str(b.get("text", "")).strip()
                            break
            except Exception:
                pass

    if not query:
        query = f"Action scene {beat}" if beat else "Action clip"

    # Fetch candidate shortlist or reuse cached results
    candidates: list[dict[str, Any]] = []
    cache_file = None
    search_error = ""

    if project_root and project_root.exists():
        clips_dir = project_root / "review" / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        safe_beat = beat.replace(":", "_") if beat else "default"
        # Include query hash in cache filename to prevent cross-query poisoning
        import hashlib
        q_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
        cache_file = clips_dir / f"search_{safe_beat}_{q_hash}.json"
        if cache_file.exists():
            try:
                candidates = json.loads(cache_file.read_text("utf-8"))
            except Exception:
                candidates = []

    if not candidates:
        try:
            # Non-blocking search executed in worker threadpool (MAJOR M4)
            candidates = await run_in_threadpool(clip_fetch.moment_search, query, limit=8)
            if cache_file and candidates:
                cache_file.write_text(json.dumps(candidates, indent=2, ensure_ascii=False), "utf-8")
        except Exception as exc:
            search_error = str(exc)
            candidates = []

    # Generate cards HTML
    cards_html = []
    if candidates:
        for i, c in enumerate(candidates, 1):
            vid = c.get("id") or "video"
            title = c.get("title") or "Video Clip"
            channel = c.get("channel") or ""
            url = c.get("url") or f"https://www.youtube.com/watch?v={vid}"
            embeddable = c.get("embeddable", True)
            moments = c.get("moments") or [{"start": 0.0, "end": 5.0, "label": "Start"}]
            first_start = float(moments[0].get("start", 0.0))

            buttons_html = []
            for m_idx, m in enumerate(moments):
                mst = float(m.get("start", 0.0))
                lbl = m.get("label") or f"Timestamp {mst:.1f}s"
                buttons_html.append(
                    f'<button class="time-btn" onclick="seekTo(\'{e(vid)}\', {mst})">{e(lbl)} ({mst:.1f}s)</button>'
                )

            if embeddable:
                player_elem = (
                    f'<div class="video-container">'
                    f'<iframe id="player_{e(vid)}" src="https://www.youtube.com/embed/{e(vid)}?enablejsapi=1" '
                    f'allow="autoplay; encrypted-media" allowfullscreen></iframe>'
                    f'</div>'
                )
            else:
                player_elem = (
                    f'<div class="no-embed">'
                    f'<p>Video này chặn nhúng trực tiếp.</p>'
                    f'<a href="{e(url)}&t={int(first_start)}s" target="_blank" rel="noopener noreferrer">Xem trên YouTube tại {first_start:.1f}s ↗</a>'
                    f'</div>'
                )

            cards_html.append(f"""
            <div class="card" id="card_{e(vid)}">
              <h3>{i}. {e(title)}</h3>
              <p class="channel">{e(channel)} | <a href="{e(url)}&t={int(first_start)}s" target="_blank" rel="noopener noreferrer">Xem trên YouTube ↗</a></p>
              {player_elem}
              <div class="timestamps">
                Gợi ý: {' '.join(buttons_html)}
              </div>
              <div class="controls">
                <label>Mốc bắt đầu (giây): <input type="number" step="0.5" id="start_{e(vid)}" value="{first_start}"></label>
                <button class="btn pick-btn" onclick="pickMoment('{e(vid)}')">✓ Dùng từ đây cho Beat {e(beat)}</button>
              </div>
            </div>
            """)
    else:
        err_msg = f" ({e(search_error)})" if search_error else ""
        cards_html.append(f"""
        <div class="empty-state">
          <p>Không tìm thấy video nào phù hợp với từ khóa "{e(query)}"{err_msg}.</p>
          <p>Vui lòng thử lại với từ khóa chi tiết hơn.</p>
        </div>
        """)

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Chọn khoảnh khắc cho Beat {e(beat)} - {e(project)}</title>
  <style>
    body {{ background: #0f1115; color: #e1e4e8; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 24px; max-width: 900px; margin: 0 auto; }}
    h1 {{ color: #58a6ff; font-size: 24px; margin-bottom: 8px; }}
    .query-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px 16px; margin-bottom: 24px; }}
    .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
    .card h3 {{ margin: 0 0 6px 0; font-size: 18px; }}
    .channel {{ color: #8b949e; font-size: 13px; margin: 0 0 12px 0; }}
    .channel a {{ color: #58a6ff; text-decoration: none; }}
    .channel a:hover {{ text-decoration: underline; }}
    .video-container {{ position: relative; width: 100%; aspect-ratio: 16/9; margin-bottom: 12px; }}
    iframe {{ width: 100%; height: 100%; border: 0; border-radius: 6px; }}
    .no-embed {{ background: #21262d; padding: 20px; text-align: center; border-radius: 6px; margin-bottom: 12px; }}
    .no-embed a {{ color: #58a6ff; font-weight: 500; text-decoration: none; }}
    .no-embed a:hover {{ text-decoration: underline; }}
    .timestamps {{ margin-bottom: 12px; }}
    .time-btn {{ background: #21262d; color: #58a6ff; border: 1px solid #30363d; padding: 6px 10px; border-radius: 4px; cursor: pointer; margin-right: 6px; margin-bottom: 6px; font-size: 13px; }}
    .time-btn:hover {{ background: #30363d; }}
    .controls {{ display: flex; align-items: center; justify-content: space-between; border-top: 1px solid #21262d; padding-top: 12px; }}
    input[type=number] {{ background: #0d1117; color: white; border: 1px solid #30363d; padding: 6px 10px; border-radius: 4px; width: 80px; font-size: 14px; }}
    .btn {{ background: #238636; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }}
    .btn:hover {{ background: #2ea043; }}
    .empty-state {{ background: #161b22; border: 1px dashed #30363d; border-radius: 8px; padding: 32px; text-align: center; color: #8b949e; }}
    .toast {{ position: fixed; bottom: 20px; right: 20px; background: #238636; color: white; padding: 12px 20px; border-radius: 6px; display: none; box-shadow: 0 4px 12px rgba(0,0,0,0.5); z-index: 999; }}
  </style>
  <script src="https://www.youtube.com/iframe_api"></script>
</head>
<body>
  <h1>Chọn khoảnh khắc cho Beat {e(beat)}</h1>
  <div class="query-box">
    <strong>Từ khóa tìm kiếm:</strong> {e(query)}
  </div>

  <div id="cards">
    {''.join(cards_html)}
  </div>

  <div id="toast" class="toast">✓ Đã gửi lựa chọn về Review Gate!</div>

  <script>
    window.players = {{}};

    function onYouTubeIframeAPIReady() {{
      const iframes = document.querySelectorAll('iframe[id^="player_"]');
      iframes.forEach(iframe => {{
        const vid = iframe.id.replace('player_', '');
        window.players[vid] = new YT.Player(iframe.id, {{
          events: {{
            'onReady': () => console.log('YT Player ready: ' + vid)
          }}
        }});
      }});
    }}

    function seekTo(vid, seconds) {{
      const input = document.getElementById('start_' + vid);
      if (input) {{
        input.value = seconds;
      }}
      const p = window.players[vid];
      if (p && typeof p.seekTo === 'function') {{
        p.seekTo(seconds, true);
        if (typeof p.playVideo === 'function') {{
          p.playVideo();
        }}
      }} else {{
        const iframe = document.getElementById('player_' + vid);
        if (iframe && iframe.contentWindow) {{
          iframe.contentWindow.postMessage(JSON.stringify({{'event': 'command', 'func': 'seekTo', 'args': [seconds, true]}}), '*');
          iframe.contentWindow.postMessage(JSON.stringify({{'event': 'command', 'func': 'playVideo', 'args': []}}), '*');
        }}
      }}
    }}

    async function pickMoment(vid) {{
      let startVal = null;
      const p = window.players[vid];
      if (p && typeof p.getCurrentTime === 'function') {{
        try {{
          const cur = p.getCurrentTime();
          if (typeof cur === 'number' && !isNaN(cur)) {{
            startVal = Math.round(cur * 10) / 10;
          }}
        }} catch (err) {{
          console.warn('Could not read getCurrentTime():', err);
        }}
      }}

      if (startVal === null) {{
        const input = document.getElementById('start_' + vid);
        startVal = parseFloat((input && input.value) || "0");
      }}

      const payload = {{
        project: '{e(project)}',
        beat: '{e(beat)}',
        video_id: vid,
        start: startVal
      }};

      try {{
        const resp = await fetch('/api/pick_moment', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload)
        }});
        if (resp.ok) {{
          const toast = document.getElementById('toast');
          toast.style.display = 'block';
          setTimeout(() => {{ toast.style.display = 'none'; }}, 3000);
        }} else {{
          alert('Lỗi gửi lựa chọn: ' + resp.statusText);
        }}
      }} catch (err) {{
        alert('Lỗi kết nối: ' + err);
      }}
    }}
  </script>
</body>
</html>"""
    return html


@router.post("/api/pick_moment")
async def pick_moment(req: PickMomentRequest, sync: bool = False) -> dict[str, Any]:
    """Store the picked moment into project manifest, download section and preview, then broadcast."""
    project_root = get_safe_project_dir(req.project, allow_missing=False)
    beat = validate_beat_key(req.beat)

    # Resolve beat duration: request duration > review/tts_status.json > cache/tts/status.json > default 3.0
    beat_dur = req.duration
    if not beat_dur or beat_dur <= 0:
        for status_p in [
            project_root / "review" / "tts_status.json",
            project_root / "cache" / "tts" / "status.json",
        ]:
            if status_p.exists():
                try:
                    s_data = json.loads(status_p.read_text("utf-8"))
                    durs = s_data.get("beat_durations") or {}
                    if beat in durs:
                        beat_dur = float(durs[beat])
                        break
                except Exception:
                    pass
    if not beat_dur or beat_dur <= 0:
        beat_dur = 3.0

    def _do_process():
        try:
            return process_clip_pick(
                project_root,
                beat=beat,
                video_id=req.video_id,
                start=req.start,
                beat_duration=beat_dur,
                url=req.url,
            )
        except Exception as exc:
            logger.warning("Background clip process failed: %s", exc)
            return None

    if sync:
        clip_entry = _do_process()
    else:
        # Launch background task for fetch & render
        t = threading.Thread(target=_do_process, daemon=True)
        t.start()

        # Update provisional entry in manifest so immediate callers get an entry
        clips_dir = project_root / "review" / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = clips_dir / "clips.json"
        manifest_data = {"clips": []}
        if manifest_path.exists():
            try:
                manifest_data = json.loads(manifest_path.read_text("utf-8"))
            except Exception:
                manifest_data = {"clips": []}

        rel_file = f"review/clips/{req.video_id}.mp4"
        provisional_entry = {
            "id": req.video_id,
            "file": rel_file,
            "beat": beat,
            "start": req.start,
            "end": req.start + beat_dur,
            "source_url": req.url or f"https://www.youtube.com/watch?v={req.video_id}",
            "source_start": req.start,
        }
        updated = False
        for item in manifest_data.get("clips", []):
            if str(item.get("beat")) == beat:
                item.update(provisional_entry)
                updated = True
                break
        if not updated:
            manifest_data.setdefault("clips", []).append(provisional_entry)

        manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), "utf-8")
        clip_entry = provisional_entry

    payload = clip_entry or req.model_dump()
    _broadcast_moment_picked(payload)
    return {"status": "ok", "data": payload}


@router.get("/api/beat_tts_status")
async def beat_tts_status(project: str = "") -> dict[str, Any]:
    """Return background TTS progress and beat duration timings."""
    if not project:
        return {"completed": False, "beat_durations": {}, "scene_durations": {}}

    try:
        project_root = get_safe_project_dir(project, allow_missing=True)
    except HTTPException:
        return {"completed": False, "beat_durations": {}, "scene_durations": {}}

    for status_file in [
        project_root / "review" / "tts_status.json",
        project_root / "cache" / "tts" / "status.json",
    ]:
        if status_file.exists():
            try:
                return json.loads(status_file.read_text("utf-8"))
            except Exception:
                pass
    return {"completed": False, "beat_durations": {}, "scene_durations": {}}


@router.post("/api/bump_beat_tts")
async def bump_beat_tts(payload: dict[str, str]) -> dict[str, Any]:
    """Bump background TTS priority for a beat."""
    project = payload.get("project", "")
    beat = payload.get("beat", "")
    if project and beat:
        try:
            get_safe_project_dir(project, allow_missing=False)
            validate_beat_key(beat)
            from stages.stage_4.background_tts import bump_priority_beat
            bump_priority_beat(project, beat)
        except Exception as exc:
            logger.warning("bump_priority_beat failed: %s", exc)
    return {"status": "ok"}
