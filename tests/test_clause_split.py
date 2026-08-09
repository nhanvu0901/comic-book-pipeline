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


# ─── straddling chunk must not starve a fragment ─────────────────────────────
# A caption chunk is an AUDIO unit and ignores fragment seams. The old rule filed a whole
# chunk under the clause covering its MIDPOINT word, so a fragment sitting entirely inside
# one chunk got an EMPTY bucket, the caller dropped it, and that fragment rendered no shot
# — losing its per-fragment lock / custom image. broken-adamantium lost "The rule is
# simple." to exactly this.

def test_fragment_inside_one_chunk_still_gets_a_bucket():
    clauses = ["The rule is simple.", "Adamantium coats his skeleton.", "It never breaks."]
    members = [("The rule is simple. Adamantium coats his skeleton.", 0.0, 6.0),
               ("It never breaks.", 6.0, 3.0)]
    buckets = _split_members_by_clause(members, clauses)
    assert len(buckets) == len(clauses)
    assert all(b for b in buckets), "a non-empty clause must never get an empty bucket"
    assert [" ".join(m[0] for m in b) for b in buckets] == clauses


def test_straddling_split_preserves_duration_and_word_order():
    clauses = ["one two", "three four five", "six"]
    members = [("one two three", 0.0, 3.0), ("four five six", 3.0, 3.0)]
    buckets = _split_members_by_clause(members, clauses)
    flat = [m for b in buckets for m in b]
    assert abs(sum(m[2] for m in flat) - 6.0) < 1e-9            # no time invented or lost
    assert " ".join(m[0] for m in flat) == "one two three four five six"
    starts = [m[1] for m in flat]
    assert starts == sorted(starts)                                # chunks stay audio-ordered


def test_single_clause_chunk_is_not_cut():
    """A chunk wholly inside one clause passes through untouched — no needless splitting."""
    clauses = ["alpha beta", "gamma delta"]
    members = [("alpha beta", 0.0, 2.0), ("gamma delta", 2.0, 2.0)]
    buckets = _split_members_by_clause(members, clauses)
    assert buckets == [[("alpha beta", 0.0, 2.0)], [("gamma delta", 2.0, 2.0)]]
