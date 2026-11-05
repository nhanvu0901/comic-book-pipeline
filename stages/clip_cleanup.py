"""Per-beat clip lifecycle shared by the web picker and both review gates.

The pick lock covers generation checks, manifest commits and deletion. Keep tombstones:
removing a dict key would let a late job reuse generation 1 after a delete/re-add.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

PICK_STATES: dict[tuple[str, str], dict[str, Any]] = {}
PICK_LOCK = threading.RLock()
_BEAT = re.compile(r"^[a-zA-Z0-9_\-\.:]{1,64}$")


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def project_key(root: Path) -> str:
    import config
    try:
        return Path(root).resolve().relative_to(Path(config.PROJECTS_ROOT).resolve()).as_posix()
    except ValueError:
        return Path(root).name


def _aliases(root: Path, beat: str) -> set[str]:
    keys = {beat}
    try:
        scenes = json.loads((root / "narration.json").read_text("utf-8")).get("scenes") or []
        from stages.review_gate import bookend_row_keys
        for unit in ("intro", "outro"):
            scene = next((s for s in scenes if s.get(f"is_{unit}")), None)
            if scene:
                rows = bookend_row_keys(scene, unit)
                if len(rows) > 1 and beat in (unit, rows[0][0]):
                    keys.update((unit, rows[0][0]))
    except (OSError, ValueError, TypeError):
        pass
    return keys


def _media_refs(entry: Any) -> set[str]:
    if not isinstance(entry, dict):
        return set()
    return {str(entry[k]) for k in ("file", "preview_file")
            if entry.get(k) and Path(str(entry[k])).suffix.lower() == ".mp4"} | _media_refs(entry.get("backup"))


def _owned(root: Path, value: str) -> Path | None:
    """Only ordinary files inside review/clips; never follow a symlink or touch originals."""
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    target = root / rel
    base = root / "review" / "clips"
    if not target.is_relative_to(base) or target == base:
        return None
    if any(_is_link(p) for p in (target, *target.parents) if p.is_relative_to(root)):
        return None
    if not target.resolve().is_relative_to(base.resolve()):
        return None
    return target if target.is_file() else None


def remove_beat_clip(project_root: Path, beat: str) -> bool:
    """Invalidate jobs and clean only this beat's unshared app-owned artifacts.

    Locks/permission errors propagate. The manifest is retained until cleanup succeeds,
    so partial cleanup is reported as an error and can be retried after closing a player.
    Shared per-source downloads in src/ are deliberately kept.
    """
    from stages.stage_5 import clips
    from utils import fs_remove
    from utils.atomic_json import write_json_atomic

    if not _BEAT.fullmatch(str(beat)):
        raise ValueError("Invalid beat key")
    root = Path(project_root).absolute()
    base = root / "review" / "clips"
    locks_path = root / "review" / "locks.json"
    for p in (root, root / "review", base, base / "clips.json", locks_path):
        if _is_link(p):
            raise ValueError(f"Refusing cleanup through symlink/junction: {p.name}")
    keys = _aliases(root, str(beat))
    with PICK_LOCK, clips._MANIFEST_LOCK:
        # Validate the manifest before deleting: a corrupt one cannot prove a file is unshared.
        manifest = base / "clips.json"
        doc = json.loads(manifest.read_text("utf-8")) if manifest.exists() else {"clips": []}
        if not isinstance(doc, dict) or not isinstance(doc.get("clips"), list):
            raise ValueError("Invalid clips manifest; cleanup refused")
        removed = [c for c in doc["clips"] if isinstance(c, dict) and str(c.get("beat")) in keys]
        kept = [c for c in doc["clips"] if c not in removed]
        targets = set().union(*(_media_refs(c) for c in removed)) if removed else set()
        shared = set().union(*(_media_refs(c) for c in kept)) if kept else set()
        # Include project aliases (e.g. a nested project name) already registered by callers.
        import config
        projects = {project_key(root)} | {p for p, _ in PICK_STATES
                                  if (Path(config.PROJECTS_ROOT) / p).resolve() == root.resolve()}
        active_other_beat = any(p in projects and key not in keys
                                and state.get("state") in ("queued", "waiting_tts", "downloading", "rendering")
                                for (p, key), state in PICK_STATES.items())
        sections = set().union(*(_media_refs({"file": c.get("file"), "backup": c.get("backup")})
                                  for c in removed)) if removed else set()
        if active_other_beat:
            # A pending pick has not committed its file references yet. Keep source sections
            # conservatively until exclusivity can be proven; per-beat previews still go.
            targets.difference_update(sections)
        for project in projects:
            for key in keys:
                old = PICK_STATES.get((project, key), {})
                PICK_STATES[(project, key)] = {"gen": old.get("gen", 0) + 1,
                                               "state": "none", "t": time.time()}
        try:
            # Approval no longer covers the selection, even if a locked file stops cleanup.
            if locks_path.exists():
                locks = json.loads(locks_path.read_text("utf-8"))
                locks.update(approved=False, approved_at=None)
                write_json_atomic(locks_path, locks)
            for key in keys:
                stem = re.escape(key.replace(":", "_"))
                pattern = re.compile(rf"(?:preview_{stem}(?:\.pending_[0-9]+)?\.mp4|search_{stem}_[0-9a-f]{{8}}\.json)")
                if base.is_dir():
                    targets.update(p.relative_to(root).as_posix() for p in base.iterdir()
                                   if pattern.fullmatch(p.name))
            # Resolve shared paths as well: differently spelled references must still protect a file.
            shared_paths = {(root / p).resolve() for p in shared}
            for rel in sorted(targets - shared):
                path = _owned(root, rel)
                if path is not None and path.resolve() not in shared_paths:
                    fs_remove.remove_file(path)
            if removed:
                doc["clips"] = kept
                clips._write_manifest_doc(root, doc)
        except (OSError, ValueError, TypeError) as exc:
            for project in projects:
                for key in keys:
                    PICK_STATES[(project, key)].update(state="error", message=f"Không xóa sạch MP4: {exc}")
            raise
        return bool(removed)
