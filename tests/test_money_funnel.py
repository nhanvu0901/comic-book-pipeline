"""MONEY SHOT funnel (stages/review_gate.py) — 3-channel recall → VLM confirm → tag/boost +
intro pin, entirely gated on answer_context.money_target.

All network is mocked (embed_batch, SigLIP load/embed, Claude SDK vision, OCR hits), so the
tests are hermetic. The money_shot sibling module (ocr_money_hits) may not exist yet — these
tests drive channel (i) text-cosine + hand-fed OCR, never importing money_shot.
"""
import json
import math
import re

import numpy as np
import pytest

import stages.review_gate as rg
import stages.stage_5.shots as shots
import stages._embedding as emb
import stages._panel_index as pidx
import stages._img_index as img
import stages._claude_sdk as sdk


# ─── helpers ─────────────────────────────────────────────────────────────────

def _fake_thumb(src, bbox, out, **k):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"x")
    return True


def _sdk_confirm_only(target_filename, calls):
    """Mock sdk_complete_vision: confidence 0.9 for the crop whose path ends in
    `target_filename`, nothing for the rest. Records each call's user prompt in `calls`."""
    def _mock(system, user, log=None):
        calls.append(user)
        lines = [ln for ln in user.splitlines() if re.match(r"\s*\d+\.\s", ln)]
        for i, ln in enumerate(lines, start=1):
            if target_filename in ln:
                return json.dumps({"panels": [{"index": i, "confidence": 0.9}]})
        return json.dumps({"panels": []})
    return _mock


def _mount_channels(monkeypatch, panel_vecs, query_vec, sdk_mock):
    """Wire channel (i) text cosine + SDK; channels (ii) SigLIP and (iii) OCR are OFF."""
    monkeypatch.setattr(emb, "embed_batch", lambda texts: [np.asarray(query_vec, dtype="float32")])
    monkeypatch.setattr(pidx, "load_vectors", lambda slug: panel_vecs)
    monkeypatch.setattr(img, "load_image_vectors", lambda slug: {})
    monkeypatch.setattr(img, "embed_texts", lambda texts, **k: None)
    monkeypatch.setattr(sdk, "sdk_available", lambda: True)
    monkeypatch.setattr(sdk, "sdk_complete_vision", sdk_mock)
    monkeypatch.setattr(rg, "_write_thumb", _fake_thumb)


def _scenario(tmp_path, n_panels, vec_fn):
    """Single-issue Q&A funnel inputs. Returns (root, answer_ctx, pages, page_to_issue,
    groups, cands_by_id, panel_vecs)."""
    root = tmp_path / "proj"
    root.mkdir()
    scene = {"scene_id": 2, "text": "beat", "page_ref": 10, "panel_ref": 0}
    panels = [{"index": i, "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}, "description": f"p{i}"}
              for i in range(n_panels)]
    page = {"page_number": 10, "source_image": "p10.png", "is_story_page": True, "panels": panels}
    pages = {10: page}
    page_to_issue = {10: ""}
    groups = {"": [scene]}
    cands_by_id = {id(scene): [
        {"page": 10, "panel_idx": i, "score": 1.0, "cosine": 0.1, "panel": panels[i], "src": "p10.png"}
        for i in range(n_panels)]}
    answer_ctx = {"money_target": {
        "money_character": None, "money_object": None,
        "money_event": "Frank makes Juggernaut throw up",
        "query_text": "Frank makes Juggernaut throw up"}}
    panel_vecs = {(10, i): np.asarray(vec_fn(i), dtype="float32") for i in range(n_panels)}
    return root, answer_ctx, pages, page_to_issue, groups, cands_by_id, panel_vecs


def _cand(cands_by_id, page, pidx_):
    for cl in cands_by_id.values():
        for c in cl:
            if c["page"] == page and c["panel_idx"] == pidx_:
                return c
    return None


# ─── (b) recall union: 3 channels ─────────────────────────────────────────────

def test_recall_union_three_channels():
    """Each channel nominates its own top-k; union deduped in channel order (text, image, OCR)."""
    scope = [(1, i) for i in range(4)]
    qv = np.asarray([1.0, 0.0], dtype="float32")
    panel_vecs = {(1, 0): np.asarray([1.0, 0.0]),   # text winner
                  (1, 1): np.asarray([0.2, 0.98]),
                  (1, 2): np.asarray([0.0, 1.0]),
                  (1, 3): np.asarray([0.0, 1.0])}
    qimg = np.asarray([0.0, 1.0], dtype="float32")
    img_vecs = {(1, 2): np.asarray([0.0, 1.0]),      # image winner
                (1, 0): np.asarray([1.0, 0.0])}
    ocr_hits = {(1, 3): 5.0, (1, 0): 1.0}            # OCR winner (1,3)

    union = rg._money_recall_union(scope, qv, panel_vecs, qimg, img_vecs, ocr_hits, k=1)
    # top-1 per channel: text (1,0), image (1,2), OCR (1,3) → union of the three
    assert union == [(1, 0), (1, 2), (1, 3)]

    # a dead channel contributes nothing; dedupe holds
    union2 = rg._money_recall_union(scope, None, {}, None, {}, ocr_hits, k=12)
    assert union2 == [(1, 3), (1, 0)]               # OCR only, score-desc


# ─── (c) confirm tags flag + bonus ─────────────────────────────────────────────

def test_confirm_tags_and_boosts(tmp_path, monkeypatch):
    root, ac, pages, p2i, groups, cands, pvecs = _scenario(
        tmp_path, 3, lambda i: [1.0, 0.0] if i == 0 else [0.0, 1.0])
    calls = []
    _mount_channels(monkeypatch, pvecs, [1.0, 0.0], _sdk_confirm_only("p010_0.jpg", calls))

    logs = []
    rg._money_funnel(root, ac, pages, p2i, groups, cands, log=logs.append)

    assert not any("funnel skipped" in l for l in logs), logs
    money = _cand(cands, 10, 0)
    assert money["money"] is True and money["money_conf"] == 0.9
    assert money["score"] == pytest.approx(1.0 + rg.MONEY_SHOT_BONUS)   # +2.0 nudge
    assert _cand(cands, 10, 1).get("money") is None                     # others untouched
    assert len(calls) == 1                                              # confirmed in union → no sweep
    # intro pinned to the money panel, first slot
    sp = json.loads((root / "subject_panels.json").read_text())
    assert sp["panels"][0]["page"] == 10 and sp["panels"][0]["panel"] == 0
    assert sp["panels"][0]["money"] is True


# ─── (d) sweep activates when the top-K union misses ───────────────────────────

def test_sweep_when_union_misses(tmp_path, monkeypatch):
    def vec(i):
        if i == 14:
            return [0.0, 1.0]                        # cosine 0 → never nominated by recall
        x = (14 - i) / 14.0
        return [x, math.sqrt(max(0.0, 1 - x * x))]

    root, ac, pages, p2i, groups, cands, pvecs = _scenario(tmp_path, 15, vec)
    calls = []
    _mount_channels(monkeypatch, pvecs, [1.0, 0.0], _sdk_confirm_only("p010_14.jpg", calls))

    logs = []
    rg._money_funnel(root, ac, pages, p2i, groups, cands, log=logs.append)

    assert not any("funnel skipped" in l for l in logs), logs
    money = _cand(cands, 10, 14)
    assert money["money"] is True and money["money_conf"] == 0.9        # only the sweep found it
    assert len(calls) >= 2, "union missed → sweep must fire extra VLM calls"
    assert any("SWEEPING" in l for l in logs)


# ─── (e) hard warning when sweep also misses ───────────────────────────────────

def test_hard_warning_when_nothing_drawn(tmp_path, monkeypatch):
    root, ac, pages, p2i, groups, cands, pvecs = _scenario(
        tmp_path, 3, lambda i: [0.0, 1.0])
    calls = []
    # SDK confirms nothing (no crop draws the event)
    _mount_channels(monkeypatch, pvecs, [1.0, 0.0], _sdk_confirm_only("NOPE.jpg", calls))

    logs = []
    rg._money_funnel(root, ac, pages, p2i, groups, cands, log=logs.append)

    assert any("WARNING: money event" in l for l in logs)
    assert any("Fear-Itself" in l for l in logs)
    assert not any(c.get("money") for cl in cands.values() for c in cl)  # nothing tagged
    assert not (root / "subject_panels.json").exists()                  # nothing pinned


# ─── (f) intro pin respects manual:true ────────────────────────────────────────

def test_intro_pin_respects_manual(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    original = {"manual": True, "subject": "Juggernaut",
                "panels": [{"page": 5, "panel": 1, "score": 9.0}]}
    (root / "subject_panels.json").write_text(json.dumps(original))

    rg._pin_money_intro(root, (10, 2), 0.95)
    assert json.loads((root / "subject_panels.json").read_text()) == original  # untouched

    # non-manual → pinned FIRST, existing kept after
    root2 = tmp_path / "proj2"
    root2.mkdir()
    (root2 / "subject_panels.json").write_text(json.dumps(
        {"subject": "X", "panels": [{"page": 3, "panel": 0, "score": 1.0}]}))
    rg._pin_money_intro(root2, (10, 2), 0.95)
    sp = json.loads((root2 / "subject_panels.json").read_text())
    assert sp["panels"][0] == {"page": 10, "panel": 2, "score": 0.95,
                               "money": True, "force_intro": True}
    assert {"page": 3, "panel": 0, "score": 1.0} in sp["panels"]


# ─── (a) no money_target → build_candidates byte-identical, funnel never fires ─

def test_no_money_target_is_inert(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "nomoney"
    proj.mkdir()
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "a beat", "page_ref": 10, "panel_ref": 0}]}))
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (proj / "answer_context.json").write_text(json.dumps({"items": [
        {"source_comic": "X", "source_year": "2020", "reader_url": "u",
         "drawable_moment": "m", "verification_note": ""}]}))   # NO money_target

    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        for _ in units:
            candidates_out.append([{"page": 10, "panel_idx": 0, "score": 5.0, "cosine": 0.5,
                "panel": {"description": "d", "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}},
                "src": "p10.png"}])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_dialog_rescore", lambda cl, q, pg: cl)
    monkeypatch.setattr(rg, "_vlm_rank_top", lambda cl, q, root, log=print: cl)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    # funnel MUST bail before any confirm when there's no money_target
    monkeypatch.setattr(rg, "_money_confirm",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("funnel fired w/o money_target")))

    data = json.loads(rg.build_candidates("nomoney", k=5).read_text())
    cand = data["beats"][0]["candidates"][0]
    assert set(cand) == {"page", "panel", "score", "thumb", "desc", "dialog"}   # no money keys
    assert not (proj / "subject_panels.json").exists()                         # nothing pinned


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
