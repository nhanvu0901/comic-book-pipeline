from stages.research_scout.models import ScoutMode
from stages.research_scout import avoid_list
from stages.research_scout.cited_sources import canonical_url
from stages.research_scout.planner import ResearchPlan, assemble_prompt
from stages.research_scout.workflow import _validate_new_general_candidates


def _plan():
    return ResearchPlan(
        unit="one character", cardinality="exhaustive", research_prompt="Find the answer.",
    )


def _candidate(index, issue, title="Candidate"):
    url = f"https://sources.test/{index}"
    return {
        "id": f"c{index}", "title": title, "series_issue_year": issue,
        "claim_citation": {"url": url, "quote": f"Evidence {index}."},
    }


def test_planner_hard_rules_lead_both_modes_and_normalize_qa_unit():
    for mode in ("qa", "micro"):
        prompt = assemble_prompt(_plan(), mode=mode, avoid_lines=["Batman #57 (2024)"])
        assert prompt.startswith("HARD RULES")
        assert "One candidate per distinct" in prompt
        assert "SCOUTED DIGEST" not in prompt
        assert "ALREADY DONE" in prompt


def test_qa_filter_rejects_old_range_adaptation_and_duplicate_issue_key():
    candidates = [
        _candidate(1, "Batman #57 (2024)"),
        _candidate(2, "Batman #57 (2024)"),
        _candidate(3, "Superman #1 (1988)"),
        _candidate(4, "Wolverine #21-25 (2024)"),
        _candidate(5, "Superman Film #1 (2024)", title="Superman Returns"),
    ]
    rows = [{"url": f"https://sources.test/{i}"} for i in range(1, 6)]
    accepted, validation = _validate_new_general_candidates(
        candidates, {canonical_url(row["url"]) for row in rows}, mode=ScoutMode.QA,
        user_intent="Which heroes?", sources=rows, protected_fingerprints=set(),
    )

    assert [item["id"] for item in accepted] == ["c1"]
    assert [item["reason"] for item in validation["rejected"]] == [
        "duplicate_issue_key", "qa_pre_2010", "qa_issue_unparsed", "qa_not_comic",
    ]


def test_qa_pre_2010_is_allowed_when_request_names_an_older_period():
    candidate = _candidate(1, "Batman #57 (1988)")
    accepted, validation = _validate_new_general_candidates(
        [candidate], {canonical_url("https://sources.test/1")}, mode=ScoutMode.QA,
        user_intent="In 1988, which Batman issue did this?", sources=[{"url": "https://sources.test/1"}],
        protected_fingerprints=set(),
    )
    assert len(accepted) == 1
    assert validation["rejected"] == []


def test_qa_current_year_does_not_exempt_a_pre_2010_issue():
    candidate = _candidate(1, "Batman #57 (1988)")
    accepted, validation = _validate_new_general_candidates(
        [candidate], {canonical_url("https://sources.test/1")}, mode=ScoutMode.QA,
        user_intent="In 2026, which Batman issue is this?", sources=[{"url": "https://sources.test/1"}],
        protected_fingerprints=set(),
    )
    assert accepted == []
    assert validation["rejected"] == [{"candidate_id": "c1", "reason": "qa_pre_2010"}]


def test_issue_suffix_parsing_does_not_crash():
    candidate = _candidate(1, "Batman #15s (2024)")
    accepted, validation = _validate_new_general_candidates(
        [candidate], {canonical_url("https://sources.test/1")}, mode=ScoutMode.QA,
        user_intent="Batman issues", sources=[{"url": "https://sources.test/1"}],
        protected_fingerprints=set(),
    )
    assert accepted == []
    assert validation["rejected"] == [{"candidate_id": "c1", "reason": "qa_issue_unparsed"}]


def test_avoid_line_parser_canonicalizes_and_expands_ranges():
    assert avoid_list._labels("| Wolverine #21-23 (2024) | already done") == [
        "Wolverine #21 (2024)", "Wolverine #22 (2024)", "Wolverine #23 (2024)",
    ]
    assert avoid_list._labels("| 2026-07-16 | [micro] Batman: The Smile Killer #1 (2020) | reason") == [
        "Batman: The Smile Killer #1 (2020)",
    ]


def test_micro_inventory_reads_embedded_and_standalone_scout_candidate(tmp_path, monkeypatch):
    import json
    import config

    embedded = tmp_path / "embedded"
    embedded.mkdir()
    (embedded / "comic_context.json").write_text(json.dumps({
        "scout_candidate": {"series_issue_year": "Daredevil #5 (2024)"},
    }), encoding="utf-8")
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    (standalone / "scout_candidate.json").write_text(json.dumps({
        "series_issue_year": "Jessica Jones #2 (2025)"},
    ), encoding="utf-8")
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)

    labels = avoid_list._inventory(ScoutMode.MICRO)
    assert "Daredevil #5 (2024)" in labels
    assert "Jessica Jones #2 (2025)" in labels


def test_micro_hard_gate_uses_full_inventory_beyond_prompt_cap(tmp_path, monkeypatch):
    from datetime import date
    import json
    import config

    year = date.today().year
    for number in range(1, 61):
        project = tmp_path / f"project-{number:02d}"
        project.mkdir()
        (project / "comic_context.json").write_text(json.dumps({
            "scout_candidate": {
                "series_issue_year": f"Inventory Series #{number} ({year})",
            },
        }), encoding="utf-8")
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)

    prompt_lines = avoid_list.relevant_avoid_lines(
        ScoutMode.MICRO, "Find a moment from Inventory Series", limit=50,
    )
    all_keys = avoid_list.inventory_issue_keys(ScoutMode.MICRO)
    assert len(prompt_lines) == 50
    assert ("inventory series", "60") in all_keys

    candidate = _candidate(60, f"Inventory Series #60 ({year})")
    accepted, validation = _validate_new_general_candidates(
        [candidate], {canonical_url("https://sources.test/60")},
        mode=ScoutMode.MICRO, user_intent="Find a new moment",
        protected_keys=all_keys, sources=[{"url": "https://sources.test/60"}],
        protected_fingerprints=set(),
    )
    assert accepted == []
    assert validation["rejected"] == [
        {"candidate_id": "c60", "reason": "duplicate_issue_key"},
    ]


def test_qa_cross_question_inventory_is_not_a_hard_gate_but_held_is():
    candidate = _candidate(1, "Batman #57 (2024)")
    accepted, validation = _validate_new_general_candidates(
        [candidate], {canonical_url("https://sources.test/1")}, mode=ScoutMode.QA,
        user_intent="Which heroes?", sources=[{"url": "https://sources.test/1"}],
        protected_keys=set(), protected_fingerprints=set(),
    )
    assert len(accepted) == 1
    assert validation["rejected"] == []

    held, validation = _validate_new_general_candidates(
        [_candidate(2, "Batman #57 (2024)")], {canonical_url("https://sources.test/2")},
        mode=ScoutMode.QA, user_intent="Which heroes?",
        sources=[{"url": "https://sources.test/2"}],
        protected_keys={("batman", "57")}, protected_fingerprints=set(),
    )
    assert held == []
    assert validation["rejected"] == [{"candidate_id": "c2", "reason": "duplicate_issue_key"}]


def test_workflow_rebinds_rewritten_urls_and_rejects_structural_number_mismatch():
    returned = "https://marvel.fandom.com/wiki/Thing_Vol_1_2_New_Slug"
    cases = [
        # T1: formatting-only rewrite (www host and tracking query).
        ("http://www.marvel.fandom.com/wiki/Thing_Vol_1_2_New_Slug?utm_source=x",
         returned, "t1_match_key"),
        # T2: slug changed but the source's volume and issue key still match.
        ("https://marvel.fandom.com/wiki/Thing_Vol_1_2_Old_Slug",
         returned, "t2_structural_id"),
    ]
    for model_url, source_url, reason in cases:
        candidate = {
            "id": reason, "title": "Thing",
            "series_issue_year": "Thing #2 (2024)",
            "claim_citation": {"url": model_url, "quote": "The decisive event happened."},
        }
        sources = [{"url": source_url}]
        accepted, validation = _validate_new_general_candidates(
            [candidate], {canonical_url(source_url)}, mode=ScoutMode.QA,
            user_intent="Who did this?", sources=sources,
            protected_fingerprints=set(),
        )
        assert len(accepted) == 1
        assert accepted[0]["claim_citation"]["url"] == source_url
        assert accepted[0]["rebound_reason"] == reason
        assert validation["rejected"] == []

    wrong_issue_url = "https://marvel.fandom.com/wiki/Thing_Vol_1_1_Old_Slug"
    candidate = {
        "id": "wrong-issue", "title": "Thing",
        "series_issue_year": "Thing #2 (2024)",
        "claim_citation": {
            "url": "https://marvel.fandom.com/wiki/Thing_Vol_1_2_New_Slug",
            "quote": "No matching retrieved snippet.",
        },
    }
    accepted, validation = _validate_new_general_candidates(
        [candidate], {canonical_url(wrong_issue_url)}, mode=ScoutMode.QA,
        user_intent="Who did this?", sources=[{"url": wrong_issue_url}],
        protected_fingerprints=set(),
    )
    assert accepted == []
    assert validation["rejected"] == [
        {"candidate_id": "wrong-issue", "reason": "claim_citation_url_not_returned"},
    ]
