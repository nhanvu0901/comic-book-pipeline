"""Multi-issue (saga) awareness for the three Stage 2 post-processing heuristics that
originally assumed the whole flattened document was ONE issue:

  Fix 1 `_demote_backmatter_tail`     — half-cutoff + tail-demotion scoped per issue_label.
  Fix 2 `_reclassify_mid_doc_covers`  — 'edge of the issue' scoped per issue_label.
  Fix 3 `_is_near_own_issue_edge`     — front/back single-page window scoped per issue_label
                                        (drives the batching loop in preprocess_project).

Every fixture that uses a SINGLE issue_label collapses each fix's grouping to exactly one
group == the whole doc, which is asserted to behave identically to the pre-fix code (see
tests/test_backmatter_tail.py, which stays green and unmodified — read first for the
original fixture style this file follows)."""
from stages.stage_2.pipeline import (
    _demote_backmatter_tail,
    _reclassify_mid_doc_covers,
    _page_state_issue_bounds,
    _is_near_own_issue_edge,
)


def _pg(pn, label, story, reason=""):
    return {"page_number": pn, "issue_label": label,
            "is_story_page": story,
            "page_type": "story" if story else "skip",
            "skip_reason": reason,
            "panels": [{"index": 0, "bbox": {}}] if story else [],
            "content_hash": ""}


def _cov(pn, label, page_type):
    return {"page_number": pn, "issue_label": label, "page_type": page_type,
            "is_story_page": page_type == "story", "content_hash": ""}


# ── Fix 1: _demote_backmatter_tail ──────────────────────────────────────────────────

def _saga_pages():
    """5 issues x 18pp, each: 1 cover, 14 story pages, 1 terminal back-matter page (its
    OWN 'to be continued'), then 2 pages the VLM mislabelled 'story' (a preview of the
    NEXT issue/another comic) — the same shape as test_backmatter_tail.py's single-issue
    case, repeated 5x back to back with GLOBAL page numbers (1-90)."""
    pages = []
    for k in range(5):
        offset = k * 18
        label = f"issue_{k + 1}"
        pages.append(_pg(offset + 1, label, False))                       # cover
        pages += [_pg(offset + j, label, True) for j in range(2, 16)]     # 14 real story pages
        pages.append(_pg(offset + 16, label, False, "letter_column"))     # own terminal tail
        pages.append(_pg(offset + 17, label, True))                       # mislabelled preview
        pages.append(_pg(offset + 18, label, True))                       # mislabelled preview
    return pages


def test_saga_tail_demotion_scoped_per_issue(tmp_path):
    """Regression for the proven HIGH-severity bug: the OLD code computed one GLOBAL
    half-cutoff (max_page * 0.5 = 45 here) across the flattened 5-issue saga, so the FIRST
    terminal back-matter page found anywhere past that global half — issue #3's own tail at
    p52 — became the cut for the ENTIRE REST of the document, wiping issue #4's and #5's
    real story pages wholesale (a live simulation measured only 51/85 story pages survived).
    After the fix, each issue's own terminal tail is found and demoted independently, so
    issue #4/#5's real story pages must SURVIVE and only their OWN 2-page preview tail
    gets demoted."""
    pages = _saga_pages()
    _demote_backmatter_tail(pages, tmp_path, log=lambda *_: None)
    by = {p["page_number"]: p for p in pages}

    # Issue #4 = pages 55-72: real story (56-69) survives; only its own tail (71,72) demoted.
    for pn in range(56, 70):
        assert by[pn]["is_story_page"] is True, f"issue4 story p{pn} wrongly wiped"
    assert by[71]["is_story_page"] is False and by[71]["skip_reason"] == "back_matter_tail"
    assert by[72]["is_story_page"] is False and by[72]["panels"] == []

    # Issue #5 = pages 73-90: same shape.
    for pn in range(74, 88):
        assert by[pn]["is_story_page"] is True, f"issue5 story p{pn} wrongly wiped"
    assert by[89]["is_story_page"] is False and by[89]["skip_reason"] == "back_matter_tail"
    assert by[90]["is_story_page"] is False

    # Sanity: issue #1's own preview tail is ALSO demoted now (own-scope cut works at the front too).
    assert by[17]["is_story_page"] is False
    assert by[18]["is_story_page"] is False


def test_single_issue_unchanged_demote_tail(tmp_path):
    """One issue_label for every page -> exactly one group -> the function takes the
    literal untouched original branch. Same numbers/expectations as
    test_backmatter_tail.py::test_tail_demotes_previews_after_letters, proving the
    per-issue refactor didn't change single-issue semantics."""
    pages = [
        _pg(1, "only", False, "cover"),
        _pg(10, "only", True), _pg(23, "only", True),
        _pg(24, "only", False, "letter_column"),
        _pg(25, "only", True), _pg(26, "only", True),
        _pg(30, "only", False, "advertisement"),
    ]
    _demote_backmatter_tail(pages, tmp_path, log=lambda *_: None)
    by = {p["page_number"]: p for p in pages}
    assert by[25]["is_story_page"] is False and by[25]["skip_reason"] == "back_matter_tail"
    assert by[26]["is_story_page"] is False
    assert by[23]["is_story_page"] is True
    assert by[10]["is_story_page"] is True


# ── Fix 2: _reclassify_mid_doc_covers ───────────────────────────────────────────────

def test_saga_own_issue_front_cover_stays_cover(tmp_path):
    """Regression: the OLD code flipped ANY 'cover' page with 2 < pn < total to story — a
    real front cover of issue #2 in a saga (global pn=11, nowhere near the WHOLE doc's
    p1-2) got wrongly flipped to story, polluting the panel pool/cold-open. The fix scopes
    'the first ~2 pages' to the cover's OWN issue_label range: issue #2's own first page
    stays a cover, but a cover planted MID-issue (the original single-issue bug this
    function exists to catch) still flips."""
    pages = (
        [_cov(1, "iss1", "cover")]
        + [_cov(pn, "iss1", "story") for pn in range(2, 11)]
        + [_cov(11, "iss2", "cover")]                              # issue2's own front cover
        + [_cov(pn, "iss2", "story") for pn in range(12, 15)]
        + [_cov(15, "iss2", "cover")]                              # mid-issue2 mislabel
        + [_cov(pn, "iss2", "story") for pn in range(16, 21)]
    )
    _reclassify_mid_doc_covers(pages, tmp_path, log=lambda *_: None)
    by = {p["page_number"]: p for p in pages}
    assert by[11]["page_type"] == "cover"       # issue2's own edge -> untouched
    assert by[11]["is_story_page"] is False
    assert by[15]["page_type"] == "story"       # mid-issue2 -> still flips (Option 1 heuristic)
    assert by[15]["is_story_page"] is True


def test_single_issue_unchanged_reclassify_cover(tmp_path):
    """One issue_label -> ranges has exactly one entry -> falls through to the literal
    original `pn <= 2 or pn >= total` check (byte-identical)."""
    pages = (
        [_cov(1, "only", "cover")]
        + [_cov(pn, "only", "story") for pn in range(2, 6)]
        + [_cov(6, "only", "cover")]     # mid-doc (total=8) -> original bug this catches
        + [_cov(pn, "only", "story") for pn in range(7, 9)]
    )
    _reclassify_mid_doc_covers(pages, tmp_path, log=lambda *_: None)
    by = {p["page_number"]: p for p in pages}
    assert by[1]["page_type"] == "cover"
    assert by[6]["page_type"] == "story"


# ── Fix 3: per-issue front/back single-page window (drives preprocess_project's batching) ──

def test_page_state_issue_bounds_single_and_multi():
    single = [{"pn": i, "label": "only"} for i in range(1, 11)]
    assert _page_state_issue_bounds(single) == {"only": (1, 10)}

    multi = [{"pn": i, "label": ("a" if i <= 5 else "b")} for i in range(1, 11)]
    assert _page_state_issue_bounds(multi) == {"a": (1, 5), "b": (6, 10)}


def test_near_issue_edge_uses_own_issue_window():
    """_ISSUE_FRONTMATTER_HEAD=3 / _ISSUE_BACKMATTER_TAIL=2: near-edge is relative to THIS
    issue's own (lo, hi), not the whole doc — pn=12 is deep mid-document globally but is
    within 3 pages of issue 'a's own start (10)."""
    bounds = {"a": (10, 30)}
    assert _is_near_own_issue_edge(10, "a", bounds) is True    # own first page
    assert _is_near_own_issue_edge(12, "a", bounds) is True    # within own head window
    assert _is_near_own_issue_edge(13, "a", bounds) is False   # outside own head window
    assert _is_near_own_issue_edge(29, "a", bounds) is True    # within own tail window
    assert _is_near_own_issue_edge(30, "a", bounds) is True    # own last page
    assert _is_near_own_issue_edge(28, "a", bounds) is False   # outside own tail window
