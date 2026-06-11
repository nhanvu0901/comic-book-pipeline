import json
from art_pipeline import fetch


def test_build_manifest_mirrors_comic_shape():
    entries = [{"label": "Wheat Field", "image_path": "/abs/art_001_436535.jpg"}]
    m = fetch.build_manifest(entries)
    assert m == [{"label": "Wheat Field", "pages": ["/abs/art_001_436535.jpg"]}]


def test_fetch_artworks_writes_layout(tmp_path, monkeypatch):
    meta = {"objectID": 1, "isPublicDomain": True, "primaryImage": "http://x/i.jpg",
            "title": "T", "artistDisplayName": "A", "objectDate": "1880",
            "department": "D", "creditLine": "C", "objectURL": "http://met/1",
            "medium": "Oil"}
    monkeypatch.setattr(fetch.met, "fetch_meta", lambda oid: meta)
    monkeypatch.setattr(fetch.met, "fetch_image",
                        lambda m, dest: (dest.write_bytes(b"fakejpg"), dest)[1])
    monkeypatch.setattr(fetch, "get_art_project_path", lambda n: tmp_path / n)
    (tmp_path / "p1").mkdir()

    out = fetch.fetch_artworks("p1", [1], mode="painting_deep_dive", log=lambda m: None)
    root = tmp_path / "p1"
    assert out["count"] == 1
    manifest = json.loads((root / "raw_art" / "manifest.json").read_text())
    assert manifest[0]["label"] == "T"
    assert (root / "met_meta_1.json").exists()
    assert json.loads((root / "selection.json").read_text())["object_ids"] == [1]


def test_fetch_artworks_refuses_non_pd(tmp_path, monkeypatch):
    meta = {"objectID": 2, "isPublicDomain": False, "primaryImage": "http://x/i.jpg"}
    monkeypatch.setattr(fetch.met, "fetch_meta", lambda oid: meta)
    monkeypatch.setattr(fetch, "get_art_project_path", lambda n: tmp_path / n)
    (tmp_path / "p2").mkdir()
    import pytest
    with pytest.raises(ValueError, match="NOT public domain"):
        fetch.fetch_artworks("p2", [2], log=lambda m: None)
