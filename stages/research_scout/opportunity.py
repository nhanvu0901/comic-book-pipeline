"""Separate sourced-material coverage from explicit editorial-quality ranking.

The scout's gates decide eligibility. This module never predicts views, rejects
cards, or turns a merely present field into a quality claim.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

RUBRIC_VERSION = 2
QUALITY_WEIGHTS = {"curiosity": 2.0, "stakes": 1.5, "payoff": 2.5, "clarity": 1.5}
QUALITY_DIMENSIONS = tuple(QUALITY_WEIGHTS)


def candidate_key(candidate: Mapping[str, Any]) -> str:
    parts = (str(candidate.get("canonical_anchor") or candidate.get("character_or_thing") or "").strip(),
             str(candidate.get("concept_key") or candidate.get("series_issue_year") or "").strip(),
             str(candidate.get("question") or candidate.get("moment") or candidate.get("title") or "").strip())
    return "|".join(" ".join(part.casefold().split()) for part in parts)


def _citation(candidate: Mapping[str, Any]) -> tuple[str, str]:
    citation = candidate.get("claim_citation")
    if isinstance(citation, Mapping):
        url, quote = str(citation.get("url") or "").strip(), str(citation.get("quote") or "").strip()
        if url and quote:
            return url, quote
    for item in candidate.get("issue_evidence") or []:
        if isinstance(item, Mapping):
            url, quote = str(item.get("source_url") or item.get("url") or "").strip(), str(item.get("quote") or "").strip()
            if url and quote:
                return url, quote
    return "", ""


def grounded_facts(candidate: Mapping[str, Any]) -> list[dict[str, str]]:
    """All allowed assessor cues, including the QA issue_evidence contract."""
    facts: list[dict[str, str]] = []
    citation = candidate.get("claim_citation")
    if isinstance(citation, Mapping):
        url, quote = str(citation.get("url") or "").strip(), str(citation.get("quote") or "").strip()
        if url and quote:
            facts.append({"kind": "claim", "url": url, "quote": quote})
    for detail in candidate.get("detail_citations") or []:
        if isinstance(detail, Mapping):
            url, quote = str(detail.get("url") or "").strip(), str(detail.get("quote") or "").strip()
            if url and quote:
                facts.append({"kind": "detail", "url": url, "quote": quote})
    for item in candidate.get("issue_evidence") or []:
        if isinstance(item, Mapping):
            url, quote = str(item.get("source_url") or item.get("url") or "").strip(), str(item.get("quote") or "").strip()
            if url and quote:
                facts.append({"kind": "issue_evidence", "url": url, "quote": quote,
                              "issue": str(item.get("issue") or "").strip()})
    return facts


def factual_input_hash(candidate: Mapping[str, Any]) -> str:
    """Invalidates a cached assessment whenever the supplied factual input changes."""
    payload = {"title": str(candidate.get("title") or ""),
               "question": str(candidate.get("question") or candidate.get("moment") or ""),
               "facts": grounded_facts(candidate), "rubric_version": RUBRIC_VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _detail_quote(candidate: Mapping[str, Any], supports: str) -> str:
    for item in candidate.get("detail_citations") or []:
        if (isinstance(item, Mapping) and str(item.get("supports") or "").strip() == supports
                and str(item.get("url") or "").strip() and str(item.get("quote") or "").strip()):
            return str(item["quote"]).strip()
    return ""


def _material(available: bool, cue: str, missing: str) -> dict[str, Any]:
    return {"status": "available" if available else "unknown", "cue": cue if available else None,
            "reason": f"Fact cue: {cue[:160]}" if available else missing}


def source_material_coverage(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Report stored facts; availability is deliberately not quality."""
    url, quote = _citation(candidate)
    premise = str(candidate.get("core_premise") or "").strip()
    turning = str(candidate.get("turning_point") or "").strip()
    payoff = str(candidate.get("viewer_payoff") or candidate.get("aftermath") or "").strip()
    visible = str(candidate.get("what_visibly_happens") or candidate.get("visible_event") or "").strip()
    context_quote, payoff_quote = _detail_quote(candidate, "context_behind"), _detail_quote(candidate, "aftermath")
    dimensions = {
        "premise": _material(bool(premise and url and quote), quote, "No bound fact cue for a premise."),
        "turning_point": _material(bool(turning and context_quote), context_quote, "No sourced context cue for the turning point."),
        "payoff": _material(bool(payoff and payoff_quote), payoff_quote, "No sourced aftermath/payoff cue."),
        "drawable_scene": _material(bool(visible and url and quote), quote, "No bound fact cue for a drawable scene."),
        "claim": _material(bool(url and quote), quote, "No bound URL and verbatim quote."),
    }
    return {"version": RUBRIC_VERSION,
            "available_dimensions": sum(d["status"] == "available" for d in dimensions.values()),
            "possible_dimensions": len(dimensions), "dimensions": dimensions,
            "explanation": "Source-material coverage only; not editorial quality or an audience prediction."}


def _unknown_quality(reason: str = "No grounded editorial assessment has been stored.") -> dict[str, Any]:
    return {"version": RUBRIC_VERSION, "status": "unknown", "total": None, "coverage": 0.0,
            "reason": reason,
            "formula": "Weighted mean of explicitly assessed dimensions only; unknown remains null.",
            "dimensions": {name: {"score": None, "rationale": reason, "fact_cues": []} for name in QUALITY_DIMENSIONS}}


def _fact_cues(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or not value:
        return None
    cues: list[dict[str, str]] = []
    for cue in value:
        if not isinstance(cue, Mapping):
            return None
        url, quote = str(cue.get("url") or "").strip(), str(cue.get("quote") or "").strip()
        if not url or not quote:
            return None
        cues.append({"url": url, "quote": quote})
    return cues


def editorial_quality(assessment: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate explicit scores. Missing/invalid input is unknown, never inferred."""
    supplied = assessment.get("dimensions") if isinstance(assessment, Mapping) else None
    if not isinstance(supplied, Mapping):
        return _unknown_quality(str(assessment.get("reason") or "No grounded editorial assessment has been stored.")
                                if isinstance(assessment, Mapping) else "No grounded editorial assessment has been stored.")
    dimensions: dict[str, dict[str, Any]] = {}
    weight, total = 0.0, 0.0
    for name in QUALITY_DIMENSIONS:
        item = supplied.get(name)
        if not isinstance(item, Mapping) or item.get("score") is None:
            dimensions[name] = {"score": None, "rationale": "Unknown: no grounded assessment.", "fact_cues": []}
            continue
        try:
            score = float(item["score"])
        except (TypeError, ValueError):
            score = -1.0
        rationale, cues = str(item.get("rationale") or "").strip(), _fact_cues(item.get("fact_cues"))
        if not 0 <= score <= 1 or not rationale or cues is None:
            dimensions[name] = {"score": None, "rationale": "Unknown: score needs rationale and bound fact cue(s).", "fact_cues": []}
            continue
        dimensions[name] = {"score": score, "rationale": rationale, "fact_cues": cues}
        weight += QUALITY_WEIGHTS[name]
        total += score * QUALITY_WEIGHTS[name]
    if not weight:
        return _unknown_quality()
    return {"version": RUBRIC_VERSION, "status": "assessed", "total": round(total / weight, 3),
            "coverage": round(weight / sum(QUALITY_WEIGHTS.values()), 3),
            "reason": "",
            "formula": "Weighted mean of explicitly assessed dimensions only; unknown remains null.",
            "dimensions": dimensions, "assessed_at": assessment.get("assessed_at"),
            "assessor": assessment.get("assessor") or "manual"}


def rank_candidates(candidates: Sequence[Mapping[str, Any]], *,
                    assessments_by_key: Mapping[str, Mapping[str, Any]] | None = None,
                    signals_by_key: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Only explicit quality can order cards; unknown cards retain input order."""
    assessments_by_key, signals_by_key = assessments_by_key or {}, signals_by_key or {}
    annotated: list[tuple[int, dict[str, Any]]] = []
    for index, original in enumerate(candidates):
        candidate, key = dict(original), candidate_key(original)
        assessment = assessments_by_key.get(key)
        if isinstance(assessment, Mapping) and assessment.get("input_hash") != factual_input_hash(candidate):
            assessment = {"reason": "Stored assessment is stale because its factual input changed."}
        quality = editorial_quality(assessment)
        demand = dict(signals_by_key.get(key, {}).get("demand") or {})
        direct = int(demand.get("direct_count") or 0) if demand.get("status") == "observed" else 0
        candidate["source_material_coverage"] = source_material_coverage(candidate)
        candidate["editorial_quality"] = quality
        candidate["ranking"] = {"status": quality["status"], "direct_demand_observations": direct,
            "reason": ("Explicit editorial assessment plus direct, source-linked demand; not a view prediction."
                       if quality["status"] == "assessed" else "No grounded editorial assessment: stable fallback order.")}
        annotated.append((index, candidate))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, float, float, int]:
        index, candidate = item
        quality = candidate["editorial_quality"]
        if quality["total"] is None:
            return (1.0, 0.0, 0.0, index)
        return (0.0, -float(quality["total"]), -float(candidate["ranking"]["direct_demand_observations"]), index)
    return [candidate for _, candidate in sorted(annotated, key=sort_key)]


def rank_with_registry(candidates: Sequence[Mapping[str, Any]], registry: Any) -> list[dict[str, Any]]:
    """Attach observations and rank without a network request."""
    signals = {candidate_key(c): registry.signals_for(candidate_key(c)) for c in candidates}
    assessments = {candidate_key(c): registry.quality_for(candidate_key(c)) for c in candidates}
    ranked = rank_candidates(candidates, assessments_by_key=assessments, signals_by_key=signals)
    for candidate in ranked:
        key = candidate_key(candidate)
        candidate["audience_evidence"] = {"signals": signals[key], "demand_evidence": registry.evidence_for(key)}
    return ranked
