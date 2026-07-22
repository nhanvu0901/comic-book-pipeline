"""_demote_backmatter_tail: once a terminal back-matter page (letters/ads/preview)
appears in the BACK half of an issue, every later page — including a preview of ANOTHER
comic the VLM mislabelled 'story' — is demoted to skip. The FRONT story is never touched.
Regression: Transformers (2023) #9 shipped with a G.I. Joe preview (p25-29) matched into
the video because the VLM tagged that real sequential art 'story'."""
from stages.stage_2.pipeline import _demote_backmatter_tail, _demote_credits_pages


def _pg(pn, story, reason="", panels=None):
    return {"page_number": pn,
            "is_story_page": story,
            "page_type": "story" if story else "skip",
            "skip_reason": reason,
            "panels": panels if panels is not None else ([{"index": 0, "bbox": {}}] if story else []),
            "content_hash": ""}


def _story_panels(n):
    """`n` panels each with a real dialog line — strong story signal."""
    return [{"index": i, "bbox": {}, "dialog": [{"text": f"line {i}", "ocr": f"line {i}"}]}
            for i in range(n)]


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


def test_lone_ad_mid_story_does_not_drag_tail(tmp_path):
    """Regression: spiderman-venom-double-trouble p17 is a Stan's Soapbox house-ad page
    sitting MID-issue (already self-demoted to skip at per-page classification time);
    p18-22 right after it are real continuing story (multiple panels, real dialogue).
    A single ad page must demote only ITSELF, not the whole tail — batman-killer-croc hit
    the same bug once already."""
    pages = (
        [_pg(1, False, "cover")]
        + [_pg(n, True, panels=_story_panels(2)) for n in range(2, 17)]   # p2-16 real story
        + [_pg(17, False, "advertisement")]                              # lone mid-issue ad
        + [_pg(n, True, panels=_story_panels(3)) for n in range(18, 23)]  # p18-22 real story
        + [_pg(23, False, "back_matter_tail")]                            # genuine tail (already skip)
        + [_pg(24, False, "advertisement")]                               # genuine tail (last page)
    )
    _demote_backmatter_tail(pages, tmp_path, log=lambda *_: None)
    by = {p["page_number"]: p for p in pages}
    for pn in range(18, 23):
        assert by[pn]["is_story_page"] is True, f"p{pn} wrongly wiped by lone mid-issue ad"
        assert by[pn]["panels"], f"p{pn} panels wrongly cleared"
    assert by[17]["is_story_page"] is False   # the ad page itself stays demoted


def test_terminal_tail_at_very_end_still_demotes(tmp_path):
    """Genuine back-matter at the END of the issue (no real story resuming after it)
    must still demote everything after it — old behaviour preserved."""
    pages = (
        [_pg(1, False, "cover")]
        + [_pg(n, True, panels=_story_panels(2)) for n in range(2, 9)]
        + [_pg(9, False, "letter_column")]
        + [_pg(10, True), _pg(11, True)]   # mislabelled preview, no real panels/dialogue
    )
    _demote_backmatter_tail(pages, tmp_path, log=lambda *_: None)
    by = {p["page_number"]: p for p in pages}
    assert by[10]["is_story_page"] is False and by[10]["skip_reason"] == "back_matter_tail"
    assert by[11]["is_story_page"] is False
    assert by[8]["is_story_page"] is True


def test_two_stories_interior_cover_keeps_second_story(tmp_path):
    """Regression: AvX: VS #5 is two fights in one issue — Hawkeye/Angel (p3-12) then
    Black Panther/Storm (p13-22), split by an interior title splash (p14). Story 1's last
    page ('WINNER … TO BE CONTINUED') is tagged 'back_matter', and a story-2 opener /
    transition page in between is also non-story — so the positional tail-demoter cut at
    p12 and wiped EVERY story page after it, killing the whole second fight (9/22 story
    pages survived instead of ~18). A terminal reason must NOT start a tail when real
    multi-panel+dialogue story resumes later in the issue, even with a non-story page
    sitting between the terminal page and the resuming story (the one-page lookahead
    missed that gap)."""
    pages = (
        [_pg(1, False, "cover"), _pg(2, False, "recap")]
        + [_pg(n, True, panels=_story_panels(3)) for n in range(3, 12)]    # story 1
        + [_pg(12, False, "back_matter"), _pg(13, False, "back_matter")]   # story-1 end + gap
        + [_pg(14, True, panels=_story_panels(1))]                         # interior title splash
        + [_pg(n, True, panels=_story_panels(4)) for n in range(15, 23)]   # story 2 (main content)
    )
    _demote_backmatter_tail(pages, tmp_path, log=lambda *_: None)
    by = {p["page_number"]: p for p in pages}
    for pn in range(15, 23):
        assert by[pn]["is_story_page"] is True, f"p{pn} (story 2) wrongly demoted"
        assert by[pn]["panels"], f"p{pn} panels wrongly cleared"
    assert by[14]["is_story_page"] is True          # interior splash kept
    for pn in range(3, 12):
        assert by[pn]["is_story_page"] is True       # story 1 untouched


def test_credits_demote_spares_real_story_with_tbc_caption(tmp_path):
    """Regression: spiderman-venom-double-trouble p22 — a real 2-panel scene (Peter
    recoils at the mirror / Eddie bursts out the window) whose final caption is a
    'TO BE CONTINUED!' teaser. `_looks_like_backmatter` matches the phrase regardless
    of what else is on the page; real story content must win over the phrase match."""
    pages = [_pg(22, True, panels=_story_panels(2))]
    pages[0]["panels"][1]["dialog"][0]["text"] += " -- TO BE CONTINUED!"
    pages[0]["panels"][1]["dialog"][0]["ocr"] += " -- TO BE CONTINUED!"
    _demote_credits_pages(pages, tmp_path, log=lambda *_: None)
    assert pages[0]["is_story_page"] is True
    assert pages[0]["panels"]


def test_credits_demote_still_catches_real_credits_page(tmp_path):
    """A genuine single-splash credits/teaser page (no real story dialogue of its own)
    must still be demoted — the guard only protects pages with real story content."""
    pages = [_pg(9, True, panels=[{
        "index": 0, "bbox": {},
        "dialog": [{"text": "WOULD YOU KNOW MORE? TO BE CONTINUED IN THE NEXT ISSUE!", "ocr": ""}],
    }])]
    _demote_credits_pages(pages, tmp_path, log=lambda *_: None)
    assert pages[0]["is_story_page"] is False
    assert pages[0]["skip_reason"] == "credits_title"
