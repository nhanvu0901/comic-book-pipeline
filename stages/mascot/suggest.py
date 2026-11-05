"""'Gợi ý tự động': propose cues from the beat structure — general rules, never per comic.

Rules (design doc section 3):
  * first beat (cold open / hook) → "rise" with the kit preset "point" near the first word
  * climax / twist / reveal beats → "shake" with the preset "shock" at the beat start
  * landing / resolution beats → "pop" with "think" (or "sly" when the kit has it)
  * hide between highlights so the mascot is on screen roughly half the video ("sink" at the
    next cut a few seconds after a highlight, "peek" back for the next one)
  * the end → "slide_out" ~0.5 s before the video ends
  * every time is snapped to the nearest cut / beat / word start (timeline.snap_time)
  * LOCKED cues are kept exactly; no new cue is placed within 0.8 s of a locked cue;
    unlocked cues from an earlier suggestion are replaced
Missing beats → fall back to scene starts; missing everything → enter at 0.2 s, exit at the end.
Only pose names that exist in the kit are used (kit.resolve_pose / kit.presets).

How it works: the beats become "highlight events"; a small state machine walks them, enters on
the first one, accents the following ones, and sinks at the next cut once `hold` seconds have
passed since the last event. `hold` is the only free knob: suggest_cues tries a grid of values
(first with highlights only, then with the in-between beats as extra accents) and keeps the
plan whose on-screen ratio lands in VISIBLE_BAND, closest to VISIBLE_TARGET.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from .config import MOVES_EXIT, Cue, MascotConfig
from .kit import Kit
from .timeline import snap_time, timeline_data

# ── tunable rules ────────────────────────────────────────────────────────────────────────
# role → keywords looked up (case-insensitive, substring) in the beat's "function".
# Vocabulary seen in the pipeline: COLD_OPEN | SETUP | COMPLICATION | ESCALATION | MIDPOINT |
# CLIMAX | LANDING (stage_3/schema.py, write_script.py), RESOLUTION (micro_moment.py);
# Q&A and screen_qa only emit COLD_OPEN / SETUP / LANDING (explore_answer.py, screen_qa.py).
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hook":    ("COLD_OPEN", "COLDOPEN", "HOOK", "TEASER", "PROLOGUE", "INTRO"),
    "landing": ("LANDING", "RESOLUTION", "PAYOFF", "OUTRO", "ENDING", "EPILOGUE", "CONCLUSION",
                "FINALE", "AFTERMATH", "DENOUEMENT"),
    "peak":    ("CLIMAX", "TWIST", "REVEAL", "SHOCK", "TURNING", "PEAK", "BETRAYAL", "CONFRONT"),
    "build":   ("ESCALATION", "COMPLICATION", "MIDPOINT", "RISING"),   # extra accents only
}
# role → (move when the mascot is already visible, kit presets to try in order)
RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "hook":    ("rise",  ("point",)),
    "peak":    ("shake", ("shock",)),
    "landing": ("pop",   ("sly", "think")),
    "other":   ("pop",   ("point",)),          # in-between beats / scenes (tier-2 accents)
}
ENTER_MOVE = "peek"          # enter move for every event except the first (hook uses RULES)
EXIT_MID, EXIT_END = "sink", "slide_out"

MIN_GAP = 1.2                # s between two new cues
LOCK_GAP = 0.8               # s kept free around every locked cue
HOOK_MIN_T = 0.2             # earliest first entrance
END_MARGIN = 0.5             # final exit this long before the end
VISIBLE_BAND = (0.45, 0.65)  # share of the video the mascot should be on screen
VISIBLE_TARGET = 0.55
HOLD_GRID = [1.5 + 0.5 * i for i in range(80)] + [math.inf]   # s a highlight keeps the mascot up


# ── helpers ──────────────────────────────────────────────────────────────────────────────
def _role(function: str) -> str:
    fn = re.sub(r"[^A-Z0-9]+", "_", str(function or "").upper())
    for role in ("hook", "landing", "peak", "build"):
        if any(k in fn for k in ROLE_KEYWORDS[role]):
            return role
    return "other"


def _pose(kit: Kit, names: tuple[str, ...]) -> tuple[str, str]:
    for n in names:
        p = kit.presets.get(n)
        if p and (not kit.bodies or p.body in kit.bodies) and (not kit.faces or p.face in kit.faces):
            return p.body, p.face
    return kit.default_body, kit.default_face


def _events(data: dict, kit: Kit) -> tuple[list[dict], list[dict], bool]:
    """(highlights, extras, fallback_only). Each event: {t, role}. Times are snapped."""
    duration = float(data.get("duration") or 0.0)
    words, beats, scenes = data.get("words") or [], data.get("beats") or [], data.get("scenes") or []
    raw: list[tuple[float, str]] = []          # (t, role)
    if beats:
        for i, b in enumerate(sorted(beats, key=lambda b: b["t"])):
            role = _role(b.get("function"))
            raw.append((float(b["t"]), "hook" if i == 0 else ("other" if role == "hook" else role)))
    elif scenes:
        starts = [float(s["t"]) for s in sorted(scenes, key=lambda s: s["t"])]
        n = len(starts)
        for i, t in enumerate(starts):
            role = "hook" if i == 0 else ("landing" if i == n - 1 else "other")
            if n >= 3 and i == round(0.6 * (n - 1)) and role == "other":
                role = "peak"
            raw.append((t, role))
    fallback_only = not raw
    if fallback_only:
        raw = [(HOOK_MIN_T, "hook")]
    hi, extra = [], []
    end_t = duration - END_MARGIN if duration > 0 else math.inf
    for t, role in raw:
        if role == "hook":                     # near the first word of the narration
            t = float(words[0]["t"]) if words and not fallback_only else t
            t = max(HOOK_MIN_T, snap_time(t, data) if not fallback_only else t)
        else:
            t = snap_time(t, data)
        if t > end_t - MIN_GAP:
            continue
        ev = {"t": round(t, 3), "role": "other" if role == "build" else role}
        (extra if role in ("build", "other") else hi).append(ev)
    return hi, extra, fallback_only


def visible_ratio(cues: list[Cue], duration: float) -> float:
    """Share of `duration` the mascot is on screen: from a non-exit cue to the next exit."""
    if duration <= 0:
        return 0.0
    vis_from: float | None = None
    total = 0.0
    for c in sorted(cues, key=lambda c: c.t):
        if c.move in MOVES_EXIT:
            if vis_from is not None:
                total += max(0.0, min(c.t, duration) - vis_from)
                vis_from = None
        elif vis_from is None:
            vis_from = c.t
    if vis_from is not None:
        total += max(0.0, duration - vis_from)
    return total / duration


def _build(events: list[dict], locked: list[Cue], data: dict, kit: Kit, hold: float) -> list[Cue]:
    duration = float(data.get("duration") or 0.0)
    end_t = duration - END_MARGIN if duration > 0 else HOOK_MIN_T + MIN_GAP
    cut_pool = sorted({float(c) for c in data.get("cuts") or []}
                      | {float(s["t"]) for s in data.get("scenes") or []}
                      | {float(b["t"]) for b in data.get("beats") or []})
    out: list[Cue] = []
    state = {"visible": False, "last_cue": -math.inf, "last_ev": -math.inf}

    def free_of_locks(t: float) -> bool:
        return all(abs(t - c.t) >= LOCK_GAP for c in locked)

    def pick_exit(target: float, limit: float) -> float | None:
        """First cut at/after `target`, else `target` itself; must fit the spacing rules."""
        later = [c for c in cut_pool if c >= target - 1e-9][:3]
        for c in later + [snap_time(target, data), target]:
            fits_end = duration <= 0 or c <= duration - 0.1
            if fits_end and c <= limit and c - state["last_cue"] >= MIN_GAP and free_of_locks(c):
                return round(c, 3)
        return None

    def sink(until: float, move: str) -> None:
        if state["visible"] and until - state["last_ev"] > hold:
            te = pick_exit(state["last_ev"] + hold, until - MIN_GAP if move == EXIT_MID else until)
            if te is not None:
                out.append(Cue(t=te, move=move))
                state.update(visible=False, last_cue=te)

    items = sorted([(c.t, 0, c) for c in locked] + [(e["t"], 1, e) for e in events],
                   key=lambda x: (x[0], x[1]))
    for t, kind, item in items:
        if kind == 0:                          # a locked cue just changes the state
            state["visible"] = item.move not in MOVES_EXIT
            state["last_cue"] = t
            if state["visible"]:
                state["last_ev"] = t
            continue
        if not free_of_locks(t):
            continue
        sink(t, EXIT_MID)
        if t - state["last_cue"] < MIN_GAP:
            continue
        move, presets = RULES[item["role"]]
        body, face = _pose(kit, presets)
        if not state["visible"] and item["role"] != "hook":
            move = ENTER_MOVE
        elif not state["visible"] and move not in ("rise", "peek"):
            move = "rise"
        out.append(Cue(t=t, move=move, body=body, face=face))
        state.update(visible=True, last_cue=t, last_ev=t)
    if state["visible"]:                               # still up at the end → slide out
        t = max(end_t, state["last_cue"] + MIN_GAP)
        if (duration <= 0 or t <= duration - 0.1) and free_of_locks(t):
            out.append(Cue(t=round(t, 3), move=EXIT_END))
    return out


def suggest_cues(project_dir: Path, cfg: MascotConfig, kit: Kit, data: dict | None = None) -> list[Cue]:
    """The full new cue list (locked cues + fresh suggestions), sorted by t. `data` is
    timeline.timeline_data(project_dir) when the caller already has it."""
    if data is None:
        data = timeline_data(Path(project_dir))
    duration = float(data.get("duration") or 0.0)
    locked = [c for c in cfg.sorted_cues() if c.locked]
    highlights, extras, fallback_only = _events(data, kit)

    if fallback_only or duration <= 0:
        plans = [(0, math.inf, highlights)]
    else:
        merged = sorted(highlights + extras, key=lambda e: e["t"])
        plans = [(0, h, highlights) for h in HOLD_GRID]
        if extras:
            plans += [(1, h, merged) for h in HOLD_GRID]

    best_key, best = None, None
    for tier, hold, evs in plans:
        fresh = _build(evs, locked, data, kit, hold)
        ratio = visible_ratio(fresh + locked, duration) if duration > 0 else VISIBLE_TARGET
        in_band = VISIBLE_BAND[0] <= ratio <= VISIBLE_BAND[1]
        key = (0 if in_band else 1, tier if in_band else abs(ratio - VISIBLE_TARGET),
               abs(ratio - VISIBLE_TARGET), tier, hold)
        if best_key is None or key < best_key:
            best_key, best = key, fresh
    return sorted(locked + (best or []), key=lambda c: c.t)


def clear_suggestions(cfg: MascotConfig) -> list[Cue]:
    """'Xoá gợi ý': only the locked cues."""
    return [c for c in cfg.sorted_cues() if c.locked]
