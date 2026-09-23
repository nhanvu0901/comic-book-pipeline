from dataclasses import dataclass
import pytest
from stages.stage_3.gemini_prompt import _align_paras_to_beats, parse_and_save_script


@dataclass
class DummyBeat:
    id: int
    name: str
    characters_active: list
    summary: str
    page_refs: list
    function: str = "SETUP"
    key_panels: list = None
    cause: str = ""


def test_align_paras_to_beats_detects_reordering():
    paras = [
        "Doctor Doom hijacks the body in an alternate reality. Emma Frost uses mind transfer.",
        "In the Ultimate universe, Kitty Pryde grows giant while Thor forces Galactus into Negative Zone.",
        "Later, Thor drains the Power Cosmic and detonates him like a bomb to kill the Black Winter.",
    ]
    beats = [
        DummyBeat(1, "The Devourer King", ["The Devourer King"], "Thor stripped Galactus of Power Cosmic and detonated him as a bomb against Black Winter.", [1]),
        DummyBeat(2, "Cataclysm", ["Cataclysm"], "Kitty Pryde and Thor force Galactus into Negative Zone.", [25]),
        DummyBeat(3, "Marvel 2-In-One", ["Marvel 2-In-One"], "Doom defeats Galactus by taking over body after Reed fails.", [45]),
    ]

    aligned = _align_paras_to_beats(paras, beats)
    assert len(aligned) == 3
    # Paragraph 0 (Doom) should match Beat 3 (Marvel 2-In-One, page 45)
    assert aligned[0].name == "Marvel 2-In-One"
    assert aligned[0].page_refs == [45]
    # Paragraph 1 (Kitty/Negative Zone) should match Beat 2 (Cataclysm, page 25)
    assert aligned[1].name == "Cataclysm"
    assert aligned[1].page_refs == [25]
    # Paragraph 2 (Thor/Bomb/Black Winter) should match Beat 1 (Devourer King, page 1)
    assert aligned[2].name == "The Devourer King"
    assert aligned[2].page_refs == [1]


def test_align_paras_to_beats_preserves_sequential_order_when_already_matching():
    paras = [
        "First story about Thor defeating the entity.",
        "Second story about Kitty Pryde in the dimension.",
        "Third story about Doctor Doom taking over.",
    ]
    beats = [
        DummyBeat(1, "Thor Story", ["Thor"], "Thor defeats the entity directly.", [1]),
        DummyBeat(2, "Kitty Story", ["Kitty Pryde"], "Kitty Pryde in the prison dimension.", [25]),
        DummyBeat(3, "Doom Story", ["Doctor Doom"], "Doctor Doom taking over.", [45]),
    ]

    aligned = _align_paras_to_beats(paras, beats)
    assert [b.id for b in aligned] == [1, 2, 3]
