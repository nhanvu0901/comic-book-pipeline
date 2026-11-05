"""
Screen 9: Mascot — place the 2-D sticker narrator over the finished Short.

A STILL preview of the clean video (drag the sprite, drag its corner to resize), a cue
timeline under it (tap = playhead, double-tap or "+ Cue tại đây" = new cue, drag a chip =
retime), and the config / cue editor on the right. The engine lives in stages/mascot/*;
this screen only edits stages.mascot.config.MascotConfig and calls the engine through the
module functions (so tests can swap them for fakes).

Flet rules this file follows (the Mac has 0.84, the Windows server 0.86.5):
  * GestureDetector events are read defensively (_event_delta / _event_pos) because the
    attribute names moved between flet releases.
  * A Button's label is `.content`; this file never assigns `.text`.
  * padding/margin helpers are keyword-only.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import flet as ft
from flet_video import Video, VideoMedia

from config import PROJECTS_ROOT
from stages.mascot import config as mcfg
from stages.mascot import kit as mkit
from stages.mascot import motion as mmotion
from stages.mascot import render as mrender
from stages.mascot import suggest as msuggest
from stages.mascot import timeline as mtimeline
from ..bridge import asset_src, format_exception, run_blocking
from ..layout import log_list, primary_button, secondary_button, three_col
from ..state import STAGE_NAMES, AppState, save_state
from ..theme import (
    ACCENT, BG_ELEVATED, BG_PANEL, BORDER, DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARN,
)

# ── geometry ─────────────────────────────────────────────────────────────────────────────
PREVIEW_W, PREVIEW_H = 315, 560            # 9:16 still preview (fits a 900 px window)
FRAME_W = 540                              # width asked from render.frame_at
TL_W = 760                                 # timeline
CHIP_TOP, CHIP_ROW_H, CHIP_ROWS = 90, 22, 3     # chips are packed greedily into up to 3 rows
TL_H = CHIP_TOP + CHIP_ROWS * CHIP_ROW_H        # tallest the timeline gets
FPS = 30.0
SNAP_Y = 0.02                              # feet within 2 % of the bottom edge → y = 1.0
SNAP_X = 0.03                              # within 3 % of a preset x → that x
GHOST_OPACITY = 0.3
PREVIEW_DEBOUNCE = 0.12                    # seconds; module level so tests can zero them
SAVE_DEBOUNCE = 0.4
CHIP_W, CHIP_H = 84, 20
HANDLE = 16

_MOVE_ICON = {"rise": "▲", "peek": "◂", "pop": "✦", "hop": "↑", "tilt": "∠", "shake": "≈",
              "hold": "•", "sink": "▼", "slide_out": "▸"}


# ── pure helpers (unit-testable) ─────────────────────────────────────────────────────────
def snap_position(x: float, y: float, *, y_snap: float = SNAP_Y, x_snap: float = SNAP_X
                  ) -> mcfg.Position:
    """What a finished drag becomes: feet near the bottom edge → y 1.0; x near a preset's x →
    that x; the preset name only when BOTH land on the same preset, else "custom"."""
    x = max(0.0, min(1.0, float(x)))
    y = max(0.0, min(1.2, float(y)))
    y_snapped = abs(y - 1.0) <= y_snap
    if y_snapped:
        y = 1.0
    preset = "custom"
    for name, (px, py) in mcfg.POSITION_PRESETS.items():
        if abs(x - px) <= x_snap:
            x = px
            if y_snapped and abs(y - py) < 1e-9:
                preset = name
            break
    return mcfg.Position(preset, round(x, 4), round(y, 4))


def _xy(obj) -> tuple[float, float] | None:
    if obj is not None and hasattr(obj, "x") and hasattr(obj, "y"):
        try:
            return float(obj.x), float(obj.y)
        except (TypeError, ValueError):
            return None
    return None


def _event_delta(e) -> tuple[float, float]:
    """(dx, dy) of a drag-update event. 0.84/0.86: local_delta (Offset); older: delta_x/delta_y;
    a horizontal-only drag may expose just primary_delta."""
    xy = _xy(getattr(e, "local_delta", None))
    if xy:
        return xy
    dx, dy = getattr(e, "delta_x", None), getattr(e, "delta_y", None)
    if dx is not None or dy is not None:
        return float(dx or 0.0), float(dy or 0.0)
    pd = getattr(e, "primary_delta", None)
    return (float(pd), 0.0) if pd is not None else (0.0, 0.0)


def _event_pos(e) -> tuple[float, float] | None:
    """Position of a tap event inside the widget. 0.84/0.86: local_position; older: local_x/y."""
    xy = _xy(getattr(e, "local_position", None))
    if xy:
        return xy
    lx, ly = getattr(e, "local_x", None), getattr(e, "local_y", None)
    if lx is not None:
        return float(lx), float(ly or 0.0)
    return None


def active_cue_index(cues: list, t: float) -> int:
    idx = -1
    for i, c in enumerate(cues):
        if c.t <= t + 1e-6:
            idx = i
    return idx


def pose_after(kit, cues: list, idx: int) -> tuple[str, str]:
    """Body/face in effect right after cues[idx]: the last non-None value wins (None = keep)."""
    body = face = None
    for c in cues[: idx + 1]:
        body = c.body or body
        face = c.face or face
    return mkit.resolve_pose(kit, body, face)


def cue_position(cfg: mcfg.MascotConfig, cue, cues: list | None = None) -> mcfg.Position:
    """Where the sprite stands at `cue`. An EXIT cue (sink / slide_out) leaves from where the
    mascot already is (the engine ignores the exit cue's own position), so it takes the
    position in effect just before it."""
    if cue is not None and cue.move in mcfg.MOVES_EXIT and cues is not None:
        i = next((k for k, c in enumerate(cues) if c.id == cue.id), 0)
        for prev in reversed(cues[:i]):
            if prev.move not in mcfg.MOVES_EXIT:
                return cue_position(cfg, prev, cues)
        return cfg.position
    return cue.position if (cue is not None and cue.position is not None) else cfg.position


def preview_plan(cfg: mcfg.MascotConfig, kit, t: float) -> dict:
    """Which sprites the still preview shows at time t: the active pose and the neighbour
    ghosts (the cue before the active one, the cue after it)."""
    cues = cfg.sorted_cues()
    idx = active_cue_index(cues, t)

    def item(i: int) -> dict:
        body, face = pose_after(kit, cues, i)
        pos = cue_position(cfg, cues[i], cues)
        return {"body": body, "face": face, "x": pos.x, "y": pos.y, "cue": cues[i].id}

    if idx >= 0:
        active = item(idx)
    else:
        body, face = mkit.resolve_pose(kit, None, None)
        active = {"body": body, "face": face, "x": cfg.position.x, "y": cfg.position.y,
                  "cue": None}
    ghosts = []
    if idx >= 1:
        ghosts.append(item(idx - 1))
    if idx + 1 < len(cues):
        ghosts.append(item(idx + 1))
    return {"t": t, "active": active, "ghosts": ghosts, "idx": idx}


def new_cue_at(cfg: mcfg.MascotConfig, t: float, visible: bool) -> mcfg.Cue:
    """A hand-made cue: "rise" when the mascot is hidden at t, else "hold". Body/face stay None,
    i.e. it inherits the previous cue's pose (and follows it if that cue changes later)."""
    return mcfg.Cue(t=round(max(0.0, t), 3), move="hold" if visible else "rise", locked=True)


# ── screen ───────────────────────────────────────────────────────────────────────────────
def _header_labels(*extra: ft.Control) -> list[ft.Control]:
    return [
        ft.Text(f"STEP 9 OF {len(STAGE_NAMES)}", size=10, color=TEXT_MUTED),
        ft.Text("Mascot", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
        *extra,
    ]


def _message_screen(state: AppState, on_go, message: str, button: ft.Control | None) -> ft.Control:
    center = ft.Container(
        content=ft.Column(
            [ft.Icon(ft.Icons.PETS, size=64, color=TEXT_MUTED),
             ft.Text(message, color=TEXT_MUTED, size=14, key="mascot-message"),
             *([button] if button else [])],
            spacing=14, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.Alignment.CENTER, expand=True,
    )
    right = ft.Column(_header_labels(), spacing=8, expand=True)
    return three_col(center, right, state=state, on_go=on_go, header_title="Mascot",
                     header_subtitle="Đặt nhân vật lên video đã render.")


def build(
    page: ft.Page,
    state: AppState,
    *,
    on_go: Callable[[int], None],
    on_state_change: Callable[[], None],
) -> ft.Control:
    project_dir = PROJECTS_ROOT / state.project_name
    final_path = project_dir / "final.mp4"
    clean_path = project_dir / "mascot" / "final_clean.mp4"
    if not state.project_name or not (final_path.exists() or clean_path.exists()):
        return _message_screen(
            state, on_go, "Chưa có final.mp4 — hãy render ở bước 8 trước",
            primary_button("Về bước 8", lambda _e: on_go(8), icon=ft.Icons.ARROW_BACK))

    lv, push_log = log_list(page)
    sprite_cache = project_dir / "mascot" / "cache" / "sprites"
    preview_cache = project_dir / "mascot" / "cache" / "previews"

    cfg = mcfg.load_config(project_dir)
    first_open = not mcfg.has_config(project_dir)

    # ── kit ──────────────────────────────────────────────────────────────────────────────
    kit_labels: dict[str, str] = {}
    try:
        for n in mkit.list_kits():
            try:
                kit_labels[n] = mkit.load_kit(n).label or n
            except Exception:
                kit_labels[n] = n
    except Exception as ex:                                   # noqa: BLE001
        push_log(format_exception(ex))
    K = SimpleNamespace(kit=None)

    def load_kit() -> bool:
        try:
            K.kit = mkit.load_kit(cfg.kit)
            return True
        except Exception as ex:                               # noqa: BLE001
            push_log(f"Không tải được kit '{cfg.kit}': {format_exception(ex)}")
            return False

    if not load_kit() and kit_labels:
        cfg.kit = next(iter(kit_labels))
        load_kit()
    if K.kit is None:
        return _message_screen(state, on_go, "Không tải được bộ nhân vật (kit). Xem log bên dưới.",
                               None)

    S = SimpleNamespace(
        t=0.0, selected=None, tdata={"duration": 0.0, "cuts": [], "scenes": [], "beats": [],
                                      "words": []},
        intervals=[], token=0, save_token=0, gtoken=0, busy=False, drag=None, resize=None,
        cdrag=None, cur=None, closed=False, thumb="", wave="", static=[], tap_t=0.0,
    )

    # ── small utilities ──────────────────────────────────────────────────────────────────
    def dur() -> float:
        d = float(S.tdata.get("duration") or 0.0)
        if d <= 0:
            cues = cfg.sorted_cues()
            d = (cues[-1].t + 5.0) if cues else 30.0
        return max(d, 0.1)

    def fail(label: str, ex: BaseException) -> None:
        push_log(f"{label}: {format_exception(ex)}")
        status.value = f"{label} — xem log."
        status.color = DANGER
        try:
            page.update()
        except Exception:                                     # noqa: BLE001
            pass

    def safe(label: str):
        """Decorator: an engine/handler error is logged and shown, never kills the screen."""
        def wrap(fn):
            def run(*a, **k):
                try:
                    return fn(*a, **k)
                except Exception as ex:                       # noqa: BLE001
                    fail(label, ex)
            return run
        return wrap

    def selected_cue():
        for c in cfg.cues:
            if c.id == S.selected:
                return c
        return None

    def is_visible(t: float) -> bool:
        return any(a - 1e-6 <= t <= b + 1e-6 for a, b in S.intervals)

    def save_now() -> None:
        try:
            mcfg.save_config(project_dir, cfg)
        except Exception as ex:                               # noqa: BLE001
            fail("Lưu mascot.json lỗi", ex)

    def spawn(fn, *args) -> bool:
        """page.run_task that stays quiet when the browser session has already closed."""
        if S.closed:
            return False
        try:
            page.run_task(fn, *args)
            return True
        except Exception:                                     # noqa: BLE001
            S.closed = True
            return False

    async def _save_task(tok: int) -> None:
        await asyncio.sleep(SAVE_DEBOUNCE)
        if tok == S.save_token:
            save_now()

    def schedule_save() -> None:
        S.save_token += 1
        spawn(_save_task, S.save_token)

    def rebuild_track() -> None:
        try:
            track = mmotion.build_track(cfg, K.kit, dur(), 1080, 1920)
            S.intervals = list(mmotion.visible_intervals(track))
        except Exception as ex:                               # noqa: BLE001
            S.intervals = []
            push_log(f"Dựng track lỗi: {format_exception(ex)}")

    # ── controls: still preview ──────────────────────────────────────────────────────────
    frame_img = ft.Image(src="", width=PREVIEW_W, height=PREVIEW_H, fit=ft.BoxFit.COVER,
                         gapless_playback=True, visible=False)
    frame_slot = ft.Container(content=frame_img, width=PREVIEW_W, height=PREVIEW_H,
                              bgcolor="#111111")
    guide_bottom = ft.Container(left=0, top=PREVIEW_H * 0.78, width=PREVIEW_W,
                                height=PREVIEW_H * 0.22,
                                bgcolor=ft.Colors.with_opacity(0.28, ft.Colors.RED),
                                visible=cfg.show_shorts_ui_guide, tooltip="Vùng UI Shorts")
    guide_right = ft.Container(left=PREVIEW_W * 0.86, top=PREVIEW_H * 0.45,
                               width=PREVIEW_W * 0.14, height=PREVIEW_H * 0.40,
                               bgcolor=ft.Colors.with_opacity(0.28, ft.Colors.RED),
                               visible=cfg.show_shorts_ui_guide, tooltip="Nút Shorts")
    guide_caption = ft.Container(left=0, top=PREVIEW_H * 0.60, width=PREVIEW_W,
                                 height=PREVIEW_H * 0.15,
                                 bgcolor=ft.Colors.with_opacity(0.25, ft.Colors.AMBER),
                                 visible=cfg.show_caption_guide, tooltip="Vùng caption CapCut")
    active_img = ft.Image(src="", fit=ft.BoxFit.FILL, width=10, height=10)
    active_gd = ft.GestureDetector(content=active_img, left=0, top=0, key="sprite-active",
                                   visible=False)
    handle = ft.Container(width=HANDLE, height=HANDLE, bgcolor=ACCENT, border_radius=HANDLE / 2,
                          left=0, top=0, visible=False, tooltip="Kéo để đổi kích thước")
    handle_gd = ft.GestureDetector(content=handle, left=0, top=0, key="sprite-handle",
                                   visible=False)
    stack = ft.Stack(
        [frame_slot, guide_bottom, guide_right, guide_caption, active_gd, handle_gd],
        width=PREVIEW_W, height=PREVIEW_H, clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    time_text = ft.Text("", size=12, color=TEXT_PRIMARY, font_family="Menlo", key="time-text")
    apply_all = ft.Checkbox(label="Áp cho mọi cue", value=False, key="apply-all")

    def _metrics(body: str, size_pct: float) -> dict:
        return mkit.sprite_metrics(K.kit, body, mkit.sprite_height_px(size_pct, PREVIEW_H))

    def _place_active(x: float, y: float, size_pct: float | None = None) -> None:
        cur = S.cur
        if not cur:
            return
        m = _metrics(cur["body"], size_pct if size_pct is not None else cfg.size_pct)
        left, top = x * PREVIEW_W - m["feet_x"], y * PREVIEW_H - m["feet_y"]
        active_img.width, active_img.height = m["w"], m["h"]
        active_gd.left, active_gd.top = left, top
        handle_gd.left, handle_gd.top = left + m["w"] - HANDLE, top
        cur["x"], cur["y"] = x, y

    def _effective_position() -> mcfg.Position:
        plan = preview_plan(cfg, K.kit, S.t)
        return mcfg.Position("custom", plan["active"]["x"], plan["active"]["y"])

    def _compute_preview(plan: dict, snap: dict) -> dict:
        """Worker thread: the frame JPEG and every sprite PNG the plan needs."""
        out = {"frame": None, "sprites": [], "error": None}
        try:
            out["frame"] = mrender.frame_at(project_dir, plan["t"], width=FRAME_W)
        except Exception as ex:                               # noqa: BLE001
            out["error"] = f"Lấy frame lỗi: {format_exception(ex)}"
        h = mkit.sprite_height_px(snap["size_pct"], PREVIEW_H)
        for role, it in [("active", plan["active"])] + [("ghost", g) for g in plan["ghosts"]]:
            try:
                png = mkit.sprite_png(
                    K.kit, it["body"], it["face"], snap["accessories"], outline=snap["outline"],
                    shadow=snap["shadow"], height_px=h, cache_dir=sprite_cache)
                m = mkit.sprite_metrics(K.kit, it["body"], h)
                out["sprites"].append({"role": role, "png": png, "m": m, **it})
            except Exception as ex:                           # noqa: BLE001
                out["error"] = f"Dựng sprite lỗi: {format_exception(ex)}"
        return out

    def _apply_preview(res: dict) -> None:
        if res.get("error"):
            push_log(res["error"])
        if res.get("frame"):
            src = asset_src(res["frame"])
            frame_img.src = src
            frame_img.visible = bool(src)
        ghosts, active = [], None
        for sp in res["sprites"]:
            m = sp["m"]
            left, top = sp["x"] * PREVIEW_W - m["feet_x"], sp["y"] * PREVIEW_H - m["feet_y"]
            if sp["role"] == "ghost":
                ghosts.append(ft.Image(src=asset_src(sp["png"]), width=m["w"], height=m["h"],
                                       left=left, top=top, opacity=GHOST_OPACITY,
                                       fit=ft.BoxFit.FILL))
            else:
                active = sp
        if active:
            m = active["m"]
            active_img.src = asset_src(active["png"])
            active_img.opacity = 1.0 if is_visible(S.t) else 0.45
            S.cur = {"body": active["body"], "x": active["x"], "y": active["y"]}
            active_gd.visible = handle_gd.visible = True
            _place_active(active["x"], active["y"])
        stack.controls = [frame_slot, guide_bottom, guide_right, guide_caption, *ghosts,
                          active_gd, handle_gd]

    async def _preview_task(token: int) -> None:
        await asyncio.sleep(PREVIEW_DEBOUNCE)
        if token != S.token:
            return
        try:
            plan = preview_plan(cfg, K.kit, S.t)
            snap = {"size_pct": cfg.size_pct, "accessories": tuple(cfg.accessories),
                    "outline": cfg.outline, "shadow": cfg.shadow}
            res = await run_blocking(_compute_preview, plan, snap)
            if token != S.token:
                return
            _apply_preview(res)
            page.update()
        except Exception as ex:                               # noqa: BLE001
            fail("Cập nhật xem trước lỗi", ex)

    def schedule_preview() -> None:
        S.token += 1
        spawn(_preview_task, S.token)

    # ── sprite drag / resize ─────────────────────────────────────────────────────────────
    def _set_target_position(pos: mcfg.Position) -> None:
        cue = selected_cue()
        if apply_all.value or cue is None:
            cfg.position = pos
            preset_dd.value = pos.preset
        else:
            cue.position = pos
            cue.locked = True

    @safe("Kéo mascot lỗi")
    def on_sprite_pan_start(e):
        if S.cur:
            S.drag = {"x": S.cur["x"], "y": S.cur["y"]}

    @safe("Kéo mascot lỗi")
    def on_sprite_pan_update(e):
        if S.drag is None:
            if not S.cur:
                return
            S.drag = {"x": S.cur["x"], "y": S.cur["y"]}
        dx, dy = _event_delta(e)
        S.drag["x"] = max(0.0, min(1.0, S.drag["x"] + dx / PREVIEW_W))
        S.drag["y"] = max(0.0, min(1.2, S.drag["y"] + dy / PREVIEW_H))
        _place_active(S.drag["x"], S.drag["y"])
        active_gd.update()
        handle_gd.update()

    @safe("Kéo mascot lỗi")
    def on_sprite_pan_end(e):
        d, S.drag = S.drag, None
        if d is None:
            return
        pos = snap_position(d["x"], d["y"])
        _set_target_position(pos)
        _place_active(pos.x, pos.y)
        save_now()
        rebuild_track()
        rebuild_timeline()
        refresh_cue_panel()
        schedule_preview()
        page.update()

    active_gd.on_pan_start = on_sprite_pan_start
    active_gd.on_pan_update = on_sprite_pan_update
    active_gd.on_pan_end = on_sprite_pan_end

    @safe("Đổi kích thước lỗi")
    def on_handle_pan_start(e):
        S.resize = {"size": cfg.size_pct}

    @safe("Đổi kích thước lỗi")
    def on_handle_pan_update(e):
        if S.resize is None:
            S.resize = {"size": cfg.size_pct}
        dx, dy = _event_delta(e)
        # the handle is the sprite's top-right corner: right/up grows it, feet stay put
        grow_px = (dx - dy) / 2.0
        S.resize["size"] = max(mcfg.SIZE_PCT_MIN, min(
            mcfg.SIZE_PCT_MAX,
            S.resize["size"] + grow_px / PREVIEW_H * 100.0 / (1 + 2 * mkit.PAD_FRAC)))
        if S.cur:
            _place_active(S.cur["x"], S.cur["y"], S.resize["size"])
        size_slider.value = S.resize["size"]
        active_gd.update()
        handle_gd.update()

    @safe("Đổi kích thước lỗi")
    def on_handle_pan_end(e):
        r, S.resize = S.resize, None
        if r is None:
            return
        cfg.size_pct = max(mcfg.SIZE_PCT_MIN, min(mcfg.SIZE_PCT_MAX, r["size"]))
        size_slider.value = cfg.size_pct
        save_now()
        schedule_preview()
        schedule_grids()
        page.update()

    handle_gd.on_pan_start = on_handle_pan_start
    handle_gd.on_pan_update = on_handle_pan_update
    handle_gd.on_pan_end = on_handle_pan_end

    # ── time navigation ──────────────────────────────────────────────────────────────────
    playhead = ft.Container(width=2, height=TL_H, bgcolor=ft.Colors.WHITE, left=0, top=0)

    def t_to_x(t: float) -> float:
        return max(0.0, min(TL_W, t / dur() * TL_W))

    def update_time_labels() -> None:
        time_text.value = f"{S.t:7.2f} s / {dur():.1f} s   (frame {int(round(S.t * FPS))})"
        playhead.left = t_to_x(S.t)

    def set_t(t: float, *, select: bool = True) -> None:
        S.t = round(max(0.0, min(dur(), float(t))), 4)
        update_time_labels()
        if select:
            idx = active_cue_index(cfg.sorted_cues(), S.t)
            S.selected = cfg.sorted_cues()[idx].id if idx >= 0 else None
        refresh_cue_panel()
        rebuild_timeline()
        schedule_preview()
        try:
            page.update()
        except Exception:                                     # noqa: BLE001
            pass

    def step(dt: float) -> Callable:
        return lambda _e: set_t(S.t + dt)

    # ── timeline ─────────────────────────────────────────────────────────────────────────
    snap_cb = ft.Checkbox(label="Hít (snap)", value=True, key="snap-cb")
    words_cb = ft.Checkbox(label="Hiện từng từ", value=False, key="words-cb")
    band = []        # visible-interval controls
    chips: list[ft.Control] = []
    inner = ft.Stack([], width=TL_W, height=TL_H)

    def x_to_t(x: float) -> float:
        return max(0.0, min(dur(), x / TL_W * dur()))

    @safe("Chạm timeline lỗi")
    def on_tl_tap_down(e):
        pos = _event_pos(e)
        if pos is not None:
            S.tap_t = x_to_t(pos[0])
            set_t(S.tap_t)

    @safe("Thêm cue lỗi")
    def on_tl_double_tap(e):
        add_cue_at(S.t)          # the tap-down that started the double tap already set S.t

    tl_detector = ft.GestureDetector(content=inner, left=0, top=0, key="timeline-detector",
                                     on_tap_down=on_tl_tap_down, on_double_tap=on_tl_double_tap)
    tl_outer = ft.Stack([tl_detector, playhead], width=TL_W, height=TL_H)

    def build_static() -> list[ft.Control]:
        ctl: list[ft.Control] = [ft.Container(width=TL_W, height=TL_H, bgcolor="#0e0e10")]
        if S.thumb:
            ctl.append(ft.Image(src=asset_src(S.thumb), width=TL_W, height=36, left=0, top=0,
                                fit=ft.BoxFit.FILL))
        if S.wave:
            ctl.append(ft.Image(src=asset_src(S.wave), width=TL_W, height=28, left=0, top=38,
                                fit=ft.BoxFit.FILL))
        for t in S.tdata.get("cuts", []):
            ctl.append(ft.Container(width=1, height=87, left=t_to_x(t), top=0,
                                    bgcolor=ft.Colors.with_opacity(0.8, ft.Colors.WHITE)))
        for b in S.tdata.get("beats", []):
            ctl.append(ft.Container(
                content=ft.Text(str(b.get("function") or b.get("name") or ""), size=8,
                                color="#000000", no_wrap=True),
                left=t_to_x(b.get("t", 0.0)), top=75, height=12, bgcolor=WARN,
                padding=ft.padding.symmetric(horizontal=3, vertical=0),
                border_radius=2, tooltip=str(b.get("name") or "")))
        if words_cb.value:
            for w in S.tdata.get("words", []):
                ctl.append(ft.Container(width=1, height=8, left=t_to_x(w.get("t", 0.0)), top=66,
                                        bgcolor=TEXT_MUTED))
        return ctl

    def _chip(cue, row: int = 0) -> ft.Control:
        kit = K.kit
        face_part = kit.faces.get(cue.face) if cue.face else None
        face_txt = (face_part.label if face_part else cue.face) if cue.face else "="
        txt = f"{_MOVE_ICON.get(cue.move, '')} {mcfg.MOVE_LABELS.get(cue.move, cue.move)}"
        selected = cue.id == S.selected
        box = ft.Container(
            content=ft.Column([
                ft.Text(txt, size=8, no_wrap=True, color=TEXT_PRIMARY,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(face_txt, size=8, no_wrap=True, color=TEXT_MUTED,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=0, tight=True),
            width=CHIP_W, height=CHIP_H, border_radius=5,
            padding=ft.padding.symmetric(horizontal=4, vertical=1),
            bgcolor=ACCENT if selected else BG_ELEVATED,
            border=ft.border.all(2 if cue.locked else 1, DANGER if cue.locked else BORDER),
        )
        gd = ft.GestureDetector(content=box, left=min(t_to_x(cue.t), TL_W - CHIP_W), top=CHIP_TOP + row * CHIP_ROW_H,
                                key=f"chip-{cue.id}")
        cid = cue.id

        def on_tap(_e):
            c = next((c for c in cfg.cues if c.id == cid), None)
            if c is not None:
                S.selected = cid
                set_t(c.t, select=False)

        def on_start(_e):
            c = next((c for c in cfg.cues if c.id == cid), None)
            if c is not None:
                S.cdrag = {"id": cid, "t": c.t, "start": c.t}

        def on_update(e):
            if S.cdrag is None or S.cdrag["id"] != cid:
                c = next((c for c in cfg.cues if c.id == cid), None)
                if c is None:
                    return
                S.cdrag = {"id": cid, "t": c.t, "start": c.t}
            dx, _dy = _event_delta(e)
            S.cdrag["t"] = max(0.0, min(dur(), S.cdrag["t"] + dx / TL_W * dur()))
            gd.left = min(t_to_x(S.cdrag["t"]), TL_W - CHIP_W)
            try:
                gd.update()
            except Exception:                                 # noqa: BLE001
                pass

        @safe("Dời cue lỗi")
        def on_end(_e):
            d, S.cdrag = S.cdrag, None
            if d is None or d["id"] != cid:
                return
            c = next((c for c in cfg.cues if c.id == cid), None)
            if c is None or abs(d["t"] - d["start"]) < 1e-6:
                return
            new_t = d["t"]
            if snap_cb.value:
                new_t = mtimeline.snap_time(new_t, S.tdata)
            c.t = round(max(0.0, min(dur(), new_t)), 3)
            c.locked = True
            S.selected = c.id
            commit(now=True, select_t=c.t)

        gd.on_tap = on_tap
        gd.on_horizontal_drag_start = on_start
        gd.on_horizontal_drag_update = on_update
        gd.on_horizontal_drag_end = on_end
        return gd

    def rebuild_timeline() -> None:
        band[:] = [ft.Container(width=max(2.0, t_to_x(b) - t_to_x(a)), height=6, left=t_to_x(a),
                                top=68, bgcolor=SUCCESS, border_radius=2)
                   for a, b in S.intervals]
        inner.controls = [*S.static, *band]
        # greedy rows: a chip goes in the first row whose last chip ends before it starts, so
        # close cues never sit on top of each other (the last row takes any overflow)
        row_end = [-1.0] * CHIP_ROWS
        chips[:] = []
        for c in cfg.sorted_cues():
            left = min(t_to_x(c.t), TL_W - CHIP_W)
            row = next((r for r in range(CHIP_ROWS) if left >= row_end[r] + 2), CHIP_ROWS - 1)
            row_end[row] = left + CHIP_W
            chips.append(_chip(c, row))
        used = 1 + max((r for r in range(CHIP_ROWS) if row_end[r] >= 0), default=0)
        h = CHIP_TOP + used * CHIP_ROW_H
        tl_outer.height = inner.height = playhead.height = h
        tl_outer.controls = [tl_detector, *chips, playhead]
        update_time_labels()

    # ── cue / config mutations ───────────────────────────────────────────────────────────
    def commit(*, now: bool, select_t: float | None = None, preview: bool = True) -> None:
        """After any edit: keep cues sorted, rebuild the derived views, save."""
        cfg.cues.sort(key=lambda c: c.t)
        if now:
            save_now()
        else:
            schedule_save()
        rebuild_track()
        if select_t is not None:
            S.t = round(max(0.0, min(dur(), select_t)), 4)
        update_time_labels()
        rebuild_timeline()
        refresh_cue_panel()
        if preview:
            schedule_preview()
        try:
            page.update()
        except Exception:                                     # noqa: BLE001
            pass

    def add_cue_at(t: float) -> mcfg.Cue:
        cue = new_cue_at(cfg, t, is_visible(t))
        cfg.cues.append(cue)
        S.selected = cue.id
        commit(now=True, select_t=cue.t)
        return cue

    @safe("Thêm cue lỗi")
    def on_add_cue(_e):
        add_cue_at(S.t)

    def edit_cue(fn: Callable[[mcfg.Cue], None], *, lock: bool = True) -> None:
        cue = selected_cue()
        if cue is None:
            return
        fn(cue)
        if lock:
            cue.locked = True        # any manual edit protects the cue from "Gợi ý tự động"
        commit(now=True)

    @safe("Xoá cue lỗi")
    def on_delete_cue(_e):
        cue = selected_cue()
        if cue is None:
            return
        cfg.cues = [c for c in cfg.cues if c.id != cue.id]
        S.selected = None
        commit(now=True)

    # ── right column: cue editor ─────────────────────────────────────────────────────────
    time_field = ft.TextField(value="", width=84, dense=True, text_size=12, key="cue-time",
                              border_color=BORDER, focused_border_color=ACCENT)
    move_dd = ft.Dropdown(
        value="hold", width=250, text_size=12, dense=True, key="cue-move",
        options=[ft.dropdown.Option(key=k, text=v) for k, v in mcfg.MOVE_LABELS.items()],
        border_color=BORDER, focused_border_color=ACCENT)
    pos_dd = ft.Dropdown(
        value="global", width=250, text_size=12, dense=True, key="cue-pos",
        options=[ft.dropdown.Option(key="global", text="Vị trí chung"),
                 ft.dropdown.Option(key="own", text="Vị trí riêng")],
        border_color=BORDER, focused_border_color=ACCENT)
    lock_cb = ft.Checkbox(label="Khoá (không bị Gợi ý ghi đè)", value=False, key="cue-lock")
    body_wrap = ft.Row([], wrap=True, spacing=4, run_spacing=4)
    face_wrap = ft.Row([], wrap=True, spacing=4, run_spacing=4)
    grid_cells: dict[tuple[str, str], ft.Container] = {}
    cue_hint = ft.Text("Chưa chọn cue — bấm một chip trên timeline.", size=11, color=TEXT_MUTED)

    @safe("Đổi thời điểm cue lỗi")
    def on_time_submit(_e):
        try:
            v = float(str(time_field.value).replace(",", "."))
        except ValueError:
            refresh_cue_panel()
            return
        cue = selected_cue()
        if cue is None:
            return
        cue.t = round(max(0.0, min(dur(), v)), 3)
        cue.locked = True
        commit(now=True, select_t=cue.t)

    def nudge(dt: float):
        @safe("Đổi thời điểm cue lỗi")
        def go(_e):
            cue = selected_cue()
            if cue is None:
                return
            cue.t = round(max(0.0, min(dur(), cue.t + dt)), 3)
            cue.locked = True
            commit(now=True, select_t=cue.t)
        return go

    time_field.on_submit = on_time_submit
    time_field.on_blur = on_time_submit

    @safe("Đổi move lỗi")
    def on_move_select(_e):
        edit_cue(lambda c: setattr(c, "move", move_dd.value or c.move))

    @safe("Đổi vị trí cue lỗi")
    def on_cuepos_select(_e):
        def fn(c):
            if pos_dd.value == "own":
                eff = _effective_position()
                c.position = mcfg.Position("custom", eff.x, eff.y)
            else:
                c.position = None
        edit_cue(fn)

    @safe("Khoá cue lỗi")
    def on_lock_change(_e):
        cue = selected_cue()
        if cue is not None:
            cue.locked = bool(lock_cb.value)
            commit(now=True, preview=False)

    move_dd.on_select = on_move_select
    pos_dd.on_select = on_cuepos_select
    lock_cb.on_change = on_lock_change

    def _pick(kind: str, name: str | None):
        @safe("Đổi tư thế lỗi")
        def go(_e):
            edit_cue(lambda c: setattr(c, kind, name))
        return go

    def _cell(kind: str, name: str | None, label: str) -> ft.Container:
        cell = ft.Container(
            content=ft.Column(
                [ft.Container(width=72, height=72, alignment=ft.Alignment.CENTER,
                              content=ft.Text("…", size=11, color=TEXT_MUTED)),
                 ft.Text(label, size=9, color=TEXT_MUTED, width=72, no_wrap=True,
                         overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER)],
                spacing=1, tight=True),
            border=ft.border.all(1, BORDER), border_radius=6, padding=2,
            on_click=_pick(kind, name), key=f"{kind}-{name or 'keep'}", ink=True)
        grid_cells[(kind, name or "")] = cell
        return cell

    def build_grids() -> None:
        grid_cells.clear()
        kit = K.kit
        body_wrap.controls = [_cell("body", None, "giữ nguyên")] + [
            _cell("body", n, p.label or n) for n, p in kit.bodies.items()]
        face_wrap.controls = [_cell("face", None, "giữ nguyên")] + [
            _cell("face", n, p.label or n) for n, p in kit.faces.items()]

    def _compute_grid_thumbs() -> dict:
        kit = K.kit
        dbody, dface = mkit.resolve_pose(kit, None, None)
        acc = tuple(cfg.accessories)
        out = {}
        for n in kit.bodies:
            out[("body", n)] = mkit.sprite_png(kit, n, dface, acc, outline=cfg.outline,
                                               shadow=cfg.shadow, height_px=72,
                                               cache_dir=sprite_cache)
        for n in kit.faces:
            out[("face", n)] = mkit.sprite_png(kit, dbody, n, acc, outline=cfg.outline,
                                               shadow=cfg.shadow, height_px=72,
                                               cache_dir=sprite_cache)
        return out

    async def _grids_task(tok: int) -> None:
        await asyncio.sleep(PREVIEW_DEBOUNCE)
        if tok != S.gtoken:
            return
        try:
            paths = await run_blocking(_compute_grid_thumbs)
        except Exception as ex:                               # noqa: BLE001
            fail("Dựng ảnh thumbnail lỗi", ex)
            return
        if tok != S.gtoken:
            return
        for key, p in paths.items():
            cell = grid_cells.get(key)
            src = asset_src(p)
            if cell is not None and src:
                cell.content.controls[0].content = ft.Image(
                    src=src, width=68, height=68, fit=ft.BoxFit.CONTAIN)
        try:
            page.update()
        except Exception:                                     # noqa: BLE001
            pass

    def schedule_grids() -> None:
        S.gtoken += 1
        spawn(_grids_task, S.gtoken)

    def refresh_cue_panel() -> None:
        cue = selected_cue()
        cue_box.disabled = cue is None
        cue_hint.visible = cue is None
        if cue is not None:
            time_field.value = f"{cue.t:.2f}"
            move_dd.value = cue.move
            pos_dd.value = "own" if cue.position is not None else "global"
            lock_cb.value = bool(cue.locked)
        for (kind, name), cell in grid_cells.items():
            chosen = (getattr(cue, kind, None) or "") if cue is not None else None
            on = cue is not None and chosen == name
            cell.border = ft.border.all(2 if on else 1, ACCENT if on else BORDER)

    cue_box = ft.Column([
        ft.Row([ft.Text("Thời điểm (s)", size=11, color=TEXT_MUTED), time_field,
                ft.IconButton(icon=ft.Icons.REMOVE, icon_size=16, tooltip="−0.1 s",
                              on_click=nudge(-0.1), key="cue-minus"),
                ft.IconButton(icon=ft.Icons.ADD, icon_size=16, tooltip="+0.1 s",
                              on_click=nudge(0.1), key="cue-plus")],
               spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Text("Move", size=11, color=TEXT_MUTED), move_dd,
        ft.Text("Body", size=11, color=TEXT_MUTED), body_wrap,
        ft.Text("Face", size=11, color=TEXT_MUTED), face_wrap,
        ft.Text("Vị trí", size=11, color=TEXT_MUTED), pos_dd,
        lock_cb,
        secondary_button("Xoá cue", on_delete_cue, icon=ft.Icons.DELETE_OUTLINE),
    ], spacing=6)

    # ── right column: shared config ──────────────────────────────────────────────────────
    def cfg_changed(*, preview: bool = True, grids: bool = False, track: bool = False) -> None:
        schedule_save()
        if track:
            rebuild_track()
            rebuild_timeline()
        if preview:
            schedule_preview()
        if grids:
            schedule_grids()
        try:
            page.update()
        except Exception:                                     # noqa: BLE001
            pass

    kit_dd = ft.Dropdown(
        value=cfg.kit, width=250, text_size=12, dense=True, key="cfg-kit",
        options=[ft.dropdown.Option(key=n, text=l) for n, l in kit_labels.items()],
        border_color=BORDER, focused_border_color=ACCENT)
    preset_dd = ft.Dropdown(
        value=cfg.position.preset if cfg.position.preset in mcfg.POSITION_LABELS else "custom",
        width=250, text_size=12, dense=True, key="cfg-preset",
        options=[ft.dropdown.Option(key=k, text=v) for k, v in mcfg.POSITION_LABELS.items()],
        border_color=BORDER, focused_border_color=ACCENT)
    size_slider = ft.Slider(min=mcfg.SIZE_PCT_MIN, max=mcfg.SIZE_PCT_MAX, value=cfg.size_pct,
                            divisions=int((mcfg.SIZE_PCT_MAX - mcfg.SIZE_PCT_MIN) * 2),
                            label="{value}%", active_color=ACCENT, key="cfg-size")
    acc_col = ft.Column([], spacing=0)
    outline_cb = ft.Checkbox(label="Viền sticker", value=cfg.outline, key="cfg-outline")
    shadow_cb = ft.Checkbox(label="Bóng", value=cfg.shadow, key="cfg-shadow")
    motion_dd = ft.Dropdown(
        value=cfg.motion, width=250, text_size=12, dense=True, key="cfg-motion",
        options=[ft.dropdown.Option(key=k, text=v) for k, v in mcfg.MOTION_LABELS.items()],
        border_color=BORDER, focused_border_color=ACCENT)
    bounce_cb = ft.Checkbox(label="Nhún theo giọng", value=cfg.talk_bounce, key="cfg-bounce")
    ui_guide_cb = ft.Checkbox(label="Hiện vùng UI Shorts", value=cfg.show_shorts_ui_guide,
                              key="cfg-ui-guide")
    cap_guide_cb = ft.Checkbox(label="Hiện vùng caption CapCut", value=cfg.show_caption_guide,
                               key="cfg-cap-guide")
    enabled_sw = ft.Switch(label="Bật mascot", value=cfg.enabled, key="cfg-enabled")

    def build_accessories() -> None:
        acc_col.controls = []
        for name, part in K.kit.accessories.items():
            cb = ft.Checkbox(label=part.label or name, value=name in cfg.accessories,
                             key=f"acc-{name}")

            def on_acc(_e, n=name, cb=cb):
                accs = [a for a in cfg.accessories if a != n]
                if cb.value:
                    accs.append(n)
                cfg.accessories = accs
                cfg_changed(grids=True)
            cb.on_change = on_acc
            acc_col.controls.append(cb)

    @safe("Đổi nhân vật lỗi")
    def on_kit_select(_e):
        cfg.kit = kit_dd.value or cfg.kit
        if not load_kit():
            return
        build_grids()
        build_accessories()
        cfg.accessories = [a for a in cfg.accessories if a in K.kit.accessories]
        build_accessories()
        refresh_cue_panel()
        cfg_changed(grids=True, track=True)

    @safe("Đổi vị trí lỗi")
    def on_preset_select(_e):
        v = preset_dd.value
        if v in mcfg.POSITION_PRESETS:
            cfg.position = mcfg.Position.from_preset(v)
        else:
            cfg.position = mcfg.Position("custom", cfg.position.x, cfg.position.y)
        cfg_changed(track=True)

    @safe("Đổi kích thước lỗi")
    def on_size_change(_e):
        cfg.size_pct = max(mcfg.SIZE_PCT_MIN, min(mcfg.SIZE_PCT_MAX, float(size_slider.value)))
        cfg_changed(grids=False)

    def bool_setter(attr: str, cb: ft.Control, *, grids: bool = False, track: bool = False):
        @safe("Lưu cấu hình lỗi")
        def go(_e):
            setattr(cfg, attr, bool(cb.value))
            if attr == "show_shorts_ui_guide":
                guide_bottom.visible = guide_right.visible = cfg.show_shorts_ui_guide
            elif attr == "show_caption_guide":
                guide_caption.visible = cfg.show_caption_guide
            cfg_changed(preview=attr not in ("show_shorts_ui_guide", "show_caption_guide"),
                        grids=grids, track=track)
        return go

    @safe("Đổi độ mạnh lỗi")
    def on_motion_select(_e):
        cfg.motion = motion_dd.value or cfg.motion
        cfg_changed(preview=False, track=True)

    kit_dd.on_select = on_kit_select
    preset_dd.on_select = on_preset_select
    size_slider.on_change = on_size_change
    motion_dd.on_select = on_motion_select
    outline_cb.on_change = bool_setter("outline", outline_cb, grids=True)
    shadow_cb.on_change = bool_setter("shadow", shadow_cb, grids=True)
    bounce_cb.on_change = bool_setter("talk_bounce", bounce_cb, track=True)
    ui_guide_cb.on_change = bool_setter("show_shorts_ui_guide", ui_guide_cb)
    cap_guide_cb.on_change = bool_setter("show_caption_guide", cap_guide_cb)
    enabled_sw.on_change = bool_setter("enabled", enabled_sw, track=True)

    status = ft.Text("", size=12, color=TEXT_MUTED, selectable=True, key="mascot-status")
    progress_bar = ft.ProgressBar(value=0, visible=False, color=ACCENT, bgcolor=BORDER)
    video_slot = ft.Container(visible=False, alignment=ft.Alignment.CENTER)
    dl_box = ft.Column([], spacing=4)

    @safe("Lưu mặc định lỗi")
    def on_save_defaults(_e):
        mcfg.save_channel_defaults(cfg)
        status.value = "Đã lưu làm mặc định cho kênh."
        status.color = SUCCESS
        page.update()

    # ── actions ──────────────────────────────────────────────────────────────────────────
    def _progress(p: float) -> None:
        try:
            progress_bar.value = max(0.0, min(1.0, float(p)))
            page.update()
        except Exception:                                     # noqa: BLE001
            pass

    def _begin(msg: str) -> bool:
        if S.busy:
            push_log("Đang chạy một tác vụ khác — chờ xong đã.")
            return False
        S.busy = True
        progress_bar.value = 0
        progress_bar.visible = True
        status.value, status.color = msg, WARN
        page.update()
        return True

    def _end(msg: str, color: str) -> None:
        S.busy = False
        progress_bar.visible = False
        status.value, status.color = msg, color
        try:
            page.update()
        except Exception:                                     # noqa: BLE001
            pass

    async def _suggest_task() -> None:
        if not _begin("Đang gợi ý cue…"):
            return
        try:
            cues = await run_blocking(msuggest.suggest_cues, project_dir, cfg, K.kit, S.tdata)
            cfg.cues = list(cues)
            S.selected = None
            commit(now=True)
            _end(f"Đã gợi ý {len(cfg.cues)} cue.", SUCCESS)
        except Exception as ex:                               # noqa: BLE001
            S.busy = False
            progress_bar.visible = False
            fail("Gợi ý lỗi", ex)

    def on_suggest(_e):
        spawn(_suggest_task)

    @safe("Xoá gợi ý lỗi")
    def on_clear_suggest(_e):
        cfg.cues = list(msuggest.clear_suggestions(cfg))
        if selected_cue() is None:
            S.selected = None
        commit(now=True)

    def _preview_job(center: float) -> Path:
        out = mrender.render_preview(project_dir, cfg, center, progress=_progress, log=push_log)
        # preview.mp4 is overwritten in place: serve a uniquely named copy so a browser
        # (web mode) cannot show the previous clip
        preview_cache.mkdir(parents=True, exist_ok=True)
        for old in preview_cache.glob("preview_*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass
        dest = preview_cache / f"preview_{Path(out).stat().st_mtime_ns}.mp4"
        shutil.copy2(out, dest)
        return dest

    async def _preview_clip_task() -> None:
        save_now()
        if not _begin("Đang render xem thử…"):
            return
        try:
            cue = selected_cue()
            center = cue.t if cue is not None else S.t
            path = await run_blocking(_preview_job, center)
            video_slot.content = Video(
                playlist=[VideoMedia(resource=asset_src(path))], autoplay=True,
                show_controls=True, width=270, height=480)
            video_slot.visible = True
            _end("Xem thử xong — đang phát bên dưới timeline.", SUCCESS)
        except Exception as ex:                               # noqa: BLE001
            S.busy = False
            progress_bar.visible = False
            fail("Xem thử lỗi", ex)

    def on_preview_clip(_e):
        spawn(_preview_clip_task)

    def refresh_downloads() -> None:
        dl_box.controls = []
        if final_path.exists():
            mb = final_path.stat().st_size / (1024 * 1024)
            url = asset_src(final_path)

            def _do_download(_e):
                try:
                    page.run_task(page.launch_url, url)
                except Exception:                             # noqa: BLE001
                    pass
            dl_box.controls = [
                primary_button(f"Download final.mp4 ({mb:.1f} MB)", on_click=_do_download,
                               url=url, icon=ft.Icons.DOWNLOAD),
                ft.TextButton("🔗 Mở / lưu final.mp4 trong tab mới", url=url,
                              style=ft.ButtonStyle(padding=ft.padding.all(0))),
            ]

    async def _render_task(disable: bool = False) -> None:
        if disable:
            cfg.enabled = False
            enabled_sw.value = False
        save_now()
        if not _begin("Đang khôi phục bản sạch…" if disable else "Đang render final.mp4…"):
            return
        try:
            out = await run_blocking(mrender.render_final, project_dir, cfg,
                                     progress=_progress, log=push_log)
            refresh_downloads()
            if disable:
                _end("Đã tắt mascot — final.mp4 là bản sạch.", SUCCESS)
            else:
                mb = Path(out).stat().st_size / (1024 * 1024) if Path(out).exists() else 0.0
                _end(f"Đã render {Path(out).name} ({mb:.1f} MB) có mascot.", SUCCESS)
                state.mark_approved(9)
                save_state(state)
                on_state_change()
        except Exception as ex:                               # noqa: BLE001
            S.busy = False
            progress_bar.visible = False
            fail("Render lỗi", ex)

    def on_render(_e):
        spawn(_render_task, False)

    def on_disable(_e):
        spawn(_render_task, True)

    # ── layout ───────────────────────────────────────────────────────────────────────────
    def section(title: str, *items: ft.Control) -> ft.Control:
        return ft.Container(
            content=ft.Column([ft.Text(title, size=12, weight=ft.FontWeight.BOLD,
                                       color=TEXT_PRIMARY), *items], spacing=6),
            padding=10, bgcolor=BG_PANEL, border=ft.border.all(1, BORDER), border_radius=8)

    def label(text: str) -> ft.Text:
        return ft.Text(text, size=11, color=TEXT_MUTED)

    config_section = section(
        "Cấu hình chung",
        enabled_sw,
        label("Nhân vật"), kit_dd,
        label("Vị trí"), preset_dd,
        label("Kích thước"), size_slider,
        label("Phụ kiện"), acc_col,
        ft.Row([outline_cb, shadow_cb], spacing=4, wrap=True),
        label("Độ mạnh chuyển động"), motion_dd,
        bounce_cb, ui_guide_cb, cap_guide_cb,
        secondary_button("Lưu làm mặc định cho kênh", on_save_defaults, icon=ft.Icons.SAVE_OUTLINED),
    )
    cue_section = section("Cue đang chọn", cue_hint, cue_box)
    actions_section = section(
        "Hành động",
        secondary_button("Gợi ý tự động", on_suggest, icon=ft.Icons.AUTO_FIX_HIGH),
        secondary_button("Xoá gợi ý", on_clear_suggest, icon=ft.Icons.CLEAR_ALL),
        secondary_button("Xem thử 3 giây quanh cue", on_preview_clip,
                         icon=ft.Icons.PLAY_CIRCLE_OUTLINE),
        primary_button("Render final.mp4", on_render, icon=ft.Icons.MOVIE_FILTER),
        secondary_button("Tắt mascot (khôi phục bản sạch)", on_disable, icon=ft.Icons.UNDO),
        progress_bar, status, dl_box,
    )
    try:
        has_out = bool(mrender.has_mascot_output(project_dir))
    except Exception:                                         # noqa: BLE001
        has_out = False
    if has_out:
        status.value = "final.mp4 đang có mascot."
    right = ft.Column(
        _header_labels(
            ft.Text("Kéo nhân vật trên ảnh, kéo góc để đổi cỡ; đặt cue trên timeline.",
                    size=12, color=TEXT_MUTED),
            config_section, cue_section, actions_section,
            ft.Container(content=lv, height=140, border=ft.border.all(1, BORDER),
                         border_radius=6),
        ), spacing=10, expand=True, scroll=ft.ScrollMode.AUTO)

    nav_row = ft.Row([
        secondary_button("−1 s", step(-1.0)),
        secondary_button("◀ frame", step(-1.0 / FPS)),
        secondary_button("frame ▶", step(1.0 / FPS)),
        secondary_button("+1 s", step(1.0)),
        time_text,
        apply_all,
    ], spacing=6, alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER)
    tl_tools = ft.Row([
        secondary_button("+ Cue tại đây", on_add_cue, icon=ft.Icons.ADD),
        snap_cb, words_cb,
    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    center = ft.Column(
        [
            ft.Container(
                content=ft.Column([
                    ft.Row([stack], alignment=ft.MainAxisAlignment.CENTER),
                    nav_row,
                    tl_tools,
                    ft.Row([tl_outer], scroll=ft.ScrollMode.AUTO),
                    video_slot,
                ], spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.symmetric(horizontal=20, vertical=6),
            ),
        ],
        spacing=0, expand=True, scroll=ft.ScrollMode.AUTO,
    )

    # ── initial population (background) ──────────────────────────────────────────────────
    build_grids()
    build_accessories()
    refresh_downloads()
    update_time_labels()
    refresh_cue_panel()

    def _timeline_images() -> tuple[str, str]:
        thumb = wave = ""
        try:
            thumb = str(mtimeline.thumb_strip_png(project_dir, TL_W, 36))
        except Exception as ex:                               # noqa: BLE001
            push_log(f"Dải thumbnail lỗi: {format_exception(ex)}")
        try:
            wave = str(mtimeline.waveform_png(project_dir, TL_W, 28))
        except Exception as ex:                               # noqa: BLE001
            push_log(f"Sóng âm lỗi: {format_exception(ex)}")
        return thumb, wave

    async def _init_task() -> None:
        try:
            S.tdata = await run_blocking(mtimeline.timeline_data, project_dir)
        except Exception as ex:                               # noqa: BLE001
            push_log(f"Đọc dữ liệu timeline lỗi: {format_exception(ex)}")
        if first_open and not cfg.cues:
            try:
                cfg.cues = list(await run_blocking(
                    msuggest.suggest_cues, project_dir, cfg, K.kit, S.tdata))
                save_now()
            except Exception as ex:                           # noqa: BLE001
                push_log(f"Gợi ý cue lỗi: {format_exception(ex)}")
        rebuild_track()
        S.thumb, S.wave = await run_blocking(_timeline_images)
        S.static = build_static()
        cues = cfg.sorted_cues()
        S.selected = cues[0].id if cues else None
        S.t = round(min(cues[0].t, dur()), 4) if cues else 0.0
        update_time_labels()
        rebuild_timeline()
        refresh_cue_panel()
        schedule_preview()
        schedule_grids()
        try:
            page.update()
        except Exception:                                     # noqa: BLE001
            pass

    def on_words_toggle(_e):
        S.static = build_static()
        rebuild_timeline()
        page.update()

    words_cb.on_change = on_words_toggle
    spawn(_init_task)

    return three_col(
        center, right, state=state, on_go=on_go,
        header_title="Mascot",
        header_subtitle="Đặt nhân vật lên video đã render: kéo thả trên ảnh, cue trên timeline.",
    )
