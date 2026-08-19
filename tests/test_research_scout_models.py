from stages.research_scout.models import ScoutMode, SessionState
from stages.research_scout.storage import SessionStore


def test_new_session_starts_in_general_draft(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(mode=ScoutMode.MICRO, user_intent="new Hulk moment")
    assert session.state is SessionState.GENERAL_DRAFT
    assert store.load(session.id).model_dump() == session.model_dump()
