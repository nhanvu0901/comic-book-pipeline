"""Derive and record typed production milestones from saved project artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
from typing import Any

from .ledger import Ledger, _issue_key, _canonical_json

_MILESTONE_LOCK = threading.RLock()
_FINAL_PROBE_CACHE: dict[tuple[str, tuple[int, int, int, int, int]], bool] = {}
_FINAL_PROBE_LOCK = threading.RLock()
_FINAL_PROBE_TIMEOUT_SECONDS = 8


def render_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    """Return the file identity tuple shared with the UI render marker."""
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file() or stat.st_size <= 0:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _marker_signature(project: Path) -> tuple[int, int, int, int, int] | None:
    # The sidecar is independent of AppState saves, which may serialize a stale
    # in-memory snapshot after rendering. Keep state.json as a legacy read fallback.
    for marker_path in (project / "final.mp4.verified.json", project / "state.json"):
        data = _read_json(marker_path)
        marker = data.get("verified_final_signature") if data else None
        try:
            if isinstance(marker, list) and len(marker) == 5:
                return tuple(int(value) for value in marker)
        except (TypeError, ValueError):
            continue
    return None


def _probe_mp4(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=format_name:stream=codec_type",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=_FINAL_PROBE_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        metadata = json.loads(result.stdout)
    except (TypeError, ValueError):
        return False
    format_names = set(str((metadata.get("format") or {}).get("format_name") or "").split(","))
    streams = metadata.get("streams") or []
    return "mp4" in format_names and any(stream.get("codec_type") == "video" for stream in streams)


def is_verified_final(project: str | Path) -> bool:
    """Accept a matching render marker or validate an unmarked legacy MP4 with ffprobe."""
    project = Path(project)
    final = project / "final.mp4"
    signature = render_signature(final)
    if signature is None:
        return False
    if _marker_signature(project) == signature:
        return True
    cache_key = (str(final.resolve()), signature)
    with _FINAL_PROBE_LOCK:
        if cache_key in _FINAL_PROBE_CACHE:
            return _FINAL_PROBE_CACHE[cache_key]
    valid = _probe_mp4(final)
    with _FINAL_PROBE_LOCK:
        if len(_FINAL_PROBE_CACHE) >= 128:
            _FINAL_PROBE_CACHE.clear()
        _FINAL_PROBE_CACHE[cache_key] = valid
    return valid


def _clear_verified_final_cache() -> None:
    """Clear the stat-keyed probe cache; exposed for deterministic tests."""
    with _FINAL_PROBE_LOCK:
        _FINAL_PROBE_CACHE.clear()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _issue_identity(value: Any, start_year: Any = None) -> str | None:
    key = _issue_key(str(value or ""))
    if not key:
        return None
    parts = key.split("|")
    if start_year and not parts[1]:
        year = str(start_year).strip()
        if len(year) == 4 and year.isdigit():
            parts[1] = year
    return "|".join(parts)


def _context_issue(context: dict[str, Any]) -> str:
    value = context.get("series_issue_year") or ""
    if value and _issue_key(str(value)):
        return str(value)
    series = str(context.get("series") or "").strip()
    issue = str(context.get("issue") or "").strip()
    year = str(context.get("source_year") or context.get("year") or "").strip()
    if series and issue:
        return f"{series} #{issue}" + (f" ({year})" if year else "")
    title = context.get("title") or value
    return str(title) if title else ""


def _has_issue_publication_year(identity: Any, source_year: Any = None) -> bool:
    if source_year and re.fullmatch(r"(?:19|20)\d{2}", str(source_year).strip()):
        return True
    return _issue_embeds_publication_year(identity)


def _issue_embeds_publication_year(identity: Any) -> bool:
    return bool(re.search(r"#\s*\d+(?:\.\d+)?[a-z]?\s*\(\s*(?:19|20)\d{2}\s*\)", str(identity), re.I))


def _narration_hash(project: Path) -> str | None:
    path = project / "narration.json"
    if _read_json(path) is None:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_event(project_name: str, kind: str, *, projects_root: Path) -> dict[str, Any] | None:
    """Return a typed event for a project's saved identity and narration."""
    if kind not in {"in_progress", "produced"}:
        raise ValueError("production milestones must be in_progress or produced")
    project = Path(projects_root) / project_name
    narration_hash = _narration_hash(project)
    if narration_hash is None:
        return None
    refs: dict[str, Any] = {"project": project_name, "narration_sha256": narration_hash}

    answer = _read_json(project / "answer_context.json")
    if answer is not None:
        keys = []
        for item in answer.get("items", []):
            if not isinstance(item, dict):
                return None
            identity = item.get("source_comic") or item.get("series_issue_year") or item.get("comic") or item.get("issue") or ""
            if identity and item.get("source_year") and not re.search(r"\b(?:19|20)\d{2}\b", str(identity)):
                identity = f"{identity} ({item['source_year']})"
            key = _issue_identity(identity, item.get("series_start_year"))
            if not key:
                return None
            keys.append(key)
        question = str(answer.get("question") or answer.get("title") or "").strip()
        if not keys or not question:
            return None
        return {"mode": "qa", "kind": kind, "key": "", "keys": sorted(set(keys)), "label": question,
                "scope": "item", "refs": refs}

    candidate = _read_json(project / "scout_candidate.json")
    context = _read_json(project / "comic_context.json")
    if candidate is None and context:
        embedded = context.get("scout_candidate")
        candidate = embedded if isinstance(embedded, dict) else None
    if context is None:
        return None
    source = candidate if candidate else context
    identity = source.get("series_issue_year") or source.get("comic") or ""
    if not identity:
        identity = _context_issue(context)
    is_micro = bool(candidate) or context.get("pipeline_mode") == "micro_moment"
    source_year = source.get("source_year") or context.get("source_year")
    if is_micro and not _has_issue_publication_year(identity, source_year):
        return None
    if source_year and not _issue_embeds_publication_year(identity):
        year = str(source_year).strip()
        if re.fullmatch(r"(?:19|20)\d{2}", year):
            identity = f"{str(identity).rstrip()} ({year})"
    key = _issue_identity(identity, source.get("series_start_year") or context.get("series_start_year"))
    if not key:
        return None
    return {"mode": "micro" if is_micro else "recap", "kind": kind, "key": key,
            "label": str(identity).strip(), "scope": "item", "refs": refs}


def record_milestone(project_name: str, kind: str, *, projects_root: Path, ledger: Ledger | None = None) -> str:
    """Record one deterministic approval/render milestone and refresh the snapshot."""
    store = ledger or Ledger()
    if not store._writer_guard():
        return "read_only"
    event = project_event(project_name, kind, projects_root=projects_root)
    if event is None:
        return "unresolved_identity"
    identity_data = {"mode": event["mode"], "project": project_name, "kind": kind}
    if kind == "in_progress":
        identity_data["narration_sha256"] = event["refs"]["narration_sha256"]
    milestone_id = hashlib.sha256(_canonical_json(identity_data).encode("utf-8")).hexdigest()
    with _MILESTONE_LOCK:
        _stored, inserted = store.append_milestone(event, milestone_id)
        try:
            store.export_jsonl(store.db_path.with_name("export.jsonl"))
        except Exception:
            return "export_pending"
    return "recorded" if inserted else "already_recorded"
