"""Narration-driven panel matcher — _match_panels (order-free content + page_ref prior).

Mocks _panel_content_score so the order-free / reuse / cos-floor-HOLD control flow is
asserted deterministically. _match_panels also calls embed_batch (imported inside the
function from stages._embedding) to embed the unit + scene text — we stub that too so the
test never hits the live LM Studio / Azure endpoint. The stub returns None vectors; with
_panel_content_score mocked those vectors are never used, so the cosine is fully controlled.
The scorer returns (10.0, 0.9) when a panel's tag word appears in the narration text, else
(0.5, 0.1) — the 2nd value is the raw cosine the floor checks, so 0.1 < PANEL_COS_FLOOR
drives the weak-hold path — and we control every decision."""
import stages._embedding as _embedding
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
