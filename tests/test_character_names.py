"""Regression: generate_intro crashed with
`TypeError: sequence item 0: expected str instance, dict found` when
comic_context['characters'] held {name,...} dicts (hand-built / summary shape)
instead of plain name strings. _character_names must accept BOTH shapes and
never raise — see stages/stage_3/write_script.py."""
from stages.stage_3.write_script import _character_names


def test_string_list():
    assert _character_names({"characters": ["Hulk", "Thor"]}) == ["Hulk", "Thor"]


def test_dict_list_extracts_names():
    # the exact crash case: a list of dicts
    ctx = {"characters": [{"name": "The Grim Knight"}, {"name": "Jim Gordon"}]}
    assert _character_names(ctx) == ["The Grim Knight", "Jim Gordon"]


def test_mixed_and_skips_empty():
    ctx = {"characters": ["Hulk", {"name": "Thor"}, {"name": ""}, "   ", {}, {"role": "x"}]}
    assert _character_names(ctx) == ["Hulk", "Thor"]


def test_missing_or_empty_key():
    assert _character_names({}) == []
    assert _character_names({"characters": []}) == []
    assert _character_names({"characters": None}) == []


def test_join_never_raises_on_dicts():
    # the failure mode was at the call site: ", ".join(...) over dicts
    ctx = {"characters": [{"name": "A"}, {"name": "B"}]}
    assert ", ".join(_character_names(ctx)) == "A, B"
