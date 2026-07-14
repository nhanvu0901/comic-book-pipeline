"""Tests for ui/intro_import.py — the pure inject/remove helper the review-gate
screen calls. Uses a real tiny PNG (PIL) converted by the real macOS `sips`, so
the subprocess path is exercised end-to-end. Skips if sips/PIL are unavailable."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ui.intro_import import import_intro_image, remove_intro_image

pytestmark = pytest.mark.skipif(
    shutil.which("sips") is None, reason="intro import needs macOS `sips`")


def _tiny_png(path: Path) -> Path:
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (48, 32), (10, 20, 30)).save(path, "PNG")
    return path


def test_import_writes_page_and_subject_entry(tmp_path: Path):
    src = _tiny_png(tmp_path / "src.png")
    entry = import_intro_image(tmp_path, src, "Mjolnir")

    # returned entry carries the force_intro flag + a top score
    assert entry["force_intro"] is True
    assert entry["score"] == 101
    assert entry["panel"] == 0
    assert entry["page"] >= 200
    assert entry["subject"] == "Mjolnir"

    page_n = entry["page"]

    # jpg produced by sips
    jpg = Path(entry["jpg_path"])
    assert jpg.exists() and jpg.suffix == ".jpg"
    assert jpg.parent == tmp_path / "raw_comic"

    # preprocessed page json is a full-image story panel, desc_verified
    page_json = Path(entry["page_json"])
    assert page_json.exists()
    doc = json.loads(page_json.read_text())
    assert doc["page_number"] == page_n
    assert doc["is_story_page"] is True and doc["desc_verified"] is True
    assert doc["preprocessing_method"] == "manual-inject"
    assert len(doc["panels"]) == 1
    panel = doc["panels"][0]
    assert panel["characters"] == ["Mjolnir"]
    bb = panel["bbox"]
    assert (bb["x"], bb["y"]) == (0, 0) and bb["w"] > 0 and bb["h"] > 0
    # content_hash is embedded in the filename and matches sha256[:16] of the jpg
    import hashlib
    assert doc["content_hash"] == hashlib.sha256(jpg.read_bytes()).hexdigest()[:16]
    assert doc["content_hash"] in page_json.name

    # subject_panels.json: entry is FIRST, force_intro=True, file marked manual
    sp = json.loads((tmp_path / "subject_panels.json").read_text())
    assert sp["manual"] is True
    assert sp["subject"] == "Mjolnir"
    assert sp["panels"][0] == {"page": page_n, "panel": 0, "score": 101,
                               "force_intro": True}


def test_import_prepends_and_preserves_existing_panels(tmp_path: Path):
    # a pre-existing (auto-built) subject_panels.json with a real ranked panel
    (tmp_path / "subject_panels.json").write_text(json.dumps({
        "subject": "Batman",
        "panels": [{"page": 20, "panel": 0, "score": 5.0}],
    }))
    src = _tiny_png(tmp_path / "s.png")
    entry = import_intro_image(tmp_path, src, "Batman")

    sp = json.loads((tmp_path / "subject_panels.json").read_text())
    assert sp["panels"][0]["force_intro"] is True          # import is first
    assert {"page": 20, "panel": 0, "score": 5.0} in sp["panels"]  # old kept
    assert sp["manual"] is True

    # second import → new page number, still first, no dupes
    entry2 = import_intro_image(tmp_path, _tiny_png(tmp_path / "s2.png"), "Batman")
    assert entry2["page"] != entry["page"]
    sp = json.loads((tmp_path / "subject_panels.json").read_text())
    intro_entries = [p for p in sp["panels"] if p.get("force_intro")]
    assert len(intro_entries) == 2
    assert sp["panels"][0]["page"] == entry2["page"]


def test_remove_cleans_page_jpg_and_entry(tmp_path: Path):
    (tmp_path / "subject_panels.json").write_text(json.dumps({
        "subject": "Batman",
        "panels": [{"page": 20, "panel": 0, "score": 5.0}],
    }))
    src = _tiny_png(tmp_path / "s.png")
    entry = import_intro_image(tmp_path, src, "Batman")
    page_n = entry["page"]

    remove_intro_image(tmp_path, page_n)

    assert not Path(entry["jpg_path"]).exists()
    assert not Path(entry["page_json"]).exists()
    sp = json.loads((tmp_path / "subject_panels.json").read_text())
    assert all(p.get("page") != page_n for p in sp["panels"])
    assert {"page": 20, "panel": 0, "score": 5.0} in sp["panels"]  # real panel survives

    # idempotent: removing again is a no-op, not an error
    remove_intro_image(tmp_path, page_n)


def test_missing_source_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        import_intro_image(tmp_path, tmp_path / "nope.png", "X")
