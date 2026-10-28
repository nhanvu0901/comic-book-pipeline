"""Bounded, fact-cue-constrained editorial assessor for scout candidates."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Mapping, Sequence

import config

from . import openrouter_gate
from .openrouter_gate import RequestFailure
from .opportunity import QUALITY_DIMENSIONS, RUBRIC_VERSION, factual_input_hash, grounded_facts

_MAX_BATCH = 3
_TIMEOUT = 45


@dataclass(frozen=True)
class AssessmentResult:
    assessments: dict[str, dict[str, Any]]
    error: str | None = None
    model: str | None = None


def candidate_token(index: int, candidate: Mapping[str, Any]) -> str:
    """Batch-local ID: provider-facing candidate IDs may legitimately collide."""
    digest = hashlib.sha256((factual_input_hash(candidate) + f":{index}").encode("utf-8")).hexdigest()[:12]
    return f"assess-{index}-{digest}"


def _facts(token: str, candidate: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{**fact, "id": f"{token}:fact:{index}"} for index, fact in enumerate(grounded_facts(candidate))]


def _request_body(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    supplied = []
    for index, candidate in enumerate(candidates[:_MAX_BATCH]):
        token = candidate_token(index, candidate)
        supplied.append({"candidate_token": token, "title": str(candidate.get("title") or ""),
                         "question": str(candidate.get("question") or candidate.get("moment") or ""),
                         "input_hash": factual_input_hash(candidate), "facts": _facts(token, candidate)})
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["assessments"],
        "properties": {"assessments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["candidate_token", "dimensions"], "properties": {
                "candidate_token": {"type": "string"}, "dimensions": {"type": "object", "additionalProperties": False,
                "required": list(QUALITY_DIMENSIONS), "properties": {name: {"type": "object", "additionalProperties": False,
                    "required": ["score", "rationale", "fact_cue_ids"], "properties": {
                        "score": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                        "rationale": {"type": "string"}, "fact_cue_ids": {"type": "array", "items": {"type": "string"}},
                    }} for name in QUALITY_DIMENSIONS}}}}}},
    }
    rubric = (
        "Rubric, all scores 0..1: curiosity 0=no concrete question, .5=clear question/conflict, "
        "1=unusual or urgent question directly supported by facts; stakes 0=no consequence, .5=specific consequence, "
        "1=high or irreversible consequence; payoff 0=no shown outcome, .5=shown outcome, 1=meaningful reversal/resolution; "
        "clarity 0=action unclear, .5=coherent action, 1=immediately visual causal action. "
        "Use null when facts do not support that dimension."
    )
    return {"model": config.SCOUT_EVIDENCE_MODEL, "temperature": 0,
            "messages": [
                {"role": "system", "content": "Assess editorial quality only from supplied facts. " + rubric + " Cite only supplied fact_cue_ids; never invent a cue, canon fact, audience metric, view prediction, or virality claim."},
                {"role": "user", "content": json.dumps({"task": "Assess the four rubric dimensions for each candidate.", "rubric_version": RUBRIC_VERSION, "candidates": supplied}, ensure_ascii=False)},
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
        known = {candidate_token(index, candidate): {fact["id"]: fact for fact in _facts(candidate_token(index, candidate), candidate)}
                 for index, candidate in enumerate(batch)}
        hashes = {candidate_token(index, candidate): factual_input_hash(candidate) for index, candidate in enumerate(batch)}
        assessments: dict[str, dict[str, Any]] = {}
        for item in content["assessments"]:
            if not isinstance(item, Mapping):
                continue
            token = str(item.get("candidate_token") or "")
            raw_dims = item.get("dimensions")
            if token not in known or not isinstance(raw_dims, Mapping):
                continue
            dims: dict[str, dict[str, Any]] = {}
            for name in QUALITY_DIMENSIONS:
                raw = raw_dims.get(name)
                if not isinstance(raw, Mapping) or raw.get("score") is None:
                    dims[name] = {"score": None, "rationale": str((raw or {}).get("rationale") or "Unknown from supplied facts."), "fact_cues": []}
                    continue
                cue_ids = raw.get("fact_cue_ids")
                if not isinstance(cue_ids, list) or not cue_ids or any(str(cue_id) not in known[token] for cue_id in cue_ids):
                    dims[name] = {"score": None, "rationale": "Unknown: assessor supplied no valid input fact cue.", "fact_cues": []}
                    continue
                dims[name] = {"score": raw.get("score"), "rationale": str(raw.get("rationale") or ""),
                              "fact_cues": [known[token][str(cue_id)] for cue_id in cue_ids]}
            assessments[token] = {"dimensions": dims, "input_hash": hashes[token],
                                  "rubric_version": RUBRIC_VERSION, "model": config.SCOUT_EVIDENCE_MODEL}
        return AssessmentResult(assessments, model=config.SCOUT_EVIDENCE_MODEL)
