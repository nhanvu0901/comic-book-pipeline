import json
from pathlib import Path

import pytest

from art_pipeline import hunt as hunt_mod
from art_pipeline.hunt import (
    build_hunt_prompt, hunt_visuals, parse_hunt_response, pick_fallback_region,
)


def _page(n, n_panels=4):
    return {"page_number": n, "source_image": f"/tmp/p{n}.jpg",
            "image_dimensions": {"width": 2000, "height": 1500},
            "preprocessing_method": "vlm-regions", "page_summary": "artwork",
            "panels": [{"index": i, "bbox": {"x": 0, "y": 0, "w": 10, "h": 10},
                        "description": f"region {i}"} for i in range(n_panels)]}


def test_parse_hunt_response_string_keys_and_skips():
    raw = ('{"images": {"3": {"image_url": "https://x/a.jpg", "title": "T",'
           ' "source_url": "https://x/page", "license": "unknown"}}}')
    out = parse_hunt_response(raw)
    assert out == {3: {"image_url": "https://x/a.jpg", "title": "T",
                       "source_url": "https://x/page", "license": "unknown"}}
    assert parse_hunt_response("not json") == {}
    assert parse_hunt_response('{"images": {"3": {"title": "no url"}}}') == {}


def test_build_hunt_prompt_mentions_xray_priority_and_subjects():
    scenes = [{"scene_id": 3, "text": "Seurat in his studio."}]
    decls = [{"scene_id": 3, "kind": "related", "subject": "portrait of Seurat"}]
    p = build_hunt_prompt({"title": "Circus Sideshow"}, scenes, decls)
    assert "x-ray" in p.lower()
    assert "portrait of Seurat" in p
    assert '"3"' in p


def test_pick_fallback_region_skips_used_and_neighbors():
    pages = {1: _page(1)}
    scene = {"scene_id": 5, "page_ref": 1}
    used = {("r", 1, 0), ("r", 1, 1)}
    neighbors = {("r", 1, 2)}
    assert pick_fallback_region(scene, pages, used, neighbors) == (1, 3)
    used |= {("r", 1, 2), ("r", 1, 3)}
    assert pick_fallback_region(scene, pages, used, neighbors) is None


def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(hunt_mod, "get_art_project_path",
                        lambda name: tmp_path / name)
    root = tmp_path / "proj"
    (root / "preprocessed").mkdir(parents=True)
    page = _page(1)
    (root / "preprocessed" / "page_001_abc.json").write_text(json.dumps(page))
    narration = {"scenes": [
        {"scene_id": 1, "text": "hook", "page_ref": 1, "panel_ref": -1, "is_intro": True},
        {"scene_id": 2, "text": "artist bio", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 3, "text": "outro", "page_ref": 1, "panel_ref": -1, "is_outro": True}]}
    plan = [{"scene_id": 1, "kind": "painting_full", "panel_ref": -1, "subject": "", "motion": "static", "fallback": ""},
            {"scene_id": 2, "kind": "related", "panel_ref": -1,
             "subject": "portrait of the artist", "motion": "pan_right", "fallback": ""},
            {"scene_id": 3, "kind": "painting_full", "panel_ref": -1, "subject": "", "motion": "zoom_out", "fallback": ""}]
    (root / "narration.json").write_text(json.dumps(narration))
    (root / "visual_plan.json").write_text(json.dumps(plan))
    (root / "art_context.json").write_text(json.dumps(
        {"title": "T", "sources": [], "artworks": [{"object_id": 1}],
         "summary": {"characters": [{"name": "Artist"}]}}))
    return root


def test_hunt_happy_path(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda system, user, log=None: json.dumps(
        {"images": {"2": {"image_url": "https://x/a.jpg", "title": "Artist photo",
                          "source_url": "https://x/page", "license": "PD"}}}))
    monkeypatch.setattr(hunt_mod, "_download",
                        lambda url, dest: (dest.write_bytes(b"x"), (1200, 900))[1])
    out = hunt_visuals("proj", log=lambda m: None)
    assert out["resolved"] == 1 and out["requested"] == 1
    narration = json.loads((root / "narration.json").read_text())
    s2 = narration["scenes"][1]
    assert s2["page_ref"] == 2 and s2["panel_ref"] == 0   # re-pointed to new page
    plan = json.loads((root / "visual_plan.json").read_text())
    assert plan[1].get("page_ref") == 2
    ctx = json.loads((root / "art_context.json").read_text())
    assert ctx["extra_image_credits"][0]["source_url"] == "https://x/page"
    pages = list((root / "preprocessed").glob("page_002_*.json"))
    assert len(pages) == 1
    assert json.loads(pages[0].read_text())["preprocessing_method"] == "web-related"
    assert (root / "hunt_manifest.json").exists()


def test_hunt_sdk_failure_falls_back_to_unused_region(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda *a, **k: None)
    out = hunt_visuals("proj", log=lambda m: None)
    assert out["resolved"] == 0
    plan = json.loads((root / "visual_plan.json").read_text())
    assert plan[1]["kind"] == "painting_region" and plan[1]["fallback"]
    narration = json.loads((root / "narration.json").read_text())
    assert narration["scenes"][1]["panel_ref"] == plan[1]["panel_ref"] >= 0


def test_hunt_force_restores_then_redoes(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda *a, **k: json.dumps(
        {"images": {"2": {"image_url": "https://x/a.jpg", "title": "t",
                          "source_url": "https://x/p", "license": "unknown"}}}))
    monkeypatch.setattr(hunt_mod, "_download",
                        lambda url, dest: (dest.write_bytes(b"x"), (1200, 900))[1])
    hunt_visuals("proj", log=lambda m: None)
    out2 = hunt_visuals("proj", log=lambda m: None)        # no force → skip
    assert out2.get("skipped")
    out3 = hunt_visuals("proj", force=True, log=lambda m: None)
    assert out3["resolved"] == 1
    # still exactly one web-related page (no page-number creep)
    rel = [p for p in (root / "preprocessed").glob("page_*.json")
           if json.loads(p.read_text())["preprocessing_method"] == "web-related"]
    assert len(rel) == 1
