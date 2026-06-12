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


def test_ua_for_picks_wikimedia_policy_ua():
    assert hunt_mod._ua_for(
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg") == hunt_mod._UA_WIKIMEDIA
    assert hunt_mod._ua_for(
        "https://en.wikipedia.org/static/x.png") == hunt_mod._UA_WIKIMEDIA
    assert hunt_mod._ua_for("https://example.com/x.jpg") == hunt_mod._UA
    # similar-looking but DIFFERENT domain must not match the suffix check
    assert hunt_mod._ua_for("https://notwikipedia.org.evil.com/x.jpg") == hunt_mod._UA


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
    monkeypatch.setattr(hunt_mod, "sdk_complete_web",
                        lambda system, user, max_turns=None, log=None: json.dumps(
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


def test_hunt_scales_sdk_max_turns_with_subject_count(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)            # 1 related decl
    seen = {}

    def fake_sdk(system, user, *, max_turns=None, log=None):
        seen["max_turns"] = max_turns
        return None

    monkeypatch.setattr(hunt_mod, "sdk_complete_web", fake_sdk)
    hunt_visuals("proj", log=lambda m: None)
    assert seen["max_turns"] == 12                    # floor: max(12, 4*1+4)

    # 6 related decls → 4*6+4 = 28
    root6 = tmp_path / "proj6"
    (root6 / "preprocessed").mkdir(parents=True)
    (root6 / "preprocessed" / "page_001_abc.json").write_text(json.dumps(_page(1)))
    scenes = [{"scene_id": 1, "text": "hook", "page_ref": 1, "panel_ref": -1, "is_intro": True}]
    plan = [{"scene_id": 1, "kind": "painting_full", "panel_ref": -1,
             "subject": "", "motion": "static", "fallback": ""}]
    for i in range(2, 8):
        scenes.append({"scene_id": i, "text": f"s{i}", "page_ref": 1, "panel_ref": -1})
        plan.append({"scene_id": i, "kind": "related", "panel_ref": -1,
                     "subject": f"subject {i}", "motion": "pan_right", "fallback": ""})
    scenes.append({"scene_id": 8, "text": "outro", "page_ref": 1, "panel_ref": -1, "is_outro": True})
    plan.append({"scene_id": 8, "kind": "painting_full", "panel_ref": -1,
                 "subject": "", "motion": "zoom_out", "fallback": ""})
    (root6 / "narration.json").write_text(json.dumps({"scenes": scenes}))
    (root6 / "visual_plan.json").write_text(json.dumps(plan))
    (root6 / "art_context.json").write_text(json.dumps(
        {"title": "T", "sources": [], "artworks": [], "summary": {}}))
    hunt_visuals("proj6", log=lambda m: None)
    assert seen["max_turns"] == 28


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


def test_hunt_one_sdk_session_per_chapter(tmp_path, monkeypatch):
    """chapters.json tồn tại + decls có chapter_id 1,1,2 → đúng 2 lần gọi sdk_complete_web;
    max_turns lần 1 = max(12, 4*2+4)=12, lần 2 = max(12, 4*1+4)=12. (floor 12 cho cả hai)"""
    monkeypatch.setattr(hunt_mod, "get_art_project_path",
                        lambda name: tmp_path / name)
    monkeypatch.setattr(hunt_mod, "_sleep", lambda s: None)
    root = tmp_path / "proj_ch"
    (root / "preprocessed").mkdir(parents=True)
    (root / "preprocessed" / "page_001_abc.json").write_text(json.dumps(_page(1)))
    # chapters.json simulates a long-form project (logic reads chapter_id from decl, not file)
    (root / "chapters.json").write_text(json.dumps(
        [{"chapter_id": 1, "title": "Ch1"}, {"chapter_id": 2, "title": "Ch2"}]))
    narration = {"scenes": [
        {"scene_id": 1, "text": "hook", "page_ref": 1, "panel_ref": -1, "is_intro": True},
        {"scene_id": 2, "text": "scene2", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 3, "text": "scene3", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 4, "text": "outro", "page_ref": 1, "panel_ref": -1, "is_outro": True}]}
    plan = [
        {"scene_id": 1, "kind": "painting_full", "panel_ref": -1, "subject": "", "motion": "static", "fallback": ""},
        {"scene_id": 2, "kind": "related", "panel_ref": -1, "chapter_id": 1,
         "subject": "subject A", "motion": "pan_right", "fallback": ""},
        {"scene_id": 3, "kind": "related", "panel_ref": -1, "chapter_id": 1,
         "subject": "subject B", "motion": "pan_left", "fallback": ""},
        {"scene_id": 4, "kind": "related", "panel_ref": -1, "chapter_id": 2,
         "subject": "subject C", "motion": "pan_right", "fallback": ""},
    ]
    (root / "narration.json").write_text(json.dumps(narration))
    (root / "visual_plan.json").write_text(json.dumps(plan))
    (root / "art_context.json").write_text(json.dumps(
        {"title": "T", "sources": [], "artworks": [], "summary": {}}))

    sdk_calls: list[int] = []

    def fake_sdk(system, user, *, max_turns=None, log=None):
        sdk_calls.append(max_turns)
        return '{"images": {}}'

    monkeypatch.setattr(hunt_mod, "sdk_complete_web", fake_sdk)
    hunt_visuals("proj_ch", log=lambda m: None)

    assert len(sdk_calls) == 2, f"expected 2 SDK calls, got {sdk_calls}"
    # chapter 1: 2 subjects → max(12, 4*2+4) = 12 (floor wins)
    assert sdk_calls[0] == max(12, 4 * 2 + 4)
    # chapter 2: 1 subject → max(12, 4*1+4) = 12 (floor wins)
    assert sdk_calls[1] == max(12, 4 * 1 + 4)


def test_hunt_duplicate_subject_reuses_page(tmp_path, monkeypatch):
    """2 decls related với cùng subject (case/space khác nhau vẫn normalize);
    SDK trả image cho cả 2; chỉ 1 download xảy ra; resolved==2; cả 2 scene trỏ cùng page_ref."""
    monkeypatch.setattr(hunt_mod, "get_art_project_path",
                        lambda name: tmp_path / name)
    monkeypatch.setattr(hunt_mod, "_sleep", lambda s: None)
    root = tmp_path / "proj_dup"
    (root / "preprocessed").mkdir(parents=True)
    (root / "preprocessed" / "page_001_abc.json").write_text(json.dumps(_page(1)))
    narration = {"scenes": [
        {"scene_id": 1, "text": "hook", "page_ref": 1, "panel_ref": -1, "is_intro": True},
        {"scene_id": 2, "text": "first mention", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 3, "text": "second mention", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 4, "text": "outro", "page_ref": 1, "panel_ref": -1, "is_outro": True}]}
    # subject khác case/space nhưng normalize ra giống nhau
    plan = [
        {"scene_id": 1, "kind": "painting_full", "panel_ref": -1, "subject": "", "motion": "static", "fallback": ""},
        {"scene_id": 2, "kind": "related", "panel_ref": -1,
         "subject": "Portrait of El Greco", "motion": "pan_right", "fallback": ""},
        {"scene_id": 3, "kind": "related", "panel_ref": -1,
         "subject": "  portrait  of  El  Greco  ", "motion": "pan_left", "fallback": ""},
        {"scene_id": 4, "kind": "painting_full", "panel_ref": -1, "subject": "", "motion": "zoom_out", "fallback": ""},
    ]
    (root / "narration.json").write_text(json.dumps(narration))
    (root / "visual_plan.json").write_text(json.dumps(plan))
    (root / "art_context.json").write_text(json.dumps(
        {"title": "T", "sources": [], "artworks": [], "summary": {}}))

    monkeypatch.setattr(hunt_mod, "sdk_complete_web", lambda *a, **k: json.dumps(
        {"images": {
            "2": {"image_url": "https://x/portrait.jpg", "title": "El Greco portrait",
                  "source_url": "https://x/page", "license": "PD"},
            "3": {"image_url": "https://x/portrait2.jpg", "title": "El Greco portrait alt",
                  "source_url": "https://x/page2", "license": "PD"},
        }}))

    download_calls: list[str] = []

    def fake_download(url, dest):
        download_calls.append(url)
        dest.write_bytes(b"x")
        return (1200, 900)

    monkeypatch.setattr(hunt_mod, "_download", fake_download)
    out = hunt_visuals("proj_dup", log=lambda m: None)

    assert out["resolved"] == 2, f"expected resolved==2, got {out}"
    assert len(download_calls) == 1, f"expected 1 download, got {download_calls}"

    narration = json.loads((root / "narration.json").read_text())
    s2 = narration["scenes"][1]
    s3 = narration["scenes"][2]
    assert s2["page_ref"] == s3["page_ref"], (
        f"expected same page_ref; scene2={s2['page_ref']}, scene3={s3['page_ref']}")


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
