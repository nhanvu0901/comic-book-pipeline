"""Stage 5.5 — post-render frame↔narration verification.

Nothing checks the RENDERED video against the narration today: a wrong panel
match, an audio/video desync, or drift is only ever caught by a human
watching the video, or by manually extracting a frame with ffmpeg and looking
at it with a vision model. This module automates that same manual loop —
after final.mp4 renders, spot-check each story scene's mid-point frame
against the line spoken over it.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT
from .._claude_sdk import sdk_available, sdk_complete_vision

# Same boolean-env idiom as PANEL_UNIQUE / PANEL_ANCHOR_BIND in shots.py.
# "0"/"false"/"no" disables the whole checker.
VERIFY_FRAMES = os.getenv("VERIFY_FRAMES", "1").strip().lower() not in ("0", "false", "no", "")

_SYSTEM = (
    "You are a QA judge for a comic-narration video. You are shown ONE frame extracted "
    "from the finished video and the narration line spoken at that moment. Judge the "
    "comic ARTWORK in the frame — ignore any burned-in captions or banner text overlaid "
    "on it, those are UI, not the panel. Does the artwork depict what the narration line "
    'describes? Reply with ONLY strict JSON: {"match": true or false, "why": "one short '
    'reason"}. No other text.'
)


def _parse_verdict(raw: str | None) -> dict | None:
    """Tolerant parse of the vision judge's {"match": bool, "why": str} verdict — strips
    ``` fences and any stray text around the JSON object. None on garbage / no "match" key."""
    if not raw:
        return None
    text = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or "match" not in data:
        return None
    return {"match": bool(data["match"]), "why": str(data.get("why", "")).strip()}


def _build_summary(matched: int, checked: int) -> str:
    return f"{matched}/{checked} matched"


def _plan_scene_checks(narration: dict, scene_timings: list[dict]) -> list[dict]:
    """One plan entry per scene_timings row: {scene_id, text, start, end, skip}. `skip`
    is None for a checkable story scene, else the reason ("intro/outro" / "scene not
    found") — intro/outro get a special cold-open/outro panel by design (not a content
    match), so checking them against the narration line would just be noise. Pure, no I/O."""
    scenes_by_id = {int(s.get("scene_id") or 0): s for s in (narration.get("scenes") or [])}
    plan = []
    for timing in scene_timings:
        sid = int(timing.get("scene_id") or 0)
        scene = scenes_by_id.get(sid)
        skip = None
        if scene is None:
            skip = "scene not found"
        elif scene.get("is_intro") or scene.get("is_outro"):
            skip = "intro/outro"
        plan.append({
            "scene_id": sid,
            "text": str(timing.get("text") or (scene or {}).get("text") or ""),
            "start": float(timing.get("start", 0.0) or 0.0),
            "end": float(timing.get("end", 0.0) or 0.0),
            "skip": skip,
        })
    return plan


def _extract_frame(video_path: Path, timestamp: float, out_png: Path) -> bool:
    """Grab one frame from `video_path` at `timestamp` seconds via ffmpeg."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    cmd = [ff, "-y", "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(video_path),
           "-frames:v", "1", "-q:v", "4", str(out_png)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0 and out_png.exists()


def _judge_frame(frame_png: Path, scene_text: str, *, log=print) -> dict | None:
    """One vision call: does `frame_png` depict `scene_text`? Reuses the same Claude-SDK
    vision path as shots.py's _vlm_rerank (the agent Reads the image file — no separate
    image-upload API). Returns the parsed verdict, or None if the SDK/judge is unavailable
    or the answer didn't parse."""
    user = f'Narration line: "{scene_text}"\n\nFrame image: {frame_png}'
    raw = sdk_complete_vision(_SYSTEM, user, log=log)
    return _parse_verdict(raw)


def verify_frames(project_name: str, *, log: Callable[[str], None] = print) -> dict:
    """Spot-check final.mp4 against narration.json: for each story scene, extract the
    mid-scene frame and ask a vision judge whether it depicts the narration line. Writes
    frame_check.json and returns it. Never raises (the pipeline.py caller wraps this in
    try/except too) — a checker bug must never fail the render.

    ponytail: one vision call per scene, not batched — batching into one multi-image call
    would need per-scene parsing out of a shared response plus cross-scene failure
    handling, for a few cents of savings on a handful of calls per project. Revisit if
    per-project vision cost actually matters.
    """
    root = PROJECTS_ROOT / project_name
    if not sdk_available():
        log("[frame-check] Claude SDK unavailable — skipping frame verification")
        return {"skipped": True}

    narration = json.loads((root / "narration.json").read_text())
    scene_timings = json.loads((root / "scene_timings.json").read_text())
    plan = _plan_scene_checks(narration, scene_timings)

    results = []
    matched = checked = skipped = 0
    tmpdir = Path(tempfile.mkdtemp(prefix="frame_check_"))
    try:
        for entry in plan:
            sid = entry["scene_id"]
            if entry["skip"]:
                results.append({"scene_id": sid, "match": "skipped", "why": entry["skip"]})
                skipped += 1
                log(f"- s{sid} (skipped: {entry['skip']})")
                continue
            ts = entry["start"] + max(0.0, entry["end"] - entry["start"]) / 2.0
            frame_png = tmpdir / f"scene_{sid:03d}.png"
            if not _extract_frame(root / "final.mp4", ts, frame_png):
                results.append({"scene_id": sid, "match": "skipped", "why": "frame extraction failed"})
                skipped += 1
                log(f"- s{sid} (frame extraction failed)")
                continue
            verdict = _judge_frame(frame_png, entry["text"], log=log)
            if verdict is None:
                results.append({"scene_id": sid, "match": "skipped", "why": "judge unavailable"})
                skipped += 1
                log(f"- s{sid} (judge unavailable)")
                continue
            checked += 1
            if verdict["match"]:
                matched += 1
                log(f"✓ s{sid}")
            else:
                log(f"✗ s{sid} — {verdict['why']}")
            results.append({"scene_id": sid, "match": verdict["match"], "why": verdict["why"]})
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    summary = _build_summary(matched, checked)
    out = {"results": results, "summary": summary}
    (root / "frame_check.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log(f"frame-check: {summary}, {skipped} skipped")
    return out


if __name__ == "__main__":
    import argparse
    _p = argparse.ArgumentParser(description="Verify rendered video frames against narration")
    _p.add_argument("--project", required=True)
    verify_frames(_p.parse_args().project)
