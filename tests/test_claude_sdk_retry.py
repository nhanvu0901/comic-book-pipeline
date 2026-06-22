"""Retry policy for the Claude Agent SDK wrapper (_complete_with_retry).

Regression for the anthology bug: a 'Reached maximum number of turns' error is
DETERMINISTIC (the agent ran out of turns), so retrying it 3x just re-burns the
same turns and wastes minutes. It must fall back after a SINGLE attempt. Generic
flaky exceptions (transport blips) must STILL be retried with backoff.

_attempt is monkeypatched so no thread / SDK / network is touched."""
import stages._claude_sdk as sdk


def _no_sleep(monkeypatch):
    monkeypatch.setattr(sdk.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sdk, "sdk_available", lambda: True)


def test_max_turns_error_is_not_retried(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []

    def fake_attempt(*_a, **_k):
        calls.append(1)
        return {"_err": Exception(
            "Claude Code returned an error result: Reached maximum number of turns (12)")}

    monkeypatch.setattr(sdk, "_attempt", fake_attempt)
    logs = []
    out = sdk._complete_with_retry("sys", "user", "model", 10, logs.append)

    assert out is None
    assert len(calls) == 1                                   # ONE attempt, no retries
    assert any("NOT retrying" in m for m in logs)


def test_generic_flaky_error_is_retried(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []

    def fake_attempt(*_a, **_k):
        calls.append(1)
        return {"_err": Exception("transport blip")}

    monkeypatch.setattr(sdk, "_attempt", fake_attempt)
    out = sdk._complete_with_retry("sys", "user", "model", 10, lambda _m: None)

    assert out is None
    assert len(calls) == sdk._TRANSIENT_RETRIES + 1          # full retry budget used


def test_ok_result_returns_text(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(sdk, "_attempt", lambda *_a, **_k: {
        "text": "hello", "rl_status": None, "api_error_status": None,
        "is_error": False, "subtype": None})
    out = sdk._complete_with_retry("sys", "user", "model", 10, lambda _m: None)
    assert out == "hello"
