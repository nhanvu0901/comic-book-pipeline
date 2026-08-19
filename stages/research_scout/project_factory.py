"""Turn a production-gated research session into a Stage 1 project."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config
from config import get_project_dirs

from stages.stage_1 import answer_research
from stages.stage_1.storage import save_comic_context

from .evidence import GateFlag
from .models import ResearchSession, ScoutMode, SessionState
from .storage import SessionStore


@dataclass(frozen=True)
class _GateArtifact:
    gates: list[dict[str, Any]]
    is_collection: bool


def evaluate_production_gates(session: ResearchSession) -> list[GateFlag]:
    """Return deterministic production flags for the selected session items.

    Evidence-gate artifacts have existed in both single-gate and collection
    shapes while the scout UI was being built.  The loader accepts those shapes
    but always evaluates the selected candidates in session order.
    """

    if not isinstance(session, ResearchSession):
        raise TypeError("session must be a ResearchSession")
    _validate_selection_count(session)

    store = SessionStore(config.RESEARCH_SESSIONS_ROOT)
    candidates = _candidate_by_id(store, session.id)
    gate_artifact = _load_gates(store, session.id)
    assignments = _gate_assignments(session, gate_artifact)
    selected_ids = session.selected_specific_candidate_ids
    flags: list[GateFlag] = []

    if len(selected_ids) != len(set(selected_ids)):
        flags.append(GateFlag.DUPLICATE)

    for index, candidate_id in enumerate(selected_ids):
        candidate = candidates.get(candidate_id)
        gate = assignments[index]
        if candidate is None or gate is None:
            flags.append(GateFlag.MALFORMED_OUTPUT)
            continue

        if not _is_confirmed(gate):
            flags.append(GateFlag.MALFORMED_OUTPUT)
        flags.extend(_gate_flags(gate.get("flags", [])))

        if not _has_exact_issue_and_year(candidate):
            flags.append(GateFlag.EXACT_ISSUE_REQUIRED)
        if session.mode is ScoutMode.MICRO and not _has_visual_event(candidate):
            flags.append(GateFlag.NO_VISUAL_EVENT)

    return _unique_flags(flags)


def create_project_from_session(session_id: str, project_slug: str) -> str:
    """Materialise one confirmed research session and mark it created last."""

    store = SessionStore(config.RESEARCH_SESSIONS_ROOT)
    session = store.load(session_id)
    if session.created_project:
        raise ValueError(
            f"session {session.id!r} already created project {session.created_project!r}"
        )
    if session.state is not SessionState.PRODUCTION_GATES:
        raise ValueError("session must be in PRODUCTION_GATES before project creation")
    if not isinstance(project_slug, str) or not project_slug.strip():
        raise ValueError("project_slug must be a non-empty string")

    _validate_selection_count(session)
    flags = evaluate_production_gates(session)
    if flags:
        rendered = ", ".join(flag.value for flag in flags)
        raise ValueError(f"production gates failed: {rendered}")

    candidates = _candidate_by_id(store, session.id)
    gate_artifact = _load_gates(store, session.id)
    assignments = _gate_assignments(session, gate_artifact)
    selected = [
        (candidates[candidate_id], assignments[index])
        for index, candidate_id in enumerate(session.selected_specific_candidate_ids)
    ]

    if session.mode is ScoutMode.QA:
        research = _qa_research(session, selected)
        # Do not catch ValueError: answer_research deliberately fails loud when
        # any item cannot be downloaded because its reader URL is empty.
        answer_research.build_contexts(session.user_intent, research, project_slug)
    else:
        candidate, gate = selected[0]
        _create_micro_project(candidate, gate, project_slug)

    session.created_project = project_slug
    store.save(session, event="project_created", detail={"project": project_slug})
    return project_slug


def _validate_selection_count(session: ResearchSession) -> None:
    count = len(session.selected_specific_candidate_ids)
    if session.mode is ScoutMode.QA and not 3 <= count <= 5:
        raise ValueError("QA requires three to five selected candidates")
    if session.mode is ScoutMode.MICRO and count != 1:
        raise ValueError("MICRO requires exactly one selected candidate")


def _candidate_by_id(store: SessionStore, session_id: str) -> dict[str, dict[str, Any]]:
    data = _read_artifact(store, session_id, "general/candidates.v1.json")
    raw_candidates = data.get("candidates", []) if isinstance(data, Mapping) else []
    if not isinstance(raw_candidates, list):
        return {}
    return {
        str(candidate["id"]): dict(candidate)
        for candidate in raw_candidates
        if isinstance(candidate, Mapping) and candidate.get("id")
    }


def _load_gates(store: SessionStore, session_id: str) -> _GateArtifact:
    data = _read_artifact(store, session_id, "specific/evidence_gate.v1.json")
    if isinstance(data, list):
        raw_gates = data
        is_collection = True
    elif isinstance(data, Mapping) and isinstance(data.get("gates"), list):
        raw_gates = data["gates"]
        is_collection = True
    elif isinstance(data, Mapping) and isinstance(data.get("gate"), Mapping):
        raw_gates = [data["gate"]]
        is_collection = False
    elif isinstance(data, Mapping):
        raw_gates = [data]
        is_collection = False
    else:
        raw_gates = []
        is_collection = False
    return _GateArtifact(
        gates=[dict(gate) for gate in raw_gates if isinstance(gate, Mapping)],
        is_collection=is_collection,
    )


def _read_artifact(store: SessionStore, session_id: str, name: str) -> Any:
    path = store.artifact_path(session_id, name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing research artifact: {name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed research artifact: {name}") from exc


def _gate_assignments(
    session: ResearchSession, artifact: _GateArtifact
) -> list[dict[str, Any] | None]:
    """Assign exactly one gate to each selected candidate, or return no assignment.

    A bare gate is the legacy artifact shape.  It remains valid for Micro's
    single selection, but cannot confirm multiple QA selections.  Collections
    may be keyed by candidate ID or entirely unkeyed; mixed collections and
    keyed collections with any mismatch are malformed rather than positional.
    """

    selected_ids = session.selected_specific_candidate_ids
    gates = artifact.gates
    invalid = [None] * len(selected_ids)

    if not artifact.is_collection:
        if session.mode is ScoutMode.QA or len(gates) != 1:
            return invalid
        key = _gate_candidate_id(gates[0])
        if key is not None and key != selected_ids[0]:
            return invalid
        return [gates[0]]

    if len(gates) != len(selected_ids):
        return invalid

    keys = [_gate_candidate_id(gate) for gate in gates]
    if all(key is None for key in keys):
        return list(gates)
    if any(key is None for key in keys):
        return invalid
    if len(set(keys)) != len(keys) or set(keys) != set(selected_ids):
        return invalid

    by_id = {key: gate for key, gate in zip(keys, gates)}
    return [by_id[candidate_id] for candidate_id in selected_ids]


def _gate_candidate_id(gate: Mapping[str, Any]) -> str | None:
    for key in ("candidate_id", "id"):
        value = gate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_confirmed(gate: Mapping[str, Any]) -> bool:
    return str(gate.get("verdict", "")).strip().lower() == "confirmed"


def _gate_flags(raw_flags: Any) -> list[GateFlag]:
    if not isinstance(raw_flags, list):
        return [GateFlag.MALFORMED_OUTPUT]
    flags: list[GateFlag] = []
    for raw_flag in raw_flags:
        if isinstance(raw_flag, GateFlag):
            flags.append(raw_flag)
            continue
        try:
            flags.append(GateFlag(str(raw_flag)))
        except ValueError:
            flags.append(GateFlag.MALFORMED_OUTPUT)
    return flags


def _unique_flags(flags: list[GateFlag]) -> list[GateFlag]:
    return list(dict.fromkeys(flags))


def _has_exact_issue_and_year(candidate: Mapping[str, Any]) -> bool:
    value = str(candidate.get("series_issue_year", "")).strip()
    return bool(re.search(r"(?:#|\bissue\s*)\s*\d+", value, re.IGNORECASE)) and bool(
        re.search(r"\b(?:19|20)\d{2}\b", value)
    )


def _has_visual_event(candidate: Mapping[str, Any]) -> bool:
    return any(
        isinstance(candidate.get(key), str) and candidate[key].strip()
        for key in ("visible_event", "what_visibly_happens", "moment")
    )


def _qa_research(
    session: ResearchSession,
    selected: list[tuple[dict[str, Any], dict[str, Any] | None]],
) -> dict[str, Any]:
    items = []
    for candidate, gate in selected:
        gate = gate or {}
        evidence_urls = gate.get("evidence_urls") or candidate.get("evidence_urls") or []
        if isinstance(evidence_urls, str):
            evidence_urls = [evidence_urls]
        verification_note = (
            candidate.get("verification_note")
            or gate.get("reason")
            or "; ".join(str(url) for url in evidence_urls)
        )
        items.append(
            {
                "entity": _first_text(candidate, "entity", "character", "title"),
                "how_or_why": _first_text(candidate, "how_or_why", "summary", "visible_event"),
                "source_comic": _first_text(candidate, "source_comic", "series_issue_year"),
                "source_year": _first_text(candidate, "source_year") or _year(candidate),
                "reader_url": _first_text(gate, "reader_url") or _first_text(candidate, "reader_url"),
                "drawable_moment": _first_text(
                    candidate, "drawable_moment", "visible_event", "moment"
                ),
                "verification_note": str(verification_note),
                "surprise_level": _first_text(candidate, "surprise_level") or "medium",
                "relationships": _first_text(candidate, "relationships"),
                "stakes_why": _first_text(candidate, "stakes_why"),
            }
        )
    return {
        "question": session.user_intent,
        "answer_summary": session.user_intent,
        "source_engine": "research_scout",
        "items": items,
    }


def _create_micro_project(
    candidate: dict[str, Any], gate: dict[str, Any] | None, project_slug: str
) -> None:
    gate = gate or {}
    exact_issue = _first_text(candidate, "series_issue_year")
    reader_url = _first_text(gate, "reader_url") or _first_text(candidate, "reader_url")
    series = _series_from_issue(exact_issue) or _first_text(candidate, "title", "series")
    year = _first_text(candidate, "source_year") or _year(candidate)
    target_moment = _first_text(candidate, "visible_event", "what_visibly_happens", "moment")
    base_context = {
        "status": "ready",
        "pipeline_mode": "micro_moment",
        "title": _first_text(candidate, "title", "entity", "character") or series,
        "series": series,
        "issues": exact_issue,
        "year": year,
        "publisher": "",
        "characters": [_first_text(candidate, "character", "entity")],
        "reader_url": reader_url,
        "batcave_url": reader_url,
        "plot_summary": _first_text(candidate, "summary", "how_or_why", "visible_event"),
    }
    context_path = Path(save_comic_context(base_context, project_slug, get_project_dirs))
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context.update(
        {
            "target_moment": target_moment,
            "series_issue_year": exact_issue,
            "issue": exact_issue,
            "issues": exact_issue,
            "year": year,
            "reader_url": reader_url,
            "batcave_url": reader_url,
        }
    )
    context_path.write_text(json.dumps(context, indent=2, ensure_ascii=False), encoding="utf-8")


def _first_text(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _year(candidate: Mapping[str, Any]) -> str:
    match = re.search(r"\b(?:19|20)\d{2}\b", _first_text(candidate, "series_issue_year"))
    return match.group(0) if match else ""


def _series_from_issue(value: str) -> str:
    series = re.sub(r"\s*#\s*\d+\b", "", value, count=1).strip()
    series = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", series).strip()
    return series
