import io
import json
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

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
    assert out == {3: {"image_url": "https://x/a.jpg", "alt_image_url": "",
                       "title": "T", "source_url": "https://x/page",
                       "license": "unknown"}}
    assert parse_hunt_response("not json") == {}
    assert parse_hunt_response('{"images": {"3": {"title": "no url"}}}') == {}


def test_parse_hunt_response_keeps_alt_url():
    raw = ('{"images": {'
           '"2": {"image_url": "https://x/a.jpg", "alt_image_url": " https://y/b.jpg ",'
           '      "title": "T", "source_url": "https://x/p", "license": "PD"},'
           '"3": {"image_url": "https://x/c.jpg", "title": "U",'
           '      "source_url": "https://x/q", "license": "unknown"}}}')
    out = parse_hunt_response(raw)
    assert out[2]["alt_image_url"] == "https://y/b.jpg"   # kept + stripped
    assert out[3]["alt_image_url"] == ""                  # absent → default ""


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


def _png_bytes(w=800, h=700):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_429():
    return urllib.error.HTTPError("https://x", 429, "Too many requests", {}, None)


def test_download_retries_once_on_429(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_429()
        return _FakeResp(_png_bytes())

    monkeypatch.setattr(hunt_mod.urllib.request, "urlopen", fake_urlopen)
    slept = []
    monkeypatch.setattr(hunt_mod, "_sleep", slept.append)
    out = hunt_mod._download("https://x/a.png", tmp_path / "a.png")
    assert out == (800, 700)                      # retry succeeded → dims tuple
    assert calls["n"] == 2
    assert slept == [hunt_mod._RETRY_429_WAIT_S]  # exactly one polite wait


def test_download_gives_up_after_second_429(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        raise _http_429()

    monkeypatch.setattr(hunt_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(hunt_mod, "_sleep", lambda s: None)
    out = hunt_mod._download("https://x/a.png", tmp_path / "a.png")
    assert isinstance(out, str) and out.startswith("http: HTTPError") and "429" in out
    assert calls["n"] == 2                        # ONE retry, no more
    assert not (tmp_path / "a.png").exists()


def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(hunt_mod, "get_art_project_path",
                        lambda name: tmp_path / name)
    monkeypatch.setattr(hunt_mod, "_sleep", lambda s: None)   # no real throttling
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


def test_hunt_primary_reject_alt_succeeds(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda *a, **k: json.dumps(
        {"images": {"2": {"image_url": "https://x/bad.jpg",
                          "alt_image_url": "https://y/good.jpg",
                          "title": "Artist photo",
                          "source_url": "https://x/page", "license": "PD"}}}))

    def fake_download(url, dest):
        if "bad" in url:
            return "http: HTTPError: HTTP Error 404: Not Found"   # primary rejected
        dest.write_bytes(b"x")
        return (1200, 900)

    monkeypatch.setattr(hunt_mod, "_download", fake_download)
    out = hunt_visuals("proj", log=lambda m: None)
    assert out["resolved"] == 1 and out["requested"] == 1
    plan = json.loads((root / "visual_plan.json").read_text())
    assert plan[1]["kind"] == "related"                  # NOT region fallback
    assert plan[1].get("page_ref") == 2
    manifest = json.loads((root / "hunt_manifest.json").read_text())
    assert manifest[0]["image_url"] == "https://y/good.jpg"   # the URL actually used
    assert manifest[0].get("fallback") is None
    ctx = json.loads((root / "art_context.json").read_text())
    assert ctx["extra_image_credits"][0]["source_url"] == "https://x/page"
    narration = json.loads((root / "narration.json").read_text())
    assert narration["scenes"][1]["page_ref"] == 2


def test_hunt_size_reject_reason_logged_and_in_manifest(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda *a, **k: json.dumps(
        {"images": {"2": {"image_url": "https://x/small.jpg",
                          "alt_image_url": "https://y/big.jpg", "title": "t",
                          "source_url": "https://x/p", "license": "PD"}}}))

    def fake_download(url, dest):
        if "small" in url:
            return "too small: 300x200"
        dest.write_bytes(b"x")
        return (1200, 900)

    monkeypatch.setattr(hunt_mod, "_download", fake_download)
    logs = []
    out = hunt_visuals("proj", log=logs.append)
    assert out["resolved"] == 1                          # rescued by the alt URL
    assert any("https://x/small.jpg" in m and "too small: 300x200" in m
               for m in logs)                            # per-URL failure logged
    manifest = json.loads((root / "hunt_manifest.json").read_text())
    assert manifest[0]["attempted"] == [
        {"url": "https://x/small.jpg", "reason": "too small: 300x200"}]
    assert manifest[0]["image_url"] == "https://y/big.jpg"


def test_hunt_sdk_failure_falls_back_to_unused_region(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda *a, **k: None)
    out = hunt_visuals("proj", log=lambda m: None)
    assert out["resolved"] == 0
    plan = json.loads((root / "visual_plan.json").read_text())
    assert plan[1]["kind"] == "painting_region" and plan[1]["fallback"]
    narration = json.loads((root / "narration.json").read_text())
    assert narration["scenes"][1]["panel_ref"] == plan[1]["panel_ref"] >= 0


def _project_two_related(tmp_path, monkeypatch):
    """4 scenes: full(intro), related A, related B, full(outro)."""
    monkeypatch.setattr(hunt_mod, "get_art_project_path",
                        lambda name: tmp_path / name)
    monkeypatch.setattr(hunt_mod, "_sleep", lambda s: None)   # no real throttling
    root = tmp_path / "proj"
    (root / "preprocessed").mkdir(parents=True)
    (root / "preprocessed" / "page_001_abc.json").write_text(json.dumps(_page(1)))
    narration = {"scenes": [
        {"scene_id": 1, "text": "hook", "page_ref": 1, "panel_ref": -1, "is_intro": True},
        {"scene_id": 2, "text": "artist bio", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 3, "text": "era photo", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 4, "text": "outro", "page_ref": 1, "panel_ref": -1, "is_outro": True}]}
    plan = [{"scene_id": 1, "kind": "painting_full", "panel_ref": -1, "subject": "", "motion": "static", "fallback": ""},
            {"scene_id": 2, "kind": "related", "panel_ref": -1,
             "subject": "portrait of the artist", "motion": "pan_right", "fallback": ""},
            {"scene_id": 3, "kind": "related", "panel_ref": -1,
             "subject": "photo of the era", "motion": "pan_right", "fallback": ""},
            {"scene_id": 4, "kind": "painting_full", "panel_ref": -1, "subject": "", "motion": "zoom_out", "fallback": ""}]
    (root / "narration.json").write_text(json.dumps(narration))
    (root / "visual_plan.json").write_text(json.dumps(plan))
    (root / "art_context.json").write_text(json.dumps(
        {"title": "T", "sources": [], "artworks": [{"object_id": 1}],
         "summary": {"characters": [{"name": "Artist"}]}}))
    return root


def test_hunt_duplicate_image_falls_back(tmp_path, monkeypatch):
    root = _project_two_related(tmp_path, monkeypatch)
    same = {"image_url": "https://x/same.jpg", "title": "One photo",
            "source_url": "https://x/p", "license": "PD"}
    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda *a, **k: json.dumps(
        {"images": {"2": same, "3": dict(same)}}))
    monkeypatch.setattr(hunt_mod, "_download",
                        lambda url, dest: (dest.write_bytes(b"x"), (1200, 900))[1])
    out = hunt_visuals("proj", log=lambda m: None)
    assert out["requested"] == 2 and out["resolved"] == 1
    plan = json.loads((root / "visual_plan.json").read_text())
    by_id = {d["scene_id"]: d for d in plan}
    assert by_id[2]["kind"] == "related" and by_id[2]["page_ref"] == 2
    assert by_id[3]["kind"] == "painting_region"
    assert by_id[3]["fallback"] == "duplicate image"
    narration = json.loads((root / "narration.json").read_text())
    assert narration["scenes"][2]["panel_ref"] == by_id[3]["panel_ref"] >= 0


def test_hunt_orphan_decl_skipped(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    plan = json.loads((root / "visual_plan.json").read_text())
    plan.append({"scene_id": 99, "kind": "related", "panel_ref": -1,
                 "subject": "ghost subject", "motion": "pan_right", "fallback": ""})
    (root / "visual_plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda *a, **k: json.dumps(
        {"images": {"2": {"image_url": "https://x/a.jpg", "title": "t",
                          "source_url": "https://x/p", "license": "PD"}}}))
    monkeypatch.setattr(hunt_mod, "_download",
                        lambda url, dest: (dest.write_bytes(b"x"), (1200, 900))[1])
    out = hunt_visuals("proj", log=lambda m: None)   # must NOT raise
    assert out["requested"] == 2 and out["resolved"] == 1
    plan = json.loads((root / "visual_plan.json").read_text())
    by_id = {d["scene_id"]: d for d in plan}
    assert by_id[2].get("page_ref") == 2             # real scene handled normally
    assert "page_ref" not in by_id[99]               # orphan untouched
    assert by_id[99]["fallback"] == ""
    narration = json.loads((root / "narration.json").read_text())
    assert narration["scenes"][1]["page_ref"] == 2


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
