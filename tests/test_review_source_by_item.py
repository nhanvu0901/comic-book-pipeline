"""The review gate cites (and searches with) the answer item a beat belongs to.

Two items can cite the same issue; they then share one downloaded chapter, so the chapter
label alone names only the FIRST of them. The beat id (= item number under the fixed item
order) tells them apart. A beat id that disagrees with the page's chapter is ignored, so an
older narration can never be pointed at the wrong item."""
from stages.review_gate import _beat_source
from stages.user_errors import UserFacingError
from ui.bridge import format_exception

A, B = "https://batcave.biz/reader/1/11", "https://batcave.biz/reader/2/22"
ANSWER = {"items": [
    {"entity": "Deadpool", "source_comic": "Issue A", "reader_url": A, "drawable_moment": "dp moment"},
    {"entity": "Black Panther", "source_comic": "Issue B", "reader_url": B, "drawable_moment": "bp moment"},
    {"entity": "Cable", "source_comic": "Issue A", "reader_url": A, "drawable_moment": "cable moment"},
]}


def test_beat_of_the_second_item_sharing_an_issue_cites_that_item():
    src = _beat_source({"beat_id": 3, "page_ref": 2}, {}, ANSWER, issue_label="#1")
    assert src["drawable_moment"] == "cable moment"


def test_first_item_of_a_shared_issue_is_unchanged():
    src = _beat_source({"beat_id": 1, "page_ref": 2}, {}, ANSWER, issue_label="#1")
    assert src["drawable_moment"] == "dp moment"


def test_beat_id_that_disagrees_with_the_chapter_is_ignored():
    src = _beat_source({"beat_id": 2, "page_ref": 2}, {}, ANSWER, issue_label="#1")
    assert src["drawable_moment"] == "dp moment"


def test_user_facing_errors_show_their_message_without_a_traceback():
    class _Mapped(UserFacingError, ValueError):
        pass

    shown = format_exception(_Mapped("item #2 was not downloaded"))
    assert shown == "item #2 was not downloaded"
    try:
        {}["boom"]
    except KeyError as exc:
        assert "Traceback" in format_exception(exc)
