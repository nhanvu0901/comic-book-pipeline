"""Stage 3 reveal-dedup guard: an early beat that foreshadows a late twist is dropped,
while a single legitimate betrayal/sacrifice beat is kept."""
from stages.stage_3.write_script import _dedupe_reveal_beats
from stages.stage_3.schema import Beat


def _beat(bid, name, page, summary, fn="SETUP"):
    return Beat(id=bid, function=fn, name=name, page_refs=[page], key_panels=[],
                summary=summary, cause="", characters_active=[])


def _ids(beats):
    return [b.id for b in beats]


def test_drops_early_foreshadow_of_late_reveal():
    # Same twist (Avengers brokered/traded Banner as a sacrifice) told twice.
    beats = [
        _beat(1, "Banner is Galactus's Herald", 1, "Bruce Banner serves as the Herald of Galactus to save worlds."),
        _beat(2, "Flashback hides the truth", 8,
              "What the flashback hides: the Avengers secretly brokered the deal, trading Banner as a sacrifice to spare Earth."),
        _beat(3, "Hulk fights the Avengers", 13, "The Hulk battles the Avengers strike team at Elysion-3.", "CLIMAX"),
        _beat(4, "Stark confesses", 21,
              "A defeated Stark confesses the Avengers secretly brokered the deal, trading Banner as a sacrifice to protect Earth.", "CLIMAX"),
    ]
    out = _dedupe_reveal_beats(beats, lambda _m: None)
    kept = _ids(out)
    assert 2 not in kept, "early foreshadow beat (p8) should be dropped"
    assert 4 in kept, "late reveal beat (p21) must be kept"
    assert {1, 3}.issubset(set(kept)), "unrelated beats untouched"


def test_keeps_single_betrayal_beat():
    # Only ONE beat mentions a concealed truth -> nothing to dedup, keep it.
    beats = [
        _beat(1, "Setup", 1, "The hero arrives in the city."),
        _beat(2, "The betrayal", 18, "The mentor secretly betrayed the hero all along.", "CLIMAX"),
        _beat(3, "Finale", 22, "The hero defeats the villain.", "LANDING"),
    ]
    out = _dedupe_reveal_beats(beats, lambda _m: None)
    assert _ids(out) == [1, 2, 3]


def test_keeps_two_distinct_reveals():
    # Two different concealed facts (low word overlap) -> both kept.
    beats = [
        _beat(1, "Reveal A", 10, "The king secretly poisoned his own brother to seize the throne."),
        _beat(2, "Reveal B", 20, "The witch concealed a stolen amulet inside the temple vault.", "CLIMAX"),
    ]
    out = _dedupe_reveal_beats(beats, lambda _m: None)
    assert _ids(out) == [1, 2]
