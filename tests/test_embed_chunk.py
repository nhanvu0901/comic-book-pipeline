"""_openai_embed sub-batches the request so LM Studio/llama.cpp don't wedge on a
huge single batch, and falls back across tiers (OpenRouter -> local LM Studio ->
llama.cpp, order set by EMBED_PRIMARY) when one is down. Verify: split into
EMBED_CHUNK requests, concat in order; a tier that fails hands off to the next
tier for that same chunk (and the process sticks with whichever tier won, so
later chunks skip the dead one); only a chunk that fails on EVERY tier collapses
the whole call to [] (graceful all-or-nothing)."""
import config
import stages._embedding as E


def _isolate(monkeypatch):
    """No real OpenRouter key + reset tier stickiness -- every test starts from
    tier 0 with just the local + llama.cpp tiers, unless it opts into cloud."""
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(E, "_OPENAI_TIER_IDX", 0)


def _fake_local(monkeypatch, fail_at=None):
    """Replace _openai_embed_one (the 'local' tier) with a recorder returning
    1-dim 'vectors' equal to text length, so order is checkable. fail_at = a
    text that makes that tier fail (returns None, like a dead server)."""
    calls = []

    def fake(texts, timeout, url=None, model=None):
        calls.append(list(texts))
        if fail_at is not None and fail_at in texts:
            return None
        return [[float(len(t))] for t in texts]  # stand-in vector

    monkeypatch.setattr(E, "_openai_embed_one", fake)
    return calls


def test_chunks_and_preserves_order(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("EMBED_CHUNK", "3")
    calls = _fake_local(monkeypatch)
    texts = [f"t{i}" for i in range(7)]  # 7 items, chunk 3 → 3 requests (3,3,1)
    out = E._openai_embed(texts)
    assert [len(c) for c in calls] == [3, 3, 1]
    assert len(out) == 7                       # aligned with input
    assert out == [[2.0]] * 7                  # each "t{i}" is 2 chars → order intact


def test_all_tiers_fail_collapses_to_empty(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("EMBED_CHUNK", "3")
    _fake_local(monkeypatch, fail_at="t4")                    # local fails on chunk 2
    monkeypatch.setattr(E, "_llamacpp_embed_one", lambda t, timeout: None)  # llama.cpp also down
    out = E._openai_embed([f"t{i}" for i in range(7)])
    assert out == []                           # every tier failed → graceful []


def test_falls_back_to_next_tier_and_sticks(monkeypatch):
    """local down for the whole run -> every chunk hands off to llama.cpp, and
    the sticky tier index stays on llama.cpp (no re-trying the dead local tier
    once it's confirmed down)."""
    _isolate(monkeypatch)
    monkeypatch.setenv("EMBED_CHUNK", "3")
    monkeypatch.setattr(E, "_openai_embed_one", lambda *a, **k: None)  # local always down
    llama_calls = []

    def fake_llama(texts, timeout):
        llama_calls.append(list(texts))
        return [[1.0]] * len(texts)

    monkeypatch.setattr(E, "_llamacpp_embed_one", fake_llama)
    out = E._openai_embed([f"t{i}" for i in range(7)])
    assert len(out) == 7
    assert [len(c) for c in llama_calls] == [3, 3, 1]   # all chunks served by llama.cpp
    assert E._OPENAI_TIER_IDX == 1                      # sticky: skipped local (idx 0) after 1st hit


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
