"""Stage 3 ending-coverage gate: when the outline stops short of the plot's final
on-page event, a LANDING beat for it is appended so the finale lands. When the
ending is already covered, nothing is added."""
from stages.stage_3.write_script import _ensure_ending_coverage
from stages.stage_3.schema import Beat


def _beat(bid, summary, fn="SETUP"):
    return Beat(id=bid, function=fn, name=f"b{bid}", page_refs=[bid], key_panels=[],
                summary=summary, cause="", characters_active=[])


_PLOT = (
    "A scarred amnesiac wakes in 2099 believing he is Doctor Doom. He fights his way "
    "to the castle. Flashbacks reveal Reed Richards was dragged to 2099 on Doom's time "
    "platform. The real Doom strips his crude armor and hurls him out a window. "
    "The issue ends with Reed plummeting, his elastic stretching powers becoming "
    "visibly apparent, his survival deliberately ambiguous."
)
_PAGES = [{"page_number": n} for n in range(1, 33)]


def test_appends_landing_when_ending_uncovered():
    # Outline ends on the flashback's close (arrived in 2099) — no plummet/stretch.
    beats = [
        _beat(1, "Amnesiac wakes in the Ravage believing he is Doom", "COLD_OPEN"),
        _beat(2, "He fights the Thorite cult"),
        _beat(3, "He forges crude armor"),
        _beat(4, "He reaches the floating castle"),
        _beat(5, "Flashback: Reed warned Doom of the war"),
        _beat(6, "The collapsing castle hurled both to 2099; Victor arrived intact", "LANDING"),
    ]
    out = _ensure_ending_coverage(beats, {"plot_summary": _PLOT}, _PAGES, lambda _m: None)
    assert len(out) == 7, "a LANDING beat for the final event should be appended"
    added = out[-1]
    assert added.function == "LANDING"
    assert "plummet" in added.summary.lower() and "stretch" in added.summary.lower()
    assert added.page_refs == [32], "grounded to the last story page"


def test_no_append_when_ending_covered():
    beats = [
        _beat(1, "Amnesiac wakes believing he is Doom", "COLD_OPEN"),
        _beat(2, "He fights the Thorite cult"),
        _beat(3, "He forges crude armor"),
        _beat(4, "He reaches the floating castle"),
        _beat(5, "Doom strips his armor and hurls him out a window"),
        _beat(6, "Reed plummets, his elastic stretching powers visibly apparent, survival ambiguous", "LANDING"),
    ]
    out = _ensure_ending_coverage(beats, {"plot_summary": _PLOT}, _PAGES, lambda _m: None)
    assert len(out) == 6, "ending already lands — nothing appended"


if __name__ == "__main__":
    test_appends_landing_when_ending_uncovered()
    test_no_append_when_ending_covered()
    print("ok")
