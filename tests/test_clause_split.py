"""Tests for the Stage-5 clause splitter (clause-anchored panels, 2026-06-18)."""
import stages.stage_5.shots as shots
from stages.stage_5.shots import _split_into_clauses, _split_members_by_clause


def test_when_he_sentence_splits_into_two_clauses():
    # The Ghost Rider sentence: subordinate "When … crashed …" + main "he declared …".
    # Appositive ("the Spirit of Vengeance") and list ("judge, jury, and executioner")
    # must NOT be split.
    s = ("When Ghost Rider, the Spirit of Vengeance, crashed Ben Grimm's interrogation "
         "of a wanted murderer, he declared himself judge, jury, and executioner.")
    cl = _split_into_clauses(s)
    assert len(cl) == 2, cl
    assert cl[0].startswith("When Ghost Rider")
    assert "wanted murderer" in cl[0]
    assert cl[1].startswith("he declared")
    assert "judge, jury, and executioner" in cl[1]   # list kept intact


def test_gerund_clause_not_split():
    # ", forcing …" is a gerund (no post-comma subject) → one clause.
    s = "Then he unleashed the Penance Stare, forcing the criminal to feel every sin."
    assert _split_into_clauses(s) == [s.strip().rstrip(".")]  or len(_split_into_clauses(s)) == 1


def test_relative_clause_with_comma_splits():
    s = "Then Ghost Rider fired a blast at Galactus, who answered with the Power Cosmic."
    cl = _split_into_clauses(s)
    assert len(cl) == 2, cl
    assert cl[1].startswith("who answered")


def test_simple_sentence_one_clause():
    assert _split_into_clauses("The criminal burned to ash.") == ["The criminal burned to ash"]


def test_empty_returns_empty():
    assert _split_into_clauses("") == []
    assert _split_into_clauses("   ") == []


def test_fallback_when_spacy_unavailable(monkeypatch):
    monkeypatch.setattr(shots, "_spacy", lambda: None)
    s = "When X happened, he ran."
    assert _split_into_clauses(s) == [s]      # whole sentence, one panel


def test_members_bucketed_by_clause():
    # 2 clauses, 4 caption-chunks (word-fragments) → 2 contiguous groups by word pos.
    clauses = ["When the hero charged the gate", "he raised his sword high"]
    members = [
        ("When the hero", 0.0, 1.0),       # words 1-3  → clause 0
        ("charged the gate", 1.0, 1.0),    # words 4-6  → clause 0
        ("he raised his", 2.0, 1.0),       # words 7-9  → clause 1
        ("sword high", 3.0, 1.0),          # words 10-11→ clause 1
    ]
    groups = _split_members_by_clause(members, clauses)
    assert len(groups) == 2
    assert [m[0] for m in groups[0]] == ["When the hero", "charged the gate"]
    assert [m[0] for m in groups[1]] == ["he raised his", "sword high"]


def test_single_clause_one_group():
    members = [("a", 0.0, 1.0), ("b", 1.0, 1.0)]
    assert _split_members_by_clause(members, ["one clause only here"]) == [members]
