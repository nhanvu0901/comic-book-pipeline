"""build_answer_beats: deterministic (no LLM) beat-per-item outline for the
explore_answer (Q&A) writer mode. One Beat per answer-research item, anchored
to that item's source-issue pages via the ch{NN}_page chapter prefix."""
from stages.stage_3.explore_answer import build_answer_beats


def _page(page_number, chapter, page_in_chapter):
    return {
        "page_number": page_number,
        "source_image": f"ch{chapter:02d}_page{page_in_chapter:02d}.png",
        "is_story_page": True,
    }


def test_build_answer_beats_order_and_function():
    comic_context = {"title": "Who has beaten Superman?", "is_arc": True}
    answer_context = {
        "items": [
            {"entity": "Wolverine", "how_or_why": "He healed through it.",
             "source_comic": "X-Men Legacy #1", "chapter_index": 1},
            {"entity": "Deadpool", "how_or_why": "He regenerated fast enough.",
             "source_comic": "Deadpool #2", "chapter_index": 2},
            {"entity": "Thanos", "how_or_why": "He simply endured it.",
             "source_comic": "Thanos #3", "chapter_index": 3},
        ]
    }
    story_pages = [
        _page(1, 1, 1), _page(2, 1, 2),
        _page(3, 2, 1), _page(4, 2, 2),
        _page(5, 3, 1), _page(6, 3, 2),
    ]

    beats = build_answer_beats(comic_context, answer_context, story_pages)

    assert [b.id for b in beats] == [1, 2, 3]
    assert [b.function for b in beats] == ["COLD_OPEN", "SETUP", "LANDING"]
    assert [b.name for b in beats] == ["Wolverine", "Deadpool", "Thanos"]
    # anchor page = earliest page of that item's chapter
    assert beats[0].page_refs == [1]
    assert beats[1].page_refs == [3]
    assert beats[2].page_refs == [5]
    assert beats[0].cause == "He healed through it."


def test_beats_keep_the_download_order_even_when_items_carry_surprise_order():
    """The download order is the order of everything: item N was downloaded as chapter N
    and is narrated as beat N. A stray surprise_order field must not re-sort the beats."""
    comic_context = {"title": "Q?"}
    answer_context = {
        "items": [
            {"entity": "A", "how_or_why": "a", "surprise_order": 2},
            {"entity": "B", "how_or_why": "b", "surprise_order": 1},
        ]
    }
    story_pages = [_page(10, 1, 1), _page(20, 2, 1)]

    beats = build_answer_beats(comic_context, answer_context, story_pages)

    assert [b.name for b in beats] == ["A", "B"]
    assert beats[0].page_refs == [10]
    assert beats[1].page_refs == [20]
    assert beats[0].function == "COLD_OPEN"
    assert beats[1].function == "LANDING"


def test_beat_chapter_comes_from_the_items_own_urls():
    """Two items citing one issue share its chapter, whatever comic_context says."""
    u1, u2 = "https://batcave.biz/reader/1/11", "https://batcave.biz/reader/2/22"
    answer_context = {"items": [
        {"entity": "A", "how_or_why": "a", "reader_url": u1},
        {"entity": "B", "how_or_why": "b", "reader_url": u2},
        {"entity": "C", "how_or_why": "c", "reader_url": u1},
    ]}
    story_pages = [_page(10, 1, 1), _page(20, 2, 1)]
    beats = build_answer_beats({"reader_urls": []}, answer_context, story_pages)
    assert [b.page_refs for b in beats] == [[10], [20], [10]]


def test_no_items_returns_empty():
    assert build_answer_beats({}, {"items": []}, []) == []


def test_missing_chapter_pages_falls_back_to_page_zero():
    answer_context = {"items": [{"entity": "Ghost", "how_or_why": "x", "chapter_index": 5}]}
    beats = build_answer_beats({}, answer_context, [])
    assert beats[0].page_refs == []


if __name__ == "__main__":
    test_build_answer_beats_order_and_function()
    test_beats_keep_the_download_order_even_when_items_carry_surprise_order()
    test_no_items_returns_empty()
    test_missing_chapter_pages_falls_back_to_page_zero()
    print("ok")
