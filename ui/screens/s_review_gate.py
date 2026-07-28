"""
Screen: Review Beats (pre-TTS panel-lock gate).

Sits between narration (stage 4) and TTS (stage 6). stages/review_gate.py
(built separately) writes review/candidates.json — one entry per narration
beat with its research source and ranked panel candidates. Here the user can
tweak the narration text, select 2-5 candidate panels per beat (rendered as
sub-shots), and Approve, which stamps review/locks.json with a sha1 of
narration.json so a later text edit can be detected as stale.

This screen reads/writes review/candidates.json + locks.json directly per the
shared contract, and never calls stages/review_gate's matcher pipeline itself —
EXCEPT the "Rebuild candidates" button (shown on a beat with zero candidates,
e.g. a Master-inserted scene), which imports build_candidates() to regenerate
the whole project's shortlist on demand.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import os
import subprocess
import sys
import time
from typing import Callable

import flet as ft

from pathlib import Path

from config import PROJECTS_ROOT
from ..bridge import (
    image_b64, is_answer_project, load_hidden_panels, load_narration, load_preprocessed,
    load_review_candidates, load_review_locks, list_review_projects, narration_sha1,
    review_thumb_b64, review_thumb_path, run_blocking, save_hidden_panels,
    save_narration_edits, save_review_locks,
)
from ..custom_image import add_custom_image, enrich_custom_image, list_custom_images
from ..intro_import import import_intro_image, remove_intro_image
from ..layout import primary_button, secondary_button, three_col
from ..state import AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)
from stages.subject_panels import load_subject_panels
from stages.review_gate import remap_locks_by_src


THUMB_H = 120     # candidate tile thumb height — small enough for ~20 tiles on screen at
                  # once (the pool is identical across beats, so browsing speed beats tile
                  # size); click a tile for the 520px lightbox when a panel needs judging.
MIN_PANELS = 2
MAX_PANELS = 5

# Candidate GALLERY = vertical GridView (was a 1-row horizontal strip showing ~4 tiles).
GRID_EXTENT = 130   # max cell width → Flutter uses ceil(width / max_extent) columns
GRID_ASPECT = 0.67  # cell width / height
GRID_SPACING = 8
GRID_H = 560        # grid viewport height — fixed so the card can't grow unbounded


def _anchor_key(page_ref, panel_ref) -> tuple[int, int]:
    p = int(page_ref) if page_ref is not None else -1
    pan = int(panel_ref) if panel_ref is not None else -1
    return (p, pan)


def _anchor_label(page_ref, panel_ref) -> str:
    if page_ref is None:
        return ""
    p, pan = _anchor_key(page_ref, panel_ref)
    return f"p{p:02d} · full" if pan < 0 else f"p{p:02d} · pan{pan:02d}"


def _normalize_lock_panels(raw: dict | None) -> list[dict]:
    """A scene's locks.json entry as a list of {"page","panel"[,"src"]} dicts. Handles
    both the current v2 shape ({"panels": [...]}) and the old v1 single-panel
    shape ({"page","panel"}) so a project locked before the multi-select
    upgrade still loads as a 1-item selection. Keeps each panel's "src" stamp (the
    source-image basename, used by remap_locks_by_src) through the round-trip — dropping it
    here would erase it the next time an unrelated toggle rewrites this beat's panel list."""
    if not raw:
        return []
    if "panels" in raw:
        out = []
        for p in raw.get("panels") or []:
            d = {"page": int(p["page"]), "panel": int(p["panel"])}
            if p.get("src"):
                d["src"] = str(p["src"])
            out.append(d)
        return out
    if "page" in raw:
        return [{"page": int(raw["page"]), "panel": int(raw["panel"])}]
    return []


def _normalize_lock_custom_image(raw: dict | None) -> str | None:
    """A scene's locks.json entry as a Master-picked custom image path ("review/custom/
    <file>"), or None. Additive v3 lock shape alongside the page/panel v1/v2 shapes above —
    mirrors stages.review_gate.lock_custom_image (see that docstring for the contract)."""
    if not raw:
        return None
    ci = raw.get("custom_image")
    return str(ci) if ci else None


def _beat_key(beat: dict) -> str:
    """The beat's lock key. `beat_key` is the phase-2 contract field; a beat from a
    project reviewed before that upgrade lacks it, so fall back to str(scene_id) —
    identical to the key every existing locks.json already uses."""
    bk = beat.get("beat_key")
    return str(bk) if bk else str(int(beat.get("scene_id") or 0))


def _beat_unit(beat: dict) -> str:
    """"scene" (old default, MULTI-select) | "fragment" | "intro" | "outro" (all SINGLE-select).
    Everything below keys off `unit == "scene"` for the multi-select/cap-2 behaviour, so a new
    single-panel unit needs no other change."""
    return str(beat.get("unit") or "scene")


def _frag_idx_from_key(beat_key: str) -> int:
    """0-based fragment index from a "<scene_id>:<frag_idx>" beat_key."""
    if ":" not in beat_key:
        return 0
    try:
        return int(beat_key.rsplit(":", 1)[1])
    except ValueError:
        return 0


def _row_label(beat: dict, beat_key: str, unit: str) -> str:
    """Row label. intro/outro get a spelled-out prefix so Master sees WHERE in the video the
    row sits; a fragment row is prefixed with its scene + fragment number. Every row now
    renders its text in an editable TextField instead (see _beat_card), so this is the
    labelling contract those fields' short labels mirror."""
    text = str(beat.get("narration_text", ""))
    if unit == "intro":
        return f"INTRO (cold-open): {text}"
    if unit == "outro":
        return f"OUTRO: {text}"
    if unit == "fragment":
        scene_id = int(beat.get("scene_id") or 0)
        return f"s{scene_id} · mảnh {_frag_idx_from_key(beat_key) + 1}: {text}"
    return text


def _init_pre_selected(beats: list[dict], locks: dict,
                       src_by_page: dict[int, str] | None = None) -> None:
    """Seed `locks` (in place) with each beat's pre_selected panels when locks.json has
    no entry for that beat_key yet — an existing lock always wins, and a beat with no
    (or empty) pre_selected is a no-op. Old-format beats (no beat_key/pre_selected
    fields) are untouched, matching prior behaviour exactly. Each seeded panel is stamped
    with "src" (like _toggle_candidate) when `src_by_page` is given, so an auto-accepted
    pre_selected pick survives a later page renumber via remap_locks_by_src too."""
    src_by_page = src_by_page or {}
    for beat in beats:
        bk = _beat_key(beat)
        if bk in locks:
            continue
        panels = []
        for p in (beat.get("pre_selected") or []):
            if p.get("page") is None:
                continue
            pg, pn = int(p["page"]), int(p["panel"])
            d = {"page": pg, "panel": pn}
            src = src_by_page.get(pg)
            if src:
                d["src"] = src
            panels.append(d)
        if panels:
            locks[bk] = {"panels": panels, "source": "pre_selected"}


def _chip(label: str, color: str, *, visible: bool = True) -> ft.Container:
    return ft.Container(
        content=ft.Text(label, size=9, color=color, weight=ft.FontWeight.BOLD),
        padding=ft.padding.symmetric(horizontal=8, vertical=3),
        border=ft.border.all(1, color), border_radius=3,
        visible=visible,
    )


# ─── ADD BEAT / DELETE UNIT: pure data-model helpers (no flet — testable
# standalone). These mutate narration.json scenes and locks.json in memory;
# the UI wiring below persists them via bridge.save_narration_edits /
# save_review_locks and un-approves (narration changed → must re-review). ──

_HANGER_CHARS = ",-–—"  # comma, hyphen, en-dash, em-dash — the only
# punctuation stage_3.beat_split._verbatim_ok lets vanish at a split point (its
# word-token regex `[a-z0-9]+` ignores all of these anyway, so dropping them
# here can never break the verbatim invariant).
_CONNECTIVES = {"and", "but", "then", "so", "yet", "however", "meanwhile"}


def _frag_text(vb) -> str:
    """A visual-beat's narration words. MICRO_MOMENT beats are {"text","page","panel"} dicts
    (writer-picks-panel); recap/Q&A beats are plain strings — either way return the text.
    Mirrors stages.stage_5.shots._vb_text so both modes flow through the same op path."""
    return str(vb.get("text", "")).strip() if isinstance(vb, dict) else str(vb).strip()


def _frag_with_text(vb, text: str):
    """A fragment shaped like `vb` but carrying `text`: a dict KEEPS its {"page","panel"} pin
    (Master relocks after if the split/merge moved the moment), a str stays a str. This is what
    lets split/merge work identically for the string (Q&A/recap) and dict (micro) modes."""
    if isinstance(vb, dict):
        out = dict(vb)
        out["text"] = text
        return out
    return text


def split_fragment_at(scene: dict, frag_idx: int, word_idx: int) -> None:
    """Split scene["visual_beats"][frag_idx] into two fragments, after its
    word_idx-th (1-based) whitespace word. Mutates `scene` in place. Both halves inherit the
    original fragment's shape (a dict keeps its page/panel pin, a str stays a str). Raises
    ValueError if frag_idx/word_idx is out of bounds or the fragment has <=1 word."""
    beats = scene.get("visual_beats") or []
    if not (0 <= frag_idx < len(beats)):
        raise ValueError(f"frag_idx {frag_idx} out of range (0..{len(beats) - 1})")
    frag = beats[frag_idx]
    words = _frag_text(frag).split()
    if len(words) <= 1:
        raise ValueError("fragment has <=1 word — nothing to split")
    if not (1 <= word_idx < len(words)):
        raise ValueError(f"word_idx {word_idx} out of bounds (1..{len(words) - 1})")
    head = " ".join(words[:word_idx]).rstrip(_HANGER_CHARS + " ").strip()
    tail = " ".join(words[word_idx:]).lstrip(_HANGER_CHARS + " ").strip()
    if not head or not tail:
        raise ValueError("split would produce an empty fragment")
    scene["visual_beats"] = (beats[:frag_idx]
                             + [_frag_with_text(frag, head), _frag_with_text(frag, tail)]
                             + beats[frag_idx + 1:])


def merge_fragment_into_prev(scene: dict, frag_idx: int) -> None:
    """Delete scene["visual_beats"][frag_idx], folding its words into the fragment
    BEFORE it (frag_idx==0 has no "before" — folds into the fragment AFTER instead). The
    surviving fragment keeps ITS OWN page/panel pin (dict mode); text order is always the
    earlier fragment's words then the later's, so the verbatim concat is preserved. Mutates
    `scene` in place. Raises ValueError if the scene has only 1 fragment or frag_idx is OOB."""
    beats = scene.get("visual_beats") or []
    if len(beats) <= 1:
        raise ValueError("scene has only 1 fragment — cannot delete it")
    if not (0 <= frag_idx < len(beats)):
        raise ValueError(f"frag_idx {frag_idx} out of range (0..{len(beats) - 1})")
    a, b = (beats[0], beats[1]) if frag_idx == 0 else (beats[frag_idx - 1], beats[frag_idx])
    merged_text = f"{_frag_text(a)} {_frag_text(b)}".strip()
    # survivor = the fragment that stays in place: the one AFTER on a frag_idx==0 delete, else
    # the one BEFORE. It donates its pin to the merged fragment (Master relocks if needed).
    survivor = b if frag_idx == 0 else a
    merged = _frag_with_text(survivor, merged_text)
    if frag_idx == 0:
        scene["visual_beats"] = [merged] + beats[2:]
    else:
        scene["visual_beats"] = beats[:frag_idx - 1] + [merged] + beats[frag_idx + 1:]


def drop_fragment(scene: dict, frag_idx: int) -> bool:
    """Delete scene["visual_beats"][frag_idx] OUTRIGHT — its words are GONE, not folded
    into a sibling (that's merge_fragment_into_prev). scene["text"] is REDEFINED as the
    concat of the surviving fragments (str: joined by " "; dict {"text","page","panel"}:
    joined by _frag_text — one path covers both Q&A/recap str frags and micro dict frags),
    so the verbatim invariant holds by construction: after the drop,
    stages.stage_3.beat_split._verbatim_ok(scene["text"], [each frag's text]) is always True.

    Returns True after dropping. Returns False WITHOUT mutating when the scene has only ONE
    fragment — dropping the last line would leave an empty scene, so the caller deletes the
    whole scene instead. Mutates in place. Raises ValueError if frag_idx is out of range."""
    beats = scene.get("visual_beats") or []
    if not (0 <= frag_idx < len(beats)):
        raise ValueError(f"frag_idx {frag_idx} out of range (0..{len(beats) - 1})")
    if len(beats) <= 1:
        return False
    remaining = beats[:frag_idx] + beats[frag_idx + 1:]
    scene["visual_beats"] = remaining
    scene["text"] = " ".join(_frag_text(vb) for vb in remaining).strip()
    return True


def apply_fragment_edits(narration: dict, edits: dict[tuple[int, int], str]) -> dict:
    """Write PER-FRAGMENT text edits into `narration` — MUTATES it in place and returns it
    (the caller owns a fresh load_narration() dict).

    `edits` maps (scene_id, frag_idx) → that fragment's new text. A dict fragment
    ({"text","page","panel"} — micro writer-picks-panel) KEEPS its page/panel pin and a str
    fragment stays a str (_frag_with_text), so both modes flow through one path. Each touched
    scene's `text` is then REDEFINED as the concat of its fragments — exactly what
    drop_fragment does — so stages.stage_3.beat_split._verbatim_ok(scene["text"], frags) holds
    BY CONSTRUCTION and Stage 5's word-position shot bucketing stays aligned; no validator
    needed. word_count/target_seconds are refreshed for those scenes and the narration totals
    recomputed the same way ui.screens.s6_review._save does.

    An edit naming a scene_id or frag_idx that no longer exists is silently SKIPPED (the card
    list can be one op behind narration.json). A fragment edited to blank is dropped — it would
    render a wordless shot; blanking EVERY fragment of a scene is ignored instead, deleting a
    whole beat is the trash icon's job, not the text box's."""
    by_sid: dict[int, dict[int, str]] = {}
    for (sid, idx), text in (edits or {}).items():
        by_sid.setdefault(int(sid), {})[int(idx)] = str(text)
    wps = float(narration.get("words_per_second") or 3.4)
    touched = False
    for s in narration.get("scenes") or []:
        pending = by_sid.get(int(s.get("scene_id") or 0))
        if not pending:
            continue
        frags = list(s.get("visual_beats") or [])
        hits = [i for i in pending if 0 <= i < len(frags)]
        if not hits:
            continue
        for i in hits:
            frags[i] = _frag_with_text(frags[i], pending[i].strip())
        frags = [vb for vb in frags if _frag_text(vb)]
        if not frags:
            continue
        s["visual_beats"] = frags
        s["text"] = " ".join(_frag_text(vb) for vb in frags).strip()
        s["word_count"] = len(s["text"].split())
        s["target_seconds"] = round(s["word_count"] / wps, 2) if wps else 0.0
        touched = True
    if touched:
        total = sum(int(s.get("word_count") or 0) for s in narration.get("scenes") or [])
        narration["total_word_count"] = total
        narration["estimated_duration_seconds"] = round(total / wps, 2) if wps else 0.0
    return narration


def insert_scene_at(narration: dict, list_index: int, text: str, neighbor: dict) -> dict:
    """Insert a brand-new scene into narration["scenes"] at list_index. scene_id is
    max(existing)+1 — existing ids are NEVER renumbered (locks.json keys off them and
    must stay stable). page_ref/beat_id are copied from `neighbor` (panel_ref is
    always -1 — a fresh scene has no anchored panel until Master picks one in Review
    Beats). Updates narration's word/duration totals. Returns the new scene dict."""
    scenes = narration.setdefault("scenes", [])
    max_id = max((int(s.get("scene_id") or 0) for s in scenes), default=0)
    wps = float(narration.get("words_per_second") or 3.4)
    wc = len(text.split())
    new_scene = {
        "scene_id": max_id + 1,
        "text": text,
        "page_ref": int(neighbor.get("page_ref") or 0),
        "panel_ref": -1,
        "word_count": wc,
        "target_seconds": round(wc / wps, 2),
        "connective": None,
        "beat_id": int(neighbor.get("beat_id") or 0),
        "is_intro": False,
        "is_outro": False,
        "visual_beats": [text],
    }
    scenes.insert(list_index, new_scene)
    narration["total_word_count"] = int(narration.get("total_word_count") or 0) + wc
    narration["estimated_duration_seconds"] = round(
        float(narration.get("estimated_duration_seconds") or 0.0) + wc / wps, 2)
    return new_scene


def remap_fragment_locks(locks: dict, sid: int, from_idx: int, delta: int) -> dict:
    """Shift every "<sid>:<i>" key with i >= from_idx by `delta` (+1 after a split
    makes room for the new second half; -1 after a merge closes the gap). Exception:
    on a merge (delta<0), the key AT from_idx itself is the fragment that just
    vanished — its lock is DROPPED rather than shifted (shifting would collide it
    with the surviving neighbor's own untouched key at from_idx-1). Non-matching
    keys pass through unchanged. Returns a NEW dict — never mutates `locks`."""
    out: dict = {}
    prefix = f"{sid}:"
    for bk, val in locks.items():
        if not bk.startswith(prefix):
            out[bk] = val
            continue
        try:
            idx = int(bk[len(prefix):])
        except ValueError:
            out[bk] = val
            continue
        if idx < from_idx:
            out[bk] = val
        elif idx == from_idx and delta < 0:
            continue  # merge: this fragment was folded away — no lock survives
        else:
            out[f"{sid}:{idx + delta}"] = val
    return out


def _remap_beats_list(beats: list[dict], sid: int, from_idx: int, delta: int) -> list[dict]:
    """Same shift rule as remap_fragment_locks, applied to review["beats"] rows
    (keyed by each row's own "beat_key" field) so the in-memory candidate rows stay
    aligned with narration.json right after a fragment split/merge — no
    build_candidates re-run needed. Order is preserved."""
    by_key = {b.get("beat_key"): b for b in beats}
    remapped = remap_fragment_locks(by_key, sid, from_idx, delta)
    out = []
    for key, b in remapped.items():
        b["beat_key"] = key
        out.append(b)
    return out


# ─── HIDDEN PANELS: project-wide candidate blacklist (pure, no flet) ────────
# The candidate pool is IDENTICAL across beats (same page range), so a panel that is
# useless for one beat is useless for all of them. Hiding it once drops it everywhere
# instead of making Master skip past it on every single beat.


def hidden_set(doc: dict | None) -> set[tuple[int, int]]:
    """review/hidden_panels.json → {(page, panel), ...}. Missing/garbage entries are
    skipped rather than raising — an old project has no file at all (empty set)."""
    out: set[tuple[int, int]] = set()
    for h in (doc or {}).get("hidden") or []:
        try:
            out.add((int(h["page"]), int(h["panel"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def filter_hidden(beats: list[dict], hidden: set[tuple[int, int]]) -> list[dict]:
    """New beats list with every hidden (page, panel) dropped from EVERY beat's
    `candidates`. All other fields pass through untouched. The unfiltered pool is kept
    on the row as `candidates_all`, so re-running this with a smaller `hidden` set puts
    the panels back (that's how "Hiện lại tất cả" works without re-reading
    candidates.json). Pure — never mutates its inputs."""
    out: list[dict] = []
    for b in beats:
        row = dict(b)
        pool = list(b.get("candidates_all") or b.get("candidates") or [])
        row["candidates_all"] = pool
        row["candidates"] = [
            c for c in pool
            if (int(c.get("page", -1)), int(c.get("panel", -1))) not in hidden
        ]
        out.append(row)
    return out


def beats_locking(locks: dict, page: int, panel: int) -> list[str]:
    """beat_keys whose lock currently SELECTS this panel (sorted). Hiding a panel that
    is still selected somewhere must drop those locks — otherwise the render would use a
    panel Master can no longer see, and no card would show the selection."""
    key = (int(page), int(panel))
    return sorted(bk for bk, lock in (locks or {}).items()
                  if key in {(p["page"], p["panel"]) for p in _normalize_lock_panels(lock)})


# ─── CANDIDATE GRID geometry (pure) ─────────────────────────────────────────


def grid_geometry(grid_w: float) -> tuple[int, float]:
    """(columns, row pitch px) of a GridView(max_extent=GRID_EXTENT, spacing=GRID_SPACING,
    child_aspect_ratio=GRID_ASPECT) laid out in `grid_w` px. Mirrors Flutter's
    SliverGridDelegateWithMaxCrossAxisExtent: columns = ceil(width / max_extent)."""
    cols = max(1, math.ceil(max(grid_w, 1.0) / GRID_EXTENT))
    cell_w = (max(grid_w, 1.0) - GRID_SPACING * (cols - 1)) / cols
    return cols, cell_w / GRID_ASPECT + GRID_SPACING


def grid_scroll_offset(index: int, grid_w: float) -> float:
    """Pixel offset that brings tile #index's ROW to the top of the grid viewport.
    Used instead of scroll_to(scroll_key=…) because the grid keeps
    build_controls_on_demand=True (200+ tiles per beat) and flet 0.85 documents
    scroll_to as ineffective for controls that build their items dynamically —
    an arithmetic offset needs no built widget to aim at."""
    cols, pitch = grid_geometry(grid_w)
    return max(0.0, (max(index, 0) // cols) * pitch)


def reconcile_beats(beats: list[dict], narration: dict, *, qa_mode: bool) -> list[dict]:
    """Realign the candidate rows (from candidates.json) to the CURRENT narration.json so a
    UI op keyed by beat_key/frag_idx can never target a stale, missing, or ghost row —
    candidates.json is a one-shot matcher export and drifts from narration whenever a build
    was interrupted or Master edited beats:
      (a) DROP a row whose scene is gone from narration, or whose frag_idx >= the scene's live
          fragment count (a "ghost" row — e.g. an 8:1 left behind when scene 8 dropped to one
          fragment, or a 3:*/5:* left behind when the scene was deleted);
      (b) ADD an empty row (candidates=[], source={}) for a narration beat that has NO row yet —
          reusing the same zero-candidate "No candidates — Rebuild/Custom image" fallback a
          freshly-inserted scene already relies on (e.g. a 13:0 the export never wrote);
      (c) take each row's narration_text from the CURRENT narration, never the (possibly stale)
          text baked into candidates.json.
    Mirrors stages.review_gate's own row derivation exactly (INTRO row + OUTRO row for EVERY mode
    when narration has that scene — Master 2026-07-24; a story scene's text fragments each become
    a "<sid>:<i>" fragment row, else a single "<sid>" scene row) so the reconciled list is
    byte-for-byte what a fresh build would emit, minus the matcher's candidates (preserved from the
    existing rows when present). Rows come back in narration (render) order: intro, body…, outro —
    so a project whose candidates.json predates the bookend rows (no "intro"/"outro" beat) gets
    them here as (b)-style empty rows instead of losing the pick. Pure — never mutates its inputs.

    `qa_mode` is accepted for call-site compatibility and no longer used: Q&A gets the bookend rows
    too (its intro/outro panel used to be auto-picked from subject_panels.json)."""
    by_key = {str(b.get("beat_key") or ""): b for b in beats}

    def _row(bk: str, unit: str, scene: dict, text: str) -> dict:
        sid = int(scene.get("scene_id") or 0)
        old = by_key.get(bk)
        if old is not None:
            row = dict(old)                     # keep candidates/source/page_ref/pre_selected
            row["beat_key"], row["unit"], row["scene_id"] = bk, unit, sid
            row["narration_text"] = text        # (c) refresh from narration
            return row
        return {                                # (b) new empty row = zero-candidate fallback
            "scene_id": sid, "narration_text": text,
            "page_ref": scene.get("page_ref") if scene.get("page_ref") is not None else None,
            "panel_ref": None, "source": {}, "candidates": [],
            "beat_key": bk, "pre_selected": [], "unit": unit,
        }

    scenes = narration.get("scenes") or []
    out: list[dict] = []
    intro = next((s for s in scenes if s.get("is_intro")), None)
    outro = next((s for s in scenes if s.get("is_outro")), None)
    if intro is not None:
        out.append(_row("intro", "intro", intro, str(intro.get("text", "") or "")))
    for s in scenes:
        if s.get("is_intro") or s.get("is_outro"):
            continue
        sid = int(s.get("scene_id") or 0)
        frags = [vb for vb in (s.get("visual_beats") or []) if _frag_text(vb)]
        if frags:
            for i, vb in enumerate(frags):
                out.append(_row(f"{sid}:{i}", "fragment", s, _frag_text(vb)))
        else:
            out.append(_row(str(sid), "scene", s, str(s.get("text", "") or "")))
    if outro is not None:
        out.append(_row("outro", "outro", outro, str(outro.get("text", "") or "")))
    return out


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    project = state.project_name
    review = load_review_candidates(project) if project else None

    if not review or not (review.get("beats") or []):
        return _empty_state(page, state, on_go, on_state_change)

    narration = load_narration(project) or {}
    # Realign the candidate rows to the CURRENT narration BEFORE anything reads them (see
    # reconcile_beats): drops ghost rows, adds missing beats, refreshes stale text — so every
    # op below keys off a row that actually exists in narration, and _init_pre_selected never
    # seeds a lock from a ghost row's pin.
    review["beats"] = reconcile_beats(review.get("beats") or [], narration,
                                      qa_mode=is_answer_project(project))
    # Project-wide hidden-panel blacklist, applied to EVERY beat right here so no card
    # below can ever render a panel Master already dismissed (see filter_hidden).
    hidden_doc = load_hidden_panels(project)
    hidden = hidden_set(hidden_doc)
    review["beats"] = filter_hidden(review["beats"], hidden)
    # PENDING text edits, both SPARSE on purpose — an entry exists only for a box Master
    # actually typed in, so "no edit" is literally "no entry" and a save writes nothing there.
    # (Pre-seeding these with every scene's text was a stale-write trap: after a drop-line
    # redefined scene["text"] from the surviving fragments, the seeded copy still held the
    # dropped words and the next save put them back — breaking the verbatim invariant.)
    edited_text: dict[int, str] = {}                    # scene_id → whole-scene text
    #   (scene_id, frag_idx) → that ONE fragment's text; cleared per scene by _rebuild after
    #   any op that shifts fragment indices (split/merge/drop/delete).
    frag_edits: dict[tuple[int, int], str] = {}

    locks_doc = load_review_locks(project)
    locks: dict = dict(locks_doc.get("locks") or {})
    # page → source-image basename for every CURRENT preprocessed page. Used to (a) remap any
    # lock whose page has drifted since it was stamped (an earlier chapter's page count changed
    # — see remap_locks_by_src) and (b) stamp new locks below (_toggle_candidate) so a FUTURE
    # renumber can detect and fix them the same way.
    src_by_page: dict[int, str] = {
        int(pg.get("page_number", -1)): Path(str(pg.get("source_image") or "")).name
        for pg in load_preprocessed(project)
    }
    _remapped_locks = remap_locks_by_src(locks, src_by_page)
    _n_remapped = sum(1 for k, v in locks.items() if _remapped_locks.get(k) != v)
    if _n_remapped:
        print(f"[review-lock] remapped {_n_remapped} lock(s) after page renumber")
    locks = _remapped_locks
    locks_doc["locks"] = locks
    _init_pre_selected(review.get("beats") or [], locks, src_by_page)

    # Custom images (Master-added, cosine only decides PLACEMENT — never a select/reject
    # gate). Loaded once per screen build; a new add triggers on_state_change() (full
    # rebuild), same pattern as the "Build candidates" flow below re-reading candidates.json.
    custom_by_beat: dict[str, list[dict]] = {}
    for entry in list_custom_images(PROJECTS_ROOT / project):
        custom_by_beat.setdefault(str(entry.get("beat_key") or ""), []).append(entry)

    status_text = ft.Text("", size=12, color=TEXT_MUTED)
    # per-beat control refs (keyed by beat_key) so a lock/dup change repaints just that card
    card_refs: dict[str, dict] = {}

    # "Đã ẩn N panel — Hiện lại tất cả" — the only undo for the blacklist (per-panel undo
    # isn't worth the UI: the whole point is speed, and re-hiding is one click).
    unhide_btn = secondary_button("", lambda _e: _unhide_all(),
                                  icon=ft.Icons.VISIBILITY_OUTLINED)
    hidden_bar = ft.Container(
        content=unhide_btn, visible=bool(hidden),
        padding=ft.padding.only(left=28, right=28, top=4),
    )

    def _grid_w() -> float:
        """Approx px width of a candidate grid: window − nav(240) − sidebar(320) −
        cards padding(2×28) − card padding(2×14). Only feeds the page-jump offset, so a
        window resize can land the jump a row off — never a crash."""
        w = getattr(page, "width", None)
        return (float(w) if isinstance(w, (int, float)) else 1400.0) - 644.0

    def _selected_keys(beat_key: str) -> set[tuple[int, int]]:
        return {(p["page"], p["panel"])
                for p in _normalize_lock_panels(locks.get(beat_key))}

    def _dup_beat_keys() -> set[str]:
        by_panel: dict[tuple[int, int], list[str]] = {}
        for bk, lock in locks.items():
            for p in _normalize_lock_panels(lock):
                key = (p["page"], p["panel"])
                by_panel.setdefault(key, []).append(bk)
        return {bk for ids in by_panel.values() if len(ids) > 1 for bk in ids}

    def _refresh_dup_badges():
        dups = _dup_beat_keys()
        for bk, refs in card_refs.items():
            refs["dup_badge"].visible = bk in dups
            try:
                refs["dup_badge"].update()
            except Exception:
                pass

    def _show_snack(msg: str):
        sb = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    # ─── Import external intro image ────────────────────────────────────────
    # Master can open a Q&A with an image from disk instead of a comic panel. The
    # inject (sips → jpg + preprocessed page + subject_panels force_intro entry)
    # lives in ui/intro_import.py (pure, tested); this screen only calls it and
    # renders the current imports as a strip above the beat cards.
    intro_list_col = ft.Column(spacing=8)
    file_picker = ft.FilePicker()
    custom_file_picker = ft.FilePicker()
    try:
        page.services.append(file_picker)
        page.services.append(custom_file_picker)
    except Exception:
        pass

    def _intro_row(entry: dict, src_by_page: dict[int, str]) -> ft.Control:
        page_n = int(entry.get("page", -1))
        b64 = image_b64(src_by_page.get(page_n, ""))
        thumb = (ft.Image(src=b64, height=90, fit=ft.BoxFit.CONTAIN, border_radius=2)
                 if b64 else ft.Container(width=68, height=90, bgcolor=BG_ELEVATED,
                                          border=ft.border.all(1, BORDER), border_radius=2))

        def _remove(_e, pn=page_n):
            try:
                remove_intro_image(PROJECTS_ROOT / project, pn)
                _show_snack(f"Removed imported intro p{pn}.")
                _rebuild_intro_box()
            except Exception as e:
                _show_snack(f"Remove failed: {e}")

        return ft.Container(
            content=ft.Row([
                thumb,
                ft.Column([
                    _chip("IMPORTED — intro", ACCENT),
                    ft.Text(f"p{page_n}", size=10, color=TEXT_MUTED, font_family="Menlo"),
                ], spacing=4),
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=WARN,
                              tooltip="Remove imported intro", on_click=_remove),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            padding=8, border=ft.border.all(1, ACCENT), border_radius=4, bgcolor=BG_PANEL,
        )

    def _rebuild_intro_box():
        src_by_page = {int(pg.get("page_number", -1)): str(pg.get("source_image") or "")
                       for pg in load_preprocessed(project)}
        entries = [p for p in (load_subject_panels(project).get("panels") or [])
                   if p.get("force_intro")]
        intro_list_col.controls = [_intro_row(e, src_by_page) for e in entries]
        try:
            intro_list_col.update()
        except Exception:
            pass

    async def _do_import():
        try:
            files = await file_picker.pick_files(
                dialog_title="Pick an intro image (avif / jpg / png)",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["avif", "jpg", "jpeg", "png"],
                allow_multiple=False,
            )
        except Exception as e:
            _show_snack(f"File picker failed: {e}")
            return
        if not files or not files[0].path:
            return
        subject = str(load_subject_panels(project).get("subject") or "").strip() or "intro"
        try:
            entry = await run_blocking(
                import_intro_image, PROJECTS_ROOT / project, Path(files[0].path), subject)
            _show_snack(f"Imported intro image → p{entry['page']} (subject: {entry['subject']}).")
            _rebuild_intro_box()
        except Exception as e:
            _show_snack(f"Import failed: {e}")

    def _on_import_click(_e):
        page.run_task(_do_import)

    def _refresh_beat_card(beat_key: str):
        refs = card_refs.get(beat_key)
        if not refs:
            return
        unit = refs["unit"]
        selected = _selected_keys(beat_key)
        for key, tile in refs["tiles"].items():
            is_sel = key in selected
            tile.border = ft.border.all(2 if is_sel else 1, ACCENT if is_sel else BORDER)
            tile.bgcolor = BG_ELEVATED if is_sel else None
            icon = refs["icons"][key]
            icon.icon = ft.Icons.CHECK_CIRCLE if is_sel else ft.Icons.RADIO_BUTTON_UNCHECKED
            icon.icon_color = ACCENT if is_sel else TEXT_MUTED
        locked_custom = _normalize_lock_custom_image(locks.get(beat_key))
        for rel_path, ctrls in refs.get("custom_tiles", {}).items():
            is_sel = rel_path == locked_custom
            ctrls["tile"].border = ft.border.all(2 if is_sel else 1, ACCENT if is_sel else BORDER)
            ctrls["tile"].bgcolor = BG_ELEVATED if is_sel else None
            ctrls["icon"].icon = ft.Icons.CHECK_CIRCLE if is_sel else ft.Icons.RADIO_BUTTON_UNCHECKED
            ctrls["icon"].icon_color = ACCENT if is_sel else TEXT_MUTED
            try:
                ctrls["tile"].update()
                ctrls["icon"].update()
            except Exception:
                pass
        has_pick = bool(selected) or bool(locked_custom)
        refs["auto_badge"].visible = not has_pick
        cap = MAX_PANELS if unit == "scene" else 1
        refs["count_chip"].value = f"{len(selected)}/{cap} selected"
        refs["warn_chip"].visible = (
            unit == "scene" and len(selected) < MIN_PANELS and not locked_custom)
        try:
            refs["cand_row"].update()
            refs["auto_badge"].update()
            refs["count_chip"].update()
            refs["warn_chip"].update()
        except Exception:
            pass
        _refresh_dup_badges()

    def _toggle_candidate(beat_key: str, cand_page: int, cand_panel: int, unit: str):
        panels = _normalize_lock_panels(locks.get(beat_key))
        key = (cand_page, cand_panel)
        already = key in {(p["page"], p["panel"]) for p in panels}
        # Stamp the source image's basename on every NEWLY-locked panel — future page
        # renumbers (an earlier chapter's length changes) can then be detected and fixed by
        # remap_locks_by_src instead of silently pointing at the wrong art.
        src = src_by_page.get(cand_page)
        new_entry = {"page": cand_page, "panel": cand_panel, **({"src": src} if src else {})}
        if unit == "scene":
            if already:
                panels = [p for p in panels if (p["page"], p["panel"]) != key]
            elif len(panels) >= MAX_PANELS:
                _show_snack(f"Max {MAX_PANELS} panels per beat.")
                return
            else:
                panels = panels + [new_entry]
        else:  # fragment / intro — single-select: pick replaces, re-pick clears
            panels = [] if already else [new_entry]
        if panels:
            locks[beat_key] = {"panels": panels, "source": "batcave"}
        else:
            locks.pop(beat_key, None)
        save_review_locks(project, locks_doc)
        _refresh_beat_card(beat_key)

    def _toggle_custom(beat_key: str, rel_path: str):
        """Lock/unlock a custom image to this beat — v3 lock shape {"custom_image": path},
        EXCLUSIVE with the normal panel-candidates lock for this beat (picking a candidate
        panel afterwards simply overwrites it, same as toggling between v1/v2 shapes)."""
        current = _normalize_lock_custom_image(locks.get(beat_key))
        if current == rel_path:
            locks.pop(beat_key, None)
        else:
            locks[beat_key] = {"custom_image": rel_path, "source": "custom"}
        save_review_locks(project, locks_doc)
        _refresh_beat_card(beat_key)

    # ─── Hide a panel PROJECT-WIDE ──────────────────────────────────────────
    def _refresh_hidden_bar():
        # flet 0.85 buttons carry their label in `content` (the first positional arg of
        # layout.secondary_button) — assigning `.text` would be a silent no-op.
        unhide_btn.content = f"👁 Đã ẩn {len(hidden)} panel — Hiện lại tất cả"
        hidden_bar.visible = bool(hidden)
        try:
            hidden_bar.update()
        except Exception:
            pass

    def _all_beat_keys() -> frozenset:
        return frozenset(_beat_key(b) for b in (review.get("beats") or []))

    def _apply_hidden(msg: str, *, warn: bool = False):
        hidden_doc["hidden"] = [{"page": p, "panel": pan} for p, pan in sorted(hidden)]
        save_hidden_panels(project, hidden_doc)
        review["beats"] = filter_hidden(review.get("beats") or [], hidden)
        _refresh_hidden_bar()
        # EVERY card's tile set changed → all dirty (a reused card would still show the
        # hidden tile). No page.update() anywhere in here — see _rebuild's comment.
        _rebuild(msg, warn=warn, dirty=_all_beat_keys())

    def _do_hide(cand_page: int, cand_panel: int, users: list[str]):
        hidden.add((cand_page, cand_panel))
        key = (cand_page, cand_panel)
        for bk in users:
            panels = [p for p in _normalize_lock_panels(locks.get(bk))
                      if (p["page"], p["panel"]) != key]
            if panels:
                locks[bk] = {"panels": panels,
                             "source": (locks.get(bk) or {}).get("source") or "batcave"}
            else:
                locks.pop(bk, None)
        if users:
            locks_doc["approved"] = False
            locks_doc["approved_at"] = None
        save_review_locks(project, locks_doc)
        note = (f" — bỏ chọn ở beat {', '.join(users)}, cần re-approve." if users else ".")
        _apply_hidden(f"Đã ẩn p{cand_page:02d}·{cand_panel} khỏi mọi beat{note}",
                      warn=bool(users))

    def _hide_panel(cand_page: int, cand_panel: int):
        """✕ on a tile: blacklist this (page, panel) for the WHOLE project. Confirms first
        ONLY when the panel is still selected somewhere (hiding it must drop those locks and
        un-approve); an unselected panel vanishes with no dialog — that's the speed."""
        users = beats_locking(locks, cand_page, cand_panel)
        if not users:
            _do_hide(cand_page, cand_panel, [])
            return

        def _confirm(_e):
            page.pop_dialog()
            _do_hide(cand_page, cand_panel, users)

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Ẩn panel p{cand_page:02d}/{cand_panel}?"),
            content=ft.Text(f"Panel này đang được chọn ở beat {', '.join(users)} — "
                            "ẩn sẽ bỏ chọn ở đó và cần re-approve trước khi render."),
            actions=[
                ft.TextButton("Hủy", on_click=lambda _e: page.pop_dialog()),
                primary_button("Ẩn", _confirm, icon=ft.Icons.VISIBILITY_OFF_OUTLINED),
            ],
        ))

    def _unhide_all():
        n = len(hidden)
        hidden.clear()
        _apply_hidden(f"Đã hiện lại {n} panel.")

    async def _do_add_custom(beat_key: str):
        # with_data=True: in WEB mode (FLET_FORCE_WEB_SERVER=1) the picked file lives in
        # the BROWSER — FilePickerFile.path is always None there (no server filesystem
        # access), only .bytes is populated. Desktop mode still gets a real .path too;
        # requesting with_data uniformly means ONE code path (bytes-first) covers both,
        # no upload_dir/get_upload_url/on_upload wiring needed (that older upload-URL
        # dance is for picker versions with no with_data option — this installed flet
        # ships it, so it's the plain, already-available fix).
        try:
            files = await custom_file_picker.pick_files(
                dialog_title="Add a custom image for this beat",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["jpg", "jpeg", "png"],
                allow_multiple=False,
                with_data=True,
            )
        except Exception as e:
            _show_snack(f"File picker failed: {e}")
            return
        if not files:
            return
        f = files[0]
        if f.bytes is None and not f.path:
            _show_snack("File picker returned no data for that file — try again.")
            return
        try:
            entry = await run_blocking(
                add_custom_image, PROJECTS_ROOT / project, Path(f.path or f.name), beat_key,
                data=f.bytes)
        except Exception as e:
            _show_snack(f"Add image failed: {e}")
            return
        _show_snack(f"Added custom image for beat {beat_key} — enriching in background…")
        # Fire-and-forget: the UI never blocks on VLM describe / embed / Qdrant upsert.
        page.run_task(run_blocking, enrich_custom_image, PROJECTS_ROOT / project, entry["file"])
        on_state_change()  # full rebuild — cheapest way to show the new tile (rare action)

    def _save_narration_text(_e=None):
        current = load_narration(project)
        if not current:
            return
        # Per-SCENE box first (scene/intro/outro rows — they have no fragments), but NEVER for a
        # scene whose fragments were edited: apply_fragment_edits re-derives that scene's text
        # from its fragments, so a per-scene write there would just be overwritten.
        frag_sids = {sid for sid, _ in frag_edits}
        for s in current.get("scenes") or []:
            sid = int(s.get("scene_id") or 0)
            if sid in edited_text and sid not in frag_sids:
                s["text"] = edited_text[sid]
        apply_fragment_edits(current, frag_edits)
        save_narration_edits(project, current)
        nonlocal narration
        narration = current
        # narration.json changed on disk → un-approve so Master must re-approve before render,
        # same as every other op here that rewrites it.
        locks_doc["approved"] = False
        locks_doc["approved_at"] = None
        save_review_locks(project, locks_doc)
        _refresh_approve_ui(push=False)   # sets a default status → overwrite it right after
        status_text.value = "Đã lưu narration — cần re-approve trước khi render."
        status_text.color = WARN
        try:
            status_text.update()
        except Exception:
            pass

    def _do_rebuild_candidates(_e=None):
        """Full-project re-run of stages.review_gate.build_candidates — the one
        place this screen calls into that module (see docstring above). Needed for a
        beat with zero candidates (e.g. a brand-new Master-inserted scene has no
        matcher shortlist yet)."""
        async def _run():
            status_text.value = "Rebuilding candidates — may take a few minutes…"
            status_text.color = TEXT_MUTED
            try:
                status_text.update()
            except Exception:
                pass
            try:
                from stages.review_gate import build_candidates
                # k=0 → ALL panels of the beat's issue, not the top-10 shortlist. The default
                # k=10 silently hid the rest, and build_candidates' own docstring warns "the
                # correct panel sometimes ranks 11th+". Manual-first picks by eye, so the full
                # pool is the only correct pool — the thumb loader below is already built for it.
                await run_blocking(build_candidates, project, 0)
            except Exception as exc:
                _show_snack(f"Rebuild candidates failed: {exc}")
                return
            on_state_change()  # candidates.json changed on disk — full reload
        page.run_task(_run)

    def _thumb(rel_path: str, *, height: int = THUMB_H) -> ft.Control:
        # DESKTOP mode: pass the file PATH as `src`, NOT base64. With ALL panels shown
        # (100+ tiles/beat × 25 beats), embedding base64 for every tile builds 2000+ inline
        # images up front and kills the Flutter client (window vanishes / OOM). A path lets
        # the desktop client load each thumb from disk lazily as the ListView scrolls it into
        # view. (Web mode can't serve arbitrary paths → base64 fallback; this review tool is
        # desktop-only — see review_thumb_path / review_thumb_b64 in bridge.py.)
        path = review_thumb_path(project, rel_path)
        if path:
            return ft.Image(src=path, height=height, fit=ft.BoxFit.CONTAIN, border_radius=2)
        return ft.Container(
            width=int(height * 0.75), height=height, bgcolor=BG_ELEVATED,
            border=ft.border.all(1, BORDER), border_radius=2, alignment=ft.Alignment.CENTER,
            content=ft.Text("no thumb", size=9, color=TEXT_MUTED, font_family="Menlo"),
        )

    def _open_preview(beat_key: str, unit: str, scene_id: int, key: tuple[int, int], c: dict):
        """Lightbox: same crop, shown large, with an Add/Remove toggle so
        selecting stays reachable without leaving the dialog."""
        def _handler(_e):
            desc = str(c.get("desc") or "")
            dialog_line = c.get("dialog")

            def _toggle(_e2, bk=beat_key, u=unit, k=key):
                _toggle_candidate(bk, k[0], k[1], u)
                page.pop_dialog()

            body: list[ft.Control] = [
                ft.Container(
                    content=_thumb(c.get("thumb", ""), height=520),
                    alignment=ft.Alignment.CENTER, expand=True,
                ),
            ]
            if desc:
                body.append(ft.Text(desc, size=12, color=TEXT_MUTED))
            if dialog_line:
                body.append(ft.Text(f"“{dialog_line}”", size=12,
                                     color=TEXT_PRIMARY, italic=True))

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(f"Scene {scene_id:02d} — p{key[0]:02d}·{key[1]}"),
                content=ft.Container(width=640, height=620,
                                      content=ft.Column(body, spacing=10, expand=True)),
                actions=[
                    ft.TextButton("Close", on_click=lambda _e: page.pop_dialog()),
                    primary_button("Add / Remove this panel", _toggle,
                                   icon=ft.Icons.CHECK_CIRCLE_OUTLINE),
                ],
            )
            page.show_dialog(dialog)
        return _handler

    def _scroll_step(row: ft.GridView, dy: int):
        # ponytail: no trackpad → ▲/▼ page the candidate grid one viewport at a time.
        def _click(_e):
            page.run_task(row.scroll_to, delta=dy, duration=400,
                          curve=ft.AnimationCurve.EASE_OUT)
        return _click

    def _beat_card(beat: dict) -> ft.Control:
        scene_id = int(beat.get("scene_id") or 0)
        beat_key = _beat_key(beat)
        unit = _beat_unit(beat)
        anchor_key = _anchor_key(beat.get("page_ref"), beat.get("panel_ref"))
        selected = _selected_keys(beat_key)

        # Editable narration on EVERY row. A fragment row edits ITS OWN clause (keyed
        # (scene_id, frag_idx) in frag_edits) — apply_fragment_edits writes it back into
        # scene["visual_beats"][i] and re-derives scene["text"] from the fragments, so the
        # verbatim invariant Stage 5 depends on holds by construction. scene/intro/outro rows
        # have no fragments and keep the per-scene box (edited_text[scene_id]).
        if unit == "fragment":
            frag_idx = _frag_idx_from_key(beat_key)
            frag_field = ft.TextField(
                label=f"s{scene_id} · mảnh {frag_idx + 1}",
                value=frag_edits.get((scene_id, frag_idx),
                                     str(beat.get("narration_text", ""))),
                multiline=True, min_lines=2, max_lines=5,
                border_color=BORDER, focused_border_color=ACCENT, text_size=13,
            )

            def _on_frag_text(e, key=(scene_id, frag_idx)):
                frag_edits[key] = e.control.value or ""
            frag_field.on_change = _on_frag_text
            text_control: ft.Control = frag_field
        else:
            text_field = ft.TextField(
                value=edited_text.get(scene_id, str(beat.get("narration_text", ""))),
                # intro/outro carry no scene number Master can recognise → say what they ARE
                # (the same wording _row_label uses for these rows).
                label={"intro": "INTRO (cold-open)", "outro": "OUTRO"}.get(unit),
                multiline=True, min_lines=2, max_lines=5,
                border_color=BORDER, focused_border_color=ACCENT, text_size=13,
            )

            def _on_text(e, sid=scene_id):
                edited_text[sid] = e.control.value or ""
            text_field.on_change = _on_text
            text_control = text_field

        source = beat.get("source") or {}
        src_label = " · ".join(x for x in (source.get("title"), source.get("issue")) if x)
        src_url = source.get("url") or ""

        def _open(url: str):
            def _click(_e, u=url):
                # flet 0.85 wraps launch_url in @deprecated, breaking
                # iscoroutinefunction; run_task needs a real coroutine function.
                async def _go():
                    await page.launch_url(u)
                page.run_task(_go)
            return _click

        source_items: list[ft.Control] = []
        # Comic Vine cross-check WARN chip — a flagged item means the cited issue may be
        # wrong (couldn't find it / character not in credits); Master should re-check the
        # source BEFORE locking panels from a possibly-wrong download.
        if source.get("verified") is False:
            source_items.append(ft.Container(
                content=ft.Text(
                    f"⚠ UNVERIFIED — {source.get('verify_note') or 'Comic Vine could not confirm this issue'}",
                    size=11, color="#FFB000", weight=ft.FontWeight.BOLD),
                bgcolor="#3A2A00", border_radius=6,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                tooltip="Comic Vine cross-check flagged this item — the downloaded issue may not match the research.",
            ))
        # Moment-present WARN (stronger, red) — the vision judge found NO panel in the
        # cited issue that depicts this beat's moment, so the issue number is likely wrong
        # (the moment lives in a neighbouring issue). Master should change the source.
        if source.get("moment_warn"):
            source_items.append(ft.Container(
                content=ft.Text(f"⛔ {source['moment_warn']}", size=11,
                                color="#FF5555", weight=ft.FontWeight.BOLD),
                bgcolor="#3A0000", border_radius=6,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                tooltip="No panel in the cited issue depicts this moment — likely the wrong issue number.",
            ))
        if src_label:
            if src_url:
                source_items.append(ft.TextButton(
                    src_label, on_click=_open(src_url),
                    style=ft.ButtonStyle(padding=ft.padding.all(0)),
                ))
            else:
                source_items.append(ft.Text(src_label, size=11, color=TEXT_MUTED))
        for i, u in enumerate(source.get("research_urls") or [], start=1):
            source_items.append(ft.TextButton(
                f"research {i}", on_click=_open(u),
                style=ft.ButtonStyle(padding=ft.padding.all(0)),
            ))

        drawable_moment = source.get("drawable_moment")

        tiles: dict[tuple[int, int], ft.Container] = {}
        icons: dict[tuple[int, int], ft.IconButton] = {}
        # ORDER: always PAGE order (p, panel) — the panel is hand-picked by eye now, so a
        # predictable page-by-page browse beats a cosine/vlm ranking (which is empty under
        # PANEL_TEXT_EMBED=0 anyway). Old projects that still carry scores also sort page-order.
        _cands = beat.get("candidates") or []
        _ordered = sorted(_cands, key=lambda c: (int(c.get("page") or 0), int(c.get("panel") or 0)))
        cand_tiles: list[ft.Control] = []
        for c in _ordered:
            key = (int(c.get("page", -1)), int(c.get("panel", -1)))
            is_selected = key in selected
            is_anchor = key == anchor_key
            tooltip = str(c.get("desc") or "")
            if c.get("dialog"):
                tooltip = f"{tooltip}\n\n“{c['dialog']}”"
            # Score goes in the TOOLTIP, not on the tile: a grid cell is ~120px wide and
            # under PANEL_TEXT_EMBED=0 there is no score at all (old cosine/vlm projects
            # still get it, just on hover).
            score = " · ".join(
                ([f"vlm {float(c['vlm']):.0f}"] if c.get("vlm") is not None else [])
                + ([f"cos {float(c.get('score') or 0):.2f}"]
                   if float(c.get("score") or 0) > 0 else []))
            if score:
                tooltip = f"{tooltip}\n\n{score}" if tooltip else score

            def _pick(_e, bk=beat_key, k=key, u=unit):
                _toggle_candidate(bk, k[0], k[1], u)

            def _hide(_e, k=key):
                _hide_panel(k[0], k[1])

            lock_icon = ft.IconButton(
                icon=ft.Icons.CHECK_CIRCLE if is_selected else ft.Icons.RADIO_BUTTON_UNCHECKED,
                icon_color=ACCENT if is_selected else TEXT_MUTED, icon_size=14,
                width=22, height=22, tooltip="Select this panel", on_click=_pick,
                style=ft.ButtonStyle(padding=ft.padding.all(0)),
            )
            icons[key] = lock_icon
            hide_btn = ft.IconButton(
                ft.Icons.CLOSE, icon_size=14, icon_color=TEXT_MUTED, width=22, height=22,
                tooltip="Ẩn panel này khỏi MỌI beat", on_click=_hide,
                style=ft.ButtonStyle(padding=ft.padding.all(0)),
            )

            meta: list[ft.Control] = [
                ft.Text(f"p{key[0]:02d}·{key[1]}", size=9, color=TEXT_MUTED,
                        font_family="Menlo"),
            ]
            if is_anchor:
                meta.append(ft.Text("⚓", size=9, color=ACCENT, tooltip="anchor panel"))
            meta += [ft.Container(expand=True), hide_btn, lock_icon]

            tile = ft.Container(
                content=ft.Column([
                    ft.Container(content=_thumb(c.get("thumb", "")), ink=True,
                                 on_click=_open_preview(beat_key, unit, scene_id, key, c),
                                 tooltip=tooltip or None),
                    ft.Row(meta, spacing=1,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ], spacing=1, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=3, border=ft.border.all(2 if is_selected else 1,
                                                 ACCENT if is_selected else BORDER),
                border_radius=4, bgcolor=BG_ELEVATED if is_selected else None,
            )
            tiles[key] = tile
            cand_tiles.append(tile)

        # Custom images (Master-added, this beat's card only) — PREPENDED so they lead the
        # gallery. Rendered like a candidate tile but with a "CUSTOM" chip instead of a score
        # (there is no matcher cosine to show — see module docstring: cosine only decides
        # PLACEMENT for an UNLOCKED custom image, never shown as a per-tile number here).
        locked_custom = _normalize_lock_custom_image(locks.get(beat_key))
        custom_tiles: dict[str, dict] = {}
        custom_controls: list[ft.Control] = []
        for entry in custom_by_beat.get(beat_key, []):
            rel_path = str(entry.get("file") or "")
            if not rel_path:
                continue
            is_selected = rel_path == locked_custom

            def _pick_custom(_e, bk=beat_key, rp=rel_path):
                _toggle_custom(bk, rp)

            custom_icon = ft.IconButton(
                icon=ft.Icons.CHECK_CIRCLE if is_selected else ft.Icons.RADIO_BUTTON_UNCHECKED,
                icon_color=ACCENT if is_selected else TEXT_MUTED, icon_size=16,
                tooltip="Select this custom image", on_click=_pick_custom,
                style=ft.ButtonStyle(padding=ft.padding.all(0)),
            )
            custom_tile = ft.Container(
                content=ft.Column([
                    ft.Container(content=_thumb(rel_path), ink=True, on_click=_pick_custom,
                                 tooltip=str(entry.get("desc") or "") or None),
                    ft.Row([_chip("CUSTOM", ACCENT), custom_icon], spacing=4,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=6, border=ft.border.all(2 if is_selected else 1,
                                                 ACCENT if is_selected else BORDER),
                border_radius=4, bgcolor=BG_ELEVATED if is_selected else None,
            )
            custom_tiles[rel_path] = {"tile": custom_tile, "icon": custom_icon}
            custom_controls.append(custom_tile)
        cand_tiles = custom_controls + cand_tiles
        # Page of each tile, parallel to cand_tiles (custom images lead the gallery and
        # belong to no comic page → -1) — the page-jump's index lookup.
        tile_pages = [-1] * len(custom_controls) + [int(c.get("page", -1)) for c in _ordered]

        # VERTICAL GridView (was a 1-row horizontal ListView showing ~4 tiles): ~18-24
        # tiles on screen at once, which is the whole point — the candidate pool is the
        # same for every beat, so Master re-skims it once per beat. GridView keeps
        # build_controls_on_demand=True so a 200-candidate beat still only builds the
        # visible cells (a wrap Row would build all 200 → decode every thumb).
        cand_row = ft.GridView(
            cand_tiles, max_extent=GRID_EXTENT, child_aspect_ratio=GRID_ASPECT,
            spacing=GRID_SPACING, run_spacing=GRID_SPACING, height=GRID_H,
            cache_extent=600,
        )
        jump_field = ft.TextField(
            width=70, height=38, hint_text="trang", text_size=12, content_padding=8,
            border_color=BORDER, focused_border_color=ACCENT,
            keyboard_type=ft.KeyboardType.NUMBER,
        )

        def _jump(_e=None, grid=cand_row, field=jump_field, pages=tile_pages):
            raw = (field.value or "").strip()
            if not raw.isdigit():
                _show_snack("Nhập số trang, ví dụ 14.")
                return
            want = int(raw)
            idx = next((i for i, p in enumerate(pages) if p == want), None)
            if idx is None:
                _show_snack(f"Beat này không có panel nào ở trang {want}.")
                return
            page.run_task(grid.scroll_to, offset=grid_scroll_offset(idx, _grid_w()),
                          duration=350, curve=ft.AnimationCurve.EASE_OUT)
        jump_field.on_submit = _jump

        has_pick = bool(selected) or bool(locked_custom)
        auto_badge = _chip("auto", TEXT_MUTED, visible=not has_pick)
        dup_badge = _chip("duplicate panel", WARN, visible=beat_key in _dup_beat_keys())
        cap = MAX_PANELS if unit == "scene" else 1
        count_chip = ft.Text(f"{len(selected)}/{cap} selected", size=9,
                              color=TEXT_MUTED, font_family="Menlo")
        warn_chip = _chip("pick at least 2", WARN,
                           visible=unit == "scene" and len(selected) < MIN_PANELS and not locked_custom)
        card_refs[beat_key] = {"tiles": tiles, "icons": icons, "cand_row": cand_row,
                               "auto_badge": auto_badge, "dup_badge": dup_badge,
                               "count_chip": count_chip, "warn_chip": warn_chip,
                               "unit": unit, "custom_tiles": custom_tiles}

        def _add_image_click(_e, bk=beat_key):
            page.run_task(_do_add_custom, bk)

        # Two DISTINCT actions on a fragment card, never conflated (this was the UX trap:
        # the trash icon used to delete the WHOLE scene):
        #   • CALL_MERGE      → fold this fragment's WORDS into a sibling (keeps the text,
        #     one fewer panel) — _delete_fragment.
        #   • DELETE_OUTLINE  → drop just THIS narration line (its words are gone), the rest
        #     of the beat stays — _drop_fragment_line. Only the beat's LAST line escalates to
        #     a whole-scene delete, and only behind a confirm.
        # A non-fragment card (a whole scene / intro) keeps the plain whole-scene delete.
        header_icons: list[ft.Control] = [
            ft.IconButton(ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED, icon_size=18,
                          tooltip="Add a custom image for this beat", on_click=_add_image_click,
                          style=ft.ButtonStyle(padding=ft.padding.all(0))),
        ]
        if unit == "fragment":
            header_icons.append(ft.IconButton(
                ft.Icons.CALL_MERGE, icon_size=18, icon_color=WARN,
                tooltip="Gộp mảnh này vào mảnh kề (giữ chữ, bớt 1 panel)",
                on_click=lambda _e, b=beat: _delete_fragment(b),
                style=ft.ButtonStyle(padding=ft.padding.all(0))))
            header_icons.append(ft.IconButton(
                ft.Icons.DELETE_OUTLINE, icon_size=18, icon_color=WARN,
                tooltip="Xóa dòng này (chỉ mảnh này, không xóa cả beat)",
                on_click=lambda _e, b=beat: _drop_fragment_line(b),
                style=ft.ButtonStyle(padding=ft.padding.all(0))))
        else:
            header_icons.append(ft.IconButton(
                ft.Icons.DELETE_OUTLINE, icon_size=18, icon_color=WARN,
                tooltip="Delete this beat (removes the scene from narration.json)",
                on_click=lambda _e, b=beat: _delete_beat(b),
                style=ft.ButtonStyle(padding=ft.padding.all(0))))

        header = ft.Row([
            ft.Text(f"{scene_id:02d}", size=12, color=TEXT_MUTED, font_family="Menlo"),
            ft.Text(_anchor_label(beat.get("page_ref"), beat.get("panel_ref")),
                    size=10, color=TEXT_MUTED, font_family="Menlo"),
            ft.Container(expand=True),
            *header_icons,
            count_chip, auto_badge, dup_badge, warn_chip,
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        children: list[ft.Control] = [header, text_control]
        if source_items:
            children.append(ft.Row(source_items, spacing=14, wrap=True))
        if drawable_moment:
            children.append(ft.Text(f"Looking for: {drawable_moment}", size=11,
                                     color=TEXT_MUTED, italic=True))
        if cand_tiles:
            children.append(ft.Row([
                ft.Text(f"{len(cand_tiles)} panel · nhảy tới trang", size=10,
                        color=TEXT_MUTED),
                jump_field,
                ft.IconButton(ft.Icons.ARROW_FORWARD, icon_size=16, tooltip="Go",
                              on_click=_jump,
                              style=ft.ButtonStyle(padding=ft.padding.all(0))),
                ft.Container(expand=True),
                # ponytail: no trackpad here — ▲/▼ page the grid by a full viewport
                # (the old ◀/▶ nudged the horizontal strip by ~1.5 tiles).
                ft.IconButton(ft.Icons.KEYBOARD_ARROW_UP, icon_size=20,
                              tooltip="Lên 1 màn", on_click=_scroll_step(cand_row, -GRID_H),
                              style=ft.ButtonStyle(padding=ft.padding.all(0))),
                ft.IconButton(ft.Icons.KEYBOARD_ARROW_DOWN, icon_size=20,
                              tooltip="Xuống 1 màn", on_click=_scroll_step(cand_row, GRID_H),
                              style=ft.ButtonStyle(padding=ft.padding.all(0))),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            children.append(cand_row)
        else:
            # A beat with zero candidates (e.g. a brand-new Master-inserted scene) —
            # general fallback for ANY zero-candidate beat, not special-cased to the
            # insert flow.
            children.append(ft.Row([
                ft.Text("No candidates — dùng Custom image hoặc Rebuild.",
                        size=11, color=WARN),
                secondary_button("Rebuild candidates", _do_rebuild_candidates,
                                 icon=ft.Icons.BUILD_OUTLINED),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        card = ft.Container(
            content=ft.Column(children, spacing=8),
            padding=14, border=ft.border.all(1, BORDER), border_radius=4, bgcolor=BG_PANEL,
        )
        card_refs[beat_key]["card"] = card
        return card

    def _delete_beat(beat: dict, *, confirm_title: str | None = None,
                     confirm_body: str | None = None):
        """Delete a beat: drop its scene from narration.json (other scene_ids stay stable so
        sibling locks don't shift), drop its lock(s), un-approve (narration changed → must
        re-approve before render), pull its card. Confirms first — destructive. The confirm
        title/body are overridable so _drop_fragment_line can reuse this exact delete path
        with a "this was the beat's last line" wording instead of the generic scene copy."""
        sid = int(beat.get("scene_id") or 0)
        bk = _beat_key(beat)

        def _do_delete(_e):
            page.pop_dialog()
            current = load_narration(project) or {}
            current["scenes"] = [s for s in (current.get("scenes") or [])
                                 if int(s.get("scene_id") or 0) != sid]
            save_narration_edits(project, current)
            nonlocal narration
            narration = current
            edited_text.pop(sid, None)
            for k in [k for k in locks if k == bk or k.startswith(f"{sid}:")]:
                locks.pop(k, None)
            locks_doc["approved"] = False
            locks_doc["approved_at"] = None
            save_review_locks(project, locks_doc)
            # Drop the beat row(s) too — review["beats"] is the ListView's source of
            # truth for a LOCAL _rebuild() (add/split/merge never re-read
            # candidates.json), so it must stay in sync or a deleted scene would
            # reappear after the next add/split/merge elsewhere.
            review["beats"] = [b for b in (review.get("beats") or [])
                                if not (str(b.get("beat_key") or "") == bk
                                        or str(b.get("beat_key") or "").startswith(f"{sid}:"))]
            _rebuild(f"Deleted beat {sid:02d} — re-approve before render.", warn=True)

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(confirm_title or f"Delete beat {sid:02d}?"),
            content=ft.Text(confirm_body or ("Removes this scene from narration.json. "
                            "You'll need to re-approve before render.")),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _e: page.pop_dialog()),
                primary_button("Delete", _do_delete, icon=ft.Icons.DELETE_OUTLINE),
            ],
        ))

    def _delete_fragment(beat: dict):
        """Delete ONE fragment (not the whole scene) — its words fold into a sibling
        fragment via merge_fragment_into_prev, its own lock is dropped, later
        fragment locks shift down. Content isn't lost (it survives merged into the
        sibling's text), so no confirm dialog — unlike the whole-scene delete above."""
        beat_key = _beat_key(beat)
        sid = int(beat.get("scene_id") or 0)
        frag_idx = _frag_idx_from_key(beat_key)
        current = load_narration(project) or {}
        scene = next((s for s in current.get("scenes") or []
                      if int(s.get("scene_id") or 0) == sid), None)
        if scene is None:
            _show_snack("Scene not found — reload the project.")
            return
        try:
            merge_fragment_into_prev(scene, frag_idx)
        except ValueError as exc:
            _show_snack(f"Cannot delete fragment: {exc}")
            return
        save_narration_edits(project, current)
        nonlocal narration, locks
        narration = current
        locks = remap_fragment_locks(locks, sid, frag_idx, -1)
        locks_doc["locks"] = locks
        locks_doc["approved"] = False
        locks_doc["approved_at"] = None
        save_review_locks(project, locks_doc)
        beats_list = _remap_beats_list(review.get("beats") or [], sid, frag_idx, -1)
        review["beats"] = beats_list
        survivor_idx = 0 if frag_idx == 0 else frag_idx - 1
        survivor_key = f"{sid}:{survivor_idx}"
        merged_beats = scene.get("visual_beats") or []
        for b in beats_list:
            if b.get("beat_key") == survivor_key and survivor_idx < len(merged_beats):
                b["narration_text"] = _frag_text(merged_beats[survivor_idx])
                break
        # Every fragment key of this scene shifted (merge closed the gap), so their cached cards
        # now map to the WRONG content — rebuild the whole scene's fragment cards fresh (cheap:
        # 1 scene) while other scenes' cards stay reused. Without this a shifted key reuses a
        # stale card → duplicate/stale rows.
        dirty = frozenset(f"{sid}:{i}" for i in range(len(merged_beats)))
        _rebuild("Đã xóa mảnh — cần re-approve trước khi render.", warn=True, dirty=dirty)

    def _drop_fragment_line(beat: dict):
        """Trash icon on a FRAGMENT card — drop just THIS narration line (its words are
        GONE, unlike _delete_fragment which folds them into a sibling). scene["text"] is
        redefined as the concat of the surviving fragments (drop_fragment), this line's own
        lock is dropped and later fragment locks shift down (remap_fragment_locks delta=-1),
        un-approve, local rebuild. If it was the beat's LAST line, dropping it would empty the
        scene → confirm + delete the whole scene via the existing _delete_beat path. (Master's
        mental model: 1 card = 1 line, trash = that line, never the whole beat.)"""
        beat_key = _beat_key(beat)
        sid = int(beat.get("scene_id") or 0)
        frag_idx = _frag_idx_from_key(beat_key)
        current = load_narration(project) or {}
        scene = next((s for s in current.get("scenes") or []
                      if int(s.get("scene_id") or 0) == sid), None)
        if scene is None:
            _show_snack("Scene not found — reload the project.")
            return
        try:
            dropped = drop_fragment(scene, frag_idx)
        except ValueError as exc:
            _show_snack(f"Cannot drop line: {exc}")
            return
        if not dropped:
            # Last remaining fragment — dropping it = emptying the scene, so this genuinely IS
            # a whole-beat delete. Reuse _delete_beat's confirm+delete path (never mutated disk
            # above: drop_fragment returned False without touching `scene`).
            _delete_beat(
                beat,
                confirm_title=f"Xóa dòng cuối của beat {sid:02d}?",
                confirm_body="Đây là dòng cuối của beat — xóa nó sẽ xóa CẢ beat khỏi "
                             "narration.json. Cần re-approve trước khi render.")
            return
        save_narration_edits(project, current)
        nonlocal narration, locks
        narration = current
        locks = remap_fragment_locks(locks, sid, frag_idx, -1)
        locks_doc["locks"] = locks
        locks_doc["approved"] = False
        locks_doc["approved_at"] = None
        save_review_locks(project, locks_doc)
        beats_list = _remap_beats_list(review.get("beats") or [], sid, frag_idx, -1)
        review["beats"] = beats_list
        # Every surviving fragment row of this scene shifted index (the drop closed the gap),
        # so each row's narration_text now maps to a DIFFERENT fragment — refresh from the new
        # visual_beats list keyed by the row's (already-remapped) fragment index.
        new_vb = scene.get("visual_beats") or []
        for b in beats_list:
            bk = str(b.get("beat_key") or "")
            if bk.startswith(f"{sid}:"):
                i = _frag_idx_from_key(bk)
                if i < len(new_vb):
                    b["narration_text"] = _frag_text(new_vb[i])
        dirty = frozenset(f"{sid}:{i}" for i in range(len(new_vb)))
        _rebuild("Đã xóa dòng — cần re-approve trước khi render.", warn=True, dirty=dirty)

    def _open_split_dialog(before: dict):
        """"Tách mảnh" dialog: `before` is the fragment card sitting right above the
        "+" row that was clicked. Shows its text as words with a cut button in every
        gap between two words — click one to split there."""
        beat_key = _beat_key(before)
        sid = int(before.get("scene_id") or 0)
        frag_idx = _frag_idx_from_key(beat_key)
        text = str(before.get("narration_text") or "")
        words = text.split()
        if len(words) <= 1:
            _show_snack("Mảnh này chỉ có 1 từ — không tách được.")
            return

        def _is_preferred(word_idx: int) -> bool:
            left = words[word_idx - 1]
            right = words[word_idx] if word_idx < len(words) else ""
            if left and left[-1] in _HANGER_CHARS:
                return True
            return right.strip(".,!?\"'“”").lower() in _CONNECTIVES

        def _confirm(word_idx: int):
            def _handler(_e):
                page.pop_dialog()
                current = load_narration(project) or {}
                scene = next((s for s in current.get("scenes") or []
                              if int(s.get("scene_id") or 0) == sid), None)
                if scene is None:
                    _show_snack("Scene not found — reload the project.")
                    return
                try:
                    split_fragment_at(scene, frag_idx, word_idx)
                except ValueError as exc:
                    _show_snack(f"Split failed: {exc}")
                    return
                save_narration_edits(project, current)
                nonlocal narration, locks
                narration = current
                locks = remap_fragment_locks(locks, sid, frag_idx + 1, 1)
                locks_doc["locks"] = locks
                locks_doc["approved"] = False
                locks_doc["approved_at"] = None
                save_review_locks(project, locks_doc)

                beats_list = _remap_beats_list(review.get("beats") or [], sid, frag_idx + 1, 1)
                review["beats"] = beats_list
                new_vb = scene.get("visual_beats") or []
                orig_i = next((i for i, b in enumerate(beats_list)
                               if b.get("beat_key") == beat_key), None)
                if orig_i is not None and frag_idx + 1 < len(new_vb):
                    orig_row = beats_list[orig_i]
                    orig_row["narration_text"] = _frag_text(new_vb[frag_idx])
                    # CLONE the sibling's candidate pool in-memory for the new second
                    # half — same scene, same shared candidate pool per the review
                    # contract; no build_candidates re-run needed.
                    new_row = dict(orig_row)
                    new_row["beat_key"] = f"{sid}:{frag_idx + 1}"
                    new_row["narration_text"] = _frag_text(new_vb[frag_idx + 1])
                    new_row["candidates"] = list(orig_row.get("candidates") or [])
                    new_row["pre_selected"] = []
                    beats_list.insert(orig_i + 1, new_row)
                # A split shifts every following fragment key of this scene by +1, so their
                # cached cards now map to the wrong content — rebuild the whole scene's
                # fragment cards fresh (other scenes stay reused). See _delete_fragment.
                dirty = frozenset(f"{sid}:{i}" for i in range(len(new_vb)))
                _rebuild("Đã tách mảnh — cần re-approve trước khi render.", warn=True, dirty=dirty)
            return _handler

        word_controls: list[ft.Control] = []
        for i, w in enumerate(words):
            word_controls.append(ft.Text(w, size=13, color=TEXT_PRIMARY))
            if i < len(words) - 1:
                word_idx = i + 1
                preferred = _is_preferred(word_idx)
                word_controls.append(ft.IconButton(
                    ft.Icons.CONTENT_CUT, icon_size=12,
                    icon_color=ACCENT if preferred else TEXT_MUTED,
                    tooltip=f"Tách sau từ #{word_idx}"
                            + (" — điểm cắt gợi ý" if preferred else ""),
                    on_click=_confirm(word_idx),
                    style=ft.ButtonStyle(padding=ft.padding.all(0)),
                ))

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Tách mảnh — scene {sid:02d}"),
            content=ft.Container(
                width=560,
                content=ft.Row(word_controls, wrap=True, spacing=2,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ),
            actions=[ft.TextButton("Đóng", on_click=lambda _e: page.pop_dialog())],
        ))

    def _open_insert_scene_dialog(boundary_idx: int, before: dict | None, after: dict | None):
        """"Thêm scene" dialog: type a brand-new scene's text, inserted at this
        boundary in both narration.json (via insert_scene_at) and the in-memory
        review["beats"] (candidates=[] — Master rebuilds or picks a Custom image)."""
        text_field = ft.TextField(
            label="Nội dung scene mới", multiline=True, min_lines=2, max_lines=5,
            border_color=BORDER, focused_border_color=ACCENT, text_size=13, autofocus=True,
        )

        def _do_confirm(_e):
            new_text = (text_field.value or "").strip()
            if not new_text:
                _show_snack("Nhập nội dung trước khi thêm scene.")
                return
            page.pop_dialog()
            current = load_narration(project) or {}
            scenes = current.get("scenes") or []
            neighbor_beat = before or after
            neighbor_sid = int((neighbor_beat or {}).get("scene_id") or 0)
            neighbor = next((s for s in scenes
                             if int(s.get("scene_id") or 0) == neighbor_sid), None)
            if neighbor is None:
                _show_snack("Neighbor scene not found — reload the project.")
                return
            if after is not None:
                after_sid = int(after.get("scene_id") or 0)
                list_index = next((i for i, s in enumerate(scenes)
                                    if int(s.get("scene_id") or 0) == after_sid), len(scenes))
            else:
                list_index = len(scenes)
            new_scene = insert_scene_at(current, list_index, new_text, neighbor)
            save_narration_edits(project, current)
            nonlocal narration
            narration = current
            locks_doc["approved"] = False
            locks_doc["approved_at"] = None
            save_review_locks(project, locks_doc)
            new_beat = {
                "scene_id": new_scene["scene_id"], "narration_text": new_text,
                "page_ref": new_scene.get("page_ref") or None, "panel_ref": None,
                "source": {}, "candidates": [], "beat_key": str(new_scene["scene_id"]),
                "pre_selected": [], "unit": "scene",
            }
            beats_list = review.setdefault("beats", [])
            beats_list.insert(boundary_idx, new_beat)
            _rebuild("Đã thêm scene mới — cần Rebuild candidates + re-approve.", warn=True)

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Thêm scene mới"),
            content=ft.Container(width=480, content=text_field),
            actions=[
                ft.TextButton("Hủy", on_click=lambda _e: page.pop_dialog()),
                primary_button("Thêm", _do_confirm, icon=ft.Icons.ADD),
            ],
        ))

    def _add_row(boundary_idx: int, beats_list: list[dict]) -> ft.Control:
        """Thin "+" row at one boundary between (or before/after) beat cards. Which
        dialog it opens depends only on the card ABOVE it: a fragment above means
        "add more to THIS scene" (split its last fragment) even when the card below
        is a totally different scene; anything else above (a whole scene/intro card,
        or nothing — the very top of the list) means "insert a brand-new scene"."""
        before = beats_list[boundary_idx - 1] if boundary_idx > 0 else None
        after = beats_list[boundary_idx] if boundary_idx < len(beats_list) else None
        is_split = before is not None and _beat_unit(before) == "fragment"
        tooltip = "Tách mảnh ngay trên thành 2" if is_split else "Thêm scene mới tại đây"

        def _click(_e, b=before, a=after, split=is_split, bidx=boundary_idx):
            if split:
                _open_split_dialog(b)
            else:
                _open_insert_scene_dialog(bidx, b, a)

        return ft.Row([
            ft.Container(content=ft.Divider(height=1, color=BORDER), expand=True),
            ft.IconButton(ft.Icons.ADD_CIRCLE_OUTLINE, icon_size=16, tooltip=tooltip,
                          on_click=_click, style=ft.ButtonStyle(padding=ft.padding.all(0))),
            ft.Container(content=ft.Divider(height=1, color=BORDER), expand=True),
        ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _build_card_controls(dirty: frozenset = frozenset()) -> list[ft.Control]:
        beats_list = review.get("beats") or []
        controls: list[ft.Control] = []
        for i, b in enumerate(beats_list):
            controls.append(_add_row(i, beats_list))
            bk = _beat_key(b)
            cached = card_refs.get(bk, {}).get("card")
            if cached is not None and bk not in dirty:
                # REUSE the already-rendered card (same control object) for an
                # unchanged beat: flet then diffs it as identical and never re-sends
                # its ~100 candidate-tile images. Rebuilding fresh cards for every
                # survivor was serializing ~2500 Images on each delete → the freeze.
                controls.append(cached)
            else:
                controls.append(_beat_card(b))
        controls.append(_add_row(len(beats_list), beats_list))
        return controls

    def _rebuild(msg: str = "", *, warn: bool = False, dirty: frozenset = frozenset()):
        """Local rebuild after an add/delete/split/merge — re-renders the card list
        from the CURRENT in-memory review/narration/locks (already saved to disk by
        the caller) instead of on_state_change()'s full app-level reload, which would
        re-read review/candidates.json from disk and lose any in-memory-only candidate
        rows cloned by a fragment split (candidates.json is never rewritten by these
        ops — only narration.json + locks.json are).

        `dirty` = beat_keys whose card MUST be rebuilt fresh (text/candidates changed);
        every other surviving beat REUSES its existing card control so flet doesn't
        re-serialize ~100 tile images per survivor. A plain scene delete passes no
        dirty keys → all survivors reused → the diff is just the removed cards."""
        # Both pending-edit maps are SPARSE (see their declaration): an unedited scene has no
        # entry and its card re-seeds straight from the reconciled narration text, so a rebuild
        # only has to drop entries that can no longer be written back safely.
        live_sids = {int(s.get("scene_id") or 0) for s in narration.get("scenes") or []}
        for sid in [k for k in edited_text if k not in live_sids]:
            edited_text.pop(sid, None)          # drop a deleted scene's stale edit
        # frag_edits is keyed by (scene_id, frag_idx) and a split/merge/drop SHIFTS those
        # indices — so a pending edit of an op'd scene would land on the wrong fragment. Drop
        # that whole scene's pending edits (its `dirty` keys name it) plus any deleted scene's;
        # narration.json on disk is the truth and the rebuilt cards re-seed from it.
        dirty_sids = {int(k.split(":", 1)[0]) for k in dirty
                      if ":" in k and k.split(":", 1)[0].isdigit()}
        for key in [k for k in frag_edits if k[0] in dirty_sids or k[0] not in live_sids]:
            frag_edits.pop(key, None)
        # Prune card_refs for beats that no longer exist (so a stale cached card is
        # never reused); keep survivors' entries so _build_card_controls can reuse them.
        live_keys = {_beat_key(b) for b in (review.get("beats") or [])}
        for k in [k for k in card_refs if k not in live_keys]:
            card_refs.pop(k, None)
        cards.controls = _build_card_controls(dirty)
        # push=False: a beat list can be ~2500 controls (Q&A --all, ~25 beats ×
        # ~100 candidate tiles). page.update() with no args patches the WHOLE
        # page tree (see ft.Page.update source) — calling it here on top of the
        # page.update() `_refresh_approve_ui()` used to fire was TWO full-tree
        # serializations of that giant list back to back, which is the freeze on
        # delete/merge. Control-level .update() below patches only that control's
        # own subtree instead.
        _refresh_approve_ui(push=False)   # button/continue state — also sets a default status
        if msg:
            status_text.value = msg
            status_text.color = WARN if warn else SUCCESS
        _refresh_dup_badges()
        cards.update()
        try:
            status_text.update()
        except Exception:
            pass

    cards = ft.ListView(
        _build_card_controls(),
        spacing=12, expand=True, padding=ft.padding.symmetric(horizontal=28, vertical=16),
    )

    # ─── Approve / Un-approve ───────────────────────────────────────────────
    continue_btn = primary_button(
        "Continue → TTS", lambda _e: _continue(),
        icon=ft.Icons.ARROW_FORWARD, disabled=not locks_doc.get("approved"),
    )
    approve_btn = primary_button("Approve", lambda _e: _toggle_approve(),
                                  icon=ft.Icons.CHECK_CIRCLE_OUTLINE)

    def _refresh_approve_ui(*, push: bool = True):
        approved = bool(locks_doc.get("approved"))
        approve_btn.text = "Un-approve" if approved else "Approve"
        approve_btn.icon = ft.Icons.UNPUBLISHED_OUTLINED if approved else ft.Icons.CHECK_CIRCLE_OUTLINE
        continue_btn.disabled = not approved
        status_text.value = (f"Approved at {locks_doc.get('approved_at')}."
                              if approved else "Not approved yet.")
        status_text.color = SUCCESS if approved else TEXT_MUTED
        if approved:
            state.mark_approved(5)
        else:
            state.approved[str(5)] = False
        save_state(state)
        # push=False (used by _rebuild, mid a full beat-list rebuild): patch just
        # these 3 controls, never the whole page — see _rebuild's comment for why.
        if push:
            page.update()
        else:
            try:
                approve_btn.update()
                continue_btn.update()
                status_text.update()
            except Exception:
                pass

    def _toggle_approve():
        if locks_doc.get("approved"):
            locks_doc["approved"] = False
            locks_doc["approved_at"] = None
        else:
            _save_narration_text()
            locks_doc["approved"] = True
            locks_doc["approved_at"] = dt.datetime.now().isoformat(timespec="seconds")
            locks_doc["narration_sha1"] = narration_sha1(project)
        save_review_locks(project, locks_doc)
        _refresh_approve_ui()

    def _continue():
        state.current_stage = 6
        save_state(state)
        on_go(6)

    _rebuild_intro_box()  # populate the imported-intro strip on first paint
    intro_section = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Text("Intro image", size=12, weight=ft.FontWeight.BOLD,
                        color=TEXT_PRIMARY),
                ft.Container(expand=True),
                secondary_button("📷 Import intro image", _on_import_click,
                                 icon=ft.Icons.IMAGE_OUTLINED),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            intro_list_col,
        ], spacing=8),
        padding=ft.padding.symmetric(horizontal=28, vertical=10),
    )

    unhide_btn.content = f"👁 Đã ẩn {len(hidden)} panel — Hiện lại tất cả"
    center = ft.Column([
        intro_section,
        hidden_bar,
        ft.Container(content=cards, expand=True),
        ft.Container(content=status_text,
                     padding=ft.padding.symmetric(horizontal=28, vertical=12)),
    ], spacing=0, expand=True)

    right = ft.Column([
        ft.Text("STEP 5 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Review Beats", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text(
            "Check each line's research source and select 2-5 of the best panels "
            "(rendered as sub-shots). Beats left on auto fall back to their "
            "grounded anchor panel.",
            size=12, color=TEXT_MUTED,
        ),
        ft.Container(height=14),
        secondary_button("Save narration edits", _save_narration_text,
                         icon=ft.Icons.SAVE_OUTLINED),
        ft.Container(height=8),
        approve_btn,
        ft.Container(height=14),
        continue_btn,
    ], spacing=8, expand=True)

    _refresh_approve_ui()

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Review Beats",
        header_subtitle="Edit text, verify sources, lock panels, then approve before TTS.",
    )


def _empty_state(page: ft.Page, state: AppState, on_go, on_state_change) -> ft.Control:
    def _switch(name: str):
        state.project_name = name
        state.current_stage = 5
        save_state(state)
        on_state_change()

    other_projects = [p for p in list_review_projects() if p != state.project_name]
    rows: list[ft.Control] = [
        ft.Icon(ft.Icons.FACT_CHECK_OUTLINED, size=64, color=TEXT_MUTED),
        ft.Text("No beat candidates yet — run the review-gate build first.",
                size=13, color=TEXT_MUTED),
    ]

    project = state.project_name
    if project:
        # ponytail: fire-and-poll — Popen the build in background, tail-less (log to
        # scratchpad/), poll for candidates.json every 2s, then on_state_change() to
        # rebuild this screen (build() re-reads candidates.json fresh, so it just works).
        build_status = ft.Text("", size=11, color=TEXT_MUTED)
        build_spinner = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
        build_btn = secondary_button(
            "Build candidates", lambda _e: page.run_task(_run_build),
            icon=ft.Icons.BUILD_OUTLINED,
        )

        async def _run_build():
            build_btn.disabled = True
            build_spinner.visible = True
            build_status.value = "Building candidates — running stages.review_gate…"
            build_status.color = TEXT_MUTED
            # ponytail: page.update() over per-control .update() — page is always
            # mounted, individual controls sometimes aren't yet (silently swallowed
            # before, which is why the UI looked frozen during a live build).
            page.update()

            repo_root = PROJECTS_ROOT.parent
            py_bin = repo_root / ".venv" / "bin" / "python"
            py = str(py_bin) if py_bin.exists() else sys.executable
            log_path = repo_root / "scratchpad" / f"ui_build_candidates_{project}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            cand_path = PROJECTS_ROOT / project / "review" / "candidates.json"

            start = time.monotonic()
            with open(log_path, "w") as logf:
                proc = subprocess.Popen(
                    [py, "-m", "stages.review_gate", "--project", project,
                     "--build-candidates", "--all"],
                    stdout=logf, stderr=subprocess.STDOUT, cwd=str(repo_root),
                    env={**os.environ, "PYTHONPATH": "."},
                )
                while proc.poll() is None and not cand_path.exists():
                    elapsed = int(time.monotonic() - start)
                    build_status.value = f"Building candidates… {elapsed}s"
                    page.update()
                    await asyncio.sleep(2)

            if cand_path.exists():
                build_status.value = "Done — reloading…"
                build_status.color = SUCCESS
                page.update()
                on_state_change()
            else:
                build_btn.disabled = False
                build_spinner.visible = False
                build_status.value = f"Build failed (exit {proc.returncode}) — see {log_path}"
                build_status.color = DANGER
                page.update()

        rows.append(ft.Row([build_btn, build_spinner], spacing=8,
                            alignment=ft.MainAxisAlignment.CENTER))
        rows.append(build_status)

    if other_projects:
        rows.append(ft.Text("Other projects ready to review:", size=11, color=TEXT_MUTED))
        for name in other_projects:
            rows.append(ft.TextButton(name, on_click=lambda _e, n=name: _switch(n)))

    center = ft.Container(
        content=ft.Column(rows, spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.Alignment.CENTER, expand=True,
    )
    right = ft.Column([
        ft.Text("STEP 5 OF 8", size=10, color=TEXT_MUTED),
        ft.Text("Review Beats", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        ft.Text("Nothing to review — generate review/candidates.json first.",
                size=12, color=TEXT_MUTED),
    ], spacing=8, expand=True)
    return three_col(center, right, state=state, on_go=on_go,
                      header_title="Review Beats",
                      header_subtitle="Run the review-gate build to get here.")
