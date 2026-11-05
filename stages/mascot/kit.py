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

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

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


def kits_root() -> Path:
    return MASCOT_ASSETS


def list_kits(root: Path | None = None) -> list[str]:
    """Names of the kits under assets/mascot/ (a folder with a kit.json), sorted."""
    raise NotImplementedError


def load_kit(name: str, root: Path | None = None) -> Kit:
    """Parse and validate kit.json. Raises ValueError naming the bad entry (missing file,
    body PNG not canvas-sized, unknown preset part…). Cached per (root, name, kit.json mtime)."""
    raise NotImplementedError


def resolve_pose(kit: Kit, body: str | None, face: str | None) -> tuple[str, str]:
    """Unknown or None parts fall back to the kit default (never raise: a project saved with
    an older kit version must still render)."""
    raise NotImplementedError


def compose(kit: Kit, body: str, face: str, accessories: tuple[str, ...] = (),
            *, outline: bool = True, shadow: bool = True, height_px: int | None = None) -> Image.Image:
    """The finished sticker as RGBA: canvas plus PAD_FRAC margin on every side, so every
    combination of one kit has the SAME size and the same feet/centre position (see
    sprite_metrics). height_px rescales the whole result (LANCZOS) to that many pixels high.
    Unknown accessories are skipped. Cached (LRU) on all arguments."""
    raise NotImplementedError


def sprite_metrics(kit: Kit, body: str, height_px: int) -> dict:
    """Geometry of compose(..., height_px=height_px) for placing it in a frame:
    {"w", "h", "feet_x", "feet_y"} in output pixels, where (feet_x, feet_y) is the bottom-centre
    point that config.Position (x = centre, y = feet line) refers to."""
    raise NotImplementedError


def sprite_png(kit: Kit, body: str, face: str, accessories: tuple[str, ...], *, outline: bool,
               shadow: bool, height_px: int, cache_dir: Path) -> Path:
    """compose() written as a PNG under cache_dir (file name = hash of the arguments + kit.json
    mtime); returns the existing file when already cached. The UI passes a cache_dir INSIDE the
    project folder, because web mode can only serve files under PROJECTS_ROOT."""
    raise NotImplementedError
