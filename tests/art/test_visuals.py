# tests/art/test_visuals.py
import json
from art_pipeline import visuals


def test_passes_swap_absolute_threshold():
    assert visuals.passes_swap(0.45) is True
    assert visuals.passes_swap(0.46) is True
    assert visuals.passes_swap(0.44) is False


def test_build_queries_keywords_not_prose():
    qs = visuals.build_queries(
        "He had devised pointillism, a method where tiny juxtaposed dots of paint allow blending",
        "Georges Seurat")
    assert any("pointillism" in q for q in qs)
    assert all("Georges Seurat" in q for q in qs)
    assert all(len(q.split()) <= 6 for q in qs)
    assert visuals.build_queries("", "") == []


def test_rank_candidates_sorted_and_dedup(monkeypatch):
    monkeypatch.setattr(visuals, "semantic_sim",
                        lambda text, title: {"good": 0.9, "weak": 0.2}.get(title, 0.0))
    cands = [{"image_url": "u1", "title": "weak"}, {"image_url": "u2", "title": "good"},
             {"image_url": "u3", "title": "good"}]
    ranked = visuals.rank_candidates("scene text", cands, used_urls={"u3"})
    assert [c["image_url"] for _s, c in ranked] == ["u2", "u1"]
    assert ranked[0][0] == 0.9


def _fake_project(tmp_path):
    root = tmp_path / "proj"
    (root / "preprocessed").mkdir(parents=True)
    (root / "preprocessed" / "page_001_abc.json").write_text(json.dumps({
        "page_number": 1, "is_story_page": True, "page_type": "story",
        "source_image": "/art.jpg", "image_dimensions": {"width": 100, "height": 100},
        "issue_label": "The Painting", "page_summary": "a painting",
        "preprocessing_method": "vlm-regions",
        "panels": [{"index": 0, "bbox": {"x": 0, "y": 0, "w": 100, "h": 100},
                    "description": "the whole canvas"},
                   {"index": 1, "bbox": {"x": 0, "y": 0, "w": 50, "h": 50},
                    "description": "a detailed face"}],
        "text_blocks": [],
    }))
    (root / "narration.json").write_text(json.dumps({"mode": "painting_deep_dive", "scenes": [
        {"scene_id": 1, "text": "hook", "page_ref": 1, "panel_ref": 0,
         "is_intro": True, "is_outro": False},
        {"scene_id": 2, "text": "the artist lived in an asylum", "page_ref": 1,
         "panel_ref": 0, "is_intro": False, "is_outro": False},   # weak context scene
        {"scene_id": 3, "text": "a detailed face appears here", "page_ref": 1,
         "panel_ref": 1, "is_intro": False, "is_outro": False},   # reveal scene
        {"scene_id": 4, "text": "outro", "page_ref": 1, "panel_ref": 0,
         "is_intro": False, "is_outro": True},
    ]}))
    (root / "art_context.json").write_text(json.dumps({
        "title": "T", "sources": ["http://wiki"], "artworks": [{"object_id": 9}],
        "summary": {"characters": [{"name": "The Artist"}]},
    }))
    return root


def _stub_sim(text, other):
    # region sims: context scene vs region text low; reveal scene high;
    # candidate title scores high for the asylum photo.
    table = {
        ("the artist lived in an asylum", "the whole canvas"): 0.10,
        ("a detailed face appears here", "a detailed face"): 0.80,
        ("the artist lived in an asylum", "Asylum photo"): 0.90,
        ("a detailed face appears here", "Asylum photo"): 0.20,
    }
    return table.get((text, other), 0.0)


def test_enrich_swaps_weak_scene_and_writes_manifest(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    monkeypatch.setattr(visuals, "get_art_project_path", lambda n: root)
    monkeypatch.setattr(visuals, "semantic_sim", _stub_sim)
    cand = {"image_url": "http://x/asylum.jpg", "title": "Asylum photo",
            "author": "A. Photographer", "license": "by", "source_url": "http://src",
            "width": 2000, "height": 1500}
    monkeypatch.setattr(visuals, "search_commons", lambda q, **k: [cand])
    monkeypatch.setattr(visuals, "search_openverse", lambda q, **k: [])
    monkeypatch.setattr(visuals, "met_artist_works", lambda a, **k: [])
    monkeypatch.setattr(visuals, "_download",
                        lambda url, dest: (dest.write_bytes(b"img"), (800, 600))[1])
    monkeypatch.setattr(visuals, "image_hash", lambda p: "deadbeef")

    out = visuals.enrich_visuals("proj", log=lambda m: None)
    assert out["swapped"] == 1
    narration = json.loads((root / "narration.json").read_text())
    s2, s3 = narration["scenes"][1], narration["scenes"][2]
    assert s2["page_ref"] == 2 and s2["panel_ref"] == 0       # swapped
    assert s3["page_ref"] == 1 and s3["panel_ref"] == 1       # reveal kept
    manifest = json.loads((root / "visuals_manifest.json").read_text())
    assert manifest[0]["scene_id"] == 2 and manifest[0]["original_page_ref"] == 1
    ctx = json.loads((root / "art_context.json").read_text())
    assert ctx["extra_image_credits"][0]["license"] == "by"
    assert "http://src" in ctx["sources"]
    pages = list((root / "preprocessed").glob("page_002_*.json"))
    assert len(pages) == 1
    page2 = json.loads(pages[0].read_text())
    assert page2["preprocessing_method"] == "web-related"
    assert page2["panels"][0]["description"] == "Asylum photo"


def test_enrich_idempotent_then_force_restores(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    monkeypatch.setattr(visuals, "get_art_project_path", lambda n: root)
    monkeypatch.setattr(visuals, "semantic_sim", _stub_sim)
    cand = {"image_url": "http://x/asylum.jpg", "title": "Asylum photo",
            "author": "", "license": "cc0", "source_url": "", "width": 0, "height": 0}
    monkeypatch.setattr(visuals, "search_commons", lambda q, **k: [cand])
    monkeypatch.setattr(visuals, "search_openverse", lambda q, **k: [])
    monkeypatch.setattr(visuals, "met_artist_works", lambda a, **k: [])
    monkeypatch.setattr(visuals, "_download",
                        lambda url, dest: (dest.write_bytes(b"img"), (800, 600))[1])
    monkeypatch.setattr(visuals, "image_hash", lambda p: "deadbeef")

    visuals.enrich_visuals("proj", log=lambda m: None)
    out2 = visuals.enrich_visuals("proj", log=lambda m: None)   # no force → skip
    assert out2.get("skipped") is True

    out3 = visuals.enrich_visuals("proj", force=True, log=lambda m: None)
    assert out3["swapped"] == 1
    narration = json.loads((root / "narration.json").read_text())
    assert narration["scenes"][1]["page_ref"] == 2              # re-swapped after restore
    # no duplicate stale related pages: exactly one web-related page on disk
    rel = [p for p in (root / "preprocessed").glob("page_*.json")
           if json.loads(p.read_text())["preprocessing_method"] == "web-related"]
    assert len(rel) == 1


def test_enrich_never_raises_when_providers_fail(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    monkeypatch.setattr(visuals, "get_art_project_path", lambda n: root)
    monkeypatch.setattr(visuals, "semantic_sim", _stub_sim)
    def boom(q, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(visuals, "search_commons", boom)
    monkeypatch.setattr(visuals, "search_openverse", boom)
    monkeypatch.setattr(visuals, "met_artist_works", lambda a, **k: [])
    out = visuals.enrich_visuals("proj", log=lambda m: None)
    assert out["swapped"] == 0   # best-effort: scenes keep the painting
