from stages.stage_3.write_script import _append_loop_tease


def test_append_joins_closure_and_tease():
    out = _append_loop_tease("The comic is Hulk: The End.", "But what he became next is the real horror.")
    assert out == "The comic is Hulk: The End. But what he became next is the real horror."


def test_append_no_tease_returns_closure_unchanged():
    assert _append_loop_tease("The comic is Hulk: The End.", "") == "The comic is Hulk: The End."


def test_append_strips_double_space():
    out = _append_loop_tease("Power costs everything.  ", "  And he paid in full.")
    assert out == "Power costs everything. And he paid in full."
