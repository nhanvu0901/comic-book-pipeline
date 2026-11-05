"""Spoken-form normaliser (utils/spoken_text.py) and its wiring."""
import json

import pytest

from utils.spoken_text import to_spoken

CASES = [
    # issue numbers
    ("Venom #2", "Venom issue two"),
    ("In Ultimate Wolverine #15, he", "In Ultimate Wolverine issue fifteen, he"),
    ("Issue #2", "issue two"),
    ("the #1 threat", "the number one threat"),
    ("It was #1.", "It was number one."),
    ("Batman (2016) #77", "Batman, twenty sixteen, issue seventy-seven"),
    ("Venom #125", "Venom issue one twenty-five"),
    ("Venom #105", "Venom issue one oh five"),
    ("Venom #200", "Venom issue two hundred"),
    ("Venom #1000", "Venom issue one thousand"),
    ("Venom #0.1", "Venom issue zero point one"),
    ("#0.1", "issue zero point one"),
    ("Venom #1-6", "Venom issues one to six"),
    ("Venom #1–6", "Venom issues one to six"),
    ("Issues #1-6", "issues one to six"),
    ("No. 5", "number five"),
    ("Vol. 2", "volume two"),
    ("vol 2", "volume two"),
    ("Pt. 2", "part two"),
    # clock times and hyphenated designations
    ("He turned 30 at 9:30 pm.", "He turned thirty at nine thirty P. M."),
    ("Meet at 9:05.", "Meet at nine oh five."),
    ("At 12:00 it ended.", "At twelve o'clock it ended."),
    ("They fight on Earth-616.", "They fight on Earth six one six."),
    ("The T-1000 walks in.", "The T one thousand walks in."),
    # years / decades
    ("in 1984", "in nineteen eighty-four"),
    ("in 2008", "in two thousand eight"),
    ("in 2015", "in twenty fifteen"),
    ("in 2000", "in two thousand"),
    ("in 1900", "in nineteen hundred"),
    ("in 1905", "in nineteen oh five"),
    ("the 1980s", "the nineteen eighties"),
    ("the '80s", "the eighties"),
    ("the 80s", "the eighties"),
    # numbers
    ("1,000 men", "one thousand men"),
    ("12 men", "twelve men"),
    ("1st", "first"),
    ("22nd", "twenty-second"),
    ("100th", "one hundredth"),
    ("2.5 miles", "two point five miles"),
    ("50% off", "fifty percent off"),
    ("$5", "five dollars"),
    ("$1.5 million", "one point five million dollars"),
    ("3-5 days", "three to five days"),
    ("1939-1945", "nineteen thirty-nine to nineteen forty-five"),
    # roman numerals
    ("Civil War II", "Civil War two"),
    ("Professor X and I and V", "Professor X and I and V"),
    ("then II happened", "then II happened"),
    # symbols
    ("Fish & chips", "Fish and chips"),
    ("a + b", "a plus b"),
    ("meet @ noon", "meet at noon"),
    ("x = y", "x equals y"),
    ("black/white", "black or white"),
    ("and/or", "and or"),
    ("**bold** _it_ ~x~", "bold it x"),
    ("a [note] b", "a note b"),
    ("Title (2015), x", "Title, twenty fifteen, x"),
    ("He left (quietly). Then", "He left, quietly. Then"),
    # abbreviations
    ("Mr. Smith", "Mister Smith"),
    ("Mrs. Smith", "Missus Smith"),
    ("Dr. Strange", "Doctor Strange"),
    ("Prof. X", "Professor X"),
    ("Capt. Ahab", "Captain Ahab"),
    ("Sgt. Rock", "Sergeant Rock"),
    ("Lt. Dan", "Lieutenant Dan"),
    ("Gen. Ross", "General Ross"),
    ("A vs. B", "A versus B"),
    ("A vs B", "A versus B"),
    ("A v. B", "A versus B"),
    ("and so on, etc., fine", "and so on, and so on, fine"),
    ("e.g. this", "for example this"),
    ("i.e. that", "that is that"),
    ("approx. ten", "about ten"),
    ("St. Louis", "St. Louis"),
    # acronyms
    ("S.H.I.E.L.D. agents", "Shield agents"),
    ("SHIELD", "Shield"),
    ("NEVER", "Never"),
    ("NASA", "Nasa"),
    ("the FBI", "the F. B. I."),
    ("the MCU", "the M. C. U."),
    ("the NYPD", "the N. Y. P. D."),
    ("He called the FBI. Then left.", "He called the F. B. I. Then left."),
    ("U.S. troops", "U. S. troops"),
    ("OK", "okay"),
    ("I and A", "I and A"),
    ("X-Men", "X-Men"),
    ("NEVER GIVE UP", "Never Give Up"),
    ("STRONG HOLDS", "Strong Holds"),
]

PLAIN = [
    "He walked into the room, and nothing was the same.",
    "\"Why now?\" she asked... He shrugged -- or maybe not.",
    "Spider-Man swings over New York at night.",
    "A quiet word, then a long silence. I wonder why.",
]


@pytest.mark.parametrize("src,want", CASES)
def test_rule(src, want):
    assert to_spoken(src) == want


@pytest.mark.parametrize("src,want", CASES)
def test_idempotent(src, want):
    once = to_spoken(src)
    assert to_spoken(once) == once


@pytest.mark.parametrize("text", PLAIN)
def test_plain_prose_unchanged(text):
    assert to_spoken(text) == text


def test_no_doubled_commas_from_parens():
    out = to_spoken("Title (2015), x (y), z.")
    assert ",," not in out and ", ," not in out


def test_mixed_paragraph():
    src = ("In Venom #2 (2018), Dr. Strange & the F.B.I. hunt Eddie vs. the NYPD. "
           "It cost $1.5 million, 50% of the 1980s budget, in Civil War II -- the #1 hit.")
    out = to_spoken(src)
    assert out == (
        "In Venom issue two, twenty eighteen, Doctor Strange and the F. B. I. hunt Eddie versus "
        "the N. Y. P. D. It cost one point five million dollars, fifty percent of the nineteen "
        "eighties budget, in Civil War two -- the number one hit.")
    assert to_spoken(out) == out
    assert not any(c.isdigit() or c in "#&$%" for c in out)


def test_save_narration_writes_spoken_form(tmp_path, monkeypatch):
    from stages.stage_3 import pipeline
    from stages.stage_3.schema import Narration, Scene

    monkeypatch.setattr(pipeline, "get_project_dirs", lambda project: {"root": tmp_path})
    narration = Narration(mode="micro_moment", title="Venom #2", hook="In Venom #2, it starts.", scenes=[
        Scene(scene_id=1, text="In Venom #2, it starts.", page_ref=1, panel_ref=0,
              word_count=5, target_seconds=1.0, is_intro=True),
        Scene(scene_id=2, text="The FBI loses 1,000 men.", page_ref=2, panel_ref=0,
              word_count=5, target_seconds=1.0, is_outro=True),
    ], total_word_count=10, estimated_duration_seconds=2.0)
    pipeline.save_narration(narration, "sample")
    saved = json.loads((tmp_path / "narration.json").read_text())
    assert saved["title"] == "Venom #2"  # display-only field untouched
    assert saved["scenes"][0]["text"] == "In Venom issue two, it starts."
    assert saved["scenes"][1]["text"].startswith("The F. B. I. loses one thousand men.")
    assert saved["scenes"][0]["word_count"] == len(saved["scenes"][0]["text"].split())
    assert saved["hook"] == "In Venom issue two, it starts."


def test_stage4_normalize_for_tts_is_spoken():
    from stages.stage_4.pipeline import _normalize_for_tts
    assert _normalize_for_tts("Venom #2 in 2015") == "Venom issue two in twenty fifteen"
