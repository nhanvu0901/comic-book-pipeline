from stages.research_scout.opportunity import editorial_quality, factual_input_hash, rank_candidates, source_material_coverage


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
    ranked = rank_candidates(candidates, assessments_by_key={"||low": {**_assessment(.2), "input_hash": factual_input_hash(candidates[0])},
                                                         "||high": {**_assessment(.9), "input_hash": factual_input_hash(candidates[1])}},
                             signals_by_key={"||high": {"demand": {"status": "observed", "direct_count": 1}}})

    assert [item["id"] for item in ranked] == ["high", "low"]
    assert "eligibility" not in ranked[0]["ranking"]
    assert "prediction" in ranked[0]["ranking"]["reason"]


def test_changed_factual_input_invalidates_cached_editorial_assessment():
    card = _candidate("a")
    assessment = {**_assessment(), "input_hash": factual_input_hash(card)}
    card["claim_citation"]["quote"] = "A changed fact."

    ranked = rank_candidates([card], assessments_by_key={"||a": assessment})

    assert ranked[0]["editorial_quality"]["status"] == "unknown"
    assert "factual input changed" in ranked[0]["editorial_quality"]["reason"]


def test_ranking_tie_break_ignores_stale_demand_observations():
    candidates = [_candidate("stale_cand"), _candidate("fresh_cand")]
    assessments = {
        "||stale_cand": {**_assessment(0.8), "input_hash": factual_input_hash(candidates[0])},
        "||fresh_cand": {**_assessment(0.8), "input_hash": factual_input_hash(candidates[1])},
    }
    signals = {
        "||stale_cand": {"demand": {"status": "stale", "direct_count": 0, "total_direct_count": 1, "checked_at": "2000-01-01T00:00:00+00:00"}},
        "||fresh_cand": {"demand": {"status": "observed", "direct_count": 1, "checked_at": "2026-10-30T00:00:00+00:00"}},
    }

    ranked = rank_candidates(candidates, assessments_by_key=assessments, signals_by_key=signals)

    assert [item["id"] for item in ranked] == ["fresh_cand", "stale_cand"]
    assert ranked[0]["ranking"]["direct_demand_observations"] == 1
    assert ranked[1]["ranking"]["direct_demand_observations"] == 0
    assert ranked[1]["ranking"]["ranking_direct_count"] == 0


def test_ranking_stale_demand_preserves_stable_input_order_against_no_demand():
    candidates = [_candidate("c1"), _candidate("c2")]
    assessments = {
        "||c1": {**_assessment(0.8), "input_hash": factual_input_hash(candidates[0])},
        "||c2": {**_assessment(0.8), "input_hash": factual_input_hash(candidates[1])},
    }
    signals_stale_first = {
        "||c1": {"demand": {"status": "stale", "direct_count": 0, "checked_at": "2000-01-01T00:00:00+00:00"}},
        "||c2": {"demand": {"status": "unknown"}},
    }

    ranked = rank_candidates(candidates, assessments_by_key=assessments, signals_by_key=signals_stale_first)
    assert [item["id"] for item in ranked] == ["c1", "c2"]

    # In reverse input order, stable order is also preserved
    ranked_rev = rank_candidates([candidates[1], candidates[0]], assessments_by_key=assessments, signals_by_key=signals_stale_first)
    assert [item["id"] for item in ranked_rev] == ["c2", "c1"]


def test_rank_with_registry_marks_stale_demand_and_zero_ranking_direct_count(tmp_path):
    from stages.research_scout.audience_evidence import AudienceEvidenceStore
    from stages.research_scout.opportunity import candidate_key, rank_with_registry

    registry = AudienceEvidenceStore(tmp_path)
    card = _candidate("card1")
    key = candidate_key(card)

    registry.add_demand_evidence(
        candidate_key=key,
        query="why hero changed",
        url="https://forum.example/thread",
        quote="Historical question from years ago",
        source_type="community",
        confidence="direct",
        checked_at="2000-01-01T00:00:00+00:00",
    )

    ranked = rank_with_registry([card], registry)

    assert len(ranked) == 1
    assert ranked[0]["id"] == "card1"
    # Status must be stale, direct count for ranking must be 0
    signals = ranked[0]["audience_evidence"]["signals"]
    assert signals["demand"]["status"] == "stale"
    assert signals["demand"]["direct_count"] == 0
    assert ranked[0]["ranking"]["direct_demand_observations"] == 0
    assert ranked[0]["ranking"]["ranking_direct_count"] == 0
    # Provenance date must remain visible
    assert signals["demand"]["checked_at"] == "2000-01-01T00:00:00+00:00"
    assert ranked[0]["audience_evidence"]["demand_evidence"][0]["checked_at"] == "2000-01-01T00:00:00+00:00"
    # Eligibility unaffected
    assert "eligibility" not in ranked[0]["ranking"]


def test_rank_with_registry_ignores_persisted_future_demand_in_tie_break(tmp_path):
    import sqlite3
    from stages.research_scout.audience_evidence import AudienceEvidenceStore
    from stages.research_scout.opportunity import candidate_key, rank_with_registry

    registry = AudienceEvidenceStore(tmp_path)
    card_future = _candidate("c_future")
    card_other = _candidate("c_other")
    key_future = candidate_key(card_future)

    # Persist a future timestamp directly into SQLite (e.g. legacy/corrupt input)
    with sqlite3.connect(registry.path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS demand_evidence (id INTEGER PRIMARY KEY, candidate_key TEXT, "
            "query_text TEXT, url TEXT, quote TEXT, source_type TEXT, confidence TEXT, checked_at TEXT)"
        )
        connection.execute(
            "INSERT INTO demand_evidence(candidate_key, query_text, url, quote, source_type, confidence, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key_future, "q", "https://example.test", "quote", "community", "direct", "2099-01-01T00:00:00+00:00"),
        )

    # Both cards have no editorial assessment (tied)
    ranked = rank_with_registry([card_future, card_other], registry)
    # The future card must not get tie-break advantage; stable input order preserved
    assert [c["id"] for c in ranked] == ["c_future", "c_other"]
    assert ranked[0]["ranking"]["direct_demand_observations"] == 0
    assert ranked[0]["ranking"]["ranking_direct_count"] == 0
    assert ranked[0]["audience_evidence"]["signals"]["demand"]["status"] == "stale"

    # In reverse input order, c_other is first
    ranked_rev = rank_with_registry([card_other, card_future], registry)
    assert [c["id"] for c in ranked_rev] == ["c_other", "c_future"]


