"""Feature A — pixel-level panel matching via a SigLIP joint image-text space.

These tests exercise the BLEND LOGIC only — no model download, no network. The SigLIP
encoder + Qdrant loader are injected as fakes (monkeypatched on stages._img_index), so the
whole degrade matrix and the poisoned-desc flip are asserted deterministically.

WHY the flip matters: the text `content` matrix trusts the VLM's DESCRIPTION, and a
fabricated description scores a FAKE-HIGH text cosine for the WRONG panel. The image cosine
(narration line vs the panel's ART pixels) never reads those words, so at w=0.35 it can flip
a low-confidence / near-tie text pick onto the panel the pixels actually depict.
"""
import numpy as np

import stages._embedding as _embedding
import stages._img_index as _img_index
import stages._panel_index as _panel_index
import stages.stage_5.shots as shots


def _pool(keys):
    # _blend_image_content only reads pool[j][0] (the (page, idx) key).
    return [(k, {}, "", []) for k in keys]


# Query text vec = [1,0]; panel B aligned (cos 1.0), panels A/C orthogonal (cos 0.0) → the
# image channel favours B. Both vectors are unit-norm so the dot IS the cosine.
_KEYS = [(10, 0), (10, 1), (10, 2)]          # A (wrong), B (right), C (filler)
_IMG_VECS = {(10, 0): np.array([0.0, 1.0], dtype="float32"),
             (10, 1): np.array([1.0, 0.0], dtype="float32"),
             (10, 2): np.array([0.0, 1.0], dtype="float32")}


def _enable_fake_image(monkeypatch):
    monkeypatch.setattr(_img_index, "PANEL_IMG_EMBED", True)
    monkeypatch.setattr(_img_index, "img_embed_available", lambda: True)
    monkeypatch.setattr(_img_index, "load_image_vectors", lambda proj: dict(_IMG_VECS))
    monkeypatch.setattr(_img_index, "embed_texts",
                        lambda texts, **k: np.array([[1.0, 0.0]] * len(texts), dtype="float32"))


def _boom(*_a, **_k):
    raise AssertionError("must not be called when the image channel is off/unavailable")


# ── (a) blend flips the pick when the image signal disagrees with a poisoned text pick ──
def test_blend_flips_ranking_on_poisoned_desc(monkeypatch):
    _enable_fake_image(monkeypatch)
    pool, units = _pool(_KEYS), [({}, "some line")]
    # text: A=6.0 (fake lead, wrong), B=5.7 (right), C=3.0 → text alone picks A.
    content = np.array([[6.0, 5.7, 3.0]])

    monkeypatch.setattr(_img_index, "PANEL_IMG_WEIGHT", 0.0)   # control: image weight off
    c0 = content.copy()
    shots._blend_image_content(c0, pool, units, "proj")
    assert int(np.argmax(c0[0])) == 0, "w=0 must leave the text pick (A) untouched"

    monkeypatch.setattr(_img_index, "PANEL_IMG_WEIGHT", 0.35)
    c1 = content.copy()
    shots._blend_image_content(c1, pool, units, "proj")
    assert int(np.argmax(c1[0])) == 1, "image signal must flip the pick to the right panel (B)"


# ── (b) no image vectors → content matrix is byte-identical to the text-only path ──
def test_no_image_vectors_is_noop(monkeypatch):
    monkeypatch.setattr(_img_index, "PANEL_IMG_EMBED", True)
    monkeypatch.setattr(_img_index, "img_embed_available", lambda: True)
    monkeypatch.setattr(_img_index, "load_image_vectors", lambda proj: {})
    monkeypatch.setattr(_img_index, "embed_texts", _boom)   # must not embed if no vectors
    content = np.array([[6.0, 5.7, 3.0]]); before = content.copy()
    shots._blend_image_content(content, _pool(_KEYS), [({}, "x")], "proj")
    assert np.array_equal(content, before)


# ── (c) PANEL_IMG_EMBED=0 → skip entirely (never even loads vectors) ──
def test_env_off_is_noop(monkeypatch):
    monkeypatch.setattr(_img_index, "PANEL_IMG_EMBED", False)
    monkeypatch.setattr(_img_index, "load_image_vectors", _boom)
    content = np.array([[6.0, 5.7, 3.0]]); before = content.copy()
    shots._blend_image_content(content, _pool(_KEYS), [({}, "x")], "proj")
    assert np.array_equal(content, before)


# ── (d) img_embed_available()==False → everything no-ops ──
def test_unavailable_is_noop(monkeypatch):
    monkeypatch.setattr(_img_index, "PANEL_IMG_EMBED", True)
    monkeypatch.setattr(_img_index, "img_embed_available", lambda: False)
    monkeypatch.setattr(_img_index, "load_image_vectors", _boom)
    content = np.array([[6.0, 5.7, 3.0]]); before = content.copy()
    shots._blend_image_content(content, _pool(_KEYS), [({}, "x")], "proj")
    assert np.array_equal(content, before)


# ── dim mismatch (SigLIP swapped since indexing) → skip, don't crash np.dot ──
def test_dim_mismatch_is_noop(monkeypatch):
    monkeypatch.setattr(_img_index, "PANEL_IMG_EMBED", True)
    monkeypatch.setattr(_img_index, "img_embed_available", lambda: True)
    monkeypatch.setattr(_img_index, "load_image_vectors", lambda proj: dict(_IMG_VECS))  # dim 2
    monkeypatch.setattr(_img_index, "embed_texts",
                        lambda texts, **k: np.zeros((len(texts), 3), dtype="float32"))    # dim 3
    monkeypatch.setattr(_img_index, "PANEL_IMG_WEIGHT", 0.35)
    content = np.array([[6.0, 5.7, 3.0]]); before = content.copy()
    shots._blend_image_content(content, _pool(_KEYS), [({}, "x")], "proj")
    assert np.array_equal(content, before)


# ── integration: the blend is WIRED into _match_panels (runs before the prior) and changes
#    the actual panel pick. Mirrors the fixture style of test_panel_match / test_flagged_anchor_rerank.
def _page3():
    return {10: {
        "source_image": "p10.png",
        "image_dimensions": {"width": 600, "height": 2700},
        "page_type": "story",
        "panels": [{"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
                    "description": d, "characters": []}
                   for i, d in enumerate(["a", "b", "c"])],
        "text_blocks": [],
    }}


def _text_scorer(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    # A(wrong) modest lead over B(right); sim 0.9 for all so nothing is cos-floor-held.
    return ({"a": 6.0, "b": 5.7, "c": 3.0}[panel.get("description")], 0.9)


def test_match_panels_image_signal_flips_pick(monkeypatch):
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(_panel_index, "load_vectors", lambda p: {})   # no text Qdrant vectors
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    monkeypatch.setattr(shots, "PANEL_SIZE_TIE_MARGIN", 0.0)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(shots, "_panel_content_score", _text_scorer)
    pages = _page3()
    units = [({"scene_id": 1, "text": "x"}, "some line")]

    # image OFF (no vectors) → text alone picks the WRONG panel A.
    monkeypatch.setattr(_img_index, "PANEL_IMG_EMBED", True)
    monkeypatch.setattr(_img_index, "img_embed_available", lambda: True)
    monkeypatch.setattr(_img_index, "load_image_vectors", lambda proj: {})
    out0 = shots._match_panels(units, pages, {}, project="proj")
    assert out0[0][0]["description"] == "a"

    # image ON favouring B → the pick flips to the RIGHT panel B.
    _enable_fake_image(monkeypatch)
    monkeypatch.setattr(_img_index, "PANEL_IMG_WEIGHT", 0.35)
    out1 = shots._match_panels(units, pages, {}, project="proj")
    assert out1[0][0]["description"] == "b"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
