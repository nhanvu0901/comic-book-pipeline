"""Re-scouting a round while keeping the candidates already confirmed.

Two things are tested here, and they are the same defect seen from two sides.
Candidate ids used to be pure list positions (`candidate-{index}`), so a gate
written for `candidate-1` in round 1 was displayed against whatever landed at
position 1 in round 2 — a green verdict on the wrong comic, with no error. That
made a "keep the confirmed one, replace the rest" button unbuildable AND
corrupted the plain feedback re-run that already shipped.
"""

import json

import pytest

from stages.research_scout import cited_sources
from stages.research_scout.models import EvidenceGate, ScoutMode, SessionState
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow


class _FakeYouCom:
    """A You.com stand-in whose candidates carry NO id of their own.

    That is the real shape: `_GENERAL_ITEM_PROPS` has no `id` property, so the
    model is never asked for one and every id on disk is one the workflow
    assigned. Tests that hand the parser pre-identified candidates cannot see
    the positional-id defect at all.
    """

    def __init__(self, rounds=None):
        self.rounds = [list(titles) for titles in rounds] if rounds else None
        self.research_calls = 0
        self.search_response = {"results": {"web": [{"url": "https://example.test/a"}]}}

    def _titles(self):
        if self.rounds:
            return self.rounds.pop(0)
        return ["Alpha", "Beta", "Gamma"]

    def research(self, prompt, schema, profile, *, effort="standard"):
        self.seen_prompt = prompt
        self.research_calls += 1
        candidates = []
        for title in self._titles():
            url = f"https://example.test/{title.casefold().replace(' ', '-')}"
            candidates.append({
                "title": title,
                "summary": f"{title} visibly happens.",
                "series_issue_year": f"{title} #1 (2001)",
                "what_visibly_happens": f"{title} lands a punch.",
                "evidence_urls": [url],
                "claim_citation": {
                    "url": url,
                    "quote": f"{title} source sentence.",
                },
                # Tier B reads this field; the general round ignores it. One fake
                # serves both callers of _extract_candidates.
                "question": f"What did {title} do?",
            })
        payload = {
            "output": {
                "content": {"candidates": candidates},
                "sources": [{"url": candidate["claim_citation"]["url"]}
                            for candidate in candidates],
            }
        }
        return type("RawCall", (), {"api": "research", "payload": payload, "error": None})()

    def search(self, query, profile):
        return type(
            "RawCall", (), {"api": "search", "payload": self.search_response, "error": None}
        )()

@pytest.fixture(autouse=True)
def _no_reader_network(monkeypatch):
    """Return the exact bound quote without opening a socket.

    These are fresh general-research candidates, so a positive gate must pass
    the same quote-presence check production applies before it calls the model.
    """
    def _fetch(url, **kwargs):
        title = url.rsplit("/", 1)[-1].replace("-", " ").title()
        return cited_sources.FetchedSource(url=url, text=f"{title} source sentence.")

    monkeypatch.setattr(
        cited_sources,
        "fetch_source",
        _fetch,
    )
    yield

@pytest.fixture
def workflow(tmp_path):
    # config.py load_dotenv()s real API keys, so an uninjected planner would hit
    # OpenRouter for real — the stub keeps every test on the fallback path.
    return ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom(),
        planner=lambda *a, **k: None,
    )


def _candidate_ids(workflow, session_id, artifact="general/candidates.v1.json"):
    path = workflow.store.artifact_path(session_id, artifact)
    data = json.loads(path.read_text(encoding="utf-8"))
    return [candidate["id"] for candidate in data["candidates"]]


# ─── 1. Candidate identity is namespaced by revision ────────────────────────

def test_round_two_ids_never_collide_with_round_one(workflow):
    """`candidate-1` used to mean nothing more than "first in this round's
    list", so round 2 re-used every id round 1 had issued."""
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    round_one = _candidate_ids(workflow, session.id)

    workflow.rerun_general(session.id)
    workflow.run_general(session.id)
    round_two = _candidate_ids(workflow, session.id)

    assert round_one == ["candidate-1", "candidate-2", "candidate-3"]
    assert round_two == ["r2-candidate-1", "r2-candidate-2", "r2-candidate-3"]
    assert not set(round_one) & set(round_two)


def test_the_tier_b_discover_batch_stays_out_of_the_revision_namespace(workflow):
    """`_extract_candidates` parses the Tier B discover batch as well.

    The revision namespace belongs to the general-research round: discovery
    has no session and no revision at all, and its entries are keyed by their
    question/moment text, so threading a prefix through every caller would
    rename ids for a path that gains nothing from it.
    """
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"]])
    batch = workflow.discover_questions(ScoutMode.QA, count=2)

    assert [entry["id"] for entry in batch] == ["candidate-1", "candidate-2"]


# ─── 5. The live defect: a plain re-run must let go of the old round ────────

def _gates_on_disk(workflow, session_id):
    path = workflow.store.artifact_path(session_id, "specific/evidence_gate.v1.json")
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["gates"]


def test_a_plain_rerun_drops_the_selection_and_the_gates(monkeypatch, workflow):
    """`rerun_general` bumped the revision and returned to GENERAL_DRAFT while
    leaving `selected_specific_candidate_ids` and `specific/evidence_gate.v1.json`
    untouched, so a confirmed gate from round 1 reattached itself to round 2.

    Namespaced ids alone would only downgrade that from a wrong reference to a
    dangling one. Letting go is what makes it correct: a plain re-run keeps
    nothing, which is precisely what distinguishes it from the new action.
    """
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="confirmed", reason="Backed."),
    )
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-1", "candidate-2", "candidate-3"])
    assert _gates_on_disk(workflow, session.id)

    reran = workflow.rerun_general(session.id, "different villains please")

    assert reran.selected_specific_candidate_ids == []
    assert _gates_on_disk(workflow, session.id) == []


# ─── 3. run_general honours the carry-over ──────────────────────────────────

def _hand_off_to_a_new_round(workflow, session_id, kept_ids):
    """Put a session where `rescout_keeping_confirmed` would leave it, without
    depending on that action — this is run_general's half of the contract."""
    session = workflow.store.load(session_id)
    session.kept_candidate_ids = list(kept_ids)
    session.selected_specific_candidate_ids = list(kept_ids)
    session.revision += 1
    session.state = SessionState.GENERAL_DRAFT
    return workflow.store.save(session)


def test_a_carried_over_candidate_keeps_its_id_and_leads_the_new_round(workflow):
    """Rewriting the kept candidate's id into the new namespace would orphan the
    gate that was already paid for, so it keeps the id its gate is keyed by and
    is simply prepended to the round that replaces the rest."""
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"], ["Delta", "Echo"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    _hand_off_to_a_new_round(workflow, session.id, ["candidate-1"])

    workflow.run_general(session.id)

    path = workflow.store.artifact_path(session.id, "general/candidates.v1.json")
    candidates = json.loads(path.read_text(encoding="utf-8"))["candidates"]
    assert [c["id"] for c in candidates] == [
        "candidate-1", "r2-candidate-1", "r2-candidate-2",
    ]
    # The kept entry is the round-1 candidate itself, not a same-id stand-in.
    assert candidates[0]["title"] == "Alpha"
    assert candidates[1]["title"] == "Delta"


def test_the_carry_over_is_spent_once_and_not_again_next_round(workflow):
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"], ["Delta"], ["Golf"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    _hand_off_to_a_new_round(workflow, session.id, ["candidate-1"])
    after = workflow.run_general(session.id)

    assert after.kept_candidate_ids == []

    workflow.rerun_general(session.id)
    workflow.run_general(session.id)

    assert _candidate_ids(workflow, session.id) == ["r3-candidate-1"]


def test_the_revision_archive_records_the_merged_list_the_user_saw(workflow):
    """`candidates.rev{N}.v1.json` is what the chat replays for a superseded
    round, so it has to hold the list that was actually on screen — the kept
    candidate included, not just the fresh half."""
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"], ["Delta"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    _hand_off_to_a_new_round(workflow, session.id, ["candidate-2"])
    workflow.run_general(session.id)

    assert _candidate_ids(
        workflow, session.id, "general/candidates.rev2.v1.json"
    ) == ["candidate-2", "r2-candidate-1"]


def test_a_carry_over_id_that_no_longer_exists_is_simply_dropped(workflow):
    """A hand-edited or truncated artifact must not take the round down with it."""
    workflow.client = _FakeYouCom(rounds=[["Alpha"], ["Delta"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    _hand_off_to_a_new_round(workflow, session.id, ["candidate-1", "ghost-99"])

    workflow.run_general(session.id)

    assert _candidate_ids(workflow, session.id) == ["candidate-1", "r2-candidate-1"]


# ─── 2. rescout_keeping_confirmed ───────────────────────────────────────────

def _mixed_verdicts(**kwargs):
    """One confirmed, one inconclusive, one rejected — the mix a verification
    round most often lands on, and the whole reason this action exists."""
    return EvidenceGate(
        verdict={
            "candidate-1": "confirmed",
            "candidate-2": "inconclusive",
            "candidate-3": "rejected",
        }[kwargs["candidate"]["id"]],
        reason="Checked.",
    )


def _verified_round_one(workflow, monkeypatch, rounds=None):
    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _mixed_verdicts)
    workflow.client = _FakeYouCom(rounds=rounds or [["Alpha", "Beta", "Gamma"], ["Delta", "Echo"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-1", "candidate-2", "candidate-3"])
    return session


def test_rescout_holds_the_confirmed_ids_and_prunes_every_other_gate(monkeypatch, workflow):
    session = _verified_round_one(workflow, monkeypatch)

    after = workflow.rescout_keeping_confirmed(session.id)

    assert after.kept_candidate_ids == ["candidate-1"]
    assert after.selected_specific_candidate_ids == ["candidate-1"]
    assert after.revision == 2
    assert after.state is SessionState.GENERAL_DRAFT
    gates = _gates_on_disk(workflow, session.id)
    assert [gate["candidate_id"] for gate in gates] == ["candidate-1"]
    assert gates[0]["verdict"] == "confirmed"


def test_rescout_refuses_when_nothing_was_confirmed(monkeypatch, workflow):
    """There is nothing to keep, and the plain feedback re-run already covers
    starting the round over — so this must say so rather than quietly becoming
    that re-run."""
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="inconclusive", reason="Thin."),
    )
    workflow.client = _FakeYouCom(rounds=[["Alpha", "Beta"]])
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    workflow.verify_selected(session.id, ["candidate-1", "candidate-2"])

    with pytest.raises(ValueError, match="confirmed"):
        workflow.rescout_keeping_confirmed(session.id)

    unchanged = workflow.store.load(session.id)
    assert unchanged.revision == 1
    assert unchanged.state is SessionState.CANDIDATE_REVIEW
    assert [g["candidate_id"] for g in _gates_on_disk(workflow, session.id)] == [
        "candidate-1", "candidate-2",
    ]


def test_rescout_is_not_allowed_before_there_is_anything_to_review(workflow):
    from stages.research_scout.workflow import InvalidTransition

    session = workflow.start(ScoutMode.QA, "Hulk questions")

    with pytest.raises(InvalidTransition, match="CANDIDATE_REVIEW"):
        workflow.rescout_keeping_confirmed(session.id)


def test_rescout_tells_the_next_prompt_what_is_held_and_what_was_turned_down(
    monkeypatch, workflow,
):
    """No prompt change: the exclusions travel as a feedback note, which
    `_intent_with_feedback` folds into the fallback prompt and the planner reads
    on the planner path."""
    session = _verified_round_one(workflow, monkeypatch)

    after = workflow.rescout_keeping_confirmed(session.id)

    note = after.feedback_log[-1].text
    assert "Alpha" in note
    assert "Beta" in note and "Gamma" in note

    workflow.run_general(session.id)
    assert "Alpha" in workflow.client.seen_prompt
    assert "Gamma" in workflow.client.seen_prompt


def test_a_kept_candidate_is_never_gated_a_second_time(monkeypatch, workflow):
    """The tripwire: the point of the whole feature is that the research already
    paid for on the confirmed candidate is not bought again. Re-verifying it on
    purpose stays possible — that is what the per-card `only` path is for."""
    session = _verified_round_one(workflow, monkeypatch)
    workflow.rescout_keeping_confirmed(session.id)
    workflow.run_general(session.id)

    def _tripwire(**kwargs):
        if kwargs["candidate"]["id"] == "candidate-1":
            raise AssertionError("the kept candidate was sent to the model a second time")
        return EvidenceGate(verdict="confirmed", reason="Backed.")

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _tripwire)
    after = workflow.verify_selected(
        session.id, ["candidate-1", "r2-candidate-1", "r2-candidate-2"]
    )

    assert after.selected_specific_candidate_ids == [
        "candidate-1", "r2-candidate-1", "r2-candidate-2",
    ]
    gates = {g["candidate_id"]: g for g in _gates_on_disk(workflow, session.id)}
    assert set(gates) == {"candidate-1", "r2-candidate-1", "r2-candidate-2"}
    # The kept gate is the one paid for in round 1, carried over untouched.
    assert gates["candidate-1"]["verdict"] == "confirmed"


def test_re_verifying_a_kept_candidate_on_purpose_still_reaches_the_model(
    monkeypatch, workflow,
):
    """Skipping an already-gated candidate must not take away the ability to
    ask for a fresh verdict on it — that is the per-card Re-verify button."""
    session = _verified_round_one(workflow, monkeypatch)
    workflow.rescout_keeping_confirmed(session.id)
    workflow.run_general(session.id)

    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="rejected", reason="Changed my mind."),
    )
    workflow.verify_selected(
        session.id, ["candidate-1", "r2-candidate-1"], only=["candidate-1"],
    )

    gates = {g["candidate_id"]: g for g in _gates_on_disk(workflow, session.id)}
    assert gates["candidate-1"]["verdict"] == "rejected"


# ─── Sessions written before any of this ────────────────────────────────────

def _sessions_on_disk():
    """The real research_sessions/ directory — sessions written long before
    revision-namespaced ids existed, carrying bare `candidate-N` ids and, in the
    oldest ones, no feedback_log or kept_candidate_ids key at all. Read-only."""
    import config

    root = config.RESEARCH_SESSIONS_ROOT
    if not root.is_dir():
        return []
    return [d for d in sorted(root.iterdir()) if (d / "session.json").is_file()]


def test_the_sessions_already_on_disk_still_load_and_their_ids_still_resolve():
    directories = _sessions_on_disk()
    if not directories:
        pytest.skip("no real research sessions on this machine")

    store = SessionStore(directories[0].parent)
    # A client is never reached: _candidates_by_id only walks the store.
    reader = ScoutWorkflow(store=store, client=object(), planner=lambda *a, **k: None)

    checked = 0
    for directory in directories:
        session = store.load(directory.name)
        # The new field is absent from every one of these files.
        assert session.kept_candidate_ids == []

        artifact = directory / "general" / "candidates.v1.json"
        if not artifact.is_file():
            continue
        on_disk = [
            candidate["id"]
            for candidate in json.loads(artifact.read_text(encoding="utf-8"))["candidates"]
        ]
        if not on_disk:
            continue
        assert on_disk[0] == "candidate-1", "these predate the revision namespace"
        # The lookup verify_selected and the gate machinery use has to keep
        # resolving them: a bare candidate-N is still a first-round id.
        resolved = reader._candidates_by_id(session)
        assert set(on_disk) <= set(resolved)
        checked += 1

    assert checked, "no candidate artifact was actually exercised"


def test_a_directly_seeded_legacy_candidate_keeps_its_original_gate_path(monkeypatch, workflow):
    """Old paid-for artifacts predate claim_citation and must remain reviewable.

    This seeds the old artifact shape directly rather than pretending a new
    general-research response may omit the new required binding.
    """
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    session.state = SessionState.CANDIDATE_REVIEW
    workflow.store.save(session)
    workflow.store.write_artifact(
        session.id,
        "general/candidates.v1.json",
        {"candidates": [{
            "id": "candidate-1",
            "title": "Legacy Alpha",
            "summary": "Legacy Alpha visibly happens.",
            "series_issue_year": "Legacy Alpha #1 (2001)",
            "what_visibly_happens": "Legacy Alpha lands a punch.",
            "evidence_urls": ["https://example.test/legacy-alpha"],
        }]},
    )
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="confirmed", reason="Legacy evidence."),
    )

    verified = workflow.verify_selected(session.id, ["candidate-1"])

    assert verified.selected_specific_candidate_ids == ["candidate-1"]
    assert _gates_on_disk(workflow, session.id)[0]["verdict"] == "confirmed"


def test_a_rerun_with_nothing_to_prune_writes_no_empty_gate_artifact(workflow):
    """An empty `gates` list on disk reads as "we gated and found nothing". A
    session that never verified anything has not, so it must not say so."""
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)

    workflow.rerun_general(session.id, "try again")

    assert not workflow.store.artifact_path(
        session.id, "specific/evidence_gate.v1.json"
    ).exists()


def test_rescout_keeping_selected_preserves_any_candidate_even_without_confirmed_gate(workflow):
    """A user can pin/keep ANY candidate they like into the next round, even if
    it is inconclusive or unverified, and it prepends to round 2."""
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)

    # Beta is candidate-2 in round 1; keep it without gating it
    session = workflow.rescout_keeping_selected(session.id, ["candidate-2"])
    assert session.kept_candidate_ids == ["candidate-2"]
    assert session.revision == 2

    round2 = workflow.run_general(session.id)
    ids2 = _candidate_ids(workflow, session.id)

    # Beta survived in front under its original id
    assert ids2[0] == "candidate-2"
    # Round 2's new candidates have the r2- prefix
    assert all(cid.startswith("r2-") for cid in ids2[1:])


def test_rescout_keeping_selected_rejects_empty_selection(workflow):
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)

    with pytest.raises(Exception, match="select at least one candidate"):
        workflow.rescout_keeping_selected(session.id, [])

