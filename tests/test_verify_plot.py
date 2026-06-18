"""Tests for the Stage-1 cross-source plot verifier (verify_plot).

verify_plot uses sdk_complete_web (a web-research LLM agent) — these tests
monkeypatch it so no network/SDK call happens."""
import json

import stages.stage_1.tools.verify_plot as vp


def _ctx():
    return {"title": "Thor Annual", "year": "2023", "issues": "#1",
            "plot_summary": "DRAFT: MODOK renamed himself M.Y.T.H.O.S. and the hero died."}


def _patch_web(monkeypatch, fn):
    # verify_plot does `from stages._claude_sdk import sdk_complete_web` at call time,
    # so patching the attribute on that module is what takes effect.
    monkeypatch.setattr("stages._claude_sdk.sdk_complete_web", fn)


def test_auto_fix_rewrites_plot_and_logs_discrepancies(monkeypatch):
    verified = {"verified_plot": "Thor battles MODOK, tears off his arm, and restores the Ten Realms.",
                "confidence": "high",
                "discrepancies": ["draft said the hero died — wrong; Thor wins",
                                  "M.Y.T.H.O.S. is not the villain's name"],
                "sources_used": ["https://cbr.com/x"]}
    _patch_web(monkeypatch, lambda system, user, **kw: json.dumps(verified))
    ctx = _ctx()
    vp.verify_plot(ctx)
    assert ctx["plot_summary"] == verified["verified_plot"]          # auto-fixed
    assert ctx["verification"]["confidence"] == "high"
    assert len(ctx["verification"]["discrepancies"]) == 2
    assert ctx["verification"]["sources_used"] == ["https://cbr.com/x"]


def test_none_keeps_draft_and_marks_unverified(monkeypatch):
    _patch_web(monkeypatch, lambda system, user, **kw: None)
    ctx = _ctx()
    before = ctx["plot_summary"]
    vp.verify_plot(ctx)
    assert ctx["plot_summary"] == before                            # untouched
    assert ctx["verification"]["confidence"] == "unverified"


def test_agent_error_keeps_draft(monkeypatch):
    def _boom(system, user, **kw):
        raise RuntimeError("sdk down")
    _patch_web(monkeypatch, _boom)
    ctx = _ctx()
    before = ctx["plot_summary"]
    vp.verify_plot(ctx)
    assert ctx["plot_summary"] == before
    assert ctx["verification"]["confidence"] == "unverified"


def test_skips_when_no_plot():
    ctx = {"title": "X", "year": "2020", "plot_summary": ""}
    vp.verify_plot(ctx)
    assert "verification" not in ctx     # nothing to verify → no-op
