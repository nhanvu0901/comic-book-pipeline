"""Persistent title banner: drawtext escaping + Narration carries banner_title."""
from stages.stage_5.shots import _drawtext_escape
from stages.stage_3.schema import Narration


def test_drawtext_escape_colon_backslash_apostrophe():
    assert _drawtext_escape("a:b") == "a\\:b"
    assert _drawtext_escape("a\\b") == "a\\\\b"
    out = _drawtext_escape("Rogue's Deal")
    assert "'" not in out and "’" in out   # straight apostrophe → curly (won't break text='...')


def test_narration_serializes_banner_title():
    n = Narration(mode="panel_walk", title="Rogue Becomes Herald", hook="...",
                  banner_title="Rogue Kissed A God")
    d = n.to_dict()
    assert d["banner_title"] == "Rogue Kissed A God"


def test_narration_banner_title_defaults_empty():
    n = Narration(mode="m", title="t", hook="h")
    assert n.banner_title == "" and n.to_dict()["banner_title"] == ""
