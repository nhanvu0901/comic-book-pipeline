"""_openai_embed sub-batches the request so LM Studio doesn't wedge on a huge
single batch. Verify: split into EMBED_CHUNK requests, concat in order, and a
failed chunk collapses to [] (graceful all-or-nothing)."""
import stages._embedding as E


def _fake_one(monkeypatch, fail_at=None):
    """Replace _openai_embed_one with a recorder returning 1-dim 'vectors' equal to
    the text, so order is checkable. fail_at = a text that makes a chunk fail."""
    calls = []

    def fake(texts, timeout):
        calls.append(list(texts))
        if fail_at is not None and fail_at in texts:
            return None
        return [[float(len(t))] for t in texts]  # stand-in vector

    monkeypatch.setattr(E, "_openai_embed_one", fake)
    return calls


def test_chunks_and_preserves_order(monkeypatch):
    monkeypatch.setenv("EMBED_CHUNK", "3")
    calls = _fake_one(monkeypatch)
    texts = [f"t{i}" for i in range(7)]  # 7 items, chunk 3 → 3 requests (3,3,1)
    out = E._openai_embed(texts)
    assert [len(c) for c in calls] == [3, 3, 1]
    assert len(out) == 7                       # aligned with input
    assert out == [[2.0]] * 7                  # each "t{i}" is 2 chars → order intact


def test_failed_chunk_collapses_to_empty(monkeypatch):
    monkeypatch.setenv("EMBED_CHUNK", "3")
    _fake_one(monkeypatch, fail_at="t4")       # lands in the 2nd chunk
    out = E._openai_embed([f"t{i}" for i in range(7)])
    assert out == []                           # any chunk fail → graceful []


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
