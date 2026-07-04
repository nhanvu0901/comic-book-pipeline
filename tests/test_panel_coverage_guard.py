"""Feature G — Magi coverage guard.

stages.stage_2.pipeline._apply_coverage_guard flags a story page where the sum of Magi
panel bbox areas covers suspiciously little of the page (< _COVERAGE_MIN) — a sign Magi
MISSED panels. Flag-only (page-level `panel_coverage_low`), no behaviour change.

Pure logic — fake page dicts, no network.
"""
import stages.stage_2.pipeline as pipe
from stages.stage_2.pipeline import _apply_coverage_guard


def _page(panels, *, w=1000, h=1000, page_type="story"):
    return {"page_number": 5, "page_type": page_type,
            "image_dimensions": {"width": w, "height": h}, "panels": panels}


def test_flags_low_coverage():
    # One 300x300 panel on a 1000x1000 page = 9% coverage → flagged.
    page = _page([{"bbox": {"x": 0, "y": 0, "w": 300, "h": 300}}])
    _apply_coverage_guard(page, log=lambda *_a: None)
    assert page["panel_coverage_low"] is True


def test_no_flag_on_good_coverage():
    # Two panels covering ~85% of the page → not flagged.
    page = _page([
        {"bbox": {"x": 0, "y": 0, "w": 1000, "h": 500}},
        {"bbox": {"x": 0, "y": 500, "w": 700, "h": 500}},
    ])
    _apply_coverage_guard(page, log=lambda *_a: None)
    assert "panel_coverage_low" not in page


def test_no_flag_when_no_panels():
    page = _page([])
    _apply_coverage_guard(page, log=lambda *_a: None)
    assert "panel_coverage_low" not in page


def test_no_flag_on_non_story_page():
    page = _page([{"bbox": {"x": 0, "y": 0, "w": 50, "h": 50}}], page_type="cover")
    _apply_coverage_guard(page, log=lambda *_a: None)
    assert "panel_coverage_low" not in page


def test_no_flag_on_zero_page_area():
    page = _page([{"bbox": {"x": 0, "y": 0, "w": 50, "h": 50}}], w=0, h=0)
    _apply_coverage_guard(page, log=lambda *_a: None)
    assert "panel_coverage_low" not in page


def test_respects_kill_switch(monkeypatch):
    monkeypatch.setattr(pipe, "COVERAGE_GUARD", False)
    page = _page([{"bbox": {"x": 0, "y": 0, "w": 10, "h": 10}}])
    _apply_coverage_guard(page, log=lambda *_a: None)
    assert "panel_coverage_low" not in page
