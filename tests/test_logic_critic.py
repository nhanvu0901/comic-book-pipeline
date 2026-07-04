"""Stage 3 narration LOGIC CRITIC — the story-editor that trims low-impact beats and
feeds zero-context clarity directives back to the writer (config ENABLE_LOGIC_CRITIC).

These tests mock the LLM call (call_with_chain) so they assert the SAFETY RAILS
deterministically: protected beats (cold-open/climax/landing) are never dropped, and
the hard floor (LOGIC_CRITIC_MIN_BEATS) can't be undercut even if the critic is greedy."""
import json
import pytest

import stages.stage_3.write_script as ws
from stages.stage_3.schema import Beat


def _beats(n=13):
    # id0 COLD_OPEN ... id(n-2) CLIMAX, id(n-1) LANDING, middle = SETUP/ESCALATION
    out = [Beat(id=0, function="COLD_OPEN", name="hook", summary="open")]
    for i in range(1, n - 2):
        out.append(Beat(id=i, function="ESCALATION", name=f"b{i}", summary=f"beat {i}"))
    out.append(Beat(id=n - 2, function="CLIMAX", name="climax", summary="climax"))
    out.append(Beat(id=n - 1, function="LANDING", name="landing", summary="landing"))
    return out


def _mock_chain(monkeypatch, drop_ids):
    def fake(*a, **k):
        return json.dumps({"drop": drop_ids, "reason": "test"}), "mock-model"
    monkeypatch.setattr(ws, "call_with_chain", fake)


def test_protected_beats_never_dropped(monkeypatch):
    beats = _beats(13)
    # critic greedily tries to drop the cold-open(0), climax(11), landing(12) + two mids
    _mock_chain(monkeypatch, [0, 3, 4, 11, 12])
    kept = ws._critique_beats_for_impact(beats, {"plot_summary": "p"}, model=None, progress=None)
    kept_ids = {b.id for b in kept}
    assert {0, 11, 12} <= kept_ids          # cold-open / climax / landing survive
    assert 3 not in kept_ids and 4 not in kept_ids   # ordinary low-impact beats dropped
    assert [b.id for b in kept] == sorted(kept_ids)   # original order preserved


def test_floor_never_undercut(monkeypatch):
    beats = _beats(13)
    # critic tries to drop almost everything
    _mock_chain(monkeypatch, list(range(1, 11)))
    kept = ws._critique_beats_for_impact(beats, {"plot_summary": "p"}, model=None, progress=None)
    assert len(kept) >= ws.LOGIC_CRITIC_MIN_BEATS   # hard floor honored


def test_no_drop_returns_all(monkeypatch):
    beats = _beats(13)
    _mock_chain(monkeypatch, [])
    kept = ws._critique_beats_for_impact(beats, {"plot_summary": "p"}, model=None, progress=None)
    assert len(kept) == len(beats)


def test_skips_when_at_or_below_floor():
    # <= floor beats → no critic call, returns the same list object untouched
    beats = _beats(ws.LOGIC_CRITIC_MIN_BEATS)
    assert ws._critique_beats_for_impact(beats, {}, model=None, progress=None) is beats


def test_causal_antecedent_not_dropped(monkeypatch):
    # Build 13 beats where a KEPT later beat's `cause` reasons from beat id1's
    # content (shares "symbiote"+"eddie"). The critic greedily drops id1 — the
    # causal guard must veto that drop so the cause->effect link survives, while
    # an unrelated low-impact beat (id5) is still dropped.
    beats = [Beat(id=0, function="COLD_OPEN", name="hook", summary="open")]
    beats.append(Beat(id=1, function="SETUP", name="symbiote bonds eddie",
                      summary="the symbiote bonds with eddie"))
    for i in range(2, 11):
        beats.append(Beat(id=i, function="ESCALATION", name=f"b{i}", summary=f"beat {i}"))
    beats.append(Beat(id=11, function="CLIMAX", name="final fight", summary="the showdown",
                      cause="because the symbiote bonded with eddie earlier"))
    beats.append(Beat(id=12, function="LANDING", name="landing", summary="end"))

    _mock_chain(monkeypatch, [1, 5])   # critic wants to drop the antecedent (1) + a filler (5)
    kept_ids = {b.id for b in ws._critique_beats_for_impact(
        beats, {"plot_summary": "p"}, model=None, progress=None)}
    assert 1 in kept_ids       # causal antecedent vetoed back in
    assert 5 not in kept_ids   # unrelated low-impact beat still dropped


def test_ubiquitous_name_does_not_trigger_false_veto(monkeypatch):
    # "eddie" appears in nearly every beat (ubiquitous character name). Sharing ONLY
    # that name with a kept beat's `cause` must NOT count toward the >=2-token veto
    # threshold — else a name that's everywhere blocks almost every drop (C3a bug:
    # a real run vetoed 4 of 5 proposed drops this way). "intervened" is genuinely
    # distinctive (appears nowhere else), so on its own it must stay below threshold.
    beats = [Beat(id=0, function="COLD_OPEN", name="hook", summary="eddie wakes up")]
    for i in range(1, 9):
        beats.append(Beat(id=i, function="ESCALATION", name=f"eddie beat {i}",
                          summary=f"eddie does thing {i}"))
    beats.append(Beat(id=9, function="ESCALATION", name="filler",
                      summary="eddie intervened suddenly"))
    beats.append(Beat(id=10, function="CLIMAX", name="final fight",
                      summary="the eddie showdown", cause="because eddie intervened"))
    beats.append(Beat(id=11, function="LANDING", name="landing", summary="eddie rests"))

    _mock_chain(monkeypatch, [9])   # critic wants to drop the ordinary filler beat
    kept_ids = {b.id for b in ws._critique_beats_for_impact(
        beats, {"plot_summary": "p"}, model=None, progress=None)}
    assert 9 not in kept_ids   # shares only the ubiquitous "eddie" — must NOT be vetoed


def test_clarity_issues_are_soft_not_critical(monkeypatch):
    # a clarity directive must NOT be treated as a critical (fidelity-breaking) error
    def fake(*a, **k):
        return json.dumps({"issues": [{"scene_id": 5, "problem": "name X never explained",
                                       "fix": "add a 4-word gloss"}]}), "mock-model"
    monkeypatch.setattr(ws, "call_with_chain", fake)
    scenes = [{"scene_id": i, "text": f"line {i}"} for i in range(1, 6)]
    issues = ws._logic_clarity_critic({"scenes": scenes}, {"plot_summary": "p"},
                                      model=None, progress=None)
    assert issues and issues[0].startswith("clarity:")
    assert not ws._is_critical_error(issues[0])   # soft — won't dominate best-draft


def test_safe_beat_int_handles_non_numeric_ids():
    # Bridge-inserted beats can carry ids like "8b"/"8.5"/"" — must not crash the
    # outline (int("8b") raised ValueError and killed Stage 3). Falls back to default.
    assert ws._safe_beat_int("8b", 99) == 8
    assert ws._safe_beat_int("8.5", 0) == 8
    assert ws._safe_beat_int("12", 0) == 12
    assert ws._safe_beat_int("", 5) == 5
    assert ws._safe_beat_int(None, 7) == 7
