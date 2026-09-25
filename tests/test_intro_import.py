"""Tests for ui/intro_import.py — the pure inject/remove helper the review-gate
screen calls. Images are built with Pillow directly in tmp_path (no binary fixtures,
no network) — conversion goes through Pillow on every OS now, so these run on
Windows too; sips is only an (untested-here, mocked-unavailable) macOS fallback for
formats Pillow can't open."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ui.intro_import import import_intro_image, remove_intro_image


def _tiny_png(path: Path) -> Path:
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (48, 32), (10, 20, 30)).save(path, "PNG")
    return path


def _png_with_transparency(path: Path) -> Path:
    """RGBA PNG: opaque red block in one corner, fully transparent elsewhere — probes
    that conversion flattens transparency onto white, not the black a bare
    .convert("RGB") would leave."""
    Image = pytest.importorskip("PIL.Image")
    im = Image.new("RGBA", (48, 32), (0, 0, 0, 0))
    for x in range(16):
        for y in range(16):
            im.putpixel((x, y), (255, 0, 0, 255))
    im.save(path, "PNG")
    return path


def _jpeg_rotated_by_exif(path: Path) -> Path:
    """JPEG whose pixel data is physically rotated 180° with an Orientation=3 EXIF
    tag telling viewers to rotate it back — stands in for a sideways phone photo."""
    Image = pytest.importorskip("PIL.Image")
    w, h = 48, 32
    upright = Image.new("RGB", (w, h), (0, 0, 255))
    for x in range(16):
        for y in range(16):
            upright.putpixel((x, y), (255, 0, 0))  # red block, top-left once upright
    stored = upright.transpose(Image.ROTATE_180)
    exif = Image.Exif()
    exif[0x0112] = 3  # Orientation tag: rotated 180°
    stored.save(path, "JPEG", exif=exif, quality=95)
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

    # jpg produced by the conversion
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


def test_import_flattens_transparency_to_white(tmp_path: Path):
    src = _png_with_transparency(tmp_path / "src.png")
    entry = import_intro_image(tmp_path, src, "Subject")

    from PIL import Image
    with Image.open(entry["jpg_path"]) as out:
        assert out.mode == "RGB"
        r, g, b = out.getpixel((40, 24))  # was transparent
        assert r > 240 and g > 240 and b > 240          # white, not the sips/PIL-default black
        r, g, b = out.getpixel((4, 4))    # was the opaque red block
        assert r > 200 and g < 60 and b < 60


def test_import_applies_exif_orientation(tmp_path: Path):
    src = _jpeg_rotated_by_exif(tmp_path / "sideways.jpg")
    entry = import_intro_image(tmp_path, src, "Subject")

    from PIL import Image
    with Image.open(entry["jpg_path"]) as out:
        tl = out.getpixel((4, 4))
        br = out.getpixel((36, 20))
    # baked upright at save time: red block top-left, blue background bottom-right —
    # nothing downstream (thumbnailing, Stage 5) applies EXIF orientation itself.
    assert tl[0] > 180 and tl[2] < 80
    assert br[2] > 180 and br[0] < 80


def test_import_raises_readable_error_when_unreadable_and_no_sips(
    tmp_path: Path, monkeypatch
):
    # stands in for a format Pillow can't open (e.g. HEIC) with sips unavailable
    # (Windows) — patches the real shutil.which so this never touches actual sips.
    src = tmp_path / "photo.heic"
    src.write_bytes(b"not actually an image")
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="JPG or PNG"):
        import_intro_image(tmp_path, src, "Subject")
