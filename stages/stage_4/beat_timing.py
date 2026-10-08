"""Beat window calculation & Windows keep-awake utility for Q&A pipeline."""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Any


@dataclass
class BeatWindow:
    beat_id: str
    scene_id: int
    start: float
    end: float
    duration: float
    text: str


def set_keep_awake(enable: bool = True) -> None:
    """Keep Windows awake during long background TTS or encoding jobs.
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    0x80000002 = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    Safe no-op on non-Windows platforms.
    """
    if sys.platform == "win32":
        try:
            if enable:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass


def _count_words(text: str) -> int:
    return len(str(text or "").strip().split())


def calculate_beat_durations(
    scenes: list[dict[str, Any]],
    sentence_timings: dict[int, dict[str, float]],
    whips: dict[int, float] | None = None,
    min_duration: float = 0.4,
) -> list[BeatWindow]:
    """Calculate exact beat timing windows from scene/sentence audio durations.

    1. Proportionally divide sentence duration among beats according to word counts.
    2. Absorb gaps so consecutive beats align.
    3. Deduct whip transition borrowings (half from preceding scene's last beat,
       half from following scene's first beat).
    4. Enforce minimum duration floor (>= min_duration, default 0.4s).
    """
    whips = whips or {}
    results: list[BeatWindow] = []

    for s_idx, sc in enumerate(scenes):
        sid = int(sc.get("scene_id") or (s_idx + 1))
        st = sentence_timings.get(sid, {})
        s_dur = float(st.get("duration", 0.0))
        s_start = float(st.get("start", 0.0))

        beats = sc.get("visual_beats") or []
        if not beats:
            # Fallback single beat for whole scene
            beats = [{"beat_id": f"{sid}:1", "text": sc.get("text", "")}]

        total_words = sum(max(1, _count_words(b.get("text", ""))) for b in beats)
        curr_t = s_start

        for b_idx, b in enumerate(beats):
            bid = str(b.get("beat_id") or f"{sid}:{b_idx+1}")
            b_words = max(1, _count_words(b.get("text", "")))
            b_frac = b_words / total_words if total_words > 0 else (1.0 / len(beats))
            b_dur = s_dur * b_frac
            b_text = str(b.get("text", ""))

            results.append(
                BeatWindow(
                    beat_id=bid,
                    scene_id=sid,
                    start=round(curr_t, 4),
                    end=round(curr_t + b_dur, 4),
                    duration=b_dur,
                    text=b_text,
                )
            )
            curr_t += b_dur

    # Apply whip deductions if boundaries exist
    if whips and len(scenes) > 1:
        # Group beat indices by scene
        scene_beat_map: dict[int, list[int]] = {}
        for idx, bw in enumerate(results):
            scene_beat_map.setdefault(bw.scene_id, []).append(idx)

        scene_ids = [int(sc.get("scene_id") or (i + 1)) for i, sc in enumerate(scenes)]
        for b_scene_idx, whip_secs in whips.items():
            if b_scene_idx + 1 < len(scene_ids):
                sid_a = scene_ids[b_scene_idx]
                sid_b = scene_ids[b_scene_idx + 1]
                half = whip_secs / 2.0
                if sid_a in scene_beat_map and scene_beat_map[sid_a]:
                    last_idx = scene_beat_map[sid_a][-1]
                    results[last_idx].duration = max(min_duration, results[last_idx].duration - half)
                    results[last_idx].end = results[last_idx].start + results[last_idx].duration
                if sid_b in scene_beat_map and scene_beat_map[sid_b]:
                    first_idx = scene_beat_map[sid_b][0]
                    results[first_idx].duration = max(min_duration, results[first_idx].duration - half)
                    results[first_idx].end = results[first_idx].start + results[first_idx].duration

    # Ensure min duration floor on all windows
    for bw in results:
        bw.duration = max(min_duration, round(bw.duration, 4))
        bw.end = round(bw.start + bw.duration, 4)

    return results
