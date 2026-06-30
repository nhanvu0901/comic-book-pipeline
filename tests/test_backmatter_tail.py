"""_demote_backmatter_tail: once a terminal back-matter page (letters/ads/preview)
appears in the BACK half of an issue, every later page — including a preview of ANOTHER
comic the VLM mislabelled 'story' — is demoted to skip. The FRONT story is never touched.
Regression: Transformers (2023) #9 shipped with a G.I. Joe preview (p25-29) matched into
the video because the VLM tagged that real sequential art 'story'."""
from stages.stage_2.pipeline import _demote_backmatter_tail


def _pg(pn, story, reason=""):
    return {"page_number": pn,
            "is_story_page": story,
            "page_type": "story" if story else "skip",
            "skip_reason": reason,
            "panels": [{"index": 0, "bbox": {}}] if story else [],
            "content_hash": ""}


def test_tail_demotes_previews_after_letters(tmp_path):
    pages = [
        _pg(1, False, "cover"),
        _pg(10, True), _pg(23, True),          # real story
        _pg(24, False, "letter_column"),       # terminal back-matter, back half
        _pg(25, True), _pg(26, True),          # OTHER comic's preview, mislabelled story
        _pg(30, False, "advertisement"),
    ]
    _demote_backmatter_tail(pages, tmp_path, log=lambda *_: None)
    by = {p["page_number"]: p for p in pages}
    assert by[25]["is_story_page"] is False and by[25]["skip_reason"] == "back_matter_tail"
    assert by[26]["is_story_page"] is False and by[26]["panels"] == []
    assert by[23]["is_story_page"] is True     # front story untouched
    assert by[10]["is_story_page"] is True


def test_tail_ignores_front_backmatter(tmp_path):
    # A back-matter reason near the FRONT (below the half cutoff) must NOT trigger.
    pages = [_pg(1, False, "letter_column")] + [_pg(n, True) for n in range(2, 7)]
    _demote_backmatter_tail(pages, tmp_path, log=lambda *_: None)
    assert all(p["is_story_page"] for p in pages if p["page_number"] >= 2)


def test_tail_noop_without_terminal(tmp_path):
    pages = [_pg(1, False, "cover"), _pg(2, True), _pg(3, True)]
    _demote_backmatter_tail(pages, tmp_path, log=lambda *_: None)
    assert all(p["is_story_page"] for p in pages if p["page_number"] >= 2)
