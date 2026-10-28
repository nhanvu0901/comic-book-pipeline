"""Bounded, fact-cue-constrained editorial assessor for scout candidates."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping, Sequence

import config

from . import openrouter_gate
from .openrouter_gate import RequestFailure
from .opportunity import QUALITY_DIMENSIONS

_MAX_BATCH = 3
_TIMEOUT = 45


@dataclass(frozen=True)
class AssessmentResult:
    assessments: dict[str, dict[str, Any]]
    error: str | None = None
    model: str | None = None


def _facts(candidate: Mapping[str, Any]) -> list[dict[str, str]]:
    candidate_id = str(candidate.get("id") or "").strip()
    facts: list[dict[str, str]] = []
    citation = candidate.get("claim_citation")
    if isinstance(citation, Mapping):
        url, quote = str(citation.get("url") or "").strip(), str(citation.get("quote") or "").strip()
        if url and quote:
            facts.append({"id": f"{candidate_id}:claim", "url": url, "quote": quote})
    for index, detail in enumerate(candidate.get("detail_citations") or []):
        if not isinstance(detail, Mapping):
            continue
        url, quote = str(detail.get("url") or "").strip(), str(detail.get("quote") or "").strip()
        if url and quote:
            facts.append({"id": f"{candidate_id}:detail:{index}", "url": url, "quote": quote})
    return facts


def _request_body(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    supplied = []
    for candidate in candidates[:_MAX_BATCH]:
        supplied.append({"candidate_id": str(candidate.get("id") or ""), "title": str(candidate.get("title") or ""),
                         "question": str(candidate.get("question") or candidate.get("moment") or ""),
                         "facts": _facts(candidate)})
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["assessments"],
        "properties": {"assessments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["candidate_id", "dimensions"], "properties": {
                "candidate_id": {"type": "string"}, "dimensions": {"type": "object", "additionalProperties": False,
                "required": list(QUALITY_DIMENSIONS), "properties": {name: {"type": "object", "additionalProperties": False,
                    "required": ["score", "rationale", "fact_cue_ids"], "properties": {
                        "score": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                        "rationale": {"type": "string"}, "fact_cue_ids": {"type": "array", "items": {"type": "string"}},
                    }} for name in QUALITY_DIMENSIONS}}}}}},
    }
    return {"model": config.SCOUT_EVIDENCE_MODEL,
            "messages": [
                {"role": "system", "content": "Assess editorial quality only from the supplied facts. For each dimension, return null when the facts do not support a judgment. Cite only supplied fact_cue_ids; never invent a cue, canon fact, audience metric, view prediction, or virality claim."},
                {"role": "user", "content": json.dumps({"task": "Assess curiosity, stakes, payoff, and clarity for each candidate.", "candidates": supplied}, ensure_ascii=False)},
            ], "response_format": {"type": "json_schema", "json_schema": {"name": "editorial_assessment", "strict": True, "schema": schema}},
            "provider": {"require_parameters": True}}


class ConfiguredEditorialAssessor:
    """Uses the project's configured SCOUT_EVIDENCE_MODEL only when UI requests it."""
    def __init__(self, *, request: Callable[[dict[str, Any]], Any] | None = None):
        self.request = request

    def assess_batch(self, candidates: Sequence[Mapping[str, Any]]) -> AssessmentResult:
        batch = list(candidates[:_MAX_BATCH])
        if not batch:
            return AssessmentResult({})
        if self.request is None and not config.OPENROUTER_API_KEY:
            return AssessmentResult({}, "OPENROUTER_API_KEY is not set", config.SCOUT_EVIDENCE_MODEL)
        body = _request_body(batch)
        content = self.request(body) if self.request is not None else openrouter_gate._request(body, timeout=_TIMEOUT)
        if isinstance(content, RequestFailure):
            # One transport retry is allowed for the user-triggered, bounded batch.
            content = self.request(body) if self.request is not None else openrouter_gate._request(body, timeout=_TIMEOUT)
            if isinstance(content, RequestFailure):
                return AssessmentResult({}, content.message, config.SCOUT_EVIDENCE_MODEL)
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                return AssessmentResult({}, "invalid assessment JSON", config.SCOUT_EVIDENCE_MODEL)
        if not isinstance(content, Mapping) or not isinstance(content.get("assessments"), list):
            return AssessmentResult({}, "invalid assessment response", config.SCOUT_EVIDENCE_MODEL)
        known = {str(candidate.get("id") or ""): {fact["id"]: fact for fact in _facts(candidate)} for candidate in batch}
        assessments: dict[str, dict[str, Any]] = {}
        for item in content["assessments"]:
            if not isinstance(item, Mapping):
                continue
            candidate_id = str(item.get("candidate_id") or "")
            raw_dims = item.get("dimensions")
            if candidate_id not in known or not isinstance(raw_dims, Mapping):
                continue
            dims: dict[str, dict[str, Any]] = {}
            for name in QUALITY_DIMENSIONS:
                raw = raw_dims.get(name)
                if not isinstance(raw, Mapping) or raw.get("score") is None:
                    dims[name] = {"score": None, "rationale": str((raw or {}).get("rationale") or "Unknown from supplied facts."), "fact_cues": []}
                    continue
                cue_ids = raw.get("fact_cue_ids")
                if not isinstance(cue_ids, list) or not cue_ids or any(str(cue_id) not in known[candidate_id] for cue_id in cue_ids):
                    dims[name] = {"score": None, "rationale": "Unknown: assessor supplied no valid input fact cue.", "fact_cues": []}
                    continue
                dims[name] = {"score": raw.get("score"), "rationale": str(raw.get("rationale") or ""),
                              "fact_cues": [known[candidate_id][str(cue_id)] for cue_id in cue_ids]}
            assessments[candidate_id] = {"dimensions": dims}
        return AssessmentResult(assessments, model=config.SCOUT_EVIDENCE_MODEL)
