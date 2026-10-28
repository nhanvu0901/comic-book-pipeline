from stages.research_scout.opportunity import editorial_quality, rank_candidates, source_material_coverage


def _candidate(candidate_id: str, **extra):
    return {"id": candidate_id, "title": candidate_id, "what_visibly_happens": "Hero chooses a path.",
            "claim_citation": {"url": "https://example.test/issue", "quote": "Hero chooses the dangerous path."},
            "turning_point": "Hero chooses.", "aftermath": "The threat changes.",
            "detail_citations": [{"supports": "context_behind", "url": "https://example.test/context", "quote": "The city will fall."},
                                 {"supports": "aftermath", "url": "https://example.test/end", "quote": "The threat changes."}], **extra}


def _assessment(score=0.8):
    cue = {"url": "https://example.test/issue", "quote": "Hero chooses the dangerous path."}
    return {"dimensions": {name: {"score": score, "rationale": "Grounded editorial judgment.", "fact_cues": [cue]}
                           for name in ("curiosity", "stakes", "payoff", "clarity")}}


def test_source_material_coverage_is_not_presented_as_quality():
    coverage = source_material_coverage(_candidate("a"))

    assert coverage["available_dimensions"] > 0
    assert "not editorial quality" in coverage["explanation"]
    assert "score" not in coverage["dimensions"]["claim"]


def test_editorial_quality_requires_score_rationale_and_bound_cue():
    assessment = _assessment()
    assessment["dimensions"]["stakes"] = {"score": 1, "rationale": "No citation", "fact_cues": []}

    quality = editorial_quality(assessment)

    assert quality["status"] == "assessed"
    assert quality["dimensions"]["stakes"]["score"] is None
    assert quality["coverage"] < 1


def test_unknown_quality_is_null_and_keeps_input_tie_order():
    ranked = rank_candidates([_candidate("b"), _candidate("a")])

    assert [item["id"] for item in ranked] == ["b", "a"]
    assert ranked[0]["editorial_quality"]["total"] is None
    assert ranked[0]["ranking"]["status"] == "unknown"


def test_explicit_assessment_and_direct_demand_order_but_never_change_eligibility():
    candidates = [_candidate("low"), _candidate("high")]
    ranked = rank_candidates(candidates, assessments_by_key={"||low": _assessment(.2), "||high": _assessment(.9)},
                             signals_by_key={"||high": {"demand": {"status": "observed", "direct_count": 1}}})

    assert [item["id"] for item in ranked] == ["high", "low"]
    assert "eligibility" not in ranked[0]["ranking"]
    assert "prediction" in ranked[0]["ranking"]["reason"]
