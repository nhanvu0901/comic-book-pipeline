"""EMBED_PRIMARY resolver order (config.py knob) for stages/_embedding.py's
'openai'/'qwen' backend, and the OpenRouter tier's request/response handling --
all with urllib.request.urlopen mocked, ZERO live network calls."""
import io
import json
import urllib.request

import config
import stages._embedding as E


def test_tier_order_openrouter_primary_default(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(config, "EMBED_PRIMARY", "openrouter", raising=False)
    names = [n for n, _fn, _t in E._openai_tiers()]
    assert names == ["openrouter", "local:1234", "llama.cpp:1235"]


def test_tier_order_local_primary_reverses_first_two(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(config, "EMBED_PRIMARY", "local", raising=False)
    names = [n for n, _fn, _t in E._openai_tiers()]
    assert names == ["local:1234", "openrouter", "llama.cpp:1235"]


def test_tier_order_no_api_key_drops_openrouter(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "EMBED_PRIMARY", "openrouter", raising=False)
    names = [n for n, _fn, _t in E._openai_tiers()]
    assert names == ["local:1234", "llama.cpp:1235"]      # llama.cpp always last resort


class _FakeResp:
    """Minimal context-manager stand-in for urllib.request.urlopen's return value."""
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return io.BytesIO(self._payload)

    def __exit__(self, *a):
        return False


def test_openrouter_embed_one_parses_response_and_sends_auth(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-test", raising=False)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data)
        # OpenRouter can return data out of order -- index field re-sorts it.
        return _FakeResp({"data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = E._openrouter_embed_one(["a", "b"], timeout=5.0)

    assert out is not None and len(out) == 2
    assert captured["url"] == config.OPENROUTER_BASE_URL.rstrip("/") + "/embeddings"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == config.EMBED_OPENROUTER_MODEL
    assert captured["body"]["input"] == ["a", "b"]
    # normalized + re-sorted by index: out[0] came from index-0 embedding [1,0]
    assert list(out[0]) == [1.0, 0.0]


def test_openrouter_embed_one_gives_up_after_2_attempts(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-test", raising=False)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise OSError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = E._openrouter_embed_one(["a"], timeout=1.0)
    assert out is None
    assert calls["n"] == 2                      # retry x2, per spec -- not 3 like local


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
