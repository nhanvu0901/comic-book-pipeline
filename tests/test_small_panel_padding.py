"""Stage 5 small-panel padding: non-highlight panels get a 30% crop margin so the
whole panel shows; highlights (>=40% of page) keep the tight 5% margin."""
from stages.stage_5.shots import _pad_pct_for, PADDING_PCT, SMALL_PANEL_PAD_PCT

PAGE_W, PAGE_H = 1988, 3056  # typical comic page


def test_small_panel_gets_wide_margin():
    # ~12% of the page -> small -> 30% per-side margin.
    assert _pad_pct_for(700, 600, PAGE_W, PAGE_H) == SMALL_PANEL_PAD_PCT


def test_highlight_panel_keeps_tight_margin():
    # Full-page splash (~99%) -> highlight -> tight 5%.
    assert _pad_pct_for(1983, 3047, PAGE_W, PAGE_H) == PADDING_PCT


def test_threshold_just_below_is_small():
    # 39% of the page -> still below the 40% highlight line -> wide margin.
    area = 0.39 * PAGE_W * PAGE_H
    w = int(area ** 0.5); h = int(area / w)
    assert _pad_pct_for(w, h, PAGE_W, PAGE_H) == SMALL_PANEL_PAD_PCT


def test_threshold_above_is_highlight():
    # 50% of the page -> highlight -> tight margin.
    area = 0.50 * PAGE_W * PAGE_H
    w = int(area ** 0.5); h = int(area / w)
    assert _pad_pct_for(w, h, PAGE_W, PAGE_H) == PADDING_PCT
