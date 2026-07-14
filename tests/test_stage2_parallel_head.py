"""Stage 2 Phase 2 single-page parallelization (2026-07-10): the front-matter/back-matter/
issue-edge path (_single_page_where) calls extract_page() with no prior_page/running_state
— each page is independent — so a CONTIGUOUS run of them is collected (_collect_single_page_
group) then built via _process_single_page_group, concurrently when VLM_PAGE_WORKERS > 1.

Tests mock the VLM-calling half (_build_page_from_single, the direct caller of
extract_page()) with a fake sleep + per-page dict, per the task's "mock extract_page"
instruction applied at that boundary — no real API/network call anywhere in this file."""
import time
from unittest.mock import patch

from PIL import Image

import stages.stage_2.pipeline as pipeline


def _page_state(pn, tmp_path, *, label="issue", make_image=False):
    img = tmp_path / f"p{pn:03d}.jpg"
    if make_image and not img.exists():
        Image.new("RGB", (10, 10)).save(img)
    return {"pn": pn, "label": label, "img": img, "hash": f"hash{pn}", "cached": None}


def _group_entry(pn, tmp_path, *, gate=None):
    return {
        "s": _page_state(pn, tmp_path),
        "dims": (10, 10),
        "magi": {"panels": [], "characters": [], "texts": []},
        "gate": gate,
    }


# ─── _single_page_where: pure classification, boundary sanity ─────────────────

def test_single_page_where_boundaries():
    n = 20
    bounds = {}
    # front-matter head: i < _FRONTMATTER_HEAD (8)
    assert pipeline._single_page_where(0, n, {"pn": 1, "label": "a"},
                                        multi_issue=False, issue_bounds=bounds) == "front-matter head"
    assert pipeline._single_page_where(7, n, {"pn": 8, "label": "a"},
                                        multi_issue=False, issue_bounds=bounds) == "front-matter head"
    # back-matter tail: i >= n - _BACKMATTER_TAIL (4)
    assert pipeline._single_page_where(16, n, {"pn": 17, "label": "a"},
                                        multi_issue=False, issue_bounds=bounds) == "back-matter tail"
    # mid-document, single issue → batched path (None)
    assert pipeline._single_page_where(10, n, {"pn": 11, "label": "a"},
                                        multi_issue=False, issue_bounds=bounds) is None


# ─── _process_single_page_group: order + concurrency ──────────────────────────

def test_group_results_in_page_order_regardless_of_finish_order(tmp_path):
    """Deliberately reversed sleep durations (page 1 finishes LAST when run concurrently) —
    the returned list must stay in GROUP (page) order, never completion order."""
    group = [_group_entry(pn, tmp_path) for pn in (1, 2, 3)]
    sleep_by_pn = {1: 0.06, 2: 0.02, 3: 0.01}

    def fake_build(*, page_number, **_kw):
        time.sleep(sleep_by_pn[page_number])
        return {"page_number": page_number, "page_type": "story"}

    with patch.object(pipeline, "VLM_PAGE_WORKERS", 4), \
         patch.object(pipeline, "_build_page_from_single", side_effect=fake_build):
        t0 = time.monotonic()
        finished = pipeline._process_single_page_group(
            group, project_root=tmp_path, log=print, story_context="",
        )
        elapsed = time.monotonic() - t0

    assert [f["page_dict"]["page_number"] for f in finished] == [1, 2, 3]
    assert all(f["gated"] is False for f in finished)
    # Ran concurrently: total time is close to the SLOWEST page, not the sum of all three.
    assert elapsed < sum(sleep_by_pn.values())


def test_workers_1_never_uses_threadpool_and_stays_in_order(tmp_path):
    """VLM_PAGE_WORKERS=1 must take the strictly-serial branch (no ThreadPoolExecutor at
    all) — the 'old serial path' the task requires this knob to preserve."""
    group = [_group_entry(pn, tmp_path) for pn in (1, 2, 3)]
    calls = []

    def fake_build(*, page_number, **_kw):
        calls.append(page_number)
        return {"page_number": page_number, "page_type": "story"}

    with patch.object(pipeline, "VLM_PAGE_WORKERS", 1), \
         patch.object(pipeline, "_build_page_from_single", side_effect=fake_build), \
         patch("concurrent.futures.ThreadPoolExecutor") as tpe:
        finished = pipeline._process_single_page_group(
            group, project_root=tmp_path, log=print, story_context="",
        )

    tpe.assert_not_called()
    assert calls == [1, 2, 3]  # invoked strictly in page order, one at a time
    assert [f["page_dict"]["page_number"] for f in finished] == [1, 2, 3]


def test_single_page_group_never_parallelized_even_if_workers_high(tmp_path):
    """A lone single-page group (len==1) takes the serial branch too — nothing to
    parallelize, matches min(workers, len(group)) <= 1."""
    group = [_group_entry(1, tmp_path)]
    with patch.object(pipeline, "VLM_PAGE_WORKERS", 8), \
         patch.object(pipeline, "_build_page_from_single",
                      side_effect=lambda *, page_number, **_kw: {"page_number": page_number}), \
         patch("concurrent.futures.ThreadPoolExecutor") as tpe:
        finished = pipeline._process_single_page_group(
            group, project_root=tmp_path, log=print, story_context="",
        )
    tpe.assert_not_called()
    assert finished[0]["page_dict"]["page_number"] == 1


# ─── error isolation: one page raises, siblings still complete ────────────────

def test_one_page_crash_isolated_others_still_complete_parallel(tmp_path):
    group = [_group_entry(pn, tmp_path) for pn in (1, 2, 3)]

    def fake_build(*, page_number, **_kw):
        if page_number == 2:
            raise RuntimeError("boom")
        return {"page_number": page_number, "page_type": "story"}

    with patch.object(pipeline, "VLM_PAGE_WORKERS", 4), \
         patch.object(pipeline, "_build_page_from_single", side_effect=fake_build):
        finished = pipeline._process_single_page_group(
            group, project_root=tmp_path, log=print, story_context="",
        )

    dicts = [f["page_dict"] for f in finished]
    assert dicts[0] == {"page_number": 1, "page_type": "story"}
    assert dicts[2] == {"page_number": 3, "page_type": "story"}
    # The crashed page is downgraded to the SAME soft-fail shape extract_page() itself
    # returns on total VLM exhaustion — never raises out of the group.
    assert dicts[1]["page_number"] == 2
    assert dicts[1]["page_type"] == "skip"
    assert dicts[1]["skip_reason"] == "vlm_failure"
    assert all(f["gated"] is False for f in finished)  # matches _build_page_from_single's own precedent

    # Persisted so a later run's cache-invalidation guard (skip_reason == "vlm_failure")
    # retries it, same contract as a normal (non-crash) VLM exhaustion.
    from stages.stage_2.cache import cache_path
    assert cache_path(tmp_path, 2, "hash2").exists()


def test_one_page_crash_isolated_others_still_complete_serial(tmp_path):
    """Same isolation guarantee holds with VLM_PAGE_WORKERS=1 (no thread pool at all) —
    a raising page never aborts the rest of the group."""
    group = [_group_entry(pn, tmp_path) for pn in (1, 2, 3)]

    def fake_build(*, page_number, **_kw):
        if page_number == 2:
            raise RuntimeError("boom")
        return {"page_number": page_number, "page_type": "story"}

    with patch.object(pipeline, "VLM_PAGE_WORKERS", 1), \
         patch.object(pipeline, "_build_page_from_single", side_effect=fake_build):
        finished = pipeline._process_single_page_group(
            group, project_root=tmp_path, log=print, story_context="",
        )

    assert [f["page_dict"].get("page_number") for f in finished] == [1, 2, 3]
    assert finished[1]["page_dict"]["skip_reason"] == "vlm_failure"


# ─── gated (pre-VLM) entries: never call the VLM build, never update prior-context ─

def test_gated_entry_skips_build_and_marked_gated(tmp_path):
    group = [
        _group_entry(1, tmp_path, gate=("cover", "", "Cover page")),
        _group_entry(2, tmp_path, gate=None),
    ]
    build_calls = []

    def fake_build(*, page_number, **_kw):
        build_calls.append(page_number)
        return {"page_number": page_number, "page_type": "story"}

    with patch.object(pipeline, "VLM_PAGE_WORKERS", 1), \
         patch.object(pipeline, "_build_page_from_single", side_effect=fake_build):
        finished = pipeline._process_single_page_group(
            group, project_root=tmp_path, log=print, story_context="",
        )

    assert build_calls == [2]  # the gated page 1 never reaches extract_page()/_build_page_from_single
    assert finished[0]["gated"] is True
    assert finished[0]["page_dict"]["page_type"] == "cover"
    assert finished[1]["gated"] is False


# ─── end-to-end: _collect_single_page_group + _process_single_page_group ──────
# vs. a hand-rolled replica of the OLD strictly-serial per-page loop, proving both
# VLM_PAGE_WORKERS=1 and >1 give byte-identical results/order/prior-context updates.

def _run_group_pipeline(page_states, *, workers, sleep_by_pn, gate_by_pn, tmp_path):
    def fake_gate(magi, pn, label, bounds):
        return gate_by_pn.get(pn)

    def fake_build(*, page_number, **_kw):
        time.sleep(sleep_by_pn.get(page_number, 0))
        return {"page_number": page_number, "page_type": "story", "issue_label": "issue"}

    with patch.object(pipeline, "VLM_PAGE_WORKERS", workers), \
         patch.object(pipeline, "detect_full", return_value={"panels": [], "characters": [], "texts": []}), \
         patch.object(pipeline, "_prevlm_gate", side_effect=fake_gate), \
         patch.object(pipeline, "_build_page_from_single", side_effect=fake_build):
        group, next_i = pipeline._collect_single_page_group(
            page_states, 0, len(page_states), magi_by_pn={}, issue_bounds={},
            multi_issue=False, log=print,
        )
        finished = pipeline._process_single_page_group(
            group, project_root=tmp_path, log=print, story_context="",
        )

    results, prev_page_dict = [], None
    for f in finished:
        results.append(f["page_dict"])
        if not f["gated"]:
            prev_page_dict = f["page_dict"]
    return results, prev_page_dict, next_i


def test_group_pipeline_matches_across_worker_counts(tmp_path):
    # 5 pages, all within _FRONTMATTER_HEAD (index 0..4 < 8) → one contiguous group.
    # pn=1 and pn=4 are gated (pre-VLM skip); pn=2, 3, 5 need the VLM build.
    page_states = [_page_state(pn, tmp_path, make_image=True) for pn in (1, 2, 3, 4, 5)]
    gate_by_pn = {
        1: ("cover", "", "Cover page"),
        4: ("skip", "back_matter", "Non-story page (pre-VLM gate)"),
    }
    # Reversed sleep durations so parallel completion order != page order.
    sleep_by_pn = {2: 0.05, 3: 0.03, 5: 0.01}

    results_par, prev_par, next_i_par = _run_group_pipeline(
        page_states, workers=4, sleep_by_pn=sleep_by_pn, gate_by_pn=gate_by_pn, tmp_path=tmp_path)
    results_ser, prev_ser, next_i_ser = _run_group_pipeline(
        page_states, workers=1, sleep_by_pn=sleep_by_pn, gate_by_pn=gate_by_pn, tmp_path=tmp_path)

    assert next_i_par == next_i_ser == 5
    assert [r["page_number"] for r in results_par] == [1, 2, 3, 4, 5]
    assert results_par == results_ser
    # prior-context: last NON-gated page in the group is pn=5 (pn=4's gate skips the update).
    assert prev_par["page_number"] == 5
    assert prev_par == prev_ser


def test_collect_group_stops_at_cache_hit(tmp_path):
    page_states = [_page_state(pn, tmp_path, make_image=True) for pn in (1, 2, 3)]
    page_states[1]["cached"] = {"page_number": 2, "page_type": "story"}  # pn=2 is a cache hit

    with patch.object(pipeline, "detect_full", return_value={"panels": [], "characters": [], "texts": []}), \
         patch.object(pipeline, "_prevlm_gate", return_value=None):
        group, next_i = pipeline._collect_single_page_group(
            page_states, 0, len(page_states), magi_by_pn={}, issue_bounds={},
            multi_issue=False, log=print,
        )

    assert next_i == 1  # stopped BEFORE the cached page — caller's while loop handles it
    assert [e["s"]["pn"] for e in group] == [1]
