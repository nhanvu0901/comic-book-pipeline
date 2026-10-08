"""How long each review beat is in the cached Chatterbox audio, and the Windows keep-awake helper.

A "beat" here is a REVIEW beat — a row of the review gate, keyed exactly like locks.json / clips.json:
"intro" | "outro" | "<scene_id>" | "<scene_id>:<fragment_idx>". The keys come from
stages.stage_5.shots._beat_rows_for_custom (the single definition the custom-image and clip
passes already share), never from a copy of that rule.

The durations are the ones Stage 5 will actually cut with, not an approximation of them. Stage 4
(flag ON) builds ONE word stream: the narration is normalised and chunked as a whole, each
chunk's words are spread evenly over that chunk's own measured length (_even_words), and
align_scenes_to_words walks that stream with each scene's word count. Stage 5 then cuts a
scene's words into its fragments by word position (shots._split_members_by_clause). This module
does the same two walks over the same stream, so a beat's duration is the sum of its words'
durations — it needs only the chunks that overlap the beat, which is what lets a beat show its
length as soon as ITS chunks are synthesized, whatever order the queue ran in.

The whip transition is deliberately not subtracted: a project with a clip never gets whips
(stages.stage_5.pipeline._assemble_video disables them for the whole video), and a beat only has
a duration worth showing once a clip is being picked for it.
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass, field
from typing import Any, Sequence


def set_keep_awake(enable: bool = True) -> None:
    """Keep Windows awake during long background TTS or encoding jobs.
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    0x80000002 = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    Safe no-op on non-Windows platforms. The state belongs to the CALLING thread, so the thread
    that runs the long job must be the one that calls this.
    """
    if sys.platform == "win32":
        try:
            if enable:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass


@dataclass
class BeatRow:
    beat_key: str          # review beat key
    scene_id: int
    text: str
    words: int


@dataclass
class BeatTimings:
    durations: dict[str, float] = field(default_factory=dict)      # beat_key -> seconds (known ones)
    windows: dict[str, tuple[float, float]] = field(default_factory=dict)   # only when the whole stream is known
    scene_durations: dict[int, float] = field(default_factory=dict)
    complete: bool = False                                         # every chunk had a duration


def beat_rows(narration: dict[str, Any]) -> list[BeatRow]:
    """The review beats of `narration` in render order, each tied to the scene whose audio it is."""
    from stages.stage_5.shots import _beat_rows_for_custom
    scenes = narration.get("scenes") or []
    intro = next((s for s in scenes if s.get("is_intro")), None)
    outro = next((s for s in scenes if s.get("is_outro")), None)
    rows: list[BeatRow] = []
    for key, text in _beat_rows_for_custom(narration):
        if key == "intro" and intro is not None:
            sid = int(intro.get("scene_id") or 0)
        elif key == "outro" and outro is not None:
            sid = int(outro.get("scene_id") or 0)
        else:
            try:
                sid = int(str(key).partition(":")[0])
            except ValueError:
                continue
        rows.append(BeatRow(str(key), sid, str(text), len(str(text).split())))
    return rows


def narration_text(scenes: Sequence[dict[str, Any]]) -> str:
    """The text Stage 4 hands the TTS: every scene's text joined, before normalisation."""
    return " ".join(str(s.get("text", "")).strip() for s in scenes if s.get("text"))


def scene_word_ranges(scenes: Sequence[dict[str, Any]], stream_words: int) -> dict[int, tuple[int, int]]:
    """{scene_id: (lo, hi)} — each scene's slice of the word stream, by the SAME cursor walk as
    stage_4.chunker.align_scenes_to_words (a scene consumes `word_count`, else its text's word
    count). Scenes the stream runs out before get no entry (Stage 4 drops them too)."""
    out: dict[int, tuple[int, int]] = {}
    cursor = 0
    for i, s in enumerate(scenes):
        wc = int(s.get("word_count", 0) or 0)
        if wc <= 0:
            wc = max(1, len(str(s.get("text", "")).split()))
        if cursor >= stream_words:
            break
        hi = min(cursor + wc, stream_words)
        out[int(s.get("scene_id", i + 1))] = (cursor, hi)
        cursor = hi
    return out


def beat_word_ranges(rows: Sequence[BeatRow],
                     scene_ranges: dict[int, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """{beat_key: (lo, hi)} in stream-word positions: a scene's words are cut into its beats by
    word position, the way shots._split_members_by_clause does (a beat covers as many words as
    its text has; anything past the last beat's text stays with the last beat)."""
    by_scene: dict[int, list[BeatRow]] = {}
    for r in rows:
        by_scene.setdefault(r.scene_id, []).append(r)
    out: dict[str, tuple[int, int]] = {}
    for sid, brs in by_scene.items():
        if sid not in scene_ranges:
            continue
        lo, hi = scene_ranges[sid]
        n = hi - lo
        total = sum(max(0, b.words) for b in brs)
        if total <= 0 or n <= 0:
            continue
        cum = 0
        for k, b in enumerate(brs):
            a = min(n, cum)
            cum += max(0, b.words)
            z = n if k == len(brs) - 1 else min(n, cum)
            if z > a:
                out[b.beat_key] = (lo + a, lo + z)
    return out


def compute_beat_timings(
    narration: dict[str, Any],
    chunk_texts: Sequence[str],
    chunk_durations: Sequence[float | None],
) -> BeatTimings:
    """Beat / scene durations for `narration` given the cached length of each chunk of its
    normalised, chunked text (None = not synthesized yet). Beats whose words all fall in known
    chunks get a duration; absolute windows are produced only when every chunk is known."""
    scenes = narration.get("scenes") or []
    word_dur: list[float | None] = []
    for text, dur in zip(chunk_texts, chunk_durations):
        n = len(str(text).split())
        word_dur.extend([None if dur is None else float(dur) / n] * n)
    res = BeatTimings(complete=all(d is not None for d in chunk_durations))
    if not word_dur:
        return res

    # absolute word start times — only meaningful (and only used) when the whole stream is known
    starts: list[float] = []
    if res.complete:
        t = 0.0
        for d in word_dur:
            starts.append(t)
            t += d or 0.0
        starts.append(t)

    def span(lo: int, hi: int) -> float | None:
        seg = word_dur[lo:hi]
        return None if (not seg or any(d is None for d in seg)) else sum(seg)   # type: ignore[arg-type]

    ranges = scene_word_ranges(scenes, len(word_dur))
    for sid, (lo, hi) in ranges.items():
        d = span(lo, hi)
        if d is not None:
            res.scene_durations[sid] = round(d, 4)
    for key, (lo, hi) in beat_word_ranges(beat_rows(narration), ranges).items():
        d = span(lo, hi)
        if d is not None:
            res.durations[key] = round(d, 4)
            if res.complete:
                res.windows[key] = (round(starts[lo], 4), round(starts[hi], 4))
    return res
