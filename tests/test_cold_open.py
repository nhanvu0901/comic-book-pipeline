"""Cold-open (#1): open on the largest STORY panel, not the cover — and never the
final pages (no ending spoiler)."""
from stages.stage_5.shots import _cold_open_panel


def _panel(w, h):
    return {"bbox": {"x": 0, "y": 0, "w": w, "h": h}}


def _pg(panels, src="p.png"):
    return {"panels": panels, "source_image": src,
            "image_dimensions": {"width": 1000, "height": 1500}}


def test_cold_open_picks_largest_non_ending_panel():
    pages = {
        1: _pg([_panel(400, 400)]),
        2: _pg([_panel(900, 1400), _panel(200, 200)]),   # biggest eligible panel
        3: _pg([_panel(500, 500)]),
        4: _pg([_panel(999, 1499)]),   # last 2 = ending → excluded even though huge
        5: _pg([_panel(999, 1499)]),
    }
    panel, src = _cold_open_panel(pages)
    assert panel is not None
    assert panel["_page_number"] == 2 and panel["bbox"]["w"] == 900
    assert src == "p.png"


def test_cold_open_skips_cover_and_ending():
    pages = {
        1: {"panels": [_panel(999, 1499)], "page_type": "cover", "source_image": "c.png"},
        2: _pg([_panel(400, 400)]),
        3: _pg([_panel(800, 800)]),   # biggest of the eligible (2,3)
        4: _pg([_panel(999, 1499)]),  # ending excluded
        5: _pg([_panel(999, 1499)]),  # ending excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None and panel["_page_number"] == 3   # cover skipped, ending excluded


def test_cold_open_empty_returns_none():
    assert _cold_open_panel({}) == (None, "")
