"""FastAPI web routes for Q&A moment review and selection."""
from __future__ import annotations

import json
from html import escape as e
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
from stages import clip_fetch
from stages.stage_5 import clips

router = APIRouter()

_listeners: list[Callable[[dict[str, Any]], None]] = []


def add_moment_picked_listener(cb: Callable[[dict[str, Any]], None]) -> None:
    """Register a callback to be notified whenever a user picks a moment from the web review tab."""
    if cb not in _listeners:
        _listeners.append(cb)


def _broadcast_moment_picked(payload: dict[str, Any]) -> None:
    for listener in list(_listeners):
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


@router.get("/moments_review", response_class=HTMLResponse)
async def moments_review(project: str = "", beat: str = "", q: str = "") -> str:
    """Web review tab embedding YouTube players for picking a clip moment."""
    project_root = config.PROJECTS_ROOT / project if project else None
    query = q.strip()

    # If no query supplied, read drawable_moment / beat text from project narration
    if not query and project_root and project_root.exists():
        narration_path = project_root / "narration.json"
        if narration_path.exists():
            try:
                narr = json.loads(narration_path.read_text())
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
    if project_root and project_root.exists():
        clips_dir = project_root / "review" / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        safe_beat = beat.replace(":", "_") if beat else "default"
        cache_file = clips_dir / f"search_{safe_beat}.json"
        if cache_file.exists():
            try:
                candidates = json.loads(cache_file.read_text())
            except Exception:
                candidates = []

    if not candidates:
        try:
            candidates = clip_fetch.moment_search(query, limit=8)
            if cache_file:
                cache_file.write_text(json.dumps(candidates, indent=2, ensure_ascii=False))
        except Exception:
            # Fallback demo candidate when network or API key is absent
            candidates = [
                {
                    "id": "dQw4w9WgXcQ",
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "title": f"Candidate Clip for Beat {beat}",
                    "channel": "YouTube",
                    "duration": 213.0,
                    "embeddable": True,
                    "moments": [
                        {"start": 10.0, "end": 14.0, "kind": "chapter", "label": "Opening"},
                        {"start": 42.0, "end": 46.0, "kind": "peak", "label": "Action peak"},
                    ],
                }
            ]

    # Generate cards HTML
    cards_html = []
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
                f'<iframe id="player_{e(vid)}" src="https://www.youtube.com/embed/{e(vid)}?enablejsapi=1" '
                f'allow="autoplay; encrypted-media" allowfullscreen></iframe>'
            )
        else:
            player_elem = (
                f'<div class="no-embed">'
                f'<p>Video này chặn nhúng trực tiếp.</p>'
                f'<a href="{e(url)}&t={int(first_start)}s" target="_blank">Xem trên YouTube tại {first_start:.1f}s ↗</a>'
                f'</div>'
            )

        cards_html.append(f"""
        <div class="card" id="card_{e(vid)}">
          <h3>{i}. {e(title)}</h3>
          <p class="channel">{e(channel)} | <a href="{e(url)}&t={int(first_start)}s" target="_blank">Xem trên YouTube ↗</a></p>
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
    iframe {{ width: 100%; aspect-ratio: 16/9; border: 0; border-radius: 6px; margin-bottom: 12px; }}
    .no-embed {{ background: #21262d; padding: 20px; text-align: center; border-radius: 6px; margin-bottom: 12px; }}
    .timestamps {{ margin-bottom: 12px; }}
    .time-btn {{ background: #21262d; color: #58a6ff; border: 1px solid #30363d; padding: 6px 10px; border-radius: 4px; cursor: pointer; margin-right: 6px; margin-bottom: 6px; font-size: 13px; }}
    .time-btn:hover {{ background: #30363d; }}
    .controls {{ display: flex; align-items: center; justify-content: space-between; border-top: 1px solid #21262d; padding-top: 12px; }}
    input[type=number] {{ background: #0d1117; color: white; border: 1px solid #30363d; padding: 6px 10px; border-radius: 4px; width: 80px; font-size: 14px; }}
    .btn {{ background: #238636; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }}
    .btn:hover {{ background: #2ea043; }}
    .toast {{ position: fixed; bottom: 20px; right: 20px; background: #238636; color: white; padding: 12px 20px; border-radius: 6px; display: none; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}
  </style>
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
    function seekTo(vid, seconds) {{
      document.getElementById('start_' + vid).value = seconds;
      const iframe = document.getElementById('player_' + vid);
      if (iframe && iframe.contentWindow) {{
        iframe.contentWindow.postMessage(JSON.stringify({{'event': 'command', 'func': 'seekTo', 'args': [seconds, true]}}), '*');
        iframe.contentWindow.postMessage(JSON.stringify({{'event': 'command', 'func': 'playVideo', 'args': []}}), '*');
      }}
    }}

    async function pickMoment(vid) {{
      const startVal = parseFloat(document.getElementById('start_' + vid).value || "0");
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
async def pick_moment(req: PickMomentRequest) -> dict[str, Any]:
    """Store the picked moment into project manifest and broadcast."""
    project_root = config.PROJECTS_ROOT / req.project
    if project_root.exists():
        clips_dir = project_root / "review" / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = clips_dir / "clips.json"

        manifest_data = {"clips": []}
        if manifest_path.exists():
            try:
                manifest_data = json.loads(manifest_path.read_text())
            except Exception:
                manifest_data = {"clips": []}

        # Update or append clip entry
        updated = False
        norm_file = f"review/clips/{req.video_id}.mp4"
        for item in manifest_data.get("clips", []):
            if str(item.get("beat")) == req.beat:
                item["id"] = req.video_id
                item["file"] = norm_file
                item["start"] = req.start
                item["end"] = req.start + (req.duration or 3.0)
                item["source_url"] = f"https://www.youtube.com/watch?v={req.video_id}"
                updated = True
                break

        if not updated:
            manifest_data.setdefault("clips", []).append({
                "id": req.video_id,
                "file": norm_file,
                "beat": req.beat,
                "start": req.start,
                "end": req.start + (req.duration or 3.0),
                "source_url": f"https://www.youtube.com/watch?v={req.video_id}",
            })

        manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False))

    payload = req.model_dump()
    _broadcast_moment_picked(payload)
    return {"status": "ok", "data": payload}


@router.get("/api/beat_tts_status")
async def beat_tts_status(project: str = "") -> dict[str, Any]:
    """Return background TTS progress and beat duration timings."""
    if not project:
        return {"completed": False, "beat_durations": {}, "scene_durations": {}}

    status_file = config.PROJECTS_ROOT / project / "cache" / "tts" / "status.json"
    if status_file.exists():
        try:
            return json.loads(status_file.read_text())
        except Exception:
            pass
    return {"completed": False, "beat_durations": {}, "scene_durations": {}}


@router.post("/api/bump_beat_tts")
async def bump_beat_tts(payload: dict[str, str]) -> dict[str, Any]:
    project = payload.get("project", "")
    beat = payload.get("beat", "")
    if project and beat:
        from stages.stage_4.background_tts import BackgroundTTSRunner
        runner = BackgroundTTSRunner(project)
        runner.bump_priority_beat(beat)
    return {"status": "ok"}
