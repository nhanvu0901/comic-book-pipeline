"""Stage 3 outline validation: a beat spanning too many pages swallows whatever
happens on them (e.g. a multi-page confrontation eating the actual fight, leaving
it unnarrated). `_validate_outline` must flag beats spanning > 4 pages."""
from stages.stage_3.write_script import _validate_outline
from stages.stage_3.schema import Beat


def _beat(bid, page_refs, fn="SETUP"):
    return Beat(id=bid, function=fn, name=f"b{bid}", page_refs=page_refs, key_panels=[],
                summary="event", cause="", characters_active=[])


def test_flags_beat_spanning_too_many_pages():
    beats = [_beat(1, list(range(22, 30)), "CLIMAX")]  # 8 distinct pages
    issues = _validate_outline(beats)
    assert any("too broad" in iss and "beat 1" in iss for iss in issues)


def test_does_not_flag_four_page_beat():
    beats = [_beat(1, [22, 23, 24, 25], "CLIMAX")]
    issues = _validate_outline(beats)
    assert not any("too broad" in iss for iss in issues)


if __name__ == "__main__":
    test_flags_beat_spanning_too_many_pages()
    test_does_not_flag_four_page_beat()
    print("ok")
