import json

import pytest

import stages.research_scout.project_factory as factory
from stages.research_scout.evidence import GateFlag
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.research_scout.storage import SessionStore


def _wire_roots(tmp_path, monkeypatch):
    sessions_root = tmp_path / "research-sessions"
    projects_root = tmp_path / "projects"

    def get_project_dirs(slug):
        root = projects_root / slug
        root.mkdir(parents=True, exist_ok=True)
        return {"root": root}

    monkeypatch.setattr(factory.config, "RESEARCH_SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(factory, "get_project_dirs", get_project_dirs)
    monkeypatch.setattr(factory.answer_research, "get_project_dirs", get_project_dirs)
    return sessions_root, projects_root


def _candidate(candidate_id, *, mode=ScoutMode.QA, index=1):
    return {
        "id": candidate_id,
        "entity": f"Hero {index}",
        "title": f"Hero {index}",
        "character": f"Hero {index}",
        "series_issue_year": f"Thor #{index} (2024)",
        "source_comic": f"Thor #{index}",
        "source_year": "2024",
        "how_or_why": f"Hero {index} performs the confirmed action.",
        "visible_event": f"Hero {index} visibly performs the action.",
        "drawable_moment": f"Hero {index} raises a hammer.",
        "verification_note": f"Verified from source {index}.",
        "surprise_level": "low" if index == 1 else "medium",
        "reader_url": f"https://batcave.biz/reader/{index}/20{index}",
        "evidence_urls": [f"https://source.test/{index}"],
    }


def _gate(candidate_id, *, reader_url=None, verdict="confirmed", flags=None):
    return {
        "candidate_id": candidate_id,
        "verdict": verdict,
        "reason": "The source confirms the selected moment.",
        "evidence_urls": ["https://source.test/primary"],
        "reader_url": reader_url,
        "flags": flags or [],
    }


def _session(tmp_path, mode, candidates, gates, *, intent="Which heroes did this?"):
    store = SessionStore(tmp_path / "research-sessions")
    session = ResearchSession(
        id=f"{mode.value}-session",
        mode=mode,
        user_intent=intent,
        state=SessionState.PRODUCTION_GATES,
        selected_specific_candidate_ids=[candidate["id"] for candidate in candidates],
    )
    store.save(session)
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": candidates})
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", {"gates": gates})
    return session


def test_micro_factory_writes_target_moment_without_changing_stage_contract(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro", mode=ScoutMode.MICRO)
    session = _session(tmp_path, ScoutMode.MICRO, [candidate], [_gate("micro")])

    slug = factory.create_project_from_session(session.id, "thor-hammer")

    context = json.loads((tmp_path / "projects" / slug / "comic_context.json").read_text())
    assert context["target_moment"] == candidate["visible_event"]
    assert context["series_issue_year"] == candidate["series_issue_year"]
    assert context["issue"] == candidate["series_issue_year"]
    assert context["reader_url"] == candidate["reader_url"]


def test_micro_factory_refuses_an_old_off_topic_candidate_even_with_override(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("wrong", mode=ScoutMode.MICRO)
    session = _session(
        tmp_path, ScoutMode.MICRO, [candidate], [_gate("wrong")],
        intent="Amazing X-Men #2: Cyclops faces Darkchild.",
    )

    assert not factory.can_override_production_gates(session, root=tmp_path / "research-sessions")
    with pytest.raises(ValueError, match="target_issue_mismatch"):
        factory.create_project_from_session(session.id, "wrong-comic", override=True)
    assert not (tmp_path / "projects" / "wrong-comic").exists()


def test_micro_factory_accepts_the_exact_requested_issue(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("right", mode=ScoutMode.MICRO)
    candidate["series_issue_year"] = "Amazing X-Men #2 (2025)"
    session = _session(
        tmp_path, ScoutMode.MICRO, [candidate], [_gate("right")],
        intent="In Amazing X-Men #2, Cyclops faces Darkchild.",
    )

    assert factory.create_project_from_session(session.id, "right-comic") == "right-comic"


def test_qa_factory_requires_three_confirmed_reader_urls(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate("a", index=1), _candidate("b", index=2)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate("a"), _gate("b")])

    with pytest.raises(ValueError, match="three"):
        factory.create_project_from_session(session.id, "hulk-question")


def test_qa_factory_builds_contexts_once_from_selected_confirmed_gates(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    candidates[1]["series_issue_year"] = "Batman #2 (2024)"
    candidates[2]["series_issue_year"] = "Iron Man #3 (2024)"
    gates = [_gate(candidate["id"]) for candidate in candidates]
    session = _session(tmp_path, ScoutMode.QA, candidates, gates)
    calls = []

    def fake_build_contexts(question, research, project_name, **kwargs):
        calls.append((question, research, project_name, kwargs))
        return (tmp_path / "answer_context.json", tmp_path / "comic_context.json")

    monkeypatch.setattr(factory.answer_research, "build_contexts", fake_build_contexts)

    assert factory.create_project_from_session(session.id, "hulk-question") == "hulk-question"
    assert len(calls) == 1
    question, research, project_name, _ = calls[0]
    assert question == session.user_intent
    assert project_name == "hulk-question"
    assert len(research["items"]) == 3
    required = {
        "entity", "how_or_why", "source_comic", "source_year", "reader_url",
        "drawable_moment", "verification_note", "surprise_level",
    }
    assert all(required <= set(item) for item in research["items"])


def test_qa_series_diversity_soft_gate_requires_override(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    candidates[1]["series_issue_year"] = "Thor #22 (2025)"
    candidates[2]["series_issue_year"] = "Thor #33 (2026)"
    candidates[1]["series_issue_year"] = "Thor #22 (2025)"
    candidates[2]["series_issue_year"] = "Thor #33 (2026)"
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate(c["id"]) for c in candidates])

    with pytest.raises(ValueError, match="series diversity"):
        factory.create_project_from_session(session.id, "same-series")
    with pytest.raises(ValueError, match="series diversity"):
        factory.create_project_from_session(session.id, "same-series-gate-override", override=True)
    assert not (tmp_path / "projects" / "same-series").exists()


def test_qa_series_diversity_override_is_written_to_audit(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    candidates[1]["series_issue_year"] = "Thor #22 (2025)"
    candidates[2]["series_issue_year"] = "Thor #33 (2026)"
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate(c["id"]) for c in candidates])
    monkeypatch.setattr(
        factory.answer_research, "build_contexts", lambda *a, **k: (tmp_path, tmp_path)
    )

    factory.create_project_from_session(
        session.id, "same-series", series_diversity_override=True,
    )

    store = SessionStore(tmp_path / "research-sessions")
    audit = [json.loads(line) for line in
             (store.session_dir(session.id) / "audit.jsonl").read_text().splitlines()]
    diversity = [event for event in audit if event["event"] == "series_diversity_overridden"]
    assert len(diversity) == 1
    assert diversity[0]["detail"]["series_diversity_override"] is True
    assert diversity[0]["detail"]["selected_series_count"] == 1
    assert not any(event["event"] == "gates_overridden" for event in audit)


def test_qa_factory_rejects_legacy_single_gate_reused_for_all_candidates(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate("a")])
    store = SessionStore(tmp_path / "research-sessions")
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", _gate("a"))

    with pytest.raises(ValueError, match="malformed"):
        factory.create_project_from_session(session.id, "hulk-question")


def test_qa_factory_rejects_keyed_gates_for_unselected_candidates(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(
        tmp_path,
        ScoutMode.QA,
        candidates,
        [_gate("x"), _gate("y"), _gate("z")],
    )

    with pytest.raises(ValueError, match="malformed"):
        factory.create_project_from_session(session.id, "hulk-question")


def test_factory_preserves_build_contexts_reader_url_failure_and_does_not_mark_created(
    tmp_path, monkeypatch
):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    candidates[1]["series_issue_year"] = "Batman #2 (2024)"
    candidates[2]["series_issue_year"] = "Iron Man #3 (2024)"
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate(c["id"]) for c in candidates])

    def fail_loudly(*args, **kwargs):
        raise ValueError("empty reader_url for item(s): Hero 2")

    monkeypatch.setattr(factory.answer_research, "build_contexts", fail_loudly)

    with pytest.raises(ValueError, match="empty reader_url"):
        factory.create_project_from_session(session.id, "hulk-question")
    assert SessionStore(tmp_path / "research-sessions").load(session.id).created_project is None


def test_factory_rejects_second_create_for_a_session_with_created_project(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro")
    session = _session(tmp_path, ScoutMode.MICRO, [candidate], [_gate("micro")])

    factory.create_project_from_session(session.id, "thor-hammer")

    with pytest.raises(ValueError, match="already created"):
        factory.create_project_from_session(session.id, "thor-hammer")


def test_evaluate_production_gates_names_the_candidate_each_flag_belongs_to(
    tmp_path, monkeypatch
):
    """A flat list of flags could not say WHICH of five candidates failed, so the
    error it produced was unactionable. The result is keyed by candidate now."""
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro")
    session = _session(
        tmp_path,
        ScoutMode.MICRO,
        [candidate],
        [_gate("micro", flags=[GateFlag.NO_VISUAL_EVENT.value])],
    )

    assert factory.evaluate_production_gates(session) == {
        "micro": [GateFlag.NO_VISUAL_EVENT],
    }


def test_a_clean_session_reports_no_flags_at_all(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro")
    session = _session(tmp_path, ScoutMode.MICRO, [candidate], [_gate("micro")])

    assert factory.evaluate_production_gates(session) == {}


# ─── Blocking rules: what a human may wave through, and what they may not ───

def test_an_inconclusive_verdict_blocks_but_can_be_overridden(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    candidates[1]["series_issue_year"] = "Batman #2 (2024)"
    candidates[2]["series_issue_year"] = "Iron Man #3 (2024)"
    gates = [_gate("a", verdict="inconclusive"), _gate("b"), _gate("c")]
    session = _session(tmp_path, ScoutMode.QA, candidates, gates)
    monkeypatch.setattr(
        factory.answer_research, "build_contexts", lambda *a, **k: (tmp_path, tmp_path)
    )

    with pytest.raises(ValueError, match="verdict inconclusive"):
        factory.create_project_from_session(session.id, "hulk-question")

    assert factory.create_project_from_session(
        session.id, "hulk-question", override=True
    ) == "hulk-question"


def test_a_model_emitted_flag_blocks_but_can_be_overridden(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    candidates[1]["series_issue_year"] = "Batman #2 (2024)"
    candidates[2]["series_issue_year"] = "Iron Man #3 (2024)"
    gates = [_gate("a", flags=["panel_is_a_flashback"]), _gate("b"), _gate("c")]
    session = _session(tmp_path, ScoutMode.QA, candidates, gates)
    monkeypatch.setattr(
        factory.answer_research, "build_contexts", lambda *a, **k: (tmp_path, tmp_path)
    )

    with pytest.raises(ValueError, match="panel_is_a_flashback"):
        factory.create_project_from_session(session.id, "hulk-question")

    assert factory.create_project_from_session(
        session.id, "hulk-question", override=True
    ) == "hulk-question"


def test_a_missing_gate_is_a_defect_and_override_cannot_wave_it_through(tmp_path, monkeypatch):
    """A gate that cannot be assigned is a broken artifact, not a judgement call —
    there is nothing for a human to have an opinion about."""
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate("a"), _gate("b")])

    with pytest.raises(ValueError, match="malformed"):
        factory.create_project_from_session(session.id, "hulk-question", override=True)


def test_a_duplicate_selection_is_a_defect_and_override_cannot_wave_it_through(
    tmp_path, monkeypatch
):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate(c["id"]) for c in candidates])
    store = SessionStore(tmp_path / "research-sessions")
    session.selected_specific_candidate_ids = ["a", "a", "b"]
    store.save(session)

    with pytest.raises(ValueError, match="duplicate"):
        factory.create_project_from_session(session.id, "hulk-question", override=True)


def test_an_override_is_recorded_in_the_audit_with_each_candidates_reason(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    candidates[1]["series_issue_year"] = "Batman #2 (2024)"
    candidates[2]["series_issue_year"] = "Iron Man #3 (2024)"
    gates = [_gate("a", verdict="inconclusive"), _gate("b"), _gate("c")]
    session = _session(tmp_path, ScoutMode.QA, candidates, gates)
    monkeypatch.setattr(
        factory.answer_research, "build_contexts", lambda *a, **k: (tmp_path, tmp_path)
    )

    factory.create_project_from_session(session.id, "hulk-question", override=True)

    store = SessionStore(tmp_path / "research-sessions")
    audit = [
        json.loads(line)
        for line in (store.session_dir(session.id) / "audit.jsonl").read_text().splitlines()
    ]
    overridden = [event for event in audit if event["event"] == "gates_overridden"]
    assert len(overridden) == 1
    assert "a" in overridden[0]["detail"]["candidates"]
    assert "inconclusive" in json.dumps(overridden[0]["detail"]["candidates"]["a"])


def test_the_failure_message_names_the_candidate_its_issue_and_the_gates_reason(
    tmp_path, monkeypatch
):
    """`production gates failed: malformed_output` told a user nothing about which
    of five candidates to look at. Spec section 7's shape does."""
    _wire_roots(tmp_path, monkeypatch)
    candidates = [_candidate(chr(97 + i), index=i + 1) for i in range(3)]
    bad = _gate("b", verdict="inconclusive")
    bad["reason"] = "cited URLs not present in raw evidence"
    session = _session(tmp_path, ScoutMode.QA, candidates, [_gate("a"), bad, _gate("c")])

    with pytest.raises(ValueError) as caught:
        factory.create_project_from_session(session.id, "hulk-question")

    message = str(caught.value)
    assert "b (Thor #2 (2024))" in message
    assert "verdict inconclusive" in message
    assert "cited URLs not present in raw evidence" in message
    # The two healthy candidates have nothing to say and must not be listed.
    assert "\na (" not in message and "\nc (" not in message


def test_a_legacy_bare_gate_object_still_works_for_micro(tmp_path, monkeypatch):
    """Micro only ever selects one candidate, so the pre-collection artifact shape
    is unambiguous for it and sessions written before the collapse must still be
    able to finish rather than be stranded one click from a project."""
    _wire_roots(tmp_path, monkeypatch)
    candidate = _candidate("micro", mode=ScoutMode.MICRO)
    session = _session(tmp_path, ScoutMode.MICRO, [candidate], [_gate("micro")])
    store = SessionStore(tmp_path / "research-sessions")
    store.write_artifact(
        session.id,
        "specific/evidence_gate.v1.json",
        {"verdict": "confirmed", "reason": "Backed.", "evidence_urls": [],
         "reader_url": None, "flags": []},
    )

    assert factory.create_project_from_session(session.id, "thor-hammer") == "thor-hammer"


# ─── scout_candidate: what the writer is handed ─────────────────────────────
#
# A real micro session produced a script that ended on a teaser. The scout had
# built the moment from a review that withheld its twist, its own verification
# had said "NOT CONFIRMED ... withholds the exact twist", and the gate had only
# been INCONCLUSIVE because "OpenRouter request failed" — and the factory's
# whitelist dropped all of it, so the writer never saw any of it.

_NINE_KEYS = {
    "character", "series_issue_year", "what_visibly_happens", "summary",
    "claim_citation", "verbatim_sentence", "source_url", "evidence_urls", "verdict",
}
_RESEARCH_FIELDS = ("aftermath", "context_behind", "unrevealed", "detail_citations")


def _micro_candidate(candidate_id="micro", **extra):
    return {
        "id": candidate_id,
        "title": "The duel in the vault",
        "summary": "Hero and Rival duel over a blade.",
        "character_or_thing": "Hero",
        "series_issue_year": "Vault Duel (2025) #3 (2026)",
        "what_visibly_happens": "Hero swings the blade at Rival.",
        "evidence_urls": ["https://aiptcomics.com/vault-duel-3"],
        "claim_citation": {
            "url": "https://aiptcomics.com/vault-duel-3", "quote": "Hero swings the blade.",
        },
        **extra,
    }


def _verify_round(verdict, notes):
    """The record verify_selected stores: the raw You.com call around its payload."""
    return {
        "api": "research",
        "error": None,
        "payload": {
            "output": {
                "content": {
                    "candidates": [{"verdict": verdict, "verbatim_sentence": "Hero swings."}],
                    "notes": notes,
                },
                "content_type": "application/json",
                "sources": [],
            },
            "warnings": [],
        },
    }


def _micro_session(tmp_path, candidate, gate, *, verify=None, created_project=None,
                   intent="Find a new micro moment"):
    """A session on disk with the same layout the workflow writes: session.json,
    general/candidates.v1.json, specific/evidence_gate.v1.json and, once the
    candidate was verified, specific/search.<id>.v1.json."""
    from stages.research_scout.workflow import specific_search_artifact

    session = _session(tmp_path, ScoutMode.MICRO, [candidate], [gate], intent=intent)
    store = SessionStore(tmp_path / "research-sessions")
    if verify is not None:
        store.write_artifact(session.id, specific_search_artifact(candidate["id"]), verify)
    if created_project:
        session.created_project = created_project
        store.save(session)
    return session


def _created_scout_candidate(tmp_path, monkeypatch, candidate, gate, *, verify=None, override=True):
    _wire_roots(tmp_path, monkeypatch)
    session = _micro_session(tmp_path, candidate, gate, verify=verify)
    slug = factory.create_project_from_session(session.id, "vault-duel", override=override)
    project = tmp_path / "projects" / slug
    context = json.loads((project / "comic_context.json").read_text(encoding="utf-8"))
    return session, context["scout_candidate"], json.loads(
        (project / "scout_candidate.json").read_text(encoding="utf-8")
    )


def test_scout_candidate_carries_the_gate_reason_the_scouts_own_check_and_the_aftermath(
    tmp_path, monkeypatch
):
    candidate = _micro_candidate(
        turning_point="Hero swings the blade at Rival.",
        why_it_lands="The blade was meant for Hero.",
        aftermath="Hero loses the duel and keeps the blade.",
        context_behind="Hero took the blade from the vault to settle a debt.",
        unrevealed="The review never says what the twist is.",
        detail_citations=[{
            "supports": "aftermath", "url": "https://cbr.com/duel", "quote": "Hero loses.",
        }],
    )
    gate = {**_gate("micro", verdict="inconclusive"), "reason": "OpenRouter request failed",
            "evidence_urls": []}
    verify = _verify_round(
        "NOT CONFIRMED as the exact proposed micro-moment",
        "the review explicitly withholds the exact twist",
    )

    _session_obj, in_context, on_disk = _created_scout_candidate(
        tmp_path, monkeypatch, candidate, gate, verify=verify,
    )

    assert in_context == on_disk
    # the nine keys the writer already read, unchanged
    assert on_disk["character"] == "Hero"
    assert on_disk["series_issue_year"] == "Vault Duel (2025) #3 (2026)"
    assert on_disk["what_visibly_happens"] == "Hero swings the blade at Rival."
    assert on_disk["summary"] == "Hero and Rival duel over a blade."
    assert on_disk["claim_citation"] == candidate["claim_citation"]
    assert on_disk["verbatim_sentence"] == "Hero swings the blade."
    assert on_disk["source_url"] == "https://aiptcomics.com/vault-duel-3"
    assert on_disk["evidence_urls"] == ["https://aiptcomics.com/vault-duel-3"]
    assert on_disk["verdict"] == "INCONCLUSIVE"
    # what the whitelist used to drop
    assert on_disk["reason"] == "OpenRouter request failed"
    assert on_disk["scout_check"] == (
        "NOT CONFIRMED as the exact proposed micro-moment — "
        "the review explicitly withholds the exact twist"
    )
    assert on_disk["turning_point"] == "Hero swings the blade at Rival."
    assert on_disk["why_it_lands"] == "The blade was meant for Hero."
    assert on_disk["aftermath"] == "Hero loses the duel and keeps the blade."
    assert on_disk["context_behind"] == "Hero took the blade from the vault to settle a debt."
    assert on_disk["unrevealed"] == "The review never says what the twist is."
    assert on_disk["detail_citations"] == candidate["detail_citations"]


def test_what_never_existed_stays_absent_rather_than_becoming_a_blank(tmp_path, monkeypatch):
    """An older candidate has no aftermath, no turning point, no verify round and a
    gate without a reason. None of those may appear as an empty placeholder."""
    gate = _gate("micro")
    del gate["reason"]

    _s, _in_context, on_disk = _created_scout_candidate(
        tmp_path, monkeypatch, _micro_candidate(), gate, override=False,
    )

    assert set(on_disk) == _NINE_KEYS


def test_the_research_fields_may_legitimately_be_blank_when_the_scout_found_nothing(
    tmp_path, monkeypatch
):
    candidate = _micro_candidate(
        aftermath="", context_behind="", unrevealed="", detail_citations=[],
    )

    _s, _in_context, on_disk = _created_scout_candidate(
        tmp_path, monkeypatch, candidate, _gate("micro"), override=False,
    )

    assert (on_disk["aftermath"], on_disk["context_behind"], on_disk["unrevealed"]) == ("", "", "")
    assert on_disk["detail_citations"] == []


def test_a_detail_citation_without_a_url_or_a_quote_is_not_passed_on(tmp_path, monkeypatch):
    candidate = _micro_candidate(
        aftermath="Hero loses.",
        detail_citations=[
            {"supports": "aftermath", "url": "https://cbr.com/duel", "quote": "Hero loses."},
            {"supports": "aftermath", "url": "https://cbr.com/other", "quote": ""},
            {"supports": "context_behind", "url": "", "quote": "A quote with no page."},
            "not an object",
        ],
    )

    _s, _in_context, on_disk = _created_scout_candidate(
        tmp_path, monkeypatch, candidate, _gate("micro"), override=False,
    )

    assert on_disk["detail_citations"] == [
        {"supports": "aftermath", "url": "https://cbr.com/duel", "quote": "Hero loses."},
    ]


def test_a_failed_verify_round_leaves_no_scout_check(tmp_path, monkeypatch):
    failed = {"api": "research", "payload": {}, "error": "HTTP 500"}

    _s, _in_context, on_disk = _created_scout_candidate(
        tmp_path, monkeypatch, _micro_candidate(), _gate("micro"), verify=failed, override=False,
    )

    assert "scout_check" not in on_disk


def test_build_scout_candidate_reads_but_never_writes(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)
    candidate = _micro_candidate(aftermath="Hero loses.")
    session = _micro_session(
        tmp_path, candidate, _gate("micro"), verify=_verify_round("CONFIRMED", "Two sources agree."),
    )
    store = SessionStore(tmp_path / "research-sessions")
    before = sorted(p.name for p in store.session_dir(session.id).rglob("*"))

    built = factory.build_scout_candidate(store, session, candidate, _gate("micro"))

    assert built["scout_check"] == "CONFIRMED — Two sources agree."
    assert built["aftermath"] == "Hero loses."
    assert sorted(p.name for p in store.session_dir(session.id).rglob("*")) == before
    assert not (tmp_path / "projects").exists()


# ─── refresh_scout_candidate: bring an existing project up to date, offline ──


def _old_project(tmp_path, *, slug="vault-duel", extra_context=None):
    """A project made before this change: the nine-key scout_candidate and nothing more."""
    project = tmp_path / "projects" / slug
    project.mkdir(parents=True)
    old = {
        "character": "Hero", "series_issue_year": "Vault Duel (2025) #3 (2026)",
        "what_visibly_happens": "Hero swings the blade at Rival.",
        "summary": "Hero and Rival duel over a blade.",
        "claim_citation": {"url": "https://aiptcomics.com/vault-duel-3",
                           "quote": "Hero swings the blade."},
        "verbatim_sentence": "Hero swings the blade.",
        "source_url": "https://aiptcomics.com/vault-duel-3",
        "evidence_urls": ["https://aiptcomics.com/vault-duel-3"],
        "verdict": "INCONCLUSIVE",
    }
    context = {"status": "ready", "pipeline_mode": "micro_moment", "title": "The duel in the vault",
               "target_moment": "Hero swings the blade at Rival.", "narration_notes": "keep me",
               "scout_candidate": old, **(extra_context or {})}
    (project / "comic_context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
    (project / "scout_candidate.json").write_text(json.dumps(old, indent=2), encoding="utf-8")
    return project, old


def _stored_session(tmp_path, candidate, gate, verify, *, slug="vault-duel", **kwargs):
    return _micro_session(tmp_path, candidate, gate, verify=verify, created_project=slug, **kwargs)


def _refresh(tmp_path, slug="vault-duel"):
    return factory.refresh_scout_candidate(
        slug, projects_root=tmp_path / "projects", sessions_root=tmp_path / "research-sessions",
    )


@pytest.fixture
def no_network(monkeypatch):
    """Refreshing is disk-only. Any socket a refresh opened would be a paid call."""
    import socket
    import urllib.request

    def boom(*args, **kwargs):
        raise AssertionError("refresh_scout_candidate must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


def test_refresh_adds_the_gate_reason_and_the_scouts_check_to_a_project_made_before(
    tmp_path, no_network
):
    project, _old = _old_project(tmp_path)
    gate = {**_gate("micro", verdict="inconclusive"), "reason": "OpenRouter request failed",
            "evidence_urls": []}
    _stored_session(
        tmp_path, _micro_candidate(), gate,
        _verify_round("NOT CONFIRMED as the exact proposed micro-moment",
                      "the review explicitly withholds the exact twist"),
    )

    refreshed = _refresh(tmp_path)

    assert refreshed["reason"] == "OpenRouter request failed"
    assert refreshed["scout_check"] == (
        "NOT CONFIRMED as the exact proposed micro-moment — "
        "the review explicitly withholds the exact twist"
    )
    # the candidate predates the aftermath fields: they stay absent, not blank
    assert not set(_RESEARCH_FIELDS) & set(refreshed)
    assert _NINE_KEYS <= set(refreshed)
    assert refreshed["verdict"] == "INCONCLUSIVE"
    on_disk = json.loads((project / "scout_candidate.json").read_text(encoding="utf-8"))
    context = json.loads((project / "comic_context.json").read_text(encoding="utf-8"))
    assert on_disk == refreshed == context["scout_candidate"]


def test_refresh_replaces_only_the_scout_candidate_in_the_context(tmp_path, no_network):
    project, _old = _old_project(tmp_path, extra_context={"year": "2026", "characters": ["Hero"]})
    _stored_session(tmp_path, _micro_candidate(), _gate("micro"), _verify_round("CONFIRMED", ""))
    before = json.loads((project / "comic_context.json").read_text(encoding="utf-8"))

    _refresh(tmp_path)

    after = json.loads((project / "comic_context.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "scout_candidate"} == {
        k: v for k, v in before.items() if k != "scout_candidate"
    }
    assert after["scout_candidate"] != before["scout_candidate"]


def test_refresh_picks_up_the_aftermath_fields_a_newer_candidate_carries(tmp_path, no_network):
    _old_project(tmp_path)
    candidate = _micro_candidate(
        aftermath="Hero loses.", context_behind="", unrevealed="The twist.",
        detail_citations=[{"supports": "aftermath", "url": "https://cbr.com/d", "quote": "Hero loses."}],
    )
    _stored_session(tmp_path, candidate, _gate("micro"), None)

    refreshed = _refresh(tmp_path)

    assert refreshed["aftermath"] == "Hero loses."
    assert refreshed["context_behind"] == ""
    assert refreshed["unrevealed"] == "The twist."
    assert refreshed["detail_citations"][0]["url"] == "https://cbr.com/d"
    assert "scout_check" not in refreshed  # no verify round was ever stored


def test_refresh_is_idempotent_and_writes_atomically(tmp_path, monkeypatch, no_network):
    from pathlib import Path

    project, _old = _old_project(tmp_path)
    _stored_session(tmp_path, _micro_candidate(), _gate("micro"), _verify_round("CONFIRMED", "ok"))
    written = []
    real = factory.write_json_atomic

    def recording(path, doc, **kwargs):
        written.append(Path(path).name)
        return real(path, doc, **kwargs)

    monkeypatch.setattr(factory, "write_json_atomic", recording)

    first = _refresh(tmp_path)
    second = _refresh(tmp_path)

    assert first == second
    # scout_candidate.json first: Stage 3 reads it before the context copy, so a
    # crash between the two must leave the fresh one in front.
    assert written == ["scout_candidate.json", "comic_context.json"] * 2
    assert not list(project.glob("*.tmp"))


@pytest.mark.parametrize("setup,message", [
    ("no_project", "project"),
    ("no_context", "comic_context.json"),
    ("no_session", "research session"),
    ("two_sessions", "more than one"),
    ("qa_session", "micro"),
    ("no_gate", "gate"),
    ("no_candidate", "candidate"),
])
def test_refresh_refuses_rather_than_guesses(tmp_path, no_network, setup, message):
    from stages.research_scout.errors import ScoutUserError

    if setup != "no_project":
        project, _old = _old_project(tmp_path)
    if setup == "no_context":
        (project / "comic_context.json").unlink()
    if setup in {"two_sessions", "qa_session", "no_gate", "no_candidate"}:
        session = _stored_session(tmp_path, _micro_candidate(), _gate("micro"), None)
        store = SessionStore(tmp_path / "research-sessions")
        if setup == "two_sessions":
            other = session.model_copy(update={"id": "another-session"})
            store.save(other)
        elif setup == "qa_session":
            session.mode = ScoutMode.QA
            store.save(session)
        elif setup == "no_gate":
            store.artifact_path(session.id, "specific/evidence_gate.v1.json").unlink()
        elif setup == "no_candidate":
            store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": []})

    with pytest.raises(ScoutUserError, match=message):
        _refresh(tmp_path)


@pytest.mark.parametrize("name", ["", "  ", ".", "..", "a/b", "../escape", "/abs/path", None])
def test_refresh_only_accepts_a_single_folder_name(tmp_path, no_network, name):
    from stages.research_scout.errors import ScoutUserError

    _old_project(tmp_path)

    with pytest.raises(ScoutUserError, match="single folder name"):
        factory.refresh_scout_candidate(
            name, projects_root=tmp_path / "projects", sessions_root=tmp_path / "research-sessions",
        )


def test_refresh_ignores_a_session_that_belongs_to_another_project(tmp_path, no_network):
    from stages.research_scout.errors import ScoutUserError

    _old_project(tmp_path)
    _stored_session(tmp_path, _micro_candidate(), _gate("micro"), None, slug="some-other-project")

    with pytest.raises(ScoutUserError, match="research session"):
        _refresh(tmp_path)


def test_refresh_defaults_to_the_configured_roots(tmp_path, monkeypatch, no_network):
    monkeypatch.setattr(factory.config, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(factory.config, "RESEARCH_SESSIONS_ROOT", tmp_path / "research-sessions")
    _old_project(tmp_path)
    _stored_session(tmp_path, _micro_candidate(), _gate("micro"), _verify_round("CONFIRMED", "ok"))

    assert factory.refresh_scout_candidate("vault-duel")["scout_check"] == "CONFIRMED — ok"
