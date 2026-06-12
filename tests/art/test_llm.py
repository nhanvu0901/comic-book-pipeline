"""art_pipeline/_llm.py — ART_FREE_MODEL transport selector, no cross-fallback."""
import pytest

from art_pipeline import _llm


def test_sdk_only_returns_sdk_output(monkeypatch):
    monkeypatch.setattr(_llm, "ART_FREE_MODEL", False)
    monkeypatch.setattr(_llm, "sdk_available", lambda: True)
    monkeypatch.setattr(_llm, "sdk_complete", lambda s, u, log=None: '{"ok": 1}')
    called = []
    monkeypatch.setattr(_llm, "call_with_chain",
                        lambda **k: called.append(k) or ("X", "or"))
    out, model = _llm.art_complete(system="s", user="u",
                                   validator=lambda c: c.startswith("{"))
    assert out == '{"ok": 1}'
    assert model.startswith("claude-sdk:")
    assert called == []          # OpenRouter NEVER touched


def test_sdk_failure_raises_no_fallback(monkeypatch):
    monkeypatch.setattr(_llm, "ART_FREE_MODEL", False)
    monkeypatch.setattr(_llm, "sdk_available", lambda: True)
    monkeypatch.setattr(_llm, "sdk_complete", lambda s, u, log=None: None)
    called = []
    monkeypatch.setattr(_llm, "call_with_chain",
                        lambda **k: called.append(k) or ("X", "or"))
    with pytest.raises(RuntimeError, match="no OpenRouter fallback|NO OpenRouter fallback"):
        _llm.art_complete(system="s", user="u")
    assert called == []          # no fallback


def test_sdk_validator_reject_raises(monkeypatch):
    monkeypatch.setattr(_llm, "ART_FREE_MODEL", False)
    monkeypatch.setattr(_llm, "sdk_available", lambda: True)
    monkeypatch.setattr(_llm, "sdk_complete", lambda s, u, log=None: "not json")
    with pytest.raises(RuntimeError):
        _llm.art_complete(system="s", user="u",
                          validator=lambda c: c.startswith("{"))


def test_sdk_unavailable_raises_actionable(monkeypatch):
    monkeypatch.setattr(_llm, "ART_FREE_MODEL", False)
    monkeypatch.setattr(_llm, "sdk_available", lambda: False)
    with pytest.raises(RuntimeError, match="ART_FREE_MODEL=true"):
        _llm.art_complete(system="s", user="u")


def test_free_model_uses_openrouter_only(monkeypatch):
    monkeypatch.setattr(_llm, "ART_FREE_MODEL", True)
    sdk_calls = []
    monkeypatch.setattr(_llm, "sdk_complete",
                        lambda *a, **k: sdk_calls.append(1) or "x")
    monkeypatch.setattr(_llm, "call_with_chain",
                        lambda **k: ("router-out", "deepseek/x"))
    out, model = _llm.art_complete(system="s", user="u", models=["deepseek/x"])
    assert (out, model) == ("router-out", "deepseek/x")
    assert sdk_calls == []        # SDK NEVER touched in free mode


def test_sdk_retries_twice_then_succeeds(monkeypatch):
    monkeypatch.setattr(_llm, "ART_FREE_MODEL", False)
    monkeypatch.setattr(_llm, "sdk_available", lambda: True)
    seq = iter([None, "ok"])
    monkeypatch.setattr(_llm, "sdk_complete", lambda s, u, log=None: next(seq))
    out, _ = _llm.art_complete(system="s", user="u")
    assert out == "ok"
