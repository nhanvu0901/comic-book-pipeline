"""Stage-5 caption-chunk → visual-beat bucketing (_split_members_by_clause).

The spaCy clause splitter was removed (it almost never fired); visual beats now come
from the Stage-3 LLM beat-splitter. This module keeps testing the WORD-POSITION
bucketing that maps caption chunks to beats — unchanged and still spaCy-free."""
from stages.stage_5.shots import _split_members_by_clause


def test_members_bucketed_by_beat():
    # 2 beats, 4 caption-chunks (word-fragments) → 2 contiguous groups by word pos.
    beats = ["When the hero charged the gate", "he raised his sword high"]
    members = [
        ("When the hero", 0.0, 1.0),       # words 1-3  → beat 0
        ("charged the gate", 1.0, 1.0),    # words 4-6  → beat 0
        ("he raised his", 2.0, 1.0),       # words 7-9  → beat 1
        ("sword high", 3.0, 1.0),          # words 10-11→ beat 1
    ]
    groups = _split_members_by_clause(members, beats)
    assert len(groups) == 2
    assert [m[0] for m in groups[0]] == ["When the hero", "charged the gate"]
    assert [m[0] for m in groups[1]] == ["he raised his", "sword high"]


def test_single_beat_one_group():
    members = [("a", 0.0, 1.0), ("b", 1.0, 1.0)]
    assert _split_members_by_clause(members, ["one beat only here"]) == [members]
