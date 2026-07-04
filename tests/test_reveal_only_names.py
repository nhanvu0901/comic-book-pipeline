"""Stage 3 concealed-identity guard: names that appear ONLY in the late (reveal)
scenes are flagged so the intro/banner can't spoil the twist — while the presented
identity (named from the first scene) is never flagged."""
from stages.stage_3.write_script import _reveal_only_names


def _sc(text, **kw):
    d = {"text": text}
    d.update(kw)
    return d


def test_flags_late_reveal_name_not_presented_identity():
    # He is called "Doom" (presented identity) from scene 1; "Reed Richards" is the
    # withheld truth, named only in the final scenes.
    scenes = [
        _sc("Intro line", is_intro=True),
        _sc("In the ruins of 2099, Doctor Doom woke with no memory."),
        _sc("Thorites dragged him across the Ravage to sacrifice him."),
        _sc("He crushed them and swore he was Victor Von Doom."),
        _sc("The Tinkerer forged him crude armor from scrap."),
        _sc("He marched on the floating castle to take back his name."),
        _sc("The two Dooms met, and the real Doom overwhelmed him."),
        _sc("Doom tore off the armor and named him aloud: Reed Richards."),
        _sc("He hurled Reed from the window; his body stretched across the sky."),
        _sc("The comic is Doom 2099.", is_outro=True),
    ]
    reveal = _reveal_only_names(scenes)
    assert "Reed" in reveal and "Richards" in reveal, "late-only true name must be flagged"
    assert "Doom" not in reveal, "presented identity (named from scene 1) must NOT be flagged"
    assert "Thorites" not in reveal and "Tinkerer" not in reveal, "early names not flagged"


def test_short_narration_flags_nothing():
    assert _reveal_only_names([_sc("a"), _sc("b"), _sc("c")]) == set()


if __name__ == "__main__":
    test_flags_late_reveal_name_not_presented_identity()
    test_short_narration_flags_nothing()
    print("ok")
