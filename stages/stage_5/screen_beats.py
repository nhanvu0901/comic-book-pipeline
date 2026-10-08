"""stages/stage_5/screen_beats.py
Pure planning for screen_qa (no ffmpeg, no flet): the canonical BEAT ROWS Master picks a visual
for, and the frame-exact TIMELINE WINDOWS those rows render into.

Why a module of its own: the review screen (ui/screens/s_screen_gate.py), the shot builder
(screen_shots.py) and the P1 clip machinery (clips.json, locks.json, /moments_review) must all
agree on what a "beat" is. They key on the SAME beat_key scheme review_gate writes for the comic
review — "intro" | "outro" | "<scene_id>" | "<scene_id>:<frag_idx>" with a 0-based frag_idx — so
a clip picked in /moments_review and a custom image locked in the gate resolve through the
unchanged stage_5.clips / stage_5.shots code. Nothing here knows about comic panels.

TIMELINE — each scene OWNS the span from the previous scene's end to its own end (scene 1 from
t=0), exactly like the comic builder, so the silence between sentences is absorbed into the
visuals and a cut never drifts off the audio. The last window runs to the end of the audio (+ the
same 0.20s tail the comic path adds so `-shortest` never clips the last word). Cut points inside
a scene land in the silence between the real words when word timestamps exist (proportional to
word counts otherwise), and EVERY boundary is snapped to a whole frame by cumulative rounding —
so sum(durations) is exact and the drift against the audio never exceeds half a frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

# The renderer floors a shot at 0.4s (render_shot / render_clip_shot do max(0.4, dur)); a shorter
# window would silently stretch the timeline.
MIN_SHOT_SECONDS = 0.4
# Same tail stage_5.pipeline adds when the shots end before the audio (BUG #122 Fix C).
AUDIO_TAIL_PAD = 0.20
# Narration pacing the writer uses to estimate a scene with no audio timing yet (Narration schema).
DEFAULT_WORDS_PER_SECOND = 3.4


@dataclass
class ScreenBeat:
    """One thing Master picks a visual for."""
    key: str                      # review beat_key
    scene_id: int
    text: str                     # the words spoken over it
    query: str = ""               # search hint for /moments_review
    unit: str = "fragment"        # intro | outro | fragment | scene
    frag_idx: int | None = None   # index among the scene's non-empty visual_beats
    is_intro: bool = False
    is_outro: bool = False

    @property
    def words(self) -> int:
        return max(1, len(self.text.split()))


@dataclass
class ScreenWindow:
    """One shot's slice of the timeline. `keys` lists every beat it covers (>1 after a merge)."""
    keys: list[str]
    scene_id: int
    text: str
    query: str
    start: float
    duration: float
    frames: int
    is_intro: bool = False
    is_outro: bool = False
    beats: list[ScreenBeat] = field(default_factory=list)


# ─── beat rows ──────────────────────────────────────────────────────────────────

def _vb_text(b: Any) -> str:
    return str(b.get("text", "")).strip() if isinstance(b, dict) else str(b or "").strip()


def _vb_query(b: Any) -> str:
    if isinstance(b, dict):
        return str(b.get("query") or b.get("visual_query") or "").strip()
    return ""


def _context_query(text: str, screen_context: dict | None) -> str:
    """The research item whose entity the beat's words name → its visual_query ('' if none)."""
    low = text.lower()
    for item in (screen_context or {}).get("items") or []:
        ent = str(item.get("entity") or "").strip().lower()
        if ent and ent in low and item.get("visual_query"):
            return str(item["visual_query"]).strip()
    return ""


def fallback_query(text: str, limit: int = 8) -> str:
    words = text.split()
    return " ".join(words[:limit])


def screen_beat_rows(narration: dict, screen_context: dict | None = None) -> list[ScreenBeat]:
    """Every beat of the narration in VIDEO order, keyed like review_gate.build_candidates.

    A bookend scene (is_intro / is_outro) with 2+ fragments yields ordinary "<sid>:<fi>" rows,
    with one fragment (or none) the legacy "intro"/"outro" row — review_gate.bookend_row_keys is
    the one definition of that rule, reused here so the keys cannot drift."""
    from ..review_gate import bookend_row_keys

    rows: list[ScreenBeat] = []
    for n, scene in enumerate(narration.get("scenes") or [], start=1):
        sid = int(scene.get("scene_id") or n)
        frags = [(_vb_text(b), _vb_query(b)) for b in (scene.get("visual_beats") or [])
                 if _vb_text(b)]
        is_intro, is_outro = bool(scene.get("is_intro")), bool(scene.get("is_outro"))

        def _query(text: str, explicit: str) -> str:
            return explicit or _context_query(text, screen_context) or fallback_query(text)

        if is_intro or is_outro:
            unit = "intro" if is_intro else "outro"
            for bk, u, txt in bookend_row_keys(scene, unit):
                fi = int(bk.rsplit(":", 1)[1]) if u == "fragment" else None
                explicit = frags[fi][1] if fi is not None else (frags[0][1] if frags else "")
                rows.append(ScreenBeat(key=bk, scene_id=sid, text=txt, query=_query(txt, explicit),
                                       unit=u, frag_idx=fi, is_intro=is_intro, is_outro=is_outro))
        elif frags:
            for fi, (txt, explicit) in enumerate(frags):
                rows.append(ScreenBeat(key=f"{sid}:{fi}", scene_id=sid, text=txt,
                                       query=_query(txt, explicit), unit="fragment", frag_idx=fi))
        else:
            txt = str(scene.get("text", "") or "")
            rows.append(ScreenBeat(key=str(sid), scene_id=sid, text=txt,
                                   query=_query(txt, ""), unit="scene"))
    return rows


# ─── scene spans (gap absorption) ───────────────────────────────────────────────

def _scene_word_count(scene: dict) -> int:
    wc = int(scene.get("word_count") or 0)
    return wc if wc > 0 else max(1, len(str(scene.get("text", "")).split()))


def _estimated_seconds(scene: dict) -> float:
    t = float(scene.get("target_seconds") or 0.0)
    if t > 0:
        return t
    return max(MIN_SHOT_SECONDS, _scene_word_count(scene) / DEFAULT_WORDS_PER_SECOND)


def _timings_by_scene(scene_timings: Any) -> dict[int, dict]:
    """{scene_id: {"start","end"}} from Stage 4's scene_timings.json. Tolerates the {} / None a
    caller passes when it has no timing yet."""
    out: dict[int, dict] = {}
    if not isinstance(scene_timings, (list, tuple)):
        return out
    for t in scene_timings:
        try:
            sid = int(t.get("scene_id"))
            start, end = float(t.get("start", 0.0)), float(t.get("end", 0.0))
        except (AttributeError, TypeError, ValueError):
            continue
        if end > start:
            out[sid] = {"start": start, "end": end}
    return out


def scene_spans(scenes: list[dict], scene_timings: Any, *, audio_duration: float = 0.0,
                tail_pad: float = AUDIO_TAIL_PAD) -> list[tuple[int, float, float]]:
    """[(scene_id, start, end)] covering [0, end_of_timeline] with NO gaps.

    Scene i owns prev_end → its own end (scene 1 from 0); a scene without Stage-4 timing is
    estimated from target_seconds / word count and follows on from the previous end. When the
    scenes end before the audio does, the last one is extended to audio_duration + tail_pad."""
    by_id = _timings_by_scene(scene_timings)
    spans: list[tuple[int, float, float]] = []
    prev_end = 0.0
    for n, scene in enumerate(scenes, start=1):
        sid = int(scene.get("scene_id") or n)
        t = by_id.get(sid)
        if t is not None:
            end = max(t["end"], prev_end + MIN_SHOT_SECONDS)
        else:
            end = prev_end + _estimated_seconds(scene)
        spans.append((sid, prev_end, end))
        prev_end = end
    if spans and audio_duration > 0 and prev_end < audio_duration:
        sid, start, _ = spans[-1]
        spans[-1] = (sid, start, audio_duration + tail_pad)
    return spans


# ─── fragment split inside a scene ──────────────────────────────────────────────

def _word_gap_boundaries(word_timestamps: list[dict] | None, first_word: int,
                         cum_words: list[int]) -> list[float] | None:
    """Time of each cut between consecutive fragments, placed in the silence between the last
    word of a fragment and the first of the next (the midpoint, like shots._silence_gaps_in_window).
    cum_words[k] = how many of the scene's words fragments 0..k hold. None → not derivable."""
    if not word_timestamps:
        return None
    out: list[float] = []
    for cw in cum_words[:-1]:
        i = first_word + cw           # index of the first word of the NEXT fragment
        if i <= 0 or i >= len(word_timestamps):
            return None
        try:
            prev_end = float(word_timestamps[i - 1]["end"])
            nxt_start = float(word_timestamps[i]["start"])
        except (KeyError, TypeError, ValueError):
            return None
        out.append((prev_end + nxt_start) / 2.0 if nxt_start >= prev_end else prev_end)
    return out


def _split_scene(beats: list[ScreenBeat], start: float, end: float,
                 cuts: list[float] | None) -> list[float]:
    """Durations of `beats` tiling [start, end]. Cuts from real word gaps when given and sane,
    else proportional to word counts."""
    n = len(beats)
    span = end - start
    if n == 1:
        return [span]
    if cuts is not None and len(cuts) == n - 1:
        pts = [start, *[min(max(c, start), end) for c in cuts], end]
        if all(b >= a for a, b in zip(pts, pts[1:])):
            return [b - a for a, b in zip(pts, pts[1:])]
    total = sum(b.words for b in beats)
    return [span * b.words / total for b in beats]


def _enforce_floor(durs: list[float], floor: float) -> list[float]:
    """Raise every duration to `floor`, paying for it out of the longest others, total conserved.
    When the span cannot hold n floors (impossible case) fall back to an even split."""
    n, total = len(durs), sum(durs)
    if n == 0:
        return durs
    if total < floor * n - 1e-9:
        return [total / n] * n
    out = list(durs)
    for _ in range(4 * n):
        low = [i for i, d in enumerate(out) if d < floor - 1e-9]
        if not low:
            break
        i = low[0]
        need = floor - out[i]
        donors = sorted((k for k in range(n) if out[k] > floor + 1e-9 and k != i),
                        key=lambda k: -out[k])
        if not donors:
            break
        k = donors[0]
        take = min(need, out[k] - floor)
        out[k] -= take
        out[i] += take
    return out


def _merge_short(group: list[dict], min_seconds: float, protected: frozenset[str]) -> list[dict]:
    """Absorb any window shorter than `min_seconds` into its LONGER neighbour inside the same
    scene (shots._merge_locked_segments' rule). A window that carries a Master pick (protected)
    is never absorbed — deleting it would delete the pick — and a lone window stays."""
    segs = [dict(g, beats=list(g["beats"])) for g in group]
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, seg in enumerate(segs):
            if seg["dur"] >= min_seconds:
                continue
            if any(b.key in protected for b in seg["beats"]):
                continue
            left = segs[i - 1] if i > 0 else None
            right = segs[i + 1] if i + 1 < len(segs) else None
            target = (left if left["dur"] >= right["dur"] else right) if (left and right) \
                else (left or right)
            if target is None:
                continue
            target["dur"] += seg["dur"]
            if target is left:
                target["beats"] = target["beats"] + seg["beats"]
            else:
                target["beats"] = seg["beats"] + target["beats"]
            del segs[i]
            changed = True
            break
    return segs


# ─── frame snapping ─────────────────────────────────────────────────────────────

def _snap_boundaries(cum: list[float], fps: int, min_frames: int, total_frames: int) -> list[int]:
    """Whole-frame boundaries f_0=0 < f_1 < ... < f_n=total_frames for cumulative times `cum`
    (len n+1, cum[0]=0). Plain rounding, then a forward and a backward pass guarantee every
    shot keeps >= min_frames. Cumulative rounding => every boundary is within half a frame of
    its exact time, so the sum of shots == total_frames exactly."""
    n = len(cum) - 1
    f = [0] + [int(round(c * fps)) for c in cum[1:-1]] + [total_frames]
    for k in range(1, n):
        f[k] = max(f[k], f[k - 1] + min_frames)
    for k in range(n - 1, 0, -1):
        f[k] = min(f[k], f[k + 1] - min_frames)
    for k in range(1, n):                       # impossible case (span < n floors): even split
        if f[k] <= f[k - 1]:
            f = [int(round(i * total_frames / n)) for i in range(n + 1)]
            break
    return f


# ─── the plan ───────────────────────────────────────────────────────────────────

def plan_windows(
    narration: dict,
    scene_timings: Any = None,
    word_timestamps: list[dict] | None = None,
    *,
    audio_duration: float = 0.0,
    screen_context: dict | None = None,
    protected: Iterable[str] = (),
    merge_short: bool = True,
    min_seconds: float | None = None,
    fps: int = 30,
) -> list[ScreenWindow]:
    """The shot windows of a screen_qa video: one per beat row, merged (same scene only) below
    `min_seconds` unless a Master pick sits on the beat. Durations are whole frames that tile
    [0, end of timeline] exactly. [] for a narration with no scenes."""
    scenes = narration.get("scenes") or []
    if not scenes:
        return []
    if min_seconds is None:
        from .shots import QA_MIN_SHOT_SECONDS
        min_seconds = QA_MIN_SHOT_SECONDS
    keep = frozenset(str(k) for k in protected)
    rows = screen_beat_rows(narration, screen_context)
    by_scene: dict[int, list[ScreenBeat]] = {}
    for r in rows:
        by_scene.setdefault(r.scene_id, []).append(r)
    spans = scene_spans(scenes, scene_timings, audio_duration=audio_duration)

    # Where each scene's words start in word_timestamps (Stage 4 consumed them sequentially).
    first_word: dict[int, int] = {}
    cursor = 0
    for n, scene in enumerate(scenes, start=1):
        sid = int(scene.get("scene_id") or n)
        first_word[sid] = cursor
        cursor += _scene_word_count(scene)
    scene_by_id = {int(s.get("scene_id") or n): s for n, s in enumerate(scenes, start=1)}

    pieces: list[dict] = []
    for sid, start, end in spans:
        beats = by_scene.get(sid) or []
        if not beats:
            continue
        cuts = None
        if len(beats) > 1 and word_timestamps:
            scene_words = _scene_word_count(scene_by_id[sid])
            total_w = sum(b.words for b in beats)
            cum, run = [], 0
            for b in beats:
                run += b.words
                cum.append(int(round(run * scene_words / total_w)))
            cuts = _word_gap_boundaries(word_timestamps, first_word[sid], cum)
        durs = _split_scene(beats, start, end, cuts)
        group = [{"dur": d, "beats": [b]} for d, b in zip(durs, beats)]
        if merge_short:
            group = _merge_short(group, min_seconds, keep)
        floored = _enforce_floor([g["dur"] for g in group], MIN_SHOT_SECONDS)
        for g, d in zip(group, floored):
            g["dur"] = d
        pieces.extend(dict(g, scene_id=sid) for g in group)

    if not pieces:
        return []
    cum = [0.0]
    for p in pieces:
        cum.append(cum[-1] + p["dur"])
    total_frames = max(int(math.ceil(cum[-1] * fps - 1e-6)), 1)
    min_frames = int(math.ceil(MIN_SHOT_SECONDS * fps - 1e-9))
    bounds = _snap_boundaries(cum, fps, min_frames, total_frames)

    out: list[ScreenWindow] = []
    for i, p in enumerate(pieces):
        frames = bounds[i + 1] - bounds[i]
        bs: list[ScreenBeat] = p["beats"]
        out.append(ScreenWindow(
            keys=[b.key for b in bs], scene_id=p["scene_id"],
            text=" ".join(b.text for b in bs).strip(),
            query=next((b.query for b in bs if b.query), ""),
            start=bounds[i] / fps, duration=frames / fps, frames=frames,
            is_intro=bs[0].is_intro, is_outro=bs[-1].is_outro, beats=bs))
    return out


def estimate_beat_seconds(narration: dict, scene_seconds: dict[Any, float] | None = None,
                          screen_context: dict | None = None) -> dict[str, float]:
    """{beat_key: seconds} — how long each beat will be on screen — for the review screen and
    for the clip-section download. `scene_seconds` is {scene_id: TTS seconds} (the background
    TTS status); without it the writer's own target_seconds / word-count estimate is used.
    Not merged: Master picks per beat, so each beat reports its own share."""
    timings = []
    cum = 0.0
    for n, scene in enumerate(narration.get("scenes") or [], start=1):
        sid = int(scene.get("scene_id") or n)
        dur = None
        if scene_seconds:
            raw = scene_seconds.get(str(sid), scene_seconds.get(sid))
            dur = float(raw) if raw else None
        dur = dur if dur and dur > 0 else _estimated_seconds(scene)
        timings.append({"scene_id": sid, "start": cum, "end": cum + dur})
        cum += dur
    wins = plan_windows(narration, timings, screen_context=screen_context, merge_short=False)
    return {w.keys[0]: w.duration for w in wins}
