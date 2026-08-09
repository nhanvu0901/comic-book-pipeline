"""recap + micro from a narration Master already wrote.

The contract: the writer TIGHTENS Master's body into the band — compressing wording while
keeping every meaning — and writes a title/hook/outro. It never drops a sentence and never
loses a name or a number.
"""
import json

import pytest

from stages.stage_3 import provided_narration as pn


# ── sentence split: pure code, verbatim ──────────────────────────────────────

def test_splits_on_sentence_ends():
    got = pn.split_sentences("He ran. She followed! Did anyone speak?")
    assert got == ["He ran.", "She followed!", "Did anyone speak?"]


def test_abbreviation_does_not_split_a_sentence():
    assert pn.split_sentences("Mr. Freeze lost.") == ["Mr. Freeze lost."]
    assert pn.split_sentences("Batman vs. Thor ended badly.") == ["Batman vs. Thor ended badly."]


def test_blank_line_is_a_hard_break():
    """Master can force a scene split by pressing enter twice."""
    assert pn.split_sentences("One two three\n\nfour five six") == ["One two three",
                                                                    "four five six"]


def test_split_is_verbatim():
    text = ("Batman walked into a tomb. Superman followed him down. Then the door opened, "
            "and nobody spoke.")
    assert " ".join(pn.split_sentences(text)) == " ".join(text.split())


# ── tightening: compress the wording, never lose a meaning ───────────────────
# Master 2026-07-31: dropping whole sentences was too aggressive. Summarise without
# losing anything — so one sentence in, one sentence out, and every name/number survives.

def _sents(n, words=5):
    return [f"Alpha{i} " + " ".join(["padding"] * (words - 2)) + " end." for i in range(n)]


def test_under_budget_is_returned_untouched():
    s = _sents(3)
    assert pn._trim_to_band(s, 100, log=lambda _m: None, model=None) == s


def test_tightening_keeps_one_sentence_per_sentence(monkeypatch):
    s = ["Batman walked slowly into the very old Egyptian tomb.",
         "Superman and Wonder Woman followed him all the way down."]
    tight = ["Batman walked into the Egyptian tomb.",
             "Superman and Wonder Woman followed him down."]
    monkeypatch.setattr(pn, "call_with_chain",
                        lambda **kw: (json.dumps({"scenes": tight}), "fake"))
    assert pn._trim_to_band(s, 12, log=lambda _m: None, model=None) == tight


def test_validator_refuses_a_dropped_sentence(monkeypatch):
    s = ["Batman entered the tomb slowly.", "Damian waited outside the whole time."]
    v = _validator_for(monkeypatch, s, word_max=9)
    assert not v(json.dumps({"scenes": ["Batman entered the tomb."]})), \
        "losing a sentence loses its meaning — that is what this pass must not do"


def test_validator_refuses_a_lost_name_or_number(monkeypatch):
    s = ["Batman quietly handed Damian the Omega Beams back in 1977."]     # 10 words
    v = _validator_for(monkeypatch, s, word_max=8)

    assert v(json.dumps({"scenes": ["Batman gave Damian the Omega Beams in 1977."]})), \
        "a tighter line that keeps every name and the year is exactly the goal"
    assert not v(json.dumps({"scenes": ["Batman gave his son the weapon in 1977."]})), \
        "Damian and Omega Beams are the concrete load, not decoration"
    assert not v(json.dumps({"scenes": ["Batman gave Damian the Omega Beams."]})), \
        "the year is a fact too"


def test_validator_refuses_going_over_budget(monkeypatch):
    s = ["Alpha one two three four five six seven eight."]
    v = _validator_for(monkeypatch, s, word_max=5)
    assert not v(json.dumps({"scenes": [s[0]]}))


def test_failed_tighten_ships_masters_words_unchanged(monkeypatch):
    """Never silently lose the ending: an over-long script Master can see beats a
    truncated one he cannot."""
    s = _sents(4)

    def boom(**kw):
        raise RuntimeError("model down")
    monkeypatch.setattr(pn, "call_with_chain", boom)
    seen: list[str] = []
    assert pn._trim_to_band(s, 5, log=seen.append, model=None) == s
    assert any("over" in m for m in seen), "the overrun must be reported, not hidden"


def test_tighten_logs_every_changed_sentence(monkeypatch):
    s = ["Batman walked very slowly into the tomb.", "Damian waited outside."]
    tight = ["Batman walked into the tomb.", "Damian waited outside."]
    monkeypatch.setattr(pn, "call_with_chain",
                        lambda **kw: (json.dumps({"scenes": tight}), "fake"))
    seen: list[str] = []
    pn._trim_to_band(s, 9, log=seen.append, model=None)
    joined = " ".join(seen)
    assert "tightened" in joined and "Batman walked very slowly" in joined
    assert "Damian waited outside.\n" not in joined, "an unchanged sentence is not logged"


def _validator_for(monkeypatch, sentences, *, word_max):
    """Pull the validator _trim_to_band hands to the LLM so it can be exercised directly."""
    captured = {}

    def fake(**kw):
        captured["v"] = kw["validator"]
        return json.dumps({"scenes": sentences}), "fake"
    monkeypatch.setattr(pn, "call_with_chain", fake)
    pn._trim_to_band(sentences, word_max, log=lambda _m: None, model=None)
    return captured["v"]


# ── discovery ────────────────────────────────────────────────────────────────

def test_empty_file_counts_as_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(pn, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "master_narration.md").write_text("   \n")
    assert pn.provided_narration_path("p") is None


def test_finds_the_master_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pn, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "master_narration.md").write_text("He ran.")
    assert pn.provided_narration_path("p").name == "master_narration.md"


def test_bands_come_from_each_mode(monkeypatch):
    lo_m, hi_m = pn._band("micro_moment")
    lo_r, hi_r = pn._band("recap_summary")
    assert lo_m < hi_m and lo_r < hi_r
    assert (lo_m, hi_m) != (lo_r, hi_r), "a micro and a recap are not the same length"
