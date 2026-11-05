"""Mascot kits: interchangeable body / face / accessory layers drawn on one template canvas.

Layout of a kit (``assets/mascot/<kit>/``)::

    kit.json
    bodies/<name>.png          RGBA, exactly canvas-sized; the body INCLUDES the head shape
    bodies/<name>_front.png    optional, canvas-sized; drawn ABOVE the face (a wing at the chin,
                               a book held up…)
    faces/<name>.png           RGBA, small; eyes + brows + beak only, transparent elsewhere
    accessories/<name>.png     RGBA, small (monocle, hat…)
    _source/                   the script that drew the placeholder art (not loaded)

``kit.json``::

    {
      "name": "rook_v1", "label": "Rook — quạ đeo kính một tròng", "version": 1,
      "canvas": [W, H],                       # every body PNG has exactly this size
      "bodies": {
        "<name>": {"file": "bodies/x.png", "front": "bodies/x_front.png" | null,
                   "head_anchor": [x, y],     # canvas px where face/accessory anchors land
                   "feet_y": y,               # canvas px of the lowest foot pixel
                   "center_x": x,             # canvas px of the body's visual centre
                   "label": "Chỉ tay"}
      },
      "faces": {"<name>": {"file": "faces/x.png", "anchor": [x, y], "label": "Cười híp"}},
      "accessories": {"<name>": {"file": "accessories/x.png", "anchor": [x, y],
                                 "label": "Kính một tròng", "default": true}},
      "presets": {"<name>": {"body": "...", "face": "...", "label": "..."}},
      "default": {"body": "...", "face": "..."}
    }

A face/accessory PNG is pasted so that its ``anchor`` pixel lands on the body's
``head_anchor``. Layer order: body → face → accessories (kit.json order) → body front.
The white sticker outline and the soft drop shadow are generated here, around the COMPOSITE
silhouette, never baked into the parts.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter

from .config import MASCOT_ASSETS


@dataclass(frozen=True)
class BodyPart:
    name: str
    file: Path
    front: Path | None
    head_anchor: tuple[int, int]
    feet_y: int
    center_x: int
    label: str


@dataclass(frozen=True)
class OverlayPart:            # a face or an accessory
    name: str
    file: Path
    anchor: tuple[int, int]
    label: str
    default: bool = False


@dataclass(frozen=True)
class Preset:
    name: str
    body: str
    face: str
    label: str


@dataclass(frozen=True)
class Kit:
    name: str
    label: str
    root: Path
    canvas: tuple[int, int]
    bodies: dict[str, BodyPart]
    faces: dict[str, OverlayPart]
    accessories: dict[str, OverlayPart]
    presets: dict[str, Preset]
    default_body: str
    default_face: str


# Margin added around the canvas by compose() so the outline and shadow never clip.
# Fraction of the canvas height, so the sticker look is resolution-independent.
PAD_FRAC = 0.04


def sprite_height_px(size_pct: float, frame_h: int) -> int:
    """compose() height for a frame `frame_h` px high: size_pct is the KIT CANVAS height as a
    % of the frame, and compose() adds PAD_FRAC above and below. The renderer and the UI
    preview both call this, so the preview matches the render at any size."""
    return max(8, round(size_pct / 100.0 * frame_h * (1 + 2 * PAD_FRAC)))


def kits_root() -> Path:
    return MASCOT_ASSETS


def list_kits(root: Path | None = None) -> list[str]:
    """Names of the kits under assets/mascot/ (a folder with a kit.json), sorted."""
    base = Path(root) if root else kits_root()
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "kit.json").is_file())


# ── loading ──────────────────────────────────────────────────────────────────────────────
_KIT_CACHE: dict[tuple, Kit] = {}


def _pair(v, what: str) -> tuple[int, int]:
    try:
        a, b = v
        return int(round(float(a))), int(round(float(b)))
    except (TypeError, ValueError):
        raise ValueError(f"{what}: expected [x, y], got {v!r}") from None


def _file(root: Path, rel, what: str) -> Path:
    if not rel or not isinstance(rel, str):
        raise ValueError(f"{what}: missing 'file'")
    p = root / rel
    if not p.is_file():
        raise ValueError(f"{what}: file not found: {rel}")
    return p


def _size_of(p: Path, what: str) -> tuple[int, int]:
    try:
        with Image.open(p) as im:
            return im.size
    except Exception as exc:
        raise ValueError(f"{what}: cannot read image {p.name}: {exc}") from None


def load_kit(name: str, root: Path | None = None) -> Kit:
    """Parse and validate kit.json. Raises ValueError naming the bad entry (missing file,
    body PNG not canvas-sized, unknown preset part…). Cached per (root, name, kit.json mtime)."""
    base = Path(root) if root else kits_root()
    kroot = base / name
    kjson = kroot / "kit.json"
    if not kjson.is_file():
        raise ValueError(f"kit '{name}': {kjson} not found")
    key = (str(kroot.resolve()), kjson.stat().st_mtime_ns)
    hit = _KIT_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        data = json.loads(kjson.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"kit '{name}': kit.json is not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ValueError(f"kit '{name}': kit.json must be an object")
    cw, ch = _pair(data.get("canvas"), f"kit '{name}' canvas")
    if cw <= 0 or ch <= 0:
        raise ValueError(f"kit '{name}': canvas must be positive, got {[cw, ch]}")

    bodies: dict[str, BodyPart] = {}
    for bn, b in (data.get("bodies") or {}).items():
        what = f"kit '{name}' body '{bn}'"
        if not isinstance(b, dict):
            raise ValueError(f"{what}: must be an object")
        f = _file(kroot, b.get("file"), what)
        if _size_of(f, what) != (cw, ch):
            raise ValueError(f"{what}: {f.name} is {_size_of(f, what)}, canvas is {(cw, ch)}")
        front = None
        if b.get("front"):
            front = _file(kroot, b["front"], what + " front")
            if _size_of(front, what + " front") != (cw, ch):
                raise ValueError(f"{what}: front {front.name} is not canvas-sized {(cw, ch)}")
        if "feet_y" not in b:
            raise ValueError(f"{what}: missing 'feet_y'")
        feet_y = int(round(float(b["feet_y"])))
        center_x = int(round(float(b["center_x"]))) if "center_x" in b else cw // 2
        bodies[bn] = BodyPart(bn, f, front, _pair(b.get("head_anchor"), what + " head_anchor"),
                              feet_y, center_x, str(b.get("label") or bn))
    if not bodies:
        raise ValueError(f"kit '{name}': no bodies")

    def overlays(section: str) -> dict[str, OverlayPart]:
        out: dict[str, OverlayPart] = {}
        for pn, p in (data.get(section) or {}).items():
            what = f"kit '{name}' {section[:-1]} '{pn}'"
            if not isinstance(p, dict):
                raise ValueError(f"{what}: must be an object")
            out[pn] = OverlayPart(pn, _file(kroot, p.get("file"), what),
                                  _pair(p.get("anchor"), what + " anchor"),
                                  str(p.get("label") or pn), bool(p.get("default", False)))
        return out

    faces = overlays("faces")
    accessories = overlays("accessories")
    if not faces:
        raise ValueError(f"kit '{name}': no faces")

    presets: dict[str, Preset] = {}
    for pn, p in (data.get("presets") or {}).items():
        what = f"kit '{name}' preset '{pn}'"
        if not isinstance(p, dict):
            raise ValueError(f"{what}: must be an object")
        if p.get("body") not in bodies:
            raise ValueError(f"{what}: unknown body {p.get('body')!r}")
        if p.get("face") not in faces:
            raise ValueError(f"{what}: unknown face {p.get('face')!r}")
        presets[pn] = Preset(pn, p["body"], p["face"], str(p.get("label") or pn))

    dflt = data.get("default") or {}
    if dflt.get("body") and dflt["body"] not in bodies:
        raise ValueError(f"kit '{name}' default: unknown body {dflt['body']!r}")
    if dflt.get("face") and dflt["face"] not in faces:
        raise ValueError(f"kit '{name}' default: unknown face {dflt['face']!r}")
    kit = Kit(str(data.get("name") or name), str(data.get("label") or name), kroot, (cw, ch),
              bodies, faces, accessories, presets,
              dflt.get("body") or next(iter(bodies)), dflt.get("face") or next(iter(faces)))
    for k in [k for k in _KIT_CACHE if k[0] == key[0]]:
        del _KIT_CACHE[k]
    _KIT_CACHE[key] = kit
    return kit


def resolve_pose(kit: Kit, body: str | None, face: str | None) -> tuple[str, str]:
    """Unknown or None parts fall back to the kit default (never raise: a project saved with
    an older kit version must still render)."""
    return (body if body in kit.bodies else kit.default_body,
            face if face in kit.faces else kit.default_face)


# ── composing ────────────────────────────────────────────────────────────────────────────
OUTLINE_RADIUS_FRAC = 8 / 544       # rook.py: MaxFilter(17) on a 544 px sprite
SHADOW_ALPHA = 0.45
SHADOW_BLUR_FRAC = 9 / 544
SHADOW_OFFSET_FRAC = (8 / 544, 10 / 544)     # down-right
_DILATE_MAX_PX = 600                # dilate on a downscaled alpha above this height (speed)

_CACHE_MAX = 64
_cache: "OrderedDict[tuple, Image.Image]" = OrderedDict()
_cache_lock = threading.Lock()


def _kit_key(kit: Kit) -> tuple:
    try:
        mtime = (kit.root / "kit.json").stat().st_mtime_ns
    except OSError:
        mtime = 0
    return (str(kit.root), mtime)


def _paste(base: Image.Image, img: Image.Image, xy: tuple[int, int]) -> None:
    """alpha_composite `img` at xy on `base`, clipping at the borders."""
    x, y = xy
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(img.width, base.width - x), min(img.height, base.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    base.alpha_composite(img, (x + sx0, y + sy0), (sx0, sy0, sx1, sy1))


def _open(p: Path) -> Image.Image:
    with Image.open(p) as im:
        return im.convert("RGBA")


def _pad_px(kit: Kit) -> int:
    return max(1, round(PAD_FRAC * kit.canvas[1]))


def _full_size(kit: Kit) -> tuple[int, int]:
    pad = _pad_px(kit)
    return kit.canvas[0] + 2 * pad, kit.canvas[1] + 2 * pad


def _scaled_w(kit: Kit, height_px: int) -> int:
    fw, fh = _full_size(kit)
    return max(1, round(fw * height_px / fh))


def _compose_full(kit: Kit, body: str, face: str, accessories: tuple[str, ...],
                  outline: bool, shadow: bool) -> Image.Image:
    cw, ch = kit.canvas
    pad = _pad_px(kit)
    b = kit.bodies[body]
    layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    layer.alpha_composite(_open(b.file))
    hx, hy = b.head_anchor
    f = kit.faces[face]
    _paste(layer, _open(f.file), (hx - f.anchor[0], hy - f.anchor[1]))
    for an in accessories:
        a = kit.accessories.get(an)
        if a is not None:
            _paste(layer, _open(a.file), (hx - a.anchor[0], hy - a.anchor[1]))
    if b.front is not None:
        layer.alpha_composite(_open(b.front))

    out = Image.new("RGBA", (cw + 2 * pad, ch + 2 * pad), (0, 0, 0, 0))
    alpha = layer.getchannel("A")
    # dilate on a bounded resolution: MaxFilter cost grows with the square of its size
    k = min(1.0, _DILATE_MAX_PX / ch)
    sw, sh = max(1, round(cw * k)), max(1, round(ch * k))
    small = alpha.resize((sw, sh), Image.BILINEAR) if k < 1 else alpha
    r = max(1, round(OUTLINE_RADIUS_FRAC * sh))
    dil = small.filter(ImageFilter.MaxFilter(2 * r + 1))
    dil = dil.filter(ImageFilter.GaussianBlur(max(0.3, 0.8 * sh / 544)))
    if k < 1:
        dil = dil.resize((cw, ch), Image.BILINEAR)
    if shadow:
        sh_a = dil.point(lambda v: int(v * SHADOW_ALPHA)).filter(
            ImageFilter.GaussianBlur(max(0.5, SHADOW_BLUR_FRAC * ch)))
        sh_img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sh_img.putalpha(sh_a)
        out.alpha_composite(sh_img, (pad + round(SHADOW_OFFSET_FRAC[0] * ch),
                                     pad + round(SHADOW_OFFSET_FRAC[1] * ch)))
    if outline:
        st = Image.new("RGBA", (cw, ch), (255, 255, 255, 0))
        st.putalpha(dil)
        out.alpha_composite(st, (pad, pad))
    out.alpha_composite(layer, (pad, pad))
    return out


def compose(kit: Kit, body: str, face: str, accessories: tuple[str, ...] = (),
            *, outline: bool = True, shadow: bool = True, height_px: int | None = None) -> Image.Image:
    """The finished sticker as RGBA: canvas plus PAD_FRAC margin on every side, so every
    combination of one kit has the SAME size and the same feet/centre position (see
    sprite_metrics). height_px rescales the whole result (LANCZOS) to that many pixels high.
    Unknown accessories are skipped. Cached (LRU) on all arguments."""
    body, face = resolve_pose(kit, body, face)
    accs = tuple(a for a in accessories if a in kit.accessories)
    hp = int(height_px) if height_px else None
    key = (_kit_key(kit), body, face, accs, bool(outline), bool(shadow), hp)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit.copy()
    img = _compose_full(kit, body, face, accs, bool(outline), bool(shadow))
    if hp and hp != img.height:
        img = img.resize((_scaled_w(kit, hp), hp), Image.LANCZOS)
    with _cache_lock:
        _cache[key] = img
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return img.copy()


def sprite_metrics(kit: Kit, body: str, height_px: int) -> dict:
    """Geometry of compose(..., height_px=height_px) for placing it in a frame:
    {"w", "h", "feet_x", "feet_y"} in output pixels, where (feet_x, feet_y) is the bottom-centre
    point that config.Position (x = centre, y = feet line) refers to."""
    body, _ = resolve_pose(kit, body, None)
    b = kit.bodies[body]
    pad = _pad_px(kit)
    fw, fh = _full_size(kit)
    h = int(height_px)
    w = _scaled_w(kit, h)
    return {"w": w, "h": h,
            "feet_x": (pad + b.center_x) * w / fw,
            "feet_y": (pad + b.feet_y + 1) * h / fh}      # bottom EDGE of the lowest foot pixel


def sprite_png(kit: Kit, body: str, face: str, accessories: tuple[str, ...], *, outline: bool,
               shadow: bool, height_px: int, cache_dir: Path) -> Path:
    """compose() written as a PNG under cache_dir (file name = hash of the arguments + kit.json
    mtime); returns the existing file when already cached. The UI passes a cache_dir INSIDE the
    project folder, because web mode can only serve files under PROJECTS_ROOT."""
    body, face = resolve_pose(kit, body, face)
    accs = tuple(a for a in accessories if a in kit.accessories)
    raw = json.dumps([_kit_key(kit), body, face, accs, bool(outline), bool(shadow), int(height_px)])
    cache_dir = Path(cache_dir)
    path = cache_dir / f"{hashlib.sha1(raw.encode()).hexdigest()[:20]}.png"
    if path.is_file():
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    img = compose(kit, body, face, accs, outline=outline, shadow=shadow, height_px=height_px)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(cache_dir))
    os.close(fd)
    try:
        img.save(tmp, format="PNG")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
