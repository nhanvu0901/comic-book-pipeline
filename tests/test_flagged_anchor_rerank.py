"""Feature C+D — trust flags gate the ANCHOR bind and force VLM rerank on distrusted units.

Stage 2 writes a page-level `desc_verified` (False = descriptions still mismatched their own
pixels after a re-describe) and a panel-level `dialog_mismatch` (True = VLM dialog contradicts
Magi OCR). An authorial (page_ref, panel_ref) anchor is only as trustworthy as the description
that produced it — a FABRICATED description silently mis-anchored doom-rocket-raccoon scene 13
and PANEL_ANCHOR_BIND rendered the wrong panel with no VLM check.

Feature C: when the anchor target panel is UNTRUSTED, don't hard-bind — leave the unit
un-anchored so it flows through normal content matching. Feature D: that un-anchored (distrusted)
unit becomes VLM-rerank-eligible EVEN when its cosine is strong, because a poisoned description
scores a HIGH FAKE cosine (bad text matched bad text) — the very picks the cos-ceil gate would
otherwise trust are the ones we least trust.

These tests mock _panel_content_score (deterministic cosine) and _vlm_rerank (no SDK/network) —
the whole trust control flow is asserted without touching LM Studio / Azure / the Claude SDK.
"""
import stages._embedding as _embedding
import stages.stage_5.shots as shots


def _page(tags, *, desc_verified=None, mismatch_idx=()):
    """One page (number 10) of stacked same-size panels. `desc_verified` sets the page-level
    Stage-2 flag (None = gate never ran → trusted); `mismatch_idx` marks panel indices whose
    own `dialog_mismatch` flag is True. Pool index == panel index here (single page, in order)."""
    page = {
        "source_image": "p10.png",
        "image_dimensions": {"width": 600, "height": 1800},
        "page_type": "story",
        "panels": [
            {"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
             "description": t, "characters": [],
             **({"dialog_mismatch": True} if i in mismatch_idx else {})}
            for i, t in enumerate(tags)
        ],
        "text_blocks": [],
    }
    if desc_verified is not None:
        page["desc_verified"] = desc_verified
    return {10: page}


def _high_cosine_beta(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    # beta = the anchor target (index 1). It scores a HIGH cosine (0.9 ≥ PANEL_RERANK_COS_CEIL)
    # AND wins content — the fake-high-cosine trap a poisoned description creates. alpha is the
    # panel the VLM would actually pick.
    return (10.0, 0.9) if panel.get("description") == "beta" else (6.0, 0.55)


def _stub(monkeypatch, rerank_calls, *, rerank_pick=0):
    """No network: stub the embed batch (vectors unused — scorer is mocked), isolate the pick
    from the size tie-break and page prior, and replace the SDK vision judge with a recorder
    that returns `rerank_pick` (a pool index) so we can prove a unit reached rerank."""
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(shots, "PANEL_RERANK", True)   # default flipped to 0 (2026-07-24); this suite tests the rerank
    monkeypatch.setattr(shots, "PANEL_SIZE_TIE_MARGIN", 0.0)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "_panel_content_score", _high_cosine_beta)

    def _fake_rerank(line, cands, *, log=print):
        rerank_calls.append((line, [c[0] for c in cands]))
        return rerank_pick

    monkeypatch.setattr(shots, "_vlm_rerank", _fake_rerank)


def test_trusted_anchor_still_binds(monkeypatch):
    # Control: a trusted anchor (page has no desc_verified flag → trusted) BINDS exactly as
    # before — pre-assigned, never reranked, wins despite alpha being the VLM's choice.
    calls = []
    _stub(monkeypatch, calls)
    pages = _page(["alpha", "beta"])                     # trusted
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 1}
    out = shots._match_panels([(scene, "some line")], pages, {})
    assert out[0][0]["index"] == 1, "trusted anchor must bind to beta"
    assert calls == [], "a bound (trusted) anchor must NOT enter VLM rerank"


def test_desc_verified_false_falls_back_and_reranks_despite_high_cosine(monkeypatch):
    # Feature C+D: page failed DESC_VERIFY → the anchor is NOT bound; the unit flows through
    # content matching (which ALSO lands on the poisoned beta, cos 0.9) and Feature D still
    # forces VLM rerank despite that strong cosine. The VLM redirects to alpha (index 0).
    calls = []
    _stub(monkeypatch, calls, rerank_pick=0)
    pages = _page(["alpha", "beta"], desc_verified=False)
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 1}
    out = shots._match_panels([(scene, "some line")], pages, {})
    assert len(calls) == 1, "distrusted unit must reach VLM rerank despite cos 0.9 ≥ ceil"
    assert out[0][0]["index"] == 0, "VLM override must win (anchor was not bound)"


def test_dialog_mismatch_panel_falls_back_and_reranks(monkeypatch):
    # Same as above but the trust break is panel-level: the anchor target beta carries
    # dialog_mismatch=True (page desc_verified absent = otherwise trusted).
    calls = []
    _stub(monkeypatch, calls, rerank_pick=0)
    pages = _page(["alpha", "beta"], mismatch_idx=(1,))
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 1}
    out = shots._match_panels([(scene, "some line")], pages, {})
    assert len(calls) == 1, "dialog_mismatch anchor must reach VLM rerank"
    assert out[0][0]["index"] == 0, "VLM override must win (anchor was not bound)"


def test_anchor_trust_off_restores_old_bind(monkeypatch):
    # Kill-switch: ANCHOR_TRUST=0 → the untrusted flag is ignored, the anchor binds to beta
    # like the old always-bind behaviour, and never enters rerank.
    calls = []
    _stub(monkeypatch, calls)
    monkeypatch.setattr(shots, "ANCHOR_TRUST", False)
    pages = _page(["alpha", "beta"], desc_verified=False)
    scene = {"scene_id": 1, "text": "x", "page_ref": 10, "panel_ref": 1}
    out = shots._match_panels([(scene, "some line")], pages, {})
    assert out[0][0]["index"] == 1, "ANCHOR_TRUST=0 must bind even an untrusted anchor"
    assert calls == [], "old bind behaviour must not enter rerank"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
