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
from utils.atomic_json import write_json_atomic

from .evidence import GateFlag
from .errors import ScoutUserError
from .issue_identity import micro_issue_rejection_reason
from .models import ResearchSession, ScoutMode, SessionState
from .storage import SessionStore
from .workflow import _selected_series_detail, specific_search_artifact, verification_summary


@dataclass(frozen=True)
class _GateArtifact:
    gates: list[dict[str, Any]]
    is_collection: bool


# These are structural defects or a proven mismatch with the selected issue.
# A confirmation override cannot turn another comic into the requested one.
_NOT_OVERRIDABLE = frozenset({
    GateFlag.DUPLICATE, GateFlag.MALFORMED_OUTPUT, GateFlag.TARGET_ISSUE_MISMATCH,
})


@dataclass(frozen=True)
class _CandidateVerdict:
    """One selected candidate's gate outcome, with enough context to say why."""

    candidate_id: str
    label: str
    flags: list[GateFlag]
    verdict: str
    reason: str
    model_flags: list[str]


def evaluate_production_gates(
    session: ResearchSession, *, root: Path | None = None,
) -> dict[str, list[GateFlag]]:
    """Return production flags per selected candidate, keyed by candidate id.

    Keyed rather than flat so a failure can name which of five candidates went
    wrong; candidates with nothing to report are absent entirely.
    """

    return {
        report.candidate_id: report.flags
        for report in _evaluate(session, root=root)
        if report.flags
    }


def can_override_production_gates(
    session: ResearchSession, *, root: Path | None = None,
) -> bool:
    """Whether this session has only human-overridable production failures.

    Reuse the factory's production evaluator so the UI cannot offer consent for
    an artifact defect or a candidate from another issue. A malformed or
    missing artifact has no trustworthy verdict to override, so it stays hidden.
    """
    try:
        flags_by_candidate = evaluate_production_gates(session, root=root)
    except ScoutUserError:
        return False
    if flags_by_candidate:
        return all(
            not _NOT_OVERRIDABLE.intersection(flags)
            for flags in flags_by_candidate.values()
        )
    if session.mode is not ScoutMode.QA:
        return False
    store = SessionStore(root if root is not None else config.RESEARCH_SESSIONS_ROOT)
    candidates = _candidate_by_id(store, session.id)
    assignments = _gate_assignments(session, _load_gates(store, session.id))
    from stages.stage_1.answer_research import is_batcave_reader_url

    return any(
        not is_batcave_reader_url(
            _first_text(assignments[index] or {}, "reader_url")
            or _first_text(candidates[candidate_id], "reader_url")
        )
        for index, candidate_id in enumerate(session.selected_specific_candidate_ids)
    )


def _evaluate(
    session: ResearchSession, *, root: Path | None = None,
) -> list[_CandidateVerdict]:
    if not isinstance(session, ResearchSession):
        raise TypeError("session must be a ResearchSession")
    _validate_selection_count(session)

    store = SessionStore(root if root is not None else config.RESEARCH_SESSIONS_ROOT)
    candidates = _candidate_by_id(store, session.id)
    assignments = _gate_assignments(session, _load_gates(store, session.id))
    selected_ids = session.selected_specific_candidate_ids

    reports: list[_CandidateVerdict] = []
    for index, candidate_id in enumerate(selected_ids):
        candidate = candidates.get(candidate_id)
        gate = assignments[index]
        flags: list[GateFlag] = []
        if selected_ids.count(candidate_id) > 1:
            flags.append(GateFlag.DUPLICATE)

        if candidate is None or gate is None:
            flags.append(GateFlag.MALFORMED_OUTPUT)
            reports.append(_CandidateVerdict(
                candidate_id=candidate_id,
                label=_label(candidate_id, candidate),
                flags=_unique_flags(flags),
                verdict="",
                reason="",
                model_flags=[],
            ))
            continue

        verdict = str(gate.get("verdict", "")).strip().lower()
        if verdict != "confirmed":
            flags.append(GateFlag.VERDICT_NOT_CONFIRMED)
        known, model_flags = _gate_flags(gate.get("flags", []))
        flags.extend(known)

        if not _has_exact_issue_and_year(candidate):
            flags.append(GateFlag.EXACT_ISSUE_REQUIRED)
        issue_reason = None
        if session.mode is ScoutMode.MICRO:
            if not _has_visual_event(candidate):
                flags.append(GateFlag.NO_VISUAL_EVENT)
            issue_reason = micro_issue_rejection_reason(session.user_intent, candidate)
            if issue_reason:
                flags.append(GateFlag.TARGET_ISSUE_MISMATCH)

        gate_reason = str(gate.get("reason", "") or "")
        if issue_reason:
            gate_reason = f"{issue_reason}; {gate_reason}" if gate_reason else issue_reason

        reports.append(_CandidateVerdict(
            candidate_id=candidate_id,
            label=_label(candidate_id, candidate),
            flags=_unique_flags(flags),
            verdict=verdict,
            reason=gate_reason,
            model_flags=model_flags,
        ))
    return reports


def _label(candidate_id: str, candidate: Mapping[str, Any] | None) -> str:
    if candidate is None:
        return candidate_id
    detail = _first_text(candidate, "series_issue_year", "title", "entity")
    return f"{candidate_id} ({detail})" if detail else candidate_id


def _describe(report: _CandidateVerdict) -> str:
    """One candidate's problems, in the shape spec section 7 asks for."""
    headlines: list[str] = []
    for flag in report.flags:
        if flag is GateFlag.VERDICT_NOT_CONFIRMED:
            headlines.append(f"verdict {report.verdict or 'missing'}")
        elif flag is GateFlag.MODEL_FLAG:
            headlines.extend(report.model_flags)
        elif flag is GateFlag.MALFORMED_OUTPUT:
            headlines.append(f"{flag.value} (gate missing or unassignable)")
        else:
            headlines.append(flag.value)
    lines = [f"{report.label}: {', '.join(headlines)}"]
    if report.reason:
        lines.append(f'  - "{report.reason}"')
    return "\n".join(lines)


def create_project_from_session(
    session_id: str, project_slug: str, *, override: bool = False,
    series_diversity_override: bool = False,
) -> str:
    """Materialise one confirmed research session and mark it created last.

    ``override`` waves through the soft gates — an unconfirmed verdict, a model
    flag, a missing issue number — after the user has explicitly confirmed
    them, and is recorded in the audit. It can never wave through a duplicate
    selection, an issue mismatch, or a gate that could not be assigned.
    ``series_diversity_override`` is a separate, explicit consent for a Q&A
    selection covering fewer than three parsed series.
    """

    store = SessionStore(config.RESEARCH_SESSIONS_ROOT)
    session = store.load(session_id)
    if session.created_project:
        raise ScoutUserError(
            f"session {session.id!r} already created project {session.created_project!r}"
        )
    if session.state is not SessionState.PRODUCTION_GATES:
        raise ScoutUserError("session must be in PRODUCTION_GATES before project creation")
    if not isinstance(project_slug, str) or not project_slug.strip():
        raise ScoutUserError("project_slug must be a non-empty string")

    _validate_selection_count(session)
    reports = [report for report in _evaluate(session) if report.flags]
    blocking = [r for r in reports if _NOT_OVERRIDABLE.intersection(r.flags)]
    if blocking:
        raise ScoutUserError(
            "production gates failed:\n" + "\n".join(_describe(r) for r in blocking)
        )
    if reports and not override:
        raise ScoutUserError(
            "production gates failed:\n"
            + "\n".join(_describe(r) for r in reports)
            + "\n(override to create the project anyway)"
        )

    candidates = _candidate_by_id(store, session.id)
    gate_artifact = _load_gates(store, session.id)
    assignments = _gate_assignments(session, gate_artifact)
    selected = [
        (candidates[candidate_id], assignments[index])
        for index, candidate_id in enumerate(session.selected_specific_candidate_ids)
    ]

    diversity = _selected_series_detail(
        session.mode,
        session.selected_specific_candidate_ids,
        {candidate_id: candidates[candidate_id]
         for candidate_id in session.selected_specific_candidate_ids
         if candidate_id in candidates},
    )
    if diversity["series_diversity_warning"] and not series_diversity_override:
        raise ScoutUserError(
            "Q&A selection has insufficient series diversity; confirm the override to create the project."
        )

    unresolved_reader_urls: list[dict] = []
    if session.mode is ScoutMode.QA:
        research = _qa_research(session, selected)
        try:
            answer_research.build_contexts(
                session.user_intent, research, project_slug, allow_missing_reader_urls=override
            )
        except answer_research.MissingReaderUrlsError as exc:
            raise ScoutUserError(
                f"{exc}\n(enable override to create the project with unresolved reader URLs)"
            ) from exc
        unresolved_reader_urls = answer_research.get_missing_reader_urls(project_slug)
    else:
        candidate, gate = selected[0]
        _create_micro_project(store, session, candidate, gate, project_slug)

    if reports or (override and session.mode is ScoutMode.QA and unresolved_reader_urls):
        store.append_audit(
            session.id,
            "gates_overridden",
            detail={
                "project": project_slug,
                "candidates": {r.candidate_id: _describe(r) for r in reports},
                "unresolved_reader_urls": unresolved_reader_urls,
            },
        )
    if diversity["series_diversity_warning"] and series_diversity_override:
        store.append_audit(session.id, "series_diversity_overridden", detail={
            **diversity,
            "series_diversity_override": True,
            "candidate_ids": list(session.selected_specific_candidate_ids),
        })
    session.created_project = project_slug
    store.save(session, event="project_created", detail={"project": project_slug})
    return project_slug


def _validate_selection_count(session: ResearchSession) -> None:
    count = len(session.selected_specific_candidate_ids)
    if session.mode is ScoutMode.QA and not 3 <= count <= 5:
        raise ScoutUserError("QA requires three to five selected candidates")
    if session.mode is ScoutMode.MICRO and count != 1:
        raise ScoutUserError("MICRO requires exactly one selected candidate")


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
        raise ScoutUserError(f"missing research artifact: {name}") from exc
    except json.JSONDecodeError as exc:
        raise ScoutUserError(f"malformed research artifact: {name}") from exc


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


def _gate_flags(raw_flags: Any) -> tuple[list[GateFlag], list[str]]:
    """Split a gate's flags into ones this code knows and ones only the model does.

    An unrecognised flag string used to become MALFORMED_OUTPUT, which now means
    "unoverridable defect" — laundering a model's opinion into one would make a
    judgement call impossible to wave through.
    """
    if not isinstance(raw_flags, list):
        return [GateFlag.MALFORMED_OUTPUT], []
    flags: list[GateFlag] = []
    model_flags: list[str] = []
    for raw_flag in raw_flags:
        if isinstance(raw_flag, GateFlag):
            flags.append(raw_flag)
            continue
        try:
            flags.append(GateFlag(str(raw_flag)))
        except ValueError:
            model_flags.append(str(raw_flag))
            flags.append(GateFlag.MODEL_FLAG)
    return flags, model_flags


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


@dataclass(frozen=True)
class _MicroFacts:
    """What the project context and the scout_candidate both derive from the
    chosen candidate, so the two cannot read it differently."""

    exact_issue: str
    reader_url: str
    series: str
    year: str
    target_moment: str
    char_name: str


def _micro_facts(candidate: Mapping[str, Any], gate: Mapping[str, Any]) -> _MicroFacts:
    exact_issue = _first_text(candidate, "series_issue_year")
    series = _series_from_issue(exact_issue) or _first_text(candidate, "title", "series")
    return _MicroFacts(
        exact_issue=exact_issue,
        reader_url=_first_text(gate, "reader_url") or _first_text(candidate, "reader_url"),
        series=series,
        year=_first_text(candidate, "source_year") or _year(candidate),
        target_moment=_first_text(candidate, "visible_event", "what_visibly_happens", "moment"),
        char_name=_first_text(candidate, "character_or_thing", "character", "entity", "title") or series,
    )


# The scout returns these as "" when no source states them, and that is an answer,
# so a blank is kept. A candidate from before the aftermath ask has no such key
# at all, and there the key stays absent.
_DETAIL_TEXT_FIELDS = ("aftermath", "context_behind", "unrevealed")


def build_scout_candidate(
    store: SessionStore,
    session: ResearchSession,
    candidate: Mapping[str, Any],
    gate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """What the writer is handed about the chosen micro moment.

    Built only from what is already on disk — the candidate, its gate, and the
    verification round stored beside them — and it never writes or calls out.
    Project creation and refresh_scout_candidate both go through it, so a project
    made before a field existed can be brought up to date without paying for
    research again.

    The nine keys the writer has always read come first and are unchanged. The
    rest appear only when they exist: a blank would say "the scout looked and
    found nothing" about data that never existed. The old whitelist dropped the
    scout's own "NOT CONFIRMED ... withholds the exact twist" and the gate's
    reason, which is how a script came to end on a teaser nobody could resolve.
    """
    gate = gate or {}
    facts = _micro_facts(candidate, gate)
    citation = candidate.get("claim_citation") if isinstance(candidate.get("claim_citation"), dict) else {}
    quote = citation.get("quote") or ""
    source_url = citation.get("url") or facts.reader_url
    ev_urls = gate.get("evidence_urls") or candidate.get("evidence_urls") or ([source_url] if source_url else [])
    if isinstance(ev_urls, str):
        ev_urls = [ev_urls]

    scout_candidate: dict[str, Any] = {
        "character": facts.char_name,
        "series_issue_year": facts.exact_issue,
        "what_visibly_happens": facts.target_moment,
        "summary": _first_text(candidate, "summary", "how_or_why", "visible_event"),
        "claim_citation": citation,
        "verbatim_sentence": quote,
        "source_url": source_url,
        "evidence_urls": ev_urls,
        "verdict": str(gate.get("verdict") or "CONFIRMED").upper(),
    }
    for key in ("turning_point", "why_it_lands"):
        text = _first_text(candidate, key)
        if text:
            scout_candidate[key] = text
    for key in _DETAIL_TEXT_FIELDS:
        if isinstance(candidate.get(key), str):
            scout_candidate[key] = candidate[key].strip()
    if isinstance(candidate.get("detail_citations"), list):
        scout_candidate["detail_citations"] = _detail_citations(candidate["detail_citations"])
    reason = _first_text(gate, "reason")
    if reason:
        scout_candidate["reason"] = reason
    check = _scout_check(store, session.id, candidate)
    if check:
        scout_candidate["scout_check"] = check
    return scout_candidate


def _detail_citations(raw: list[Any]) -> list[dict[str, str]]:
    """The detail citations that name a page and quote it; one missing either
    cannot be checked, so the writer is not handed it."""
    kept: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        url, quote = _first_text(item, "url"), _first_text(item, "quote")
        if url and quote:
            kept.append({"supports": _first_text(item, "supports"), "url": url, "quote": quote})
    return kept


def _scout_check(store: SessionStore, session_id: str, candidate: Mapping[str, Any]) -> str:
    """The verdict and notes of this candidate's verification round, or "".

    That round ran after the candidate was proposed and is where a withheld twist
    is on record ("NOT CONFIRMED ... the review explicitly withholds the exact
    twist"). Not having run is normal — "" — and so is a round that failed."""
    candidate_id = _first_text(candidate, "id")
    if not candidate_id:
        return ""
    try:
        record = json.loads(
            store.artifact_path(session_id, specific_search_artifact(candidate_id)).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return ""
    return verification_summary(record.get("payload")) if isinstance(record, Mapping) else ""


def _write_scout_candidate(
    context_path: Path, context: dict[str, Any], scout_candidate: dict[str, Any]
) -> None:
    """Both copies of the scout_candidate, each atomically.

    scout_candidate.json goes first: Stage 3 reads it before the copy inside
    comic_context.json, so a crash between the two leaves the fresh one in front
    instead of a stale one."""
    context["scout_candidate"] = scout_candidate
    write_json_atomic(context_path.parent / "scout_candidate.json", scout_candidate)
    write_json_atomic(context_path, context)


def refresh_scout_candidate(
    project_name: str,
    *,
    projects_root: Path | None = None,
    sessions_root: Path | None = None,
) -> dict[str, Any]:
    """Rebuild a micro project's scout_candidate from its research session, offline.

    For a project made before the scout_candidate carried the gate's reason, the
    scout's own verification check or the aftermath fields: it finds the session
    that created the project, rebuilds the dict from that session's stored
    artifacts, and rewrites scout_candidate.json and the copy inside
    comic_context.json — nothing else in the context. No network and no paid call.
    A candidate that predates the aftermath ask has none to add, so those keys
    stay absent; ``reason`` and ``scout_check`` come from the stored gate and
    verification round. Returns the new dict.

    From a shell:
        python3 -c "from stages.research_scout.project_factory import refresh_scout_candidate as r; print(r('my-project'))"
    """
    name = Path(project_name) if isinstance(project_name, str) else None
    if (
        name is None
        or not project_name.strip()
        or name.is_absolute()
        or len(name.parts) != 1
        or name.name in {".", ".."}
    ):
        raise ScoutUserError("project name must be a single folder name")
    projects = Path(projects_root) if projects_root is not None else Path(config.PROJECTS_ROOT)
    project_dir = projects / project_name
    if not project_dir.is_dir():
        raise ScoutUserError(f"project {project_name!r} was not found in {projects}")
    context_path = project_dir / "comic_context.json"
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScoutUserError(f"project {project_name!r} has no comic_context.json") from exc
    except ValueError as exc:
        raise ScoutUserError(f"{context_path} is not valid JSON") from exc
    if not isinstance(context, dict):
        raise ScoutUserError(f"{context_path} is not a JSON object")

    store = SessionStore(Path(sessions_root) if sessions_root is not None else config.RESEARCH_SESSIONS_ROOT)
    session = _session_that_created(store, project_name)
    if session.mode is not ScoutMode.MICRO:
        raise ScoutUserError(
            f"project {project_name!r} came from a {session.mode.value} session; "
            "only micro-moment projects carry a scout_candidate"
        )
    _validate_selection_count(session)
    candidate_id = session.selected_specific_candidate_ids[0]
    candidate = _candidate_by_id(store, session.id).get(candidate_id)
    if candidate is None:
        raise ScoutUserError(
            f"candidate {candidate_id!r} is no longer in research session {session.id!r}"
        )
    gate = _gate_assignments(session, _load_gates(store, session.id))[0]
    if gate is None:
        raise ScoutUserError(
            f"research session {session.id!r} has no evidence gate for its selected candidate"
        )

    scout_candidate = build_scout_candidate(store, session, candidate, gate)
    _write_scout_candidate(context_path, context, scout_candidate)
    return scout_candidate


def _session_that_created(store: SessionStore, project_name: str) -> ResearchSession:
    """The one session whose ``created_project`` is this project; never a guess."""
    matches: list[ResearchSession] = []
    for entry in sorted(store.root.iterdir()):
        if not entry.is_dir() or not (entry / "session.json").is_file():
            continue
        try:
            session = store.load(entry.name)
        except Exception:
            continue  # a corrupt session is not this project's session
        if session.created_project == project_name:
            matches.append(session)
    if not matches:
        raise ScoutUserError(f"no research session created project {project_name!r}")
    if len(matches) > 1:
        raise ScoutUserError(
            f"more than one research session claims project {project_name!r}: "
            + ", ".join(session.id for session in matches)
        )
    return matches[0]


def _create_micro_project(
    store: SessionStore,
    session: ResearchSession,
    candidate: dict[str, Any],
    gate: dict[str, Any] | None,
    project_slug: str,
) -> None:
    gate = gate or {}
    facts = _micro_facts(candidate, gate)
    base_context = {
        "status": "ready",
        "pipeline_mode": "micro_moment",
        "title": _first_text(candidate, "title", "entity", "character") or facts.series,
        "series": facts.series,
        "issues": facts.exact_issue,
        "year": facts.year,
        "publisher": "",
        "characters": [facts.char_name],
        "reader_url": facts.reader_url,
        "batcave_url": facts.reader_url,
        "plot_summary": _first_text(candidate, "summary", "how_or_why", "visible_event"),
    }
    context_path = Path(save_comic_context(base_context, project_slug, get_project_dirs))
    context = json.loads(context_path.read_text(encoding="utf-8"))

    context.update(
        {
            "target_moment": facts.target_moment,
            "series_issue_year": facts.exact_issue,
            "issue": facts.exact_issue,
            "issues": facts.exact_issue,
            "year": facts.year,
            "reader_url": facts.reader_url,
            "batcave_url": facts.reader_url,
        }
    )
    _write_scout_candidate(context_path, context, build_scout_candidate(store, session, candidate, gate))


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
