"""remap_locks_by_src — fixes a locked panel's stale `page` after the project's GLOBAL page
numbering shifts (Master swaps a mid-story issue for a shorter/longer one → every LATER
chapter's page number moves; review/locks.json still holds the OLD page number). See
stages/review_gate.py::remap_locks_by_src for the full contract.
"""
import stages.review_gate as rg


def test_remap_finds_new_page_when_src_moved():
    """(a) lock has src, its page drifted 68 -> 60 (issue swap) -> remapped to 60."""
    locks = {"5": {"panels": [{"page": 68, "panel": 2, "src": "ch03_page_08.jpg"}],
                   "source": "batcave"}}
    src_by_page = {68: "ch03_page_16.jpg",   # page 68 now holds DIFFERENT art
                   60: "ch03_page_08.jpg"}   # the locked art now lives on page 60
    out = rg.remap_locks_by_src(locks, src_by_page)
    panel = out["5"]["panels"][0]
    assert (panel["page"], panel["panel"], panel["src"]) == (60, 2, "ch03_page_08.jpg")


def test_remap_noop_when_page_unchanged():
    """(b) lock has src, page did NOT move -> untouched (same object, no needless rewrite)."""
    locks = {"5": {"panels": [{"page": 68, "panel": 2, "src": "ch03_page_08.jpg"}],
                   "source": "batcave"}}
    src_by_page = {68: "ch03_page_08.jpg"}
    out = rg.remap_locks_by_src(locks, src_by_page)
    assert out == locks
    assert out["5"] is locks["5"]


def test_remap_noop_without_src_stamp_byte_identical():
    """(c) old lock with NO "src" (pre-fix) -> passed through byte-identical, even though its
    page number would otherwise look "stale" against src_by_page. Absolute backward compat."""
    locks = {"5": {"panels": [{"page": 68, "panel": 2}], "source": "batcave"}}
    src_by_page = {60: "ch03_page_08.jpg"}   # page 68 isn't even in the current pool
    out = rg.remap_locks_by_src(locks, src_by_page)
    assert out == locks
    assert out["5"]["panels"][0] is locks["5"]["panels"][0]


def test_remap_keeps_stale_page_when_src_not_found_anywhere():
    """(d) src no longer exists ANYWHERE in the current page pool -> keep the stale page,
    never raise (nothing sane to remap to)."""
    locks = {"5": {"panels": [{"page": 68, "panel": 2, "src": "ch03_page_08.jpg"}],
                   "source": "batcave"}}
    src_by_page = {68: "ch03_page_16.jpg"}   # "ch03_page_08.jpg" isn't in the pool at all
    out = rg.remap_locks_by_src(locks, src_by_page)
    assert out["5"]["panels"][0]["page"] == 68


def test_remap_passes_through_non_panel_lock_shapes():
    """v1 legacy ({"page","panel"} at top level, no "panels" list) and v3 custom-image
    ({"custom_image": ...}) locks are untouched — remap only ever touches a v2 "panels" list."""
    locks = {"intro": {"page": 3, "panel": 1, "source": "batcave"},
             "7": {"custom_image": "review/custom/x.jpg", "source": "custom"}}
    out = rg.remap_locks_by_src(locks, {3: "ch01_page_04.jpg"})
    assert out == locks
