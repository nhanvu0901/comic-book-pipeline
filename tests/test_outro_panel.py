"""_outro_panel: the thematic outro line should land on a striking FOCAL panel from the
CLOSING third, NOT the final whole-page splash (which renders cluttered, no clear subject).
Regression: Deadpool/Batman #1 outro landed on p31, a whole-page talky splash."""
from stages.stage_5.shots import _outro_panel


def _page(pn, panels):
    return {"page_number": pn, "is_story_page": True, "page_type": "story",
            "source_image": f"p{pn}.png", "image_dimensions": {"width": 1000, "height": 1500},
            "panels": panels}


def test_outro_skips_whole_page_splash():
    pages = {
        3: _page(3, [{"bbox": {"x": 0, "y": 0, "w": 500, "h": 500}, "dialog": []}]),
        10: _page(10, [{"bbox": {"x": 0, "y": 0, "w": 600, "h": 500}, "dialog": []}]),   # focal
        11: _page(11, [{"bbox": {"x": 0, "y": 0, "w": 1000, "h": 1500},                  # whole-page
                        "dialog": [{"text": "wait!"}]}]),
    }
    p, src = _outro_panel(pages)
    assert p is not None
    assert p["_page_number"] == 10                 # the focal closing panel, not p11 splash
    assert p["bbox"]["w"] * p["bbox"]["h"] < 1000 * 1500


def test_outro_prefers_low_text_among_closing():
    pages = {
        20: _page(20, [{"bbox": {"x": 0, "y": 0, "w": 700, "h": 600}, "dialog": [{"text": "a"}] * 8}]),  # big but text-wall
        21: _page(21, [{"bbox": {"x": 0, "y": 0, "w": 650, "h": 600}, "dialog": []}]),                    # slightly smaller, clean
    }
    p, _ = _outro_panel(pages)
    assert p["_page_number"] == 21                 # clean low-text panel wins the tie-break


def test_outro_none_when_no_story_pages():
    assert _outro_panel({}) == (None, "")
