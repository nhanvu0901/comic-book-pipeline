"""Chatterbox chunking: short sentences merge forward, cap respected, comma split kept."""
from stages.stage_4 import chatterbox_tts as ct


def test_short_sentence_merges_into_next():
    out = ct._chunks("Yes. This second sentence is long enough to stand on its own here.")
    assert out == ["Yes. This second sentence is long enough to stand on its own here."]


def test_short_last_sentence_merges_into_previous():
    long = "This first sentence is long enough to stand on its own here."
    assert ct._chunks(f"{long} Done.") == [f"{long} Done."]


def test_long_sentences_stay_separate():
    a = "This first sentence is long enough to stand on its own here."
    b = "This second sentence is also long enough to stand alone."
    assert ct._chunks(f"{a} {b}") == [a, b]


def test_merge_respects_max(monkeypatch):
    monkeypatch.setattr(ct, "_MAX_CHUNK_CHARS", 50)
    monkeypatch.setattr(ct, "_MIN_CHUNK_CHARS", 20)
    long = "This sentence is exactly forty-five chars long."
    out = ct._chunks(f"Hi there. {long}")
    assert out == ["Hi there.", long]
    assert all(len(c) <= 50 for c in out)


def test_chain_of_short_sentences_merges():
    assert ct._chunks("One. Two. Three. Four. Five.") == ["One. Two. Three. Four. Five."]


def test_overlong_sentence_still_splits_at_commas(monkeypatch):
    monkeypatch.setattr(ct, "_MAX_CHUNK_CHARS", 60)
    s = "alpha beta gamma delta, epsilon zeta eta theta, iota kappa lambda mu, nu xi omicron pi."
    out = ct._chunks(s)
    assert len(out) > 1 and all(len(c) <= 60 for c in out)
    assert " ".join(out) == s


def test_default_caps():
    assert ct._MAX_CHUNK_CHARS == 280 and ct._MIN_CHUNK_CHARS == 40
