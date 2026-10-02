"""MONEY SHOT funnel (stages/review_gate.py) — OCR keyword recall → VLM confirm → tag/boost +
intro pin, entirely gated on answer_context.money_target and switched OFF unless MONEY_SHOT_PIN.

All network is mocked (Claude SDK vision). The OCR channel is the real, pure-lexical
stages.money_shot.ocr_money_hits scoring the fixture pages' own dialog, so the tests are
hermetic.
"""
import json
import re

import pytest

import stages.review_gate as rg
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


def _mount_vlm(monkeypatch, sdk_mock):
    """Wire the Claude-SDK vision judge and the thumb writer — the only things the funnel calls."""
    monkeypatch.setattr(sdk, "sdk_available", lambda: True)
    monkeypatch.setattr(sdk, "sdk_complete_vision", sdk_mock)
    monkeypatch.setattr(rg, "_write_thumb", _fake_thumb)


_MONEY_TARGET = {"money_character": None, "money_object": "vomit",
                 "money_event": "Frank makes Juggernaut throw up"}


def _panels(n_panels, ocr_on=()):
    """n stacked panels; those in `ocr_on` carry OCR dialog naming the money object."""
    return [{"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
             "description": f"p{i}",
             "dialog": [{"ocr": "BLEAARGH! VOMIT EVERYWHERE"}] if i in ocr_on else []}
            for i in range(n_panels)]


def _scenario(tmp_path, n_panels, ocr_on=()):
    """Single-issue Q&A funnel inputs. Returns (root, answer_ctx, pages, page_to_issue,
    groups, cands_by_id)."""
    root = tmp_path / "proj"
    root.mkdir()
    scene = {"scene_id": 2, "text": "beat", "page_ref": 10, "panel_ref": 0}
    panels = _panels(n_panels, ocr_on)
    page = {"page_number": 10, "source_image": "p10.png", "is_story_page": True, "panels": panels}
    pages = {10: page}
    page_to_issue = {10: ""}
    groups = {"": [scene]}
    cands_by_id = {id(scene): [
        {"page": 10, "panel_idx": i, "score": 0.0, "panel": panels[i], "src": "p10.png"}
        for i in range(n_panels)]}
    return root, {"money_target": dict(_MONEY_TARGET)}, pages, page_to_issue, groups, cands_by_id


def _cand(cands_by_id, page, pidx_):
    for cl in cands_by_id.values():
        for c in cl:
            if c["page"] == page and c["panel_idx"] == pidx_:
                return c
    return None


# ─── (b) OCR recall: top-k scored panels inside the scope ─────────────────────

def test_ocr_recall_nominates_the_top_k_scored_panels_in_scope():
    scope = [(1, i) for i in range(4)]
    hits = {(1, 3): 5.0, (1, 0): 1.0, (1, 2): 0.0, (9, 9): 7.0}   # a zero score + an out-of-scope hit

    assert rg._money_ocr_recall(scope, hits, k=1) == [(1, 3)]
    assert rg._money_ocr_recall(scope, hits, k=12) == [(1, 3), (1, 0)]   # score-desc, noise dropped
    assert rg._money_ocr_recall(scope, {}, k=12) == []                    # no hits → nominate nothing
    assert rg._money_ocr_recall(scope, None) == []


# ─── (c) confirm tags flag + bonus ─────────────────────────────────────────────

def test_confirm_tags_and_boosts(tmp_path, monkeypatch):
    root, ac, pages, p2i, groups, cands = _scenario(tmp_path, 3, ocr_on={2})
    calls = []
    _mount_vlm(monkeypatch, _sdk_confirm_only("p010_2.jpg", calls))

    logs = []
    rg._money_funnel(root, ac, pages, p2i, groups, cands, log=logs.append)

    assert not any("funnel skipped" in l for l in logs), logs
    money = _cand(cands, 10, 2)
    assert money["money"] is True and money["money_conf"] == 0.9
    assert money["score"] == pytest.approx(0.0 + rg.MONEY_SHOT_BONUS)   # +2.0 nudge
    assert _cand(cands, 10, 0).get("money") is None                     # others untouched
    assert [c["panel_idx"] for cl in cands.values() for c in cl] == [2, 0, 1]   # boosted → first
    assert len(calls) == 1                                              # confirmed among nominees → no sweep
    assert "p010_2.jpg" in calls[0] and "p010_0.jpg" not in calls[0]    # only the OCR nominee was shown
    # intro pinned to the money panel, first slot
    sp = json.loads((root / "subject_panels.json").read_text())
    assert sp["panels"][0]["page"] == 10 and sp["panels"][0]["panel"] == 2
    assert sp["panels"][0]["money"] is True


# ─── (d) sweep activates when the OCR nominees miss ────────────────────────────

def test_sweep_when_ocr_nominees_miss(tmp_path, monkeypatch):
    # OCR nominates panel 0 only; the panel that actually draws the event (14) has no text.
    root, ac, pages, p2i, groups, cands = _scenario(tmp_path, 15, ocr_on={0})
    calls = []
    _mount_vlm(monkeypatch, _sdk_confirm_only("p010_14.jpg", calls))

    logs = []
    rg._money_funnel(root, ac, pages, p2i, groups, cands, log=logs.append)

    assert not any("funnel skipped" in l for l in logs), logs
    money = _cand(cands, 10, 14)
    assert money["money"] is True and money["money_conf"] == 0.9        # only the sweep found it
    assert _cand(cands, 10, 0).get("money") is None                     # the OCR decoy stays untagged
    assert len(calls) >= 2, "nominees missed → sweep must fire extra VLM calls"
    assert any("SWEEPING" in l for l in logs)


# ─── (e) hard warning when sweep also misses ───────────────────────────────────

def test_hard_warning_when_nothing_drawn(tmp_path, monkeypatch):
    root, ac, pages, p2i, groups, cands = _scenario(tmp_path, 3)
    calls = []
    # SDK confirms nothing (no crop draws the event)
    _mount_vlm(monkeypatch, _sdk_confirm_only("NOPE.jpg", calls))

    logs = []
    rg._money_funnel(root, ac, pages, p2i, groups, cands, log=logs.append)

    assert any("WARNING: money event" in l for l in logs)
    assert any("Fear-Itself" in l for l in logs)
    assert len(calls) == 1                                              # no nominees → straight to the sweep
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


# ─── build_candidates wiring ────────────────────────────────────────────────────

def _qa_project(tmp_path, slug, *, money_target):
    """1-beat Q&A project over one 3-panel page; panel 2 carries OCR naming the money object."""
    proj = tmp_path / slug
    (proj / "preprocessed").mkdir(parents=True)
    (proj / "preprocessed" / "page_010.json").write_text(json.dumps({
        "page_number": 10, "source_image": "p10.png", "page_type": "story",
        "image_dimensions": {"width": 600, "height": 2700},
        "panels": _panels(3, ocr_on={2}), "text_blocks": []}))
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "a beat", "page_ref": 10, "panel_ref": 0}]}))
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    ctx = {"items": [{"source_comic": "X", "source_year": "2020", "reader_url": "u",
                      "drawable_moment": "m", "verification_note": ""}]}
    if money_target:
        ctx["money_target"] = dict(_MONEY_TARGET)
    (proj / "answer_context.json").write_text(json.dumps(ctx))
    return proj


def test_build_candidates_tags_and_pins_the_confirmed_money_panel(tmp_path, monkeypatch):
    """End to end with MONEY_SHOT_PIN on: the OCR channel nominates the panel naming the money
    object, the vision judge confirms it, and candidates.json lists it FIRST with the money flag
    while subject_panels.json pins it to the intro."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "MONEY_SHOT_PIN", True)
    calls = []
    _mount_vlm(monkeypatch, _sdk_confirm_only("p010_2.jpg", calls))
    proj = _qa_project(tmp_path, "money", money_target=True)

    data = json.loads(rg.build_candidates("money").read_text())
    cands = data["beats"][0]["candidates"]
    assert [c["panel"] for c in cands] == [2, 0, 1]
    assert cands[0]["money"] is True and cands[0]["money_conf"] == 0.9
    assert cands[0]["score"] == pytest.approx(rg.MONEY_SHOT_BONUS)
    assert all("money" not in c for c in cands[1:])
    assert len(calls) == 1
    sp = json.loads((proj / "subject_panels.json").read_text())
    assert (sp["panels"][0]["page"], sp["panels"][0]["panel"]) == (10, 2)


# ─── (a) no money_target → build_candidates byte-identical, funnel never fires ─

def test_no_money_target_is_inert(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "MONEY_SHOT_PIN", True)       # funnel switched ON — and must still stand down
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    # funnel MUST bail before any confirm when there's no money_target
    monkeypatch.setattr(rg, "_money_confirm",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("funnel fired w/o money_target")))
    proj = _qa_project(tmp_path, "nomoney", money_target=False)

    data = json.loads(rg.build_candidates("nomoney").read_text())
    cand = data["beats"][0]["candidates"][0]
    assert set(cand) == {"page", "panel", "score", "thumb", "desc", "dialog"}   # no money keys
    assert not (proj / "subject_panels.json").exists()                         # nothing pinned


def test_funnel_stays_off_without_money_shot_pin(tmp_path, monkeypatch):
    """MONEY_SHOT_PIN off (the default): even with a money_target the vision sweep never runs and
    nothing is pinned — Master picks the intro by hand."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "MONEY_SHOT_PIN", False)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    monkeypatch.setattr(rg, "_money_funnel",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("funnel ran while OFF")))
    proj = _qa_project(tmp_path, "pinoff", money_target=True)

    data = json.loads(rg.build_candidates("pinoff").read_text())
    assert [c["panel"] for c in data["beats"][0]["candidates"]] == [0, 1, 2]    # plain page order
    assert not (proj / "subject_panels.json").exists()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
