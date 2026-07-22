"""Drift/dup fixes (wolverine-debt-of-death, 2026-07-18):

  FIX 1  content-align scene→beat (write_script._content_align_scenes / reanchor_narration):
         positional scene[i]→beat[i] drifts when the writer's scene order slips from beat
         order; content-align recovers the right beat (page_ref) while keeping scene order.
  FIX 2  fragment spread (shots._match_panels): a scene's several fragment units all carry
         its ONE anchor → binding every one to key_panels[0] repeats a panel; siblings must
         spread to DISTINCT panels of the anchor page.
  FIX 3  anchor re-check (shots._match_panels): a bound anchor whose cosine is far below the
         best-content panel's (a wrong page_ref) enters the VLM rerank instead of skipping it.
"""
import numpy as np
import pytest

import stages._embedding as _embedding
import stages.stage_3.write_script as ws
import stages.stage_5.shots as shots
from stages.stage_3.schema import Beat


# ── FIX 1: content-align ────────────────────────────────────────────────────────────────
_VOCAB = ["ninja", "arrive", "robot", "ending"]


def _kw_embed(texts):
    """One-hot over a fixed vocab → cosine 1.0 for a shared keyword (deterministic, offline)."""
    out = []
    for t in texts:
        v = [1.0 if w in t.lower() else 0.0 for w in _VOCAB]
        out.append(v if any(v) else [0.25, 0.25, 0.25, 0.25])
    return out


def _beats():
    return [Beat(id=1, function="COLD_OPEN", name="ninja assassins", summary="ninja kill"),
            Beat(id=2, function="SETUP", name="hero arrive", summary="arrive debt"),
            Beat(id=3, function="CLIMAX", name="robot swarm", summary="robot unleashed"),
            Beat(id=4, function="LANDING", name="final ending", summary="ending image")]


def test_content_align_recovers_shifted_mapping(monkeypatch):
    # scenes in the writer's (shifted) order: a framing 'arrive' line opens BEFORE the
    # cold-open 'ninja' — positional would map scene0→beat0(ninja); content must map by topic.
    monkeypatch.setattr(_embedding, "embed_batch", _kw_embed)
    scene_texts = ["hero arrive in japan", "ninja kill superintendent",
                   "robot swarm attacks", "hero tells the ending"]
    f = ws._content_align_scenes(scene_texts, _beats())
    assert f == [1, 0, 2, 3], f            # arrive→beat1, ninja→beat0 (swap recovered)


def test_content_align_allows_two_scenes_one_beat(monkeypatch):
    # Two near-duplicate 'arrive' scenes: independent argmax lets BOTH point at beat1
    # (a 1:1 bijection would exile one to a far wrong beat).
    monkeypatch.setattr(_embedding, "embed_batch", _kw_embed)
    f = ws._content_align_scenes(["hero arrive one", "hero arrive two", "robot swarm"], _beats())
    assert f[0] == 1 and f[1] == 1 and f[2] == 2, f


def test_content_align_no_embed_falls_back(monkeypatch):
    monkeypatch.setenv("STAGE3_NO_EMBED", "1")
    assert ws._content_align_scenes(["a", "b"], _beats()) is None


def test_content_align_low_confidence_falls_back(monkeypatch):
    # scenes share NO keyword with any beat → every cosine is weak (~0.5) → below a high
    # MIN_COS → None (don't trust a guess when nothing matches; keep positional).
    monkeypatch.setattr(_embedding, "embed_batch", _kw_embed)
    monkeypatch.setenv("ANCHOR_ALIGN_MIN_COS", "0.9")
    assert ws._content_align_scenes(["foo", "bar", "baz"], _beats()) is None


def test_reanchor_narration_rewrites_drifted_page_refs(monkeypatch):
    monkeypatch.setattr(_embedding, "embed_batch", _kw_embed)
    beats_json = [{"id": 1, "name": "ninja assassins", "summary": "ninja kill",
                   "key_panels": [{"page": 5, "panel": 0}]},
                  {"id": 2, "name": "hero arrive", "summary": "arrive debt",
                   "key_panels": [{"page": 7, "panel": 2}]},
                  {"id": 3, "name": "robot swarm", "summary": "robot unleashed",
                   "key_panels": [{"page": 9, "panel": 1}]}]
    # scenes DRIFTED by the old positional anchor: 'ninja' scene wrongly carries page 7.
    narration = {"beats": beats_json, "scenes": [
        {"scene_id": 1, "text": "ninja kill scene", "page_ref": 7, "panel_ref": 2, "beat_id": 2},
        {"scene_id": 2, "text": "hero arrive scene", "page_ref": 9, "panel_ref": 1, "beat_id": 3},
        {"scene_id": 3, "text": "robot swarm scene", "page_ref": 5, "panel_ref": 0, "beat_id": 1},
    ]}
    assert ws.reanchor_narration(narration) is True
    by_text = {s["text"]: (s["page_ref"], s["panel_ref"]) for s in narration["scenes"]}
    assert by_text["ninja kill scene"] == (5, 0)     # → beat1 (was 7,2)
    assert by_text["hero arrive scene"] == (7, 2)     # → beat2 (was 9,1)
    assert by_text["robot swarm scene"] == (9, 1)     # → beat3 (was 5,0)


# ── FIX 2 + FIX 3: matcher (mock scorer + embed, same pattern as test_panel_match) ────────
def _no_network(monkeypatch):
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(shots, "PANEL_SIZE_TIE_MARGIN", 0.0)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)


def _fake_score(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    tag = str(panel.get("description", "")).strip().lower()
    return (10.0, 0.9) if tag and tag in (chunk_text or "").lower() else (0.5, 0.1)


def _page(tags, page=10):
    return {page: {
        "source_image": f"p{page}.png",
        "image_dimensions": {"width": 600, "height": 900 * len(tags)},
        "page_type": "story",
        "panels": [{"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
                    "description": t, "characters": []} for i, t in enumerate(tags)],
        "text_blocks": [],
    }}


def test_fragment_spread_gives_siblings_distinct_panels(monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = _page(["alpha", "beta", "gamma", "delta"])
    scene = {"scene_id": 1, "text": "s", "page_ref": 10, "panel_ref": 0}   # anchor = panel 0
    # 3 fragment units of ONE scene, each best-matching a DIFFERENT same-page panel.
    units = [(scene, "alpha"), (scene, "beta"), (scene, "gamma")]
    idx = [p["index"] if p else None for p, _ in shots._match_panels(units, pages, {})]
    assert len(set(idx)) == 3, f"siblings must get distinct panels, got {idx}"
    assert idx[0] == 0, idx                     # frag 0 keeps the grounded anchor


def test_fragment_spread_off_repeats(monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    monkeypatch.setattr(shots, "FRAGMENT_SPREAD", False)
    pages = _page(["alpha", "beta", "gamma"])
    scene = {"scene_id": 1, "text": "s", "page_ref": 10, "panel_ref": 0}
    units = [(scene, "alpha"), (scene, "beta"), (scene, "gamma")]
    idx = [p["index"] if p else None for p, _ in shots._match_panels(units, pages, {})]
    assert idx == [0, 0, 0], f"spread OFF must collapse siblings to the anchor, got {idx}"


def test_anchor_recheck_lets_vlm_override_wrong_bind(monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_RERANK", True)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    # scene anchored to panel 0 (alpha) but the line is about 'beta' → anchor cosine 0.1,
    # best-content 0.9, gap 0.8 > margin → recheck; VLM picks panel 1.
    monkeypatch.setattr(shots, "_vlm_rerank", lambda text, cands: 1)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "beta line", "page_ref": 10, "panel_ref": 0}
    out = shots._match_panels([(scene, "beta")], pages, {})
    assert out[0][0]["index"] == 1, "VLM must override the wrong bound anchor"


def test_anchor_recheck_skipped_when_cosine_agrees(monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(shots, "PANEL_RERANK", True)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    # anchor panel 0 (alpha) and the line IS 'alpha' → cosine agrees → no VLM, bind stands.
    called = {"n": 0}
    def _spy(text, cands):
        called["n"] += 1
        return 1
    monkeypatch.setattr(shots, "_vlm_rerank", _spy)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "alpha line", "page_ref": 10, "panel_ref": 0}
    out = shots._match_panels([(scene, "alpha")], pages, {})
    assert out[0][0]["index"] == 0, "agreeing bind must stand"
    assert called["n"] == 0, "VLM must not be called when the bind agrees with cosine"
