"""PANEL_TEXT_EMBED=0 refactor (Master 2026-07-24): the render + review-candidate build no longer
touch the embedding backend / Qdrant — Master hand-picks panels, so:

  (a) review_gate.build_candidates lists ALL panels of a beat's issue PAGE-SORTED (no cosine query),
  (b) shots._match_panels assigns UNLOCKED scenes deterministically (first panel of page_ref) and
      never calls load_vectors / embed_batch,
  (c) every RENDERED panel that carries a dialog bbox is bubble-inpainted; a panel with dialog but
      NO bbox is warned; a one-line summary prints.

These tests OPT OUT of tests/conftest.py's cosine-path fixture by setting PANEL_TEXT_EMBED=False.
"""
import json

import pytest

import stages.review_gate as rg
import stages.stage_5.shots as shots
import stages._embedding as _embedding
import stages._panel_index as _panel_index
from stages.stage_5.schema import Shot


def _story_page(pn, src, panels):
    return {"page_number": pn, "source_image": src, "page_type": "story",
            "image_dimensions": {"width": 600, "height": 2700},
            "panels": panels, "text_blocks": []}


def _panel(idx, y, desc="", dialog=None):
    p = {"index": idx, "bbox": {"x": 0, "y": y, "w": 600, "h": 900},
         "description": desc, "characters": []}
    if dialog is not None:
        p["dialog"] = dialog
    return p


# ── (a) build_candidates page-sort, no vector query ───────────────────────────

def test_build_candidates_page_sorted_no_embed(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PANEL_TEXT_EMBED", False)
    monkeypatch.setattr(shots, "PANEL_TEXT_EMBED", False)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    # PROVE the cosine matcher is never invoked in the no-embed path.
    monkeypatch.setattr(shots, "_match_panels",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("_match_panels called")))
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)

    proj = tmp_path / "cand"
    (proj / "preprocessed").mkdir(parents=True)
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 1, "text": "a beat", "page_ref": 10, "panel_ref": 1}]}))
    # panels written OUT OF ORDER on disk to prove the output is (page,panel)-sorted.
    (proj / "preprocessed" / "page_10.json").write_text(json.dumps(
        _story_page(10, "p10.png", [_panel(2, 1800), _panel(0, 0), _panel(1, 900)])))

    data = json.loads(rg.build_candidates("cand", k=0).read_text())
    cands = data["beats"][0]["candidates"]
    assert [(c["page"], c["panel"]) for c in cands] == [(10, 0), (10, 1), (10, 2)]
    assert all(set(c) == {"page", "panel", "score", "thumb", "desc", "dialog"} for c in cands)
    assert all(c["score"] == 0.0 for c in cands)                # no cosine → blank score, no vlm key


# ── (b) shots._match_panels deterministic fallback, no embed / no load_vectors ─

def test_match_panels_deterministic_no_embed(monkeypatch):
    monkeypatch.setattr(shots, "PANEL_TEXT_EMBED", False)
    monkeypatch.setattr(_embedding, "embed_batch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("embed_batch called")))
    monkeypatch.setattr(_panel_index, "load_vectors",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("load_vectors called")))

    pages = {10: _story_page(10, "p10.png", [_panel(0, 0), _panel(1, 900)]),
             11: _story_page(11, "p11.png", [_panel(0, 0), _panel(1, 900)])}
    units = [
        ({"scene_id": 1, "text": "one", "page_ref": 11, "panel_ref": 1}, "one"),  # exact anchor
        ({"scene_id": 2, "text": "two", "page_ref": 10, "panel_ref": -1}, "two"),  # page only → panel 0
        ({"scene_id": 3, "text": "three", "page_ref": 99, "panel_ref": -1}, "three"),  # nearest page
    ]
    out = shots._match_panels(units, pages, {}, project="p")
    got = [(p["_page_number"], p["index"]) for p, _s in out]
    assert got == [(11, 1), (10, 0), (11, 0)]   # anchor honored, page-first, nearest-page fallback


def test_build_shots_headless_no_embed_no_crash(monkeypatch):
    """build_shots (recap per-chunk) runs with an empty panel index and never touches embed/Qdrant."""
    monkeypatch.setattr(shots, "PANEL_TEXT_EMBED", False)
    monkeypatch.setattr(_embedding, "embed_batch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("embed_batch called")))
    monkeypatch.setattr(_panel_index, "load_vectors",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("load_vectors called")))
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    narration = {"scenes": [{"scene_id": 1, "text": "hello world", "page_ref": 10, "panel_ref": 0}]}
    pages = {10: _story_page(10, "p10.png", [_panel(0, 0, "a"), _panel(1, 900, "b")])}
    chunks = [{"text": "hello world", "start": 0.0, "end": 1.5}]
    built = shots.build_shots(narration, caption_chunks=chunks, pages_by_number=pages,
                              scene_timings=[{"scene_id": 1, "start": 0.0, "end": 1.5}], project=None)
    assert built and all(isinstance(s, Shot) for s in built)


# ── (c) bubble-clean applies to every shot with a dialog bbox + warns/summarises ─

def test_bubble_clean_audit_counts_and_warns(monkeypatch):
    logs = []
    pages = {10: _story_page(10, "p10.png", [
        _panel(0, 0, "clean", dialog=[{"ocr": "HI", "bbox": {"x": 5, "y": 5, "w": 50, "h": 20}}]),
        _panel(1, 900, "no-bbox", dialog=[{"ocr": "OW"}]),   # dialog present, NO bbox → warn
        _panel(2, 1800, "silent"),                           # no dialog → neither
    ])}
    s_inpaint = Shot(shot_id=0, scene_id=1, duration_seconds=1.0,
                     panel_bbox={"x": 0, "y": 0, "w": 600, "h": 900}, source_image="p10.png",
                     motion="zoom_in", text_bboxes=[{"x": 5, "y": 5, "w": 50, "h": 20}])
    s_warn = Shot(shot_id=1, scene_id=2, duration_seconds=1.0,
                  panel_bbox={"x": 0, "y": 900, "w": 600, "h": 900}, source_image="p10.png",
                  motion="zoom_in", text_bboxes=[])
    s_silent = Shot(shot_id=2, scene_id=3, duration_seconds=1.0,
                    panel_bbox={"x": 0, "y": 1800, "w": 600, "h": 900}, source_image="p10.png",
                    motion="zoom_in", text_bboxes=[])
    s_custom = Shot(shot_id=3, scene_id=4, duration_seconds=1.0, panel_bbox={}, source_image="",
                    motion="zoom_in", text_bboxes=[], custom_image="/x.png")

    shots._bubble_clean_audit([s_inpaint, s_warn, s_silent, s_custom], pages, log=logs.append)
    blob = "\n".join(logs)
    assert "[stage5] bubble-clean: 1/4 shots inpainted, 1 warned" in blob
    assert "⚠ bubble-clean: p10/1 has dialog but no bbox" in blob


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
