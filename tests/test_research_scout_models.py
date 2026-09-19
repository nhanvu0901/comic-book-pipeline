from stages.research_scout.models import ScoutMode, SessionState
from stages.research_scout.storage import SessionStore


def test_new_session_starts_in_general_draft(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(mode=ScoutMode.MICRO, user_intent="new Hulk moment")
    assert session.state is SessionState.GENERAL_DRAFT
    assert store.load(session.id).model_dump() == session.model_dump()


def test_legacy_review_states_load_as_candidate_review(tmp_path):
    """Sessions written before the two review steps collapsed are still on disk
    (research_sessions/*/session.json carries "general_review"). Both legacy
    review states are the one review step now, so they must load rather than
    fail validation and strand the session."""
    store = SessionStore(tmp_path)
    for legacy in ("general_review", "specific_review"):
        (tmp_path / legacy).mkdir()
        (tmp_path / legacy / "session.json").write_text(
            '{"id": "%s", "mode": "qa", "user_intent": "x", "state": "%s",'
            ' "selected_general_candidate_id": null}' % (legacy, legacy),
            encoding="utf-8",
        )
        assert store.load(legacy).state is SessionState.CANDIDATE_REVIEW


def test_the_general_candidate_pick_is_gone_from_the_session(tmp_path):
    """One selection replaced two, so the radio's field has no meaning left.
    A legacy file still carrying it must load with the field simply dropped."""
    from stages.research_scout.models import ResearchSession

    assert not hasattr(ResearchSession(id="x", mode=ScoutMode.QA, user_intent="y"),
                       "selected_general_candidate_id")
