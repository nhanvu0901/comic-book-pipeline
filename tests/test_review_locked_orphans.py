"""A locked panel must always get a tile, even when candidates.json no longer lists it.

Regression: candidates.json rebuilt with a cap (build_candidates k>0) AFTER locks were
written dropped every lock outside the first k panels out of the gallery. The beat card then
read as "nothing picked" and the next click overwrote Master's lock with no warning.
"""
from ui.screens.s_review_gate import splice_locked_orphans


def _cands(*pairs):
    return [{"page": p, "panel": n, "thumb": f"review/thumbs/p{p:03d}_{n}.jpg"}
            for p, n in pairs]


def test_lock_inside_candidates_changes_nothing():
    cands = _cands((1, 0), (2, 0))
    out = splice_locked_orphans(cands, {"panels": [{"page": 2, "panel": 0}]})
    assert out == cands


def test_lock_outside_candidates_gets_spliced_in():
    out = splice_locked_orphans(_cands((1, 0), (2, 0)),
                                {"panels": [{"page": 29, "panel": 3}]})
    assert (29, 3) in {(c["page"], c["panel"]) for c in out}
    assert len(out) == 3
    orphan = next(c for c in out if c["page"] == 29)
    assert orphan["thumb"] == "review/thumbs/p029_3.jpg"


def test_multi_panel_lock_splices_only_the_missing_ones():
    out = splice_locked_orphans(
        _cands((1, 0)),
        {"panels": [{"page": 1, "panel": 0}, {"page": 41, "panel": 0}]})
    assert len(out) == 2
    assert (41, 0) in {(c["page"], c["panel"]) for c in out}


def test_v1_single_panel_lock_shape():
    out = splice_locked_orphans(_cands((1, 0)), {"page": 7, "panel": 2})
    assert (7, 2) in {(c["page"], c["panel"]) for c in out}


def test_custom_image_lock_adds_no_tile():
    """A v3 custom-image lock has no page/panel — it must not synthesise a bogus tile."""
    cands = _cands((1, 0))
    out = splice_locked_orphans(cands, {"custom_image": "review/custom/x.png",
                                        "source": "custom"})
    assert out == cands


def test_no_lock_and_empty_inputs():
    assert splice_locked_orphans(_cands((1, 0)), None) == _cands((1, 0))
    assert splice_locked_orphans([], None) == []
    assert splice_locked_orphans([], {"panels": [{"page": 5, "panel": 1}]}) == [
        {"page": 5, "panel": 1, "thumb": "review/thumbs/p005_1.jpg"}]


def test_duplicate_panel_in_lock_tiles_once():
    out = splice_locked_orphans([], {"panels": [{"page": 5, "panel": 1},
                                                {"page": 5, "panel": 1}]})
    assert len(out) == 1


def test_caller_list_not_mutated():
    cands = _cands((1, 0))
    splice_locked_orphans(cands, {"panels": [{"page": 99, "panel": 0}]})
    assert cands == _cands((1, 0))


def test_hidden_panel_is_never_resurrected():
    """The hide blacklist beats a lock. A lock can be re-created automatically (source
    "pre_selected"), so without this a hidden panel crawls back onto the card next open."""
    out = splice_locked_orphans(_cands((1, 0)),
                                {"panels": [{"page": 14, "panel": 0}],
                                 "source": "pre_selected"},
                                {(14, 0)})
    assert (14, 0) not in {(c["page"], c["panel"]) for c in out}
    assert len(out) == 1


def test_hidden_only_blocks_the_hidden_one():
    out = splice_locked_orphans(_cands((1, 0)),
                                {"panels": [{"page": 14, "panel": 0},
                                            {"page": 41, "panel": 0}]},
                                {(14, 0)})
    keys = {(c["page"], c["panel"]) for c in out}
    assert (14, 0) not in keys and (41, 0) in keys


def test_build_candidates_defaults_to_whole_pool():
    """The cap that created this bug must stay off by default (k=0 → ALL)."""
    import inspect
    from stages.review_gate import build_candidates
    assert inspect.signature(build_candidates).parameters["k"].default == 0
