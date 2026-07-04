"""Identity-based VLM response mapping (2026-07-02): a page/panel description must
be matched back to its bbox by the model's OWN echoed page_index/index, never by
list position. A dropped or reordered entry that keeps the list COUNT the same
(padding elsewhere) used to shift every later page/panel onto the wrong neighbor's
description — the real corruption seen on doom-2099 (a castle description on a
ghost-figure panel, a "call me Victor" line on a Doom-helmet panel). Pure-parsing
tests only — no VLM/network calls."""
from stages.stage_2.pipeline import _map_vlm_entries
from stages.stage_2.vlm_extract import _map_batch_pages


# ─── _map_batch_pages (page-level, keyed by page_index) ───────────────────────

def test_batch_pages_complete_map_correctly():
    pages_out = [
        {"page_index": 0, "page_summary": "p0"},
        {"page_index": 1, "page_summary": "p1"},
        {"page_index": 2, "page_summary": "p2"},
    ]
    mapped = _map_batch_pages(pages_out, 3)
    assert [p["page_summary"] for p in mapped] == ["p0", "p1", "p2"]


def test_batch_pages_missing_middle_fails_whole_batch_not_shift():
    # Model drops page_index=1 but pads the count back to 3 by duplicating page 2 —
    # exactly the "same count, wrong content" shift a bare len() check would miss.
    pages_out = [
        {"page_index": 0, "page_summary": "p0"},
        {"page_index": 2, "page_summary": "p2-first"},
        {"page_index": 2, "page_summary": "p2-dup"},
    ]
    assert _map_batch_pages(pages_out, 3) is None  # untrustworthy → caller falls back


def test_batch_pages_out_of_range_dropped_and_logged():
    logged = []
    pages_out = [
        {"page_index": 0, "page_summary": "p0"},
        {"page_index": 5, "page_summary": "bogus"},
        {"page_index": 1, "page_summary": "p1"},
    ]
    assert _map_batch_pages(pages_out, 2, log=logged.append) is not None
    mapped = _map_batch_pages(pages_out, 2)
    assert [p["page_summary"] for p in mapped] == ["p0", "p1"]
    assert any("out of range" in m for m in logged)


# ─── _map_vlm_entries (panel-level, keyed by index) ────────────────────────────

def test_panel_entries_complete_map_correctly():
    entries = [
        {"index": 0, "description": "a"},
        {"index": 1, "description": "b"},
        {"index": 2, "description": "c"},
    ]
    mapped = _map_vlm_entries(entries, 3)
    assert [mapped[i]["description"] for i in range(3)] == ["a", "b", "c"]


def test_panel_entries_missing_middle_no_shift():
    # Panel 1's entry never came back; panel 2's description must NOT slide into slot 1.
    entries = [
        {"index": 0, "description": "a"},
        {"index": 2, "description": "c"},
    ]
    mapped = _map_vlm_entries(entries, 3)
    assert mapped[0]["description"] == "a"
    assert 1 not in mapped                    # dropped, not backfilled from a neighbor
    assert mapped[2]["description"] == "c"    # stays on ITS OWN panel


def test_panel_entries_out_of_range_dropped_with_warning():
    logged = []
    entries = [
        {"index": 0, "description": "a"},
        {"index": 7, "description": "bogus"},
    ]
    mapped = _map_vlm_entries(entries, 1, page_number=12, log=logged.append)
    assert list(mapped) == [0]
    assert any("page 12" in m and "out of range" in m for m in logged)


def test_panel_entries_duplicate_index_keeps_first_drops_rest():
    logged = []
    entries = [
        {"index": 0, "description": "first"},
        {"index": 0, "description": "second"},
    ]
    mapped = _map_vlm_entries(entries, 1, log=logged.append)
    assert mapped[0]["description"] == "first"
    assert any("duplicate" in m for m in logged)
