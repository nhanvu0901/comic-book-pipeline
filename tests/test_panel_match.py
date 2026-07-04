"""Narration-driven panel matcher — _match_panels (order-free content + page_ref prior).

Mocks _panel_content_score so the order-free / reuse / cos-floor-HOLD control flow is
asserted deterministically. _match_panels also calls embed_batch (imported inside the
function from stages._embedding) to embed the unit + scene text — we stub that too so the
test never hits the live LM Studio / Azure endpoint. The stub returns None vectors; with
_panel_content_score mocked those vectors are never used, so the cosine is fully controlled.
The scorer returns (10.0, 0.9) when a panel's tag word appears in the narration text, else
(0.5, 0.1) — the 2nd value is the raw cosine the floor checks, so 0.1 < PANEL_COS_FLOOR
drives the weak-hold path — and we control every decision."""
import pytest

import stages._embedding as _embedding
import stages._panel_index as _panel_index
import stages.stage_5.shots as shots


def _no_network_embed(monkeypatch):
    # _match_panels does `from .._embedding import embed_batch as _embed_batch` INSIDE the
    # function, so patching the source module's attribute is what the import resolves to.
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    # #6 VLM rerank is a Claude-SDK network call — off in unit tests (we test the cosine +
    # page_ref-prior selection path here, not the vision judge).
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    # Big-shot tie-break is a separate selection layer — off here so these tests isolate the
    # content + page_ref-prior pick (the synthetic content gaps are tiny on purpose).
    monkeypatch.setattr(shots, "PANEL_SIZE_TIE_MARGIN", 0.0)


def _page(tags):
    return {10: {
        "source_image": "p10.png",
        "image_dimensions": {"width": 600, "height": 2700},
        "page_type": "story",
        "panels": [
            {"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
             "description": t, "characters": []}
            for i, t in enumerate(tags)
        ],
        "text_blocks": [],
    }}


def _fake_score(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    tag = str(panel.get("description", "")).strip().lower()
    return (10.0, 0.9) if tag and tag in (chunk_text or "").lower() else (0.5, 0.1)


def test_content_mode_picks_best_out_of_order(monkeypatch):
    # Order-FREE content match: each unit takes its best-content panel even when that panel
    # sits OUT of reading order (the Doom backstory fix).
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)        # pure content, no prior nudge
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = _page(["gamma", "beta", "alpha"])               # panels in REVERSE of narration
    scene = {"scene_id": 1, "text": "x"}
    units = [(scene, "alpha here"), (scene, "beta here"), (scene, "gamma here")]
    out = shots._match_panels(units, pages, {})
    idx = [p["index"] if p else None for p, _ in out]
    assert idx == [2, 1, 0]      # alpha→panel2, beta→panel1, gamma→panel0 (order-free)


def test_pageref_gaussian_pulls_backstory_back(monkeypatch):
    # Per-unit page_ref prior. Content slightly prefers a LATE panel, but the line's page_ref
    # points to an EARLY page (a flashback/callback). The Gaussian bump centred on the
    # page_ref must out-pull the weak content lead so the EARLY panel wins. Proven against a
    # control: with the prior OFF (amp 0) pure content picks the WRONG late panel.
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_PRIOR_SIGMA_PAGES", 1.0)

    def _one_panel_page(src, tag):
        return {"source_image": src, "image_dimensions": {"width": 600, "height": 900},
                "page_type": "story",
                "panels": [{"index": 0, "bbox": {"x": 0, "y": 0, "w": 600, "h": 900},
                            "description": tag, "characters": []}],
                "text_blocks": []}

    pages = {1: _one_panel_page("p1.png", "early"),      # correct flashback panel (page 1)
             3: _one_panel_page("p3.png", "late")}       # late panel content prefers slightly

    def fake(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
        # both clear the cos floor; content leans (wrongly) to the LATE panel
        return (6.5, 0.65) if panel.get("description") == "late" else (6.0, 0.60)

    monkeypatch.setattr(shots, "_panel_content_score", fake)
    scene = {"scene_id": 1, "text": "callback", "page_ref": 1}   # beat anchored to page 1
    units = [(scene, "callback to the start")]

    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)            # control: prior OFF
    out0 = shots._match_panels(units, pages, {})
    assert out0[0][0]["_page_number"] == 3                       # pure content → WRONG late

    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 1.0)           # prior ON
    out1 = shots._match_panels(units, pages, {})
    assert out1[0][0]["_page_number"] == 1                       # page_ref pulls back → RIGHT


def test_weak_match_holds_not_force(monkeypatch):
    # cos-floor HOLD: a unit whose best panel's raw cosine is below PANEL_COS_FLOOR holds the
    # previous panel instead of forcing an unrelated one (Master's "giữ panel hiện tại" rule).
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "x"}
    units = [(scene, "alpha here"), (scene, "something off-panel nobody drew")]
    out = shots._match_panels(units, pages, {})
    idx = [p["index"] if p else None for p, _ in out]
    assert idx == [0, 0]   # opened on alpha, then HELD it (no forced beta)


def test_unique_assignment_spreads_magnet_panel(monkeypatch):
    # PANEL_UNIQUE: two scenes whose BEST-content panel is the SAME "magnet" must get DISTINCT
    # panels (optimal assignment), not both the magnet. The Motorstorm bug was 4 scenes all
    # grabbing one splash. Control: with PANEL_UNIQUE off the greedy soft-penalty reuses it.
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)

    # Non-match cosine 0.5 > PANEL_COS_FLOOR so the 2nd-choice panel is SHOWN (not weak-held),
    # which would otherwise mask the assignment behind a HOLD.
    def fake(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
        tag = str(panel.get("description", "")).strip().lower()
        return (10.0, 0.9) if tag and tag in (chunk_text or "").lower() else (5.0, 0.5)

    monkeypatch.setattr(shots, "_panel_content_score", fake)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "x"}
    units = [(scene, "alpha one"), (scene, "alpha two")]   # both want panel 0 (the magnet)

    monkeypatch.setattr(shots, "PANEL_UNIQUE", True)
    idx = sorted(p["index"] for p, _ in shots._match_panels(units, pages, {}))
    assert idx == [0, 1]      # distinct panels — magnet shown once

    monkeypatch.setattr(shots, "PANEL_UNIQUE", False)
    idx2 = [p["index"] for p, _ in shots._match_panels(units, pages, {})]
    assert idx2 == [0, 0]     # control: greedy soft-penalty reuses the magnet


def test_anchor_bonus_flips_ambiguous_pick(monkeypatch):
    # C2, BIND=0 (safety-valve mode): a scene carrying a Stage-3 grounded (page_ref,
    # panel_ref) gets a bonus for that exact panel. On a near-tie content score, the
    # bonus must flip the pick to the grounded panel even though it is not the raw
    # content winner. Default BIND=1 makes the anchor binding, not a bonus — see
    # test_anchor_bind_wins_over_strong_cosine_conflict for that path.
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "PANEL_ANCHOR_BIND", False)

    def fake(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
        return (5.0, 0.5) if panel.get("description") == "alpha" else (4.9, 0.49)

    monkeypatch.setattr(shots, "_panel_content_score", fake)
    pages = _page(["alpha", "beta"])   # index 0 = alpha (content winner), index 1 = beta
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 1}   # grounded to beta
    units = [(scene, "some line")]

    monkeypatch.setattr(shots, "PANEL_ANCHOR_BONUS", 0.0)
    out0 = shots._match_panels(units, pages, {})
    assert out0[0][0]["index"] == 0    # control: bonus off → raw content wins (alpha)

    monkeypatch.setattr(shots, "PANEL_ANCHOR_BONUS", 2.5)
    out1 = shots._match_panels(units, pages, {})
    assert out1[0][0]["index"] == 1    # bonus on → flips to the grounded panel (beta)


def test_anchor_bonus_does_not_override_strong_cosine_conflict(monkeypatch):
    # BIND=0 (safety-valve mode): the bonus is SOFT — when the chunk-level cosine
    # strongly disagrees with the grounded panel (deficit well past what
    # PANEL_ANCHOR_BONUS can cover), the content winner still wins. With default
    # BIND=1 the anchor wins this same conflict instead — see
    # test_anchor_bind_wins_over_strong_cosine_conflict.
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "PANEL_ANCHOR_BIND", False)
    monkeypatch.setattr(shots, "PANEL_ANCHOR_BONUS", 2.5)

    def fake(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
        return (10.0, 0.9) if panel.get("description") == "alpha" else (2.0, 0.2)

    monkeypatch.setattr(shots, "_panel_content_score", fake)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 1}   # grounded to beta
    units = [(scene, "some line")]

    out = shots._match_panels(units, pages, {})
    assert out[0][0]["index"] == 0     # alpha (content) still wins over the soft anchor


def test_anchor_bind_wins_over_strong_cosine_conflict(monkeypatch):
    # Fix 2 (default BIND=1): the anchor is BINDING, not a score — pre-assigned before
    # Hungarian/greedy runs, so it wins even against a strong cosine conflict that the
    # old soft PANEL_ANCHOR_BONUS could not survive (control above).
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "PANEL_ANCHOR_BIND", True)

    def fake(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
        return (10.0, 0.9) if panel.get("description") == "alpha" else (2.0, 0.2)

    monkeypatch.setattr(shots, "_panel_content_score", fake)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 1}   # grounded to beta
    units = [(scene, "some line")]

    out = shots._match_panels(units, pages, {})
    assert out[0][0]["index"] == 1     # BOUND: the anchor wins despite the cosine gap


def test_anchor_bind_allows_duplicate_anchor(monkeypatch):
    # Two scenes hand/grounded-anchored to the SAME panel is a legal authorial repeat
    # (e.g. two narration lines about one splash) — BIND must not error or silently
    # redirect one of them elsewhere.
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "PANEL_ANCHOR_BIND", True)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 0}   # both -> alpha
    units = [(scene, "line one"), (scene, "line two")]

    out = shots._match_panels(units, pages, {})
    idx = [p["index"] for p, _ in out]
    assert idx == [0, 0]                # duplicate anchor allowed, no error


def test_anchor_bind_falls_back_when_key_missing(monkeypatch):
    # A grounded/hand (page_ref, panel_ref) that doesn't exist in the panel pool (bad
    # index, e.g. a stale Stage-3 ref) must fall back to ordinary content match for
    # that row instead of crashing.
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "PANEL_ANCHOR_BIND", True)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 99}   # bad index
    units = [(scene, "alpha here")]

    out = shots._match_panels(units, pages, {})
    assert out[0][0]["index"] == 0      # falls back to content match (alpha)


def test_cascade_hold_guard_raises_when_backend_down(monkeypatch):
    # M5: if the embedding backend is down, every sim is ~0 → every story unit falls
    # below PANEL_COS_FLOOR. Silently HOLDing the cold-open panel for the whole video
    # is worse than a loud failure.
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = _page(["p0", "p1", "p2", "p3", "p4"])
    scene = {"scene_id": 1, "text": "x"}
    # None of these lines mention any panel's tag → every sim stays at the weak 0.1.
    units = [(scene, f"unmatched line {i}") for i in range(5)]
    with pytest.raises(RuntimeError, match="panel-match"):
        shots._match_panels(units, pages, {})


def test_load_vectors_dim_mismatch_helper():
    # M4: factor the dim check into a pure function so it's testable without a
    # live Qdrant client. A persisted index under one embedding backend (e.g.
    # Gemini 3072-dim) must be rejected when the live backend differs (e.g. Qwen
    # 4096-dim), rather than crashing the matcher's np.dot on a shape mismatch.
    import numpy as np
    vecs = {(1, 0): np.zeros(3072, dtype="float32")}
    assert _panel_index._dim_mismatch(vecs, 4096) is True
    assert _panel_index._dim_mismatch(vecs, 3072) is False
    assert _panel_index._dim_mismatch({}, 4096) is False


def test_render_adjust_favors_bigger_panel():
    # Favor-bigger lever: among content-similar panels, _render_adjust must score a large
    # (>=50%-of-page splash) panel higher than a tiny one. This is what the raised
    # PANEL_SALIENCE_W / PANEL_RENDER_ADJ_CAP defaults exploit to pick highlight panels.
    page_area = 1054 * 1600
    big = {"bbox": {"x": 0, "y": 0, "w": 1054, "h": 1600}, "_page_area": page_area}     # full page
    small = {"bbox": {"x": 0, "y": 0, "w": 210, "h": 210}, "_page_area": page_area}     # ~2.6% tiny
    big_s = shots._render_adjust(big, None, salience_w=shots.PANEL_SALIENCE_W)
    small_s = shots._render_adjust(small, None, salience_w=shots.PANEL_SALIENCE_W)
    assert big_s > small_s
    # And the default weight is strong enough to matter vs the content swing (W_COS·cos ~ up to
    # ~10): the big-vs-tiny render gap should clear the clamp so it isn't a negligible nudge.
    assert big_s - small_s >= 1.0
