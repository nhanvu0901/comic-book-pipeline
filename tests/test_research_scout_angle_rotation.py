"""Tier B of the empty-intent fallback: angle rotation.

ScoutWorkflow._angle() used to always return angles[0] (Master 2026-08-22 bug
find) — 4 of the 5 angles per mode in research_policies/general_angles.v1.json
were dead code. The fix rotates deterministically by counting existing
sessions for that mode, so calls across SEPARATE sessions actually advance
instead of repeating angle[0] forever, and wrap back to angle[0] once every
angle has been used — no randomness, so a run stays reproducible.
"""
from stages.research_scout.models import ScoutMode
from stages.research_scout.policies import PolicyBundle
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow


def _workflow(tmp_path):
    # planner stub: keeps this test on the fallback prompt path with zero network
    # risk, same reasoning as tests/test_research_scout_workflow.py's mock_workflow.
    return ScoutWorkflow(store=SessionStore(tmp_path), planner=lambda *a, **k: None)


def test_next_angle_rotates_across_separate_sessions_and_wraps(tmp_path):
    workflow = _workflow(tmp_path)
    angles = PolicyBundle.load(ScoutMode.QA).general_angles["qa"]
    assert len(angles) == 5

    seen = []
    for _ in range(len(angles) + 2):  # one full cycle plus two, to observe the wrap
        seen.append(workflow.next_angle(ScoutMode.QA))
        workflow.start(ScoutMode.QA, "placeholder intent")

    assert seen == [angles[i % len(angles)] for i in range(len(angles) + 2)]
    assert seen[5] == seen[0] == angles[0]  # explicit wrap: 6th call repeats the 1st


def test_next_angle_is_the_same_pointer_the_fallback_prompt_actually_renders(tmp_path):
    """_angle() (used inside run_general's fallback prompt) and next_angle() (the
    public Tier B entry point ui/bridge.py calls) must share one rotation sequence —
    otherwise a session seeded from next_angle() could render a prompt with a
    DIFFERENT angle than the one that picked its own intent."""
    workflow = _workflow(tmp_path)
    bundle = PolicyBundle.load(ScoutMode.MICRO)

    first = workflow.next_angle(ScoutMode.MICRO)
    assert first == workflow._angle(bundle, ScoutMode.MICRO)
    workflow.start(ScoutMode.MICRO, first)
    second = workflow.next_angle(ScoutMode.MICRO)
    assert second == workflow._angle(bundle, ScoutMode.MICRO)
    assert second != first


def test_qa_and_micro_rotations_advance_independently(tmp_path):
    workflow = _workflow(tmp_path)
    qa_angles = PolicyBundle.load(ScoutMode.QA).general_angles["qa"]
    micro_angles = PolicyBundle.load(ScoutMode.MICRO).general_angles["micro"]

    assert workflow.next_angle(ScoutMode.QA) == qa_angles[0]
    workflow.start(ScoutMode.QA, "qa intent 1")
    # A QA session must not advance MICRO's pointer.
    assert workflow.next_angle(ScoutMode.MICRO) == micro_angles[0]
    workflow.start(ScoutMode.MICRO, "micro intent 1")
    assert workflow.next_angle(ScoutMode.QA) == qa_angles[1]
    assert workflow.next_angle(ScoutMode.MICRO) == micro_angles[1]
