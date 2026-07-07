"""Sentence sub-shot matcher (Q&A / explore_answer mode).

Deterministic like test_review_gate: stub embed_batch (no network), no-op the SigLIP image
blend, and mock _panel_content_score so a sentence matches the panel whose description shares
the most words. Covers: lock_panels normalises v1+v2 shapes, sentence splitting merges a short
countdown label, and build_sentence_panels distributes a beat's sentences across its locked
panels (matched → page in the chosen set with start<end; weak match / no candidate → null).
"""
import json
import re

import pytest

import stages.review_gate as rg
import stages.sentence_match as sm
import stages.stage_5.shots as shots
import stages._embedding as _embedding
import stages._panel_index as _panel_index


# ─── deliverable 1: lock_panels normalises both shapes ───────────────────────

def test_lock_panels_normalises_shapes():
    # v2 multi-panel
    v2 = {"panels": [{"page": 5, "panel": 0}, {"page": 5, "panel": 2}], "source": "batcave"}
    assert rg.lock_panels(v2) == [{"page": 5, "panel": 0}, {"page": 5, "panel": 2}]
    # old v1 single-panel → 1-item list
    v1 = {"page": 18, "panel": 1, "source": "batcave"}
    assert rg.lock_panels(v1) == [{"page": 18, "panel": 1}]
    # empty / malformed → []
    assert rg.lock_panels(None) == []
    assert rg.lock_panels({}) == []
    assert rg.lock_panels({"source": "batcave"}) == []


def test_split_sentences_merges_short_label():
    # "The Punisher." (2 words) folds forward into the next sentence, not its own shot.
    out = sm._split_sentences("The Punisher. In Thunderbolts he stood back up. He looked confused.")
    assert out == ["The Punisher. In Thunderbolts he stood back up.", "He looked confused."]
    # a lone trailing fragment folds backward
    assert sm._split_sentences("A full sentence here now. Ok.") == ["A full sentence here now. Ok."]


# ─── fixture builder ─────────────────────────────────────────────────────────

def _write_project(root):
    """2 story scenes. Scene 10 locks 3 panels on page 5; its sentences should split across
    them (2 match, 1 weak→null). Scene 11 has no lock and no panel anchor → all sparse."""
    (root / "review").mkdir(parents=True)
    (root / "preprocessed").mkdir()

    s10 = "Frank Castle vomited on the floor. The hero punched Johnny Blaze hard. Nothing here otherwise."
    s11 = "An unrelated closing thought entirely."
    (root / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 10, "text": s10, "page_ref": 5, "panel_ref": -1},
        {"scene_id": 11, "text": s11, "page_ref": 9, "panel_ref": -1},
    ]}))

    # word_timestamps aligned to the concatenated scene words, 0.1s per word.
    words, t = [], 0.0
    for w in (s10 + " " + s11).split():
        words.append({"word": w, "start": round(t, 3), "end": round(t + 0.09, 3)})
        t += 0.1
    (root / "word_timestamps.json").write_text(json.dumps(words))

    # locks.json — scene 10 locks 3 panels (v2 shape); scene 11 has none.
    (root / "review" / "locks.json").write_text(json.dumps({
        "approved": False, "approved_at": None, "narration_sha1": None,
        "locks": {"10": {"panels": [{"page": 5, "panel": 0}, {"page": 5, "panel": 1},
                                    {"page": 5, "panel": 2}], "source": "batcave"}},
    }))

    descs = ["frank castle vomited floor", "hero punched johnny blaze", "unrelated background rubble"]
    (root / "preprocessed" / "page_005.json").write_text(json.dumps({
        "page_number": 5, "page_type": "story", "source_image": "p5.png",
        "image_dimensions": {"width": 600, "height": 2700},
        "panels": [{"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
                    "description": d, "characters": []} for i, d in enumerate(descs)],
        "text_blocks": [],
    }))


def _fake_score(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    cw = set(re.findall(r"[a-z]+", (chunk_text or "").lower()))
    dw = set(re.findall(r"[a-z]+", str(panel.get("description", "")).lower()))
    sim = min(0.9, 0.15 * len(cw & dw))
    return sim, sim


def _stub(monkeypatch):
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(_panel_index, "load_vectors", lambda project: {})
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    monkeypatch.setattr(shots, "_blend_image_content", lambda *a, **k: None)


# ─── deliverable 2 + 3: build_sentence_panels ────────────────────────────────

def test_build_sentence_panels_matches_and_sparse(tmp_path, monkeypatch):
    _stub(monkeypatch)
    root = tmp_path / "qa"
    _write_project(root)

    out_path = sm.build_sentence_panels(root)          # path arg → no PROJECTS_ROOT needed
    doc = json.loads(out_path.read_text())
    scenes = {s["scene_id"]: s["sentences"] for s in doc["scenes"]}
    assert set(scenes) == {10, 11}

    s10 = scenes[10]
    assert len(s10) == 3
    # every sentence has a valid time span
    for sent in s10:
        assert sent["start"] is not None and sent["end"] is not None
        assert sent["start"] < sent["end"]
    # sentence 0 → panel 0, sentence 1 → panel 1 (page in the chosen lock set)
    assert (s10[0]["page"], s10[0]["panel"]) == (5, 0)
    assert (s10[1]["page"], s10[1]["panel"]) == (5, 1)
    assert s10[0]["score"] >= shots.PANEL_COS_FLOOR
    # every matched panel is one Master locked
    chosen = {(5, 0), (5, 1), (5, 2)}
    for sent in s10:
        assert sent["page"] is None or (sent["page"], sent["panel"]) in chosen
    # sentence 2 ("Nothing here otherwise.") matches no description → sparse null
    assert s10[2]["page"] is None and s10[2]["panel"] is None and s10[2]["score"] is None

    # scene 11 has no lock and no panel anchor → every sentence sparse (null), still timed
    s11 = scenes[11]
    assert s11 and all(x["page"] is None and x["score"] is None for x in s11)
    assert all(x["start"] < x["end"] for x in s11)


def test_sentence_query_blends_drawable_moment(monkeypatch):
    """FIX A: each sentence's match query carries the scene's drawable_moment (the precise visual
    every sentence of a Q&A beat targets) alongside the sentence text."""
    seen = []

    def rec_score(panel, pv, cv, sv, ptb, *, chunk_text, scene_text):
        seen.append(chunk_text)
        return 0.0, 0.0

    monkeypatch.setattr(_embedding, "embed_batch", lambda t: [None] * len(t))
    monkeypatch.setattr(shots, "_panel_content_score", rec_score)
    monkeypatch.setattr(shots, "_blend_image_content", lambda *a, **k: None)

    scene = {"scene_id": 1, "text": "He stood back up."}
    cands = [((5, 0), {"description": "x"}, "p5.png", [])]
    sm._match_sentences(["He stood back up."], [(0.0, 1.0)], scene, cands, {}, "proj",
                        log=lambda *a: None,
                        drawable_moment="Frank on his knees glaring up at a recoiling Ghost Rider")
    assert any("Ghost Rider" in c for c in seen)     # drawable_moment reached the sentence query
    assert any("He stood back up" in c for c in seen)  # sentence text kept


def test_no_lock_falls_back_to_scene_anchor(tmp_path, monkeypatch):
    """When a scene has NO lock but a real (page_ref, panel_ref) anchor, that panel is the
    single candidate (backward-compatible with the pre-multi-select world)."""
    _stub(monkeypatch)
    root = tmp_path / "qa2"
    _write_project(root)
    # drop the lock and give scene 10 a resolvable panel anchor instead
    (root / "review" / "locks.json").write_text(json.dumps({
        "approved": False, "locks": {}}))
    nar = json.loads((root / "narration.json").read_text())
    nar["scenes"][0]["panel_ref"] = 0          # page_ref 5, panel 0
    (root / "narration.json").write_text(json.dumps(nar))

    doc = json.loads(sm.build_sentence_panels(root).read_text())
    s10 = {s["scene_id"]: s["sentences"] for s in doc["scenes"]}[10]
    # only (5,0) is a candidate → the sentence that mentions it matches there, others null
    assert (s10[0]["page"], s10[0]["panel"]) == (5, 0)
    assert all(x["page"] in (5, None) for x in s10)
