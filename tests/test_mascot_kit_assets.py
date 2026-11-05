"""Sanity checks for the placeholder Rook kit (plain json + PIL, independent of stages.mascot)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
KIT_DIR = ROOT / "assets" / "mascot" / "rook_v1"
GEN = KIT_DIR / "_source" / "draw_kit.py"


@pytest.fixture(scope="module")
def kit():
    return json.loads((KIT_DIR / "kit.json").read_text(encoding="utf-8"))


def _files(kit):
    out = []
    for b in kit["bodies"].values():
        out.append(b["file"])
        if b["front"]:
            out.append(b["front"])
    out += [f["file"] for f in kit["faces"].values()]
    out += [a["file"] for a in kit["accessories"].values()]
    return out


def test_files_exist(kit):
    for rel in _files(kit):
        assert (KIT_DIR / rel).is_file(), rel


def test_bodies_canvas_sized_rgba(kit):
    w, h = kit["canvas"]
    for name, b in kit["bodies"].items():
        for rel in (b["file"], b["front"]):
            if rel:
                im = Image.open(KIT_DIR / rel)
                assert im.mode == "RGBA" and im.size == (w, h), (name, rel, im.mode, im.size)


def test_anchors_inside_canvas(kit):
    w, h = kit["canvas"]
    for name, b in kit["bodies"].items():
        x, y = b["head_anchor"]
        assert 0 <= x < w and 0 <= y < h, name
        assert 0 <= b["feet_y"] < h and 0 <= b["center_x"] < w, name
    for coll in ("faces", "accessories"):
        for name, part in kit[coll].items():
            im = Image.open(KIT_DIR / part["file"])
            assert im.mode == "RGBA"
            x, y = part["anchor"]
            assert 0 <= x < im.width and 0 <= y < im.height, (coll, name)


def test_presets_and_default(kit):
    for name, p in kit["presets"].items():
        assert p["body"] in kit["bodies"] and p["face"] in kit["faces"], name
    assert kit["default"]["body"] in kit["bodies"] and kit["default"]["face"] in kit["faces"]
    assert len(kit["bodies"]) == 7 and len(kit["faces"]) == 8 and len(kit["presets"]) == 10


def test_faces_fit_inside_every_head(kit):
    for bname, b in kit["bodies"].items():
        alpha = Image.open(KIT_DIR / b["file"]).getchannel("A").point(lambda v: 255 if v >= 250 else 0)
        solid = alpha.filter(ImageFilter.MinFilter(9))       # opaque with a 4px safety margin
        hx, hy = b["head_anchor"]
        for fname, f in kit["faces"].items():
            fim = Image.open(KIT_DIR / f["file"])
            fa = fim.getchannel("A").point(lambda v: 255 if v > 40 else 0)
            ax, ay = f["anchor"]
            layer = Image.new("L", alpha.size, 0)
            layer.paste(fa, (hx - ax, hy - ay))
            outside = ImageChops.subtract(layer, solid)
            bad = sum(1 for v in outside.getdata() if v)
            assert bad <= 12, f"{fname} pokes {bad}px outside the body of {bname}"


def test_generator_is_deterministic(kit, tmp_path):
    subprocess.run([sys.executable, str(GEN), "--out", str(tmp_path), "--no-sheets"], check=True,
                   cwd=ROOT, capture_output=True)
    for rel in _files(kit) + ["kit.json"]:
        assert (tmp_path / rel).read_bytes() == (KIT_DIR / rel).read_bytes(), rel
