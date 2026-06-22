"""Mirror guard (2026-06-19): Stage 5 mirrors every panel for dedup, but a panel
whose ART contains readable story-critical text (a gravestone, a sign, a nameplate)
must NOT be flipped — mirroring reverses the letters and breaks the reveal (the
'PETER' gravestone payoff in Weapon VIII / Edge of Spider-Verse #1)."""
from stages.stage_5.shots import _panel_has_critical_text


def test_gravestone_panel_skips_mirror():
    panel = {"description": "A close-up shot shows a stone gravestone engraved with the name 'PETER'."}
    assert _panel_has_critical_text(panel) is True


def test_sign_and_nameplate_skip_mirror():
    assert _panel_has_critical_text({"description": "a sign reading DANGER hangs over the door"})
    assert _panel_has_critical_text({"description": "a brass nameplate on the desk"})
    assert _panel_has_critical_text({"description": "his dog tag stamped LOGAN"})


def test_ordinary_panel_is_mirrored():
    panel = {"description": "Weapon VIII sitting shirtless and scarred while small robots crawl nearby"}
    assert _panel_has_critical_text(panel) is False


def test_missing_or_empty_description():
    assert _panel_has_critical_text(None) is False
    assert _panel_has_critical_text({}) is False
    assert _panel_has_critical_text({"description": ""}) is False
