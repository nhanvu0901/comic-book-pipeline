"""mascot.json / channel_defaults.json: the data the Mascot step (UI step 9) edits.

This module is the SHARED CONTRACT between the kit, the motion engine, the renderer, the cue
suggester and the UI. Keep it free of rendering code.

Coordinates are resolution-independent so the UI preview (e.g. 405x720) and the 1080x1920
render agree:
  * Position.x  = horizontal centre of the sprite, as a fraction of the frame WIDTH (0..1)
  * Position.y  = the sprite's FEET line, as a fraction of the frame HEIGHT (0..1);
                  1.0 = feet on the bottom edge of the frame
  * size_pct    = height of the sprite's kit canvas, as a percentage of the frame HEIGHT
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MASCOT_ASSETS = REPO_ROOT / "assets" / "mascot"
CHANNEL_DEFAULTS_PATH = MASCOT_ASSETS / "channel_defaults.json"
CONFIG_NAME = "mascot.json"

# ── moves ────────────────────────────────────────────────────────────────────────────────
# ENTER moves make a hidden mascot appear; EXIT moves hide it until the next ENTER;
# ACCENT moves play while it is visible. Any cue may also change body/face (pose swap with a
# scale pop) and/or position (a short glide). A non-EXIT cue that lands while the mascot is
# hidden behaves as an ENTER ("rise" unless it is "peek").
MOVES_ENTER = ("rise", "peek")                 # peek = slides in from the NEAREST side edge
MOVES_EXIT = ("sink", "slide_out")             # slide_out = leaves toward the NEAREST side edge
MOVES_ACCENT = ("pop", "hop", "tilt", "shake", "hold")   # hold = change pose/position only
MOVES = MOVES_ENTER + MOVES_ACCENT + MOVES_EXIT
MOVE_LABELS = {
    "rise": "Trồi lên", "peek": "Ló từ cạnh", "pop": "Đổi tư thế (pop)", "hop": "Nhảy",
    "tilt": "Nghiêng", "shake": "Rung", "hold": "Giữ", "sink": "Lặn xuống",
    "slide_out": "Trượt ra",
}

MOTION_LEVELS = ("calm", "normal", "lively")
MOTION_LABELS = {"calm": "Nhẹ", "normal": "Vừa", "lively": "Sôi động"}

# Position presets: x is the sprite centre, y the feet line (see module docstring).
POSITION_PRESETS: dict[str, tuple[float, float]] = {
    "bottom_left": (0.20, 1.0),
    "bottom_center": (0.50, 1.0),
    "bottom_right": (0.80, 1.0),
}
POSITION_LABELS = {"bottom_left": "Trái-dưới", "bottom_center": "Giữa-dưới",
                   "bottom_right": "Phải-dưới", "custom": "Tuỳ chỉnh"}

SIZE_PCT_MIN, SIZE_PCT_MAX = 8.0, 60.0


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:                                      # NaN
        return default
    return max(lo, min(hi, f))


@dataclass
class Position:
    preset: str = "bottom_left"                     # a POSITION_PRESETS key or "custom"
    x: float = POSITION_PRESETS["bottom_left"][0]
    y: float = POSITION_PRESETS["bottom_left"][1]

    @classmethod
    def from_dict(cls, d: Any) -> "Position":
        if not isinstance(d, dict):
            return cls()
        preset = str(d.get("preset") or "custom")
        if preset in POSITION_PRESETS:
            px, py = POSITION_PRESETS[preset]
            # a preset may carry an x/y the user nudged; absent → the preset's own spot
            return cls(preset, _clamp(d.get("x", px), 0.0, 1.0, px), _clamp(d.get("y", py), 0.0, 1.2, py))
        return cls("custom", _clamp(d.get("x"), 0.0, 1.0, 0.5), _clamp(d.get("y"), 0.0, 1.2, 1.0))

    @classmethod
    def from_preset(cls, preset: str) -> "Position":
        x, y = POSITION_PRESETS.get(preset, POSITION_PRESETS["bottom_left"])
        return cls(preset if preset in POSITION_PRESETS else "bottom_left", x, y)


@dataclass
class Cue:
    t: float
    move: str = "hold"
    body: str | None = None            # None = keep the previous cue's body
    face: str | None = None            # None = keep the previous cue's face
    position: Position | None = None   # None = the global position
    locked: bool = False               # True = edited by hand; "Gợi ý tự động" never replaces it
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @classmethod
    def from_dict(cls, d: dict) -> "Cue | None":
        if not isinstance(d, dict):
            return None
        t = _clamp(d.get("t"), -1.0, 24 * 3600.0, -1.0)   # missing/negative → dropped
        if t < 0:
            return None
        move = str(d.get("move") or "hold")
        if move not in MOVES:
            move = "hold"
        pos = d.get("position")
        return cls(
            t=round(t, 3), move=move,
            body=(str(d["body"]) if d.get("body") else None),
            face=(str(d["face"]) if d.get("face") else None),
            position=Position.from_dict(pos) if isinstance(pos, dict) else None,
            locked=bool(d.get("locked", False)),
            id=str(d.get("id") or uuid.uuid4().hex[:8]),
        )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "t": round(self.t, 3), "move": self.move}
        if self.body:
            out["body"] = self.body
        if self.face:
            out["face"] = self.face
        if self.position is not None:
            out["position"] = asdict(self.position)
        out["locked"] = self.locked
        return out


@dataclass
class MascotConfig:
    kit: str = "rook_v1"
    enabled: bool = True
    position: Position = field(default_factory=Position)
    size_pct: float = 23.0
    accessories: list[str] = field(default_factory=lambda: ["monocle"])
    outline: bool = True
    shadow: bool = True
    motion: str = "normal"              # one of MOTION_LEVELS
    talk_bounce: bool = True
    # UI-only overlays on the still preview; the renderer ignores them
    show_caption_guide: bool = False    # the zone where Master puts CapCut captions
    show_shorts_ui_guide: bool = True   # the Shorts title/buttons overlay zones
    cues: list[Cue] = field(default_factory=list)

    # ── (de)serialisation ────────────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, d: Any) -> "MascotConfig":
        base = cls()
        if not isinstance(d, dict):
            return base
        cues = [c for c in (Cue.from_dict(x) for x in (d.get("cues") or [])) if c is not None]
        cues.sort(key=lambda c: c.t)
        acc = d.get("accessories", base.accessories)
        motion = str(d.get("motion") or base.motion)
        return cls(
            kit=str(d.get("kit") or base.kit),
            enabled=bool(d.get("enabled", base.enabled)),
            position=Position.from_dict(d.get("position")) if d.get("position") else base.position,
            size_pct=_clamp(d.get("size_pct", base.size_pct), SIZE_PCT_MIN, SIZE_PCT_MAX, base.size_pct),
            accessories=[str(a) for a in acc] if isinstance(acc, list) else list(base.accessories),
            outline=bool(d.get("outline", base.outline)),
            shadow=bool(d.get("shadow", base.shadow)),
            motion=motion if motion in MOTION_LEVELS else base.motion,
            talk_bounce=bool(d.get("talk_bounce", base.talk_bounce)),
            show_caption_guide=bool(d.get("show_caption_guide", base.show_caption_guide)),
            show_shorts_ui_guide=bool(d.get("show_shorts_ui_guide", base.show_shorts_ui_guide)),
            cues=cues,
        )

    def to_dict(self, *, with_cues: bool = True) -> dict:
        out = {
            "kit": self.kit, "enabled": self.enabled, "position": asdict(self.position),
            "size_pct": round(self.size_pct, 2), "accessories": list(self.accessories),
            "outline": self.outline, "shadow": self.shadow, "motion": self.motion,
            "talk_bounce": self.talk_bounce, "show_caption_guide": self.show_caption_guide,
            "show_shorts_ui_guide": self.show_shorts_ui_guide,
        }
        if with_cues:
            out["cues"] = [c.to_dict() for c in sorted(self.cues, key=lambda c: c.t)]
        return out

    def sorted_cues(self) -> list[Cue]:
        return sorted(self.cues, key=lambda c: c.t)


# ── files ────────────────────────────────────────────────────────────────────────────────
def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def config_path(project_dir: Path) -> Path:
    return Path(project_dir) / CONFIG_NAME


def load_channel_defaults(path: Path | None = None) -> MascotConfig:
    """The channel's saved look (kit, position, size, …) with NO cues."""
    cfg = MascotConfig.from_dict(_read_json(path or CHANNEL_DEFAULTS_PATH))
    cfg.cues = []
    return cfg


def save_channel_defaults(cfg: MascotConfig, path: Path | None = None) -> None:
    """'Lưu làm mặc định cho kênh': everything except the per-video cues."""
    _write_json_atomic(path or CHANNEL_DEFAULTS_PATH, cfg.to_dict(with_cues=False))


def has_config(project_dir: Path) -> bool:
    return config_path(project_dir).is_file()


def load_config(project_dir: Path, *, defaults_path: Path | None = None) -> MascotConfig:
    """mascot.json of the project; a project without one starts from the channel defaults."""
    data = _read_json(config_path(project_dir))
    if isinstance(data, dict):
        return MascotConfig.from_dict(data)
    return load_channel_defaults(defaults_path)


def save_config(project_dir: Path, cfg: MascotConfig) -> None:
    _write_json_atomic(config_path(project_dir), cfg.to_dict())
