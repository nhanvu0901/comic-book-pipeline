"""stages/stage_5/screen_selection.py
What Master has picked for each screen_qa beat — and every operation on it — with no UI in sight
(ui/screens/s_screen_gate.py is a thin view over this; stages/ never imports ui/).

It writes the SAME files the rest of the pipeline already reads, so nothing here needs a reader of
its own:

  clip     review/clips/clips.json   {"clips":[{"beat": <key>, "file", "start", "end", "source_url",
                                       "source_start", "backup": {...}}]}   ← /api/pick_moment (P1)
  still    review/locks.json         locks[<key>] = {"custom_image": "review/custom/<f>", ...}
           review/custom/custom_images.json  {"images": [{"file", "beat_key", ...}]}   (sidecar)
  approval review/locks.json         approved / approved_at / narration_sha1 → ensure_reviewed()
  pin      review/screen_gate.json   {"narration_sha1"} — the narration the picks were made against

RULE (Master decision a, applied to screen_qa): the beat keys mean nothing once the narration
changes, so when narration.json is no longer the one the picks were made against EVERY pick is
cleared and the approval dropped (sync_with_narration) — never a half-stale set.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable

from utils.atomic_json import write_json_atomic

CLIPS_REL = Path("review") / "clips" / "clips.json"
SIDECAR_REL = Path("review") / "custom" / "custom_images.json"
PIN_REL = Path("review") / "screen_gate.json"


def _root(project_root) -> Path:
    return Path(project_root)


def _read(path: Path, default: Any) -> Any:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return doc if isinstance(doc, type(default)) else default


def load_narration(project_root) -> dict:
    """narration.json or {} (never raises)."""
    return _read(_root(project_root) / "narration.json", {})


def load_screen_context(project_root) -> dict:
    """screen_context.json ({question, items:[...]}) or {} — p3-core's research output."""
    return _read(_root(project_root) / "screen_context.json", {})


# ─── approval (review/locks.json) ───────────────────────────────────────────────

def _locks_doc(root: Path) -> dict:
    from ..review_gate import load_state
    return load_state(root)


def _save_locks(root: Path, doc: dict) -> None:
    from ..review_gate import save_state
    save_state(root, doc)


def is_approved(project_root) -> bool:
    return bool(_locks_doc(_root(project_root)).get("approved"))


def approved_at(project_root) -> str | None:
    return _locks_doc(_root(project_root)).get("approved_at")


def set_approved(project_root, approved: bool) -> None:
    """Approve (pinning the narration the way ensure_reviewed checks it) or withdraw approval."""
    from ..review_gate import narration_sha1
    root = _root(project_root)
    doc = _locks_doc(root)
    if approved:
        doc["approved"] = True
        doc["approved_at"] = dt.datetime.now().isoformat(timespec="seconds")
        doc["narration_sha1"] = narration_sha1(root)
        pin_narration(root)
    else:
        doc["approved"], doc["approved_at"] = False, None
    _save_locks(root, doc)


def _unapprove(root: Path) -> None:
    doc = _locks_doc(root)
    if doc.get("approved"):
        doc["approved"], doc["approved_at"] = False, None
        _save_locks(root, doc)


def withdraw_approval(project_root) -> None:
    """A pick changed after Master approved: approval must be given again (as in the comic gate)."""
    _unapprove(_root(project_root))


# ─── clips (review/clips/clips.json) ────────────────────────────────────────────

def _manifest(root: Path) -> dict:
    doc = _read(root / CLIPS_REL, {})
    doc.setdefault("clips", [])
    if not isinstance(doc["clips"], list):
        doc["clips"] = []
    return doc


def _write_manifest(root: Path, doc: dict) -> None:
    write_json_atomic(root / CLIPS_REL, doc)


def load_clips(project_root) -> dict[str, dict]:
    """{beat_key: manifest entry} for every enabled entry that names a beat."""
    out: dict[str, dict] = {}
    for c in _manifest(_root(project_root))["clips"]:
        if isinstance(c, dict) and c.get("beat") and c.get("enabled", True) is not False:
            out[str(c["beat"])] = c
    return out


def remove_clip(project_root, beat: str) -> bool:
    root = _root(project_root)
    doc = _manifest(root)
    keep = [c for c in doc["clips"] if not (isinstance(c, dict) and str(c.get("beat")) == beat)]
    if len(keep) == len(doc["clips"]):
        return False
    doc["clips"] = keep
    _write_manifest(root, doc)
    _unapprove(root)
    return True


def set_backup(project_root, beat: str, backup: dict | None, *,
               only_if_clip_id: str | None = None) -> bool:
    """Attach (or drop, with None) the backup clip of the entry on `beat`. `only_if_clip_id`
    guards a slow background fetch against the primary having been replaced meanwhile."""
    root = _root(project_root)
    doc = _manifest(root)
    for c in doc["clips"]:
        if isinstance(c, dict) and str(c.get("beat")) == beat:
            if only_if_clip_id is not None and str(c.get("id")) != only_if_clip_id:
                return False
            if backup:
                c["backup"] = backup
            else:
                c.pop("backup", None)
            _write_manifest(root, doc)
            return True
    return False


# ─── stills (locks.json + custom-image sidecar) ─────────────────────────────────

def load_stills(project_root) -> dict[str, str]:
    """{beat_key: project-relative image path} for every beat locked to a custom image."""
    from ..review_gate import lock_custom_image
    out: dict[str, str] = {}
    for key, lock in (_locks_doc(_root(project_root)).get("locks") or {}).items():
        ci = lock_custom_image(lock) if isinstance(lock, dict) else None
        if ci:
            out[str(key)] = ci
    return out


def _sidecar_drop(root: Path, rel_file: str) -> None:
    """Forget an image in the custom-image sidecar. An UNLOCKED sidecar image is auto-placed on
    some beat by Stage 5 (a custom image is never dropped), so a still Master removes must leave
    the sidecar too or it would reappear on another beat. The file itself stays on disk."""
    doc = _read(root / SIDECAR_REL, {})
    images = [e for e in (doc.get("images") or []) if e.get("file") != rel_file]
    if len(images) != len(doc.get("images") or []):
        doc["images"] = images
        write_json_atomic(root / SIDECAR_REL, doc)


def lock_still(project_root, beat: str, rel_file: str) -> None:
    """Pin `rel_file` (already in review/custom/ — ui.custom_image.add_custom_image put it
    there) to `beat`. Replaces the beat's previous still."""
    root = _root(project_root)
    doc = _locks_doc(root)
    locks = doc.setdefault("locks", {})
    old = locks.get(beat)
    old_file = old.get("custom_image") if isinstance(old, dict) else None
    locks[beat] = {"custom_image": rel_file, "source": "custom"}
    doc["approved"], doc["approved_at"] = False, None
    _save_locks(root, doc)
    if old_file and old_file != rel_file:
        _sidecar_drop(root, old_file)
    pin_narration(root)


def unlock_still(project_root, beat: str) -> bool:
    root = _root(project_root)
    doc = _locks_doc(root)
    lock = (doc.get("locks") or {}).get(beat)
    ci = lock.get("custom_image") if isinstance(lock, dict) else None
    if not ci:
        return False
    del doc["locks"][beat]
    doc["approved"], doc["approved_at"] = False, None
    _save_locks(root, doc)
    _sidecar_drop(root, ci)
    return True


def clear_beat(project_root, beat: str) -> None:
    """Back to the text card: drop the beat's clip (and its backup) and still."""
    remove_clip(project_root, beat)
    unlock_still(project_root, beat)


def clear_all(project_root) -> None:
    """Drop EVERY pick and the approval (Master decision a)."""
    root = _root(project_root)
    doc = _manifest(root)
    doc["clips"] = []
    if (root / CLIPS_REL).exists():
        _write_manifest(root, doc)
    locks_doc = _locks_doc(root)
    for key, lock in list((locks_doc.get("locks") or {}).items()):
        ci = lock.get("custom_image") if isinstance(lock, dict) else None
        if ci:
            _sidecar_drop(root, ci)
        locks_doc["locks"].pop(key, None)
    locks_doc["approved"], locks_doc["approved_at"] = False, None
    _save_locks(root, locks_doc)


# ─── narration pin ──────────────────────────────────────────────────────────────

def pin_narration(project_root) -> None:
    from ..review_gate import narration_sha1
    root = _root(project_root)
    write_json_atomic(root / PIN_REL, {"narration_sha1": narration_sha1(root)})


def sync_with_narration(project_root) -> bool:
    """True when the picks were cleared because narration.json is not the one they were made
    against. First sight of a project just records the pin."""
    from ..review_gate import narration_sha1
    root = _root(project_root)
    current = narration_sha1(root)
    pinned = _read(root / PIN_REL, {}).get("narration_sha1")
    if pinned is None:
        if current:
            pin_narration(root)
        return False
    if current and pinned != current:
        clear_all(root)
        pin_narration(root)
        return True
    return False


# ─── TTS durations (ONE status path) ────────────────────────────────────────────

def tts_status(project_root) -> dict:
    """The background-TTS progress doc: {"completed": bool, "scene_durations": {sid: sec},
    "beat_durations": {beat_key: sec}, ...}.

    ONE status path: stages.stage_4.tts_status (P1) owns it — projects/<p>/review/tts_status.json,
    written by the background runner and read by the review gate and the /moments_review routes.
    Until that module is merged into the branch the same documented path is read directly."""
    root = _root(project_root)
    try:
        from ..stage_4 import tts_status as _tts
    except ImportError:
        doc = _read(root / "review" / "tts_status.json", {})
    else:
        doc = _tts.read_status(root)
    doc = dict(doc) if isinstance(doc, dict) else {}
    doc.setdefault("completed", False)
    doc.setdefault("scene_durations", {})
    doc.setdefault("beat_durations", {})
    return doc


def beat_seconds(project_root, narration: dict, screen_context: dict | None = None) -> dict[str, float]:
    """{beat_key: seconds} — the real TTS durations when the runner has produced them (its own
    per-beat numbers where it has them, else this module's split of the scene durations), else
    the writer's estimate."""
    from .screen_beats import estimate_beat_seconds
    st = tts_status(project_root)
    out = estimate_beat_seconds(narration, st.get("scene_durations") or None, screen_context)
    for key, v in (st.get("beat_durations") or {}).items():
        try:
            if key in out and float(v) > 0:
                out[key] = float(v)
        except (TypeError, ValueError):
            continue
    return out


# ─── backup clip ────────────────────────────────────────────────────────────────

def _cached_shortlist(root: Path, beat: str) -> list[dict]:
    """The shortlist /moments_review already searched for this beat (review/clips/search_*.json),
    newest first — so choosing a backup costs a download, not another search."""
    safe = beat.replace(":", "_")
    files = sorted((root / "review" / "clips").glob(f"search_{safe}*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        rows = _read(f, [])
        if rows:
            return rows
    return []


def suggest_backup(
    project_root, beat: str, *, query: str = "", seconds: float = 3.0,
    search: Callable[..., list[dict]] | None = None,
    fetch: Callable[..., Path] | None = None,
    log: Callable[[str], None] = print,
) -> dict | None:
    """Give the clip on `beat` a BACKUP: the best OTHER video of the beat's shortlist, its first
    suggested moment downloaded as a section file and written to the entry's "backup". Level 2
    of the never-crash chain plays it when the primary will not render. Best effort — any
    failure returns None and the chain simply has one level fewer."""
    from .. import clip_fetch
    root = _root(project_root)
    primary = load_clips(root).get(beat)
    if not primary:
        return None
    primary_id = str(primary.get("id") or "")
    primary_vid = clip_fetch.video_id(str(primary.get("source_url") or "")) or primary_id
    rows = _cached_shortlist(root, beat)
    if not rows and query:
        try:
            rows = (search or clip_fetch.moment_search)(query, limit=6, log=log)
        except Exception as exc:                                     # noqa: BLE001 — best effort
            log(f"[screen_qa] backup search failed ({exc})")
            return None
    for cand in rows:
        vid = str(cand.get("id") or "")
        if not vid or "error" in cand or vid == primary_vid:
            continue
        moments = cand.get("moments") or []
        start = float(moments[0].get("start", 0.0)) if moments else 0.0
        url = str(cand.get("url") or f"https://www.youtube.com/watch?v={vid}")
        try:
            section = (fetch or clip_fetch.fetch_clip_section)(
                url, root / "review" / "clips", start=start, beat_duration=seconds, log=log)
        except Exception as exc:                                     # noqa: BLE001 — next candidate
            log(f"[screen_qa] backup {vid} download failed ({exc})")
            continue
        try:
            rel = Path(section).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = str(section)
        backup = {"id": f"{vid}-backup", "file": rel, "start": 0.0, "end": round(float(seconds), 3),
                  "source_url": url, "source_start": start}
        if set_backup(root, beat, backup, only_if_clip_id=primary_id):
            return backup
        return None                                  # the primary changed under us
    return None
