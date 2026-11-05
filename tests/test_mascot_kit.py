"""Mascot kit: loading/validation, pose fallback, compose geometry and caching."""
import json

import pytest
from PIL import Image, ImageDraw

from stages.mascot import kit as kit_mod
from stages.mascot.kit import (PAD_FRAC, compose, list_kits, load_kit, resolve_pose, sprite_height_px,
                               sprite_metrics, sprite_png)

CANVAS = (100, 200)


def make_kit(root, name="fx", *, bodies=("a", "b"), canvas=CANVAS):
    """A fixture kit with simple shapes, following the kit.json schema in kit.py."""
    k = root / name
    for d in ("bodies", "faces", "accessories"):
        (k / d).mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "label": name, "version": 1, "canvas": list(canvas), "bodies": {}, "faces": {},
            "accessories": {}, "presets": {}, "default": {"body": bodies[0], "face": "smile"}}
    colors = {"a": (200, 60, 60, 255), "b": (60, 60, 200, 255), "c": (60, 200, 60, 255)}
    for bn in bodies:
        im = Image.new("RGBA", canvas, (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle((30, 20, 70, 189), fill=colors.get(bn, (9, 9, 9, 255)))
        im.save(k / "bodies" / f"{bn}.png")
        entry = {"file": f"bodies/{bn}.png", "front": None, "head_anchor": [50, 40], "feet_y": 189,
                 "center_x": 50, "label": bn}
        if bn == "b":
            front = Image.new("RGBA", canvas, (0, 0, 0, 0))
            ImageDraw.Draw(front).rectangle((40, 60, 60, 80), fill=(255, 255, 0, 255))
            front.save(k / "bodies" / "b_front.png")
            entry["front"] = "bodies/b_front.png"
        meta["bodies"][bn] = entry
    smile = Image.new("RGBA", (20, 10), (255, 255, 255, 255))
    smile.save(k / "faces" / "smile.png")
    Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(k / "faces" / "plain.png")
    meta["faces"] = {"smile": {"file": "faces/smile.png", "anchor": [10, 5], "label": "smile"},
                     "plain": {"file": "faces/plain.png", "anchor": [0, 0], "label": "plain"}}
    Image.new("RGBA", (20, 10), (255, 200, 0, 255)).save(k / "accessories" / "hat.png")
    meta["accessories"] = {"hat": {"file": "accessories/hat.png", "anchor": [10, 10], "label": "hat",
                                   "default": True}}
    meta["presets"] = {"point": {"body": bodies[0], "face": "smile", "label": "point"}}
    (k / "kit.json").write_text(json.dumps(meta), encoding="utf-8")
    return k


@pytest.fixture
def kit(tmp_path):
    make_kit(tmp_path)
    return load_kit("fx", tmp_path)


def test_list_and_load(tmp_path):
    make_kit(tmp_path)
    (tmp_path / "notakit").mkdir()
    assert list_kits(tmp_path) == ["fx"]
    k = load_kit("fx", tmp_path)
    assert k.canvas == CANVAS and set(k.bodies) == {"a", "b"} and k.default_face == "smile"
    assert k.bodies["b"].front is not None and k.presets["point"].body == "a"


def test_validation_errors_name_the_entry(tmp_path):
    k = make_kit(tmp_path)
    meta = json.loads((k / "kit.json").read_text())
    meta["bodies"]["a"]["file"] = "bodies/missing.png"
    (k / "kit.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="body 'a'.*missing.png"):
        load_kit("fx", tmp_path)

    k = make_kit(tmp_path, "fx2")
    Image.new("RGBA", (10, 10)).save(k / "bodies" / "a.png")
    with pytest.raises(ValueError, match="body 'a'"):
        load_kit("fx2", tmp_path)

    k = make_kit(tmp_path, "fx3")
    meta = json.loads((k / "kit.json").read_text())
    meta["presets"]["bad"] = {"body": "nope", "face": "smile"}
    (k / "kit.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="preset 'bad'.*nope"):
        load_kit("fx3", tmp_path)
    with pytest.raises(ValueError):
        load_kit("absent", tmp_path)


def test_unknown_part_falls_back_to_default(kit):
    assert resolve_pose(kit, "zzz", "yyy") == ("a", "smile")
    assert resolve_pose(kit, None, None) == ("a", "smile")
    assert resolve_pose(kit, "b", "plain") == ("b", "plain")
    img = compose(kit, "zzz", "yyy", ("nohat",))          # never raises
    assert img.size == compose(kit, "a", "smile").size


def test_all_combos_same_size_and_padding(kit):
    pad = round(PAD_FRAC * CANVAS[1])
    sizes = {compose(kit, b, f, acc).size for b in kit.bodies for f in kit.faces for acc in ((), ("hat",))}
    assert sizes == {(CANVAS[0] + 2 * pad, CANVAS[1] + 2 * pad)}
    assert compose(kit, "a", "smile", outline=False, shadow=False).size == next(iter(sizes))
    small = compose(kit, "a", "smile", height_px=80)
    assert small.height == 80 and small.width == sprite_metrics(kit, "a", 80)["w"]


def test_feet_metrics_match_the_pixels(kit):
    for hp in (None, 216, 108):
        img = compose(kit, "a", "smile", outline=False, shadow=False, height_px=hp)
        m = sprite_metrics(kit, "a", img.height)
        bbox = img.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
        assert m["w"] == img.width and m["h"] == img.height
        assert abs(bbox[3] - m["feet_y"]) <= 1.5               # lowest foot pixel's bottom edge
        assert abs((bbox[0] + bbox[2]) / 2 - m["feet_x"]) <= 1.5


def test_outline_and_shadow_surround_the_silhouette(kit):
    plain = compose(kit, "a", "plain", outline=False, shadow=False)
    sticker = compose(kit, "a", "plain", outline=True, shadow=False)
    shadowed = compose(kit, "a", "plain", outline=True, shadow=True)
    pb = plain.getchannel("A").getbbox()
    sb = sticker.getchannel("A").getbbox()
    assert sb[0] < pb[0] and sb[2] > pb[2] and sb[1] < pb[1]
    # outline pixel just outside the body is opaque white
    x, y = pb[0] - 2, (pb[1] + pb[3]) // 2
    assert sticker.getpixel((x, y))[:3] == (255, 255, 255) and sticker.getpixel((x, y))[3] > 200
    assert shadowed.getchannel("A").getbbox()[2] >= sb[2]       # shadow reaches right/down
    assert shadowed.getchannel("A").getbbox()[3] >= sb[3]
    # nothing is clipped by the padding
    a = shadowed.getchannel("A")
    assert a.getpixel((0, a.height // 2)) == 0 and a.getpixel((a.width - 1, a.height // 2)) == 0


def test_layer_order_and_face_anchor(kit):
    img = compose(kit, "a", "smile", outline=False, shadow=False)
    pad = round(PAD_FRAC * CANVAS[1])
    # face (white 20x10) anchored at (10,5) → lands on head_anchor (50,40): covers x 40..59, y 35..44
    assert img.getpixel((pad + 45, pad + 40))[:3] == (255, 255, 255)
    # accessory above the face: anchor (10,10) at head → hat covers y 30..39, drawn over the face
    with_hat = compose(kit, "a", "smile", ("hat",), outline=False, shadow=False)
    assert with_hat.getpixel((pad + 45, pad + 35))[:3] == (255, 200, 0)
    # body "b" has a front layer drawn above the face
    b = compose(kit, "b", "smile", outline=False, shadow=False)
    assert b.getpixel((pad + 50, pad + 70))[:3] == (255, 255, 0)
    assert compose(kit, "a", "smile", outline=False, shadow=False).getpixel((pad + 50, pad + 70))[:3] == (200, 60, 60)


def test_compose_is_cached_and_returns_independent_copies(kit):
    one = compose(kit, "a", "smile", ("hat",), height_px=120)
    one.putpixel((0, 0), (1, 2, 3, 4))
    two = compose(kit, "a", "smile", ("hat",), height_px=120)
    assert two.getpixel((0, 0)) != (1, 2, 3, 4)
    n = len(kit_mod._cache)
    compose(kit, "a", "smile", ("hat",), height_px=120)
    assert len(kit_mod._cache) == n


def test_sprite_png_atomic_cache(kit, tmp_path):
    cache = tmp_path / "cache"
    p = sprite_png(kit, "a", "smile", ("hat",), outline=True, shadow=True, height_px=100, cache_dir=cache)
    assert p.is_file() and Image.open(p).height == 100
    assert not list(cache.glob("*.tmp"))
    mt = p.stat().st_mtime_ns
    assert sprite_png(kit, "a", "smile", ("hat",), outline=True, shadow=True, height_px=100,
                      cache_dir=cache) == p
    assert p.stat().st_mtime_ns == mt
    other = sprite_png(kit, "b", "smile", (), outline=True, shadow=True, height_px=100, cache_dir=cache)
    assert other != p


def test_sprite_height_rule():
    assert sprite_height_px(25.0, 1920) == round(0.25 * 1920 * (1 + 2 * PAD_FRAC))
