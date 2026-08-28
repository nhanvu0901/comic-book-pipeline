"""ui/bridge.py::start_scout_session — the empty-intent two-tier fallback.

Before this, an empty research intent raised ValueError two layers up (here AND
in the UI's own guard in ui/screens/s1_research_scout.py) — Master 2026-08-22
wanted Stage 1 to do something useful instead. Tier A (a still-open
qa_question_bank.md question, QA only, zero API cost) is preferred; Tier B
(ScoutWorkflow.discover_question — spend one research call turning the next
rotated angle into a real question, see tests/test_research_scout_discover_
question.py for that method's own coverage) is the last resort when Tier A has
nothing usable. A NON-empty intent must go through completely unchanged — the
fallback only ever activates on a blank/whitespace-only box.
"""
import pytest

import ui.bridge as bridge
from stages.research_scout.models import ScoutMode
from stages.research_scout.policies import PolicyBundle


class _NetworkTripwireYouCom:
    """Stands in for the real YouComClient inside every test in this file.

    bridge._scout_workflow() builds its ScoutWorkflow with no client override, so
    without this stub it would construct a real stages.research_scout.workflow.
    YouComClient() — and config.load_dotenv() means YDC_API_KEY can be a LIVE key
    in this process. Tier B (discover_question) now spends one client.research()
    call, so an unstubbed run here would silently hit the network. Raising forces
    discover_question's mandatory fallback path, which returns the angle itself —
    exactly the value every assertion below already expects."""

    def research(self, *args, **kwargs):
        raise AssertionError("test tried to reach the real You.com client")


@pytest.fixture(autouse=True)
def _stub_youcom_client(monkeypatch):
    monkeypatch.setattr(
        "stages.research_scout.workflow.YouComClient", lambda *a, **k: _NetworkTripwireYouCom()
    )


def _use_tmp_store(tmp_path, monkeypatch):
    root = tmp_path / "research_sessions"
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", root)
    return root


def test_nonempty_intent_is_used_verbatim_and_never_touches_the_fallback(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    session = bridge.start_scout_session("qa", "  Who has beaten Superman?  ")
    # .strip() is the only transformation a real intent ever got, before or after
    # this change — the fallback must not add, reorder, or otherwise touch it.
    assert session.user_intent == "Who has beaten Superman?"


def test_empty_intent_uses_the_top_open_bank_question_for_qa(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    bank = tmp_path / "qa_question_bank.md"
    banlist = tmp_path / "qa_question_banlist.md"
    bank.write_text(
        "| Status | Question | Answer items (comic, year) | Notes |\n"
        "|--------|----------|----------------------------|-------|\n"
        "| SAVE-FOR-LATER | A still-open bank question? | item | note |\n",
        encoding="utf-8",
    )
    banlist.write_text(
        "| Date | Question | Reason |\n|------|----------|--------|\n", encoding="utf-8",
    )
    monkeypatch.setattr("stages.research_scout.bank_fallback._REPO_ROOT", tmp_path)

    session = bridge.start_scout_session("qa", "")
    assert session.user_intent == "A still-open bank question?"


def test_empty_intent_falls_back_to_next_angle_when_bank_has_nothing_open(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    # No qa_question_bank.md at all under this fake repo root -> Tier A finds nothing.
    monkeypatch.setattr("stages.research_scout.bank_fallback._REPO_ROOT", tmp_path)
    angles = PolicyBundle.load(ScoutMode.QA).general_angles["qa"]

    session = bridge.start_scout_session("qa", "")
    assert session.user_intent == angles[0]


def test_empty_intent_for_micro_skips_the_bank_entirely(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    # Real repo root deliberately left in place: qa_question_bank.md DOES exist
    # there, but MICRO must never read it (no bank file exists for that mode).
    angles = PolicyBundle.load(ScoutMode.MICRO).general_angles["micro"]

    session = bridge.start_scout_session("micro", "   ")
    assert session.user_intent == angles[0]


def test_second_empty_session_for_the_same_mode_advances_past_the_first_angle(
    tmp_path, monkeypatch,
):
    _use_tmp_store(tmp_path, monkeypatch)
    monkeypatch.setattr("stages.research_scout.bank_fallback._REPO_ROOT", tmp_path)
    angles = PolicyBundle.load(ScoutMode.MICRO).general_angles["micro"]

    first = bridge.start_scout_session("micro", "")
    second = bridge.start_scout_session("micro", "")
    assert first.user_intent == angles[0]
    assert second.user_intent == angles[1]


def test_start_scout_session_no_longer_accepts_skip_bank(tmp_path, monkeypatch):
    """The Stage 1 UI now discovers Tier B questions via bridge.discover_intent()
    and lands them in the intent box for human review BEFORE any session exists
    (Master 2026-08-28 — see ui/screens/s1_research_scout.py::_send_click and
    bridge.discover_intent's docstring for why). That was the only caller of
    skip_bank, so the parameter is gone; start_scout_session("qa", "") must keep
    doing bank-then-discover for any programmatic (non-UI) caller, unchanged
    (see the Tier A/Tier B tests above), and must reject an unknown kwarg rather
    than silently accepting it again."""
    _use_tmp_store(tmp_path, monkeypatch)
    with pytest.raises(TypeError):
        bridge.start_scout_session("qa", "", skip_bank=True)

