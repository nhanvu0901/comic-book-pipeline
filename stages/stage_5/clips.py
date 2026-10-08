"""Video-clip shots (opt-in): play a trimmed, muted, 9:16-fitted video clip on a beat instead of
the Ken Burns panel — the hybrid "comic panels + animation clips" format.

Nothing here runs for a project without a manifest: load_manifest() returns [] and every caller
returns its input untouched, so such a project renders byte-identically to before this existed.

MANIFEST — <project>/review/clips/clips.json (written by hand or by `python -m stages.clip_fetch`):

    {"clips": [
      {"id": "antimonitor-1",                    optional; default = file stem / "clip<N>"
       "file": "review/clips/gKiT1ekWIAA.mp4",   project-relative or absolute; may be absent while
                                                 "source_url" is set (Stage 5 / `clip_fetch sync`
                                                 fetch it and write the path back)
       "source_url": "https://www.youtube.com/watch?v=gKiT1ekWIAA",   credit
       "start": 12.4, "end": 15.2,               seconds in the file; "end" optional (= play as
                                                 long as the shot needs)
       "beat": "3:1",                            review beat_key: "intro" | "outro" | "<scene_id>"
                                                 | "<scene_id>:<frag_idx>" (the custom-image keys)
       "desc": "...",                            optional; only used when "beat" is absent
       "crop": {"cx": 0.62},                     optional subject crop, see parse_crop
       "enabled": true}                          optional; false = ignore the entry
    ]}

PRECEDENCE on one beat, highest first:
  1. a clip whose entry names that beat ("beat")
  2. a custom image (review/custom/custom_images.json, locked or word-overlap placed)
  3. a review panel lock (review/locks.json)
  4. the matcher's panel pick
A clip never deletes what is below it: the panel/custom image the builder put on the shot stays
on the Shot and is what renders when the clip fails (missing file, in-point past the end, ffmpeg
error, output contract mismatch) — the reason goes to shots.json as clip.fallback_reason.
An entry with no "beat" but a "desc" is placed by the SAME word-overlap assignment custom images
use (shots.assign_custom_images), but only onto beats that hold no panel lock, no custom image
and no explicit clip: a guess never outranks a pick.

PLAYBACK RULES
  • One clip on a beat with several shots plays CONTINUOUSLY across them (each later shot's
    in-point advances by the earlier shots' durations) — the cuts between them stay invisible.
  • Several clips on one beat play in manifest order and share the beat's time span; when there
    are more clips than shots the longest shot is halved until each clip has one.
  • A clip SHORTER than its shot HOLDS ITS LAST FRAME for the remainder (shots.json reports
    frozen_tail_seconds) — also across a beat's later shots once the clip has run out. A clip
    longer than its shot is cut at the shot's end.
  • Audio is always dropped; the frame is contain+blur like _prepare_panel_frame (whole clip
    sharp, upscale capped at CLIP_MAX_UPSCALE = the panel path's 2x by default, over a blurred
    cover-fill of itself), after the optional subject crop.
"""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from . import shots as _sh
from .schema import Shot

MANIFEST_REL = Path("review") / "clips" / "clips.json"
CLIP_DIR_REL = Path("review") / "clips"

# A shot is only halved for an extra clip when both halves keep at least this long
# (render_shot floors a shot at 0.4s, so anything shorter would stretch the timeline).
_MIN_SPLIT_SECONDS = 0.4
# Largest upscale a clip gets: caps the sharp foreground (the panel path's _FG_MAX_SCALE look),
# and a {"cx"} fill-crop that would need more than this is dropped for the full-frame contain fit
# (a 720p source's 9:16 window needs 2.67x). Animation upscales cleanly — raise it to let 720p
# sources fill the frame.
CLIP_MAX_UPSCALE = float(os.getenv("CLIP_MAX_UPSCALE", str(_sh._FG_MAX_SCALE)))
# A fitted clip within this fraction of the frame size is scaled to fill it exactly instead of
# leaving a 1-2px rim of blur (a 9:16 crop of a 1080p source rounds to 608x1080).
_SNAP_FRAC = 0.01


@dataclass
class ClipEntry:
    id: str
    file: str                      # absolute path, "" = not fetched yet
    start: float = 0.0
    end: float = 0.0               # 0 = open-ended
    beat: str = ""
    desc: str = ""
    crop: dict = field(default_factory=dict)
    source_url: str = ""


# ─── manifest ───────────────────────────────────────────────────────────────────

def manifest_path(project_root: Path) -> Path:
    return Path(project_root) / MANIFEST_REL


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_crop(raw) -> tuple[dict, str]:
    """Normalise a crop hint → ({...} or {}, problem). Two shapes, all values fractions 0-1 of
    the clip frame:
      {"cx": 0.6, "cy": 0.5}          centre of the largest 9:16 window that fits the frame
                                      (cy optional, default 0.5) — "follow the subject"
      {"x":0.3,"y":0,"w":0.4,"h":1}   an explicit region, then the normal contain+blur fit
    Anything else → ({}, reason): the hint is ignored, the clip still renders full-frame."""
    if not raw:
        return {}, ""
    if not isinstance(raw, dict):
        return {}, f"crop must be an object, got {type(raw).__name__}"
    if "cx" in raw or "cy" in raw:
        cx, cy = _num(raw.get("cx", 0.5)), _num(raw.get("cy", 0.5))
        if cx is None or cy is None or not (0 <= cx <= 1 and 0 <= cy <= 1):
            return {}, f"crop cx/cy must be numbers in 0-1: {raw}"
        return {"cx": cx, "cy": cy}, ""
    vals = {k: _num(raw.get(k)) for k in ("x", "y", "w", "h")}
    if any(v is None for v in vals.values()):
        return {}, f"crop needs cx/cy or all of x,y,w,h: {raw}"
    x, y, w, h = vals["x"], vals["y"], vals["w"], vals["h"]
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
            and x + w <= 1 + 1e-6 and y + h <= 1 + 1e-6):
        return {}, f"crop region must lie inside the frame (fractions 0-1): {raw}"
    return {"x": x, "y": y, "w": w, "h": h}, ""


def parse_manifest(raw, project_root: Path) -> tuple[list[ClipEntry], list[str]]:
    """clips.json content → (usable entries in manifest order, human-readable problems).
    Never raises: a bad entry is skipped and reported, the rest still apply."""
    problems: list[str] = []
    items = raw.get("clips") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return [], (["clips.json has no \"clips\" list"] if raw else [])
    root = Path(project_root)
    out: list[ClipEntry] = []
    seen_ids: set[str] = set()
    for n, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            problems.append(f"entry #{n}: not an object")
            continue
        if it.get("enabled", True) is False:
            continue
        f = str(it.get("file") or "").strip()
        url = str(it.get("source_url") or "").strip()
        if not f and not url:
            problems.append(f"entry #{n}: needs \"file\" or \"source_url\"")
            continue
        start = _num(it.get("start", 0.0))
        end = _num(it.get("end")) if it.get("end") not in (None, "") else 0.0
        if start is None or start < 0 or end is None or end < 0:
            problems.append(f"entry #{n}: start/end must be non-negative numbers")
            continue
        if end and end <= start:
            problems.append(f"entry #{n}: end ({end}) must be after start ({start})")
            continue
        beat = str(it.get("beat") or "").strip()
        desc = str(it.get("desc") or "").strip()
        if not beat and not desc:
            problems.append(f"entry #{n}: needs \"beat\" (or a \"desc\" to place it by words)")
            continue
        crop, why = parse_crop(it.get("crop"))
        if why:
            problems.append(f"entry #{n}: {why} — crop ignored")
        cid = str(it.get("id") or (Path(f).stem if f else "") or f"clip{n}")
        while cid in seen_ids:
            cid += f"-{n}"
        seen_ids.add(cid)
        path = ""
        if f:
            p = Path(f)
            path = str(p if p.is_absolute() else root / p)
        out.append(ClipEntry(id=cid, file=path, start=start, end=end, beat=beat,
                             desc=desc, crop=crop, source_url=url))
    return out, problems


def load_manifest(project: str | None, *, log=print) -> list[ClipEntry]:
    """The project's usable clip entries, [] when there is no manifest (the common case)."""
    if not project:
        return []
    from ..review_gate import _project_root
    root = _project_root(project)
    p = manifest_path(root)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"[stage5] clips: {p} is unreadable ({exc}) — no clips this render")
        return []
    entries, problems = parse_manifest(raw, root)
    for msg in problems:
        log(f"[stage5] clips: {msg}")
    return entries


# ─── beat assignment (precedence) ──────────────────────────────────────────────

def resolve_clip_assignments(entries: list[ClipEntry], narration: dict, *,
                             claimed_beats: set[str] | None = None,
                             log=print) -> list[tuple[str, ClipEntry]]:
    """[(beat_key, entry), ...] in manifest order. Explicit "beat" entries are taken as-is (they
    outrank custom images and panel locks — see PRECEDENCE). "desc"-only entries go through
    shots.assign_custom_images over the beats NOT in `claimed_beats` (panel-locked + custom-image
    beats, from the caller) and not explicitly clipped."""
    explicit = [(e.beat, e) for e in entries if e.beat]
    guessed = [e for e in entries if not e.beat]
    if not guessed:
        return explicit
    taken = set(claimed_beats or ()) | {bk for bk, _ in explicit}
    rows = [(bk, txt) for bk, txt in _sh._beat_rows_for_custom(narration) if bk not in taken]
    bookends = {bk for rows_ in _sh._bookend_rows_for_custom(narration) for bk, _t in rows_}
    by_id = {e.id: e for e in guessed}
    placed = _sh.assign_custom_images(
        rows, [{"file": e.id, "desc": e.desc} for e in guessed], {},
        panel_locked=taken, bookend_keys=bookends, score_fn=_sh._score_custom_image)
    got = {cid: bk for bk, cid in placed.items()}
    out = list(explicit)
    for e in guessed:
        if e.id in got:
            out.append((got[e.id], by_id[e.id]))
        else:
            log(f"[stage5] clips: '{e.id}' has no beat and no free beat to place it on — skipped")
    return out


def _beat_groups(shots: list) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = {}
    for i, sh in enumerate(shots):
        sid = sh.beat_id if getattr(sh, "beat_id", None) is not None else sh.scene_id
        groups.setdefault(int(sid), []).append(i)
    return groups


def _target_indices(shots: list, beat_key: str, narration: dict | None) -> list[int]:
    """Shot indices a beat_key covers — the same mapping _apply_custom_images_to_shots uses
    ("intro" → the is_intro shot, "outro" → the last shot, "<sid>" → every shot of the scene,
    "<sid>:<fi>" → the shot that SPEAKS that fragment, else the fi-th shot, clamped)."""
    if not shots:
        return []
    if beat_key == "intro":
        i = next((i for i, sh in enumerate(shots) if getattr(sh, "is_intro", False)), None)
        return [] if i is None else [i]
    if beat_key == "outro":
        return [len(shots) - 1]
    sid_s, _, frag_s = beat_key.partition(":")
    try:
        sid = int(sid_s)
        fi = int(frag_s) if frag_s else None
    except ValueError:
        return []
    idxs = _beat_groups(shots).get(sid) or []
    if fi is None or not idxs:
        return idxs
    hit = _sh._shot_for_fragment(shots, idxs, _sh._fragment_text(narration, sid, fi))
    if hit is not None:
        return [hit]
    return [idxs[min(max(fi, 0), len(idxs) - 1)]]


def _split_in_time(shots: list, i: int) -> int | None:
    """Halve shot i in place (same panel, same caption — like _time_split_shots) and return the
    index of the new second half, or None when the halves would be too short."""
    sh = shots[i]
    dur = float(sh.duration_seconds)
    if dur < 2 * _MIN_SPLIT_SECONDS:
        return None
    head = round(dur / 2, 3)
    second = replace(sh, duration_seconds=round(dur - head, 3),
                     panel_bbox=dict(sh.panel_bbox),
                     text_bboxes=list(sh.text_bboxes or []),
                     char_bboxes=list(sh.char_bboxes or []),
                     clip_crop=dict(sh.clip_crop or {}))
    sh.duration_seconds = head
    shots.insert(i + 1, second)
    return i + 1


def _partition(durs: list[float], n: int) -> list[list[int]]:
    """Split positions 0..len(durs)-1 into n contiguous non-empty groups, cutting at the shot
    boundary nearest each equal share of the total duration. Requires n <= len(durs)."""
    total = sum(durs)
    cum, run = [], 0.0
    for d in durs:
        run += d
        cum.append(run)
    cuts, prev = [], 0
    for k in range(1, n):
        lo, hi = prev + 1, len(durs) - (n - k)
        j = min(range(lo, hi + 1), key=lambda j: abs(cum[j - 1] - total * k / n))
        cuts.append(j)
        prev = j
    bounds = [0, *cuts, len(durs)]
    return [list(range(bounds[g], bounds[g + 1])) for g in range(n)]


def _stamp(sh: Shot, e: ClipEntry, clip_in: float) -> None:
    sh.clip_id = e.id
    sh.clip_source_url = e.source_url
    sh.clip_in = round(clip_in, 3)
    sh.clip_out = e.end
    sh.clip_crop = dict(e.crop)
    if e.file:
        sh.clip_path = e.file
        sh.clip_fallback = ""
    else:
        sh.clip_path = ""
        sh.clip_fallback = (f"clip '{e.id}' has no local file yet — fetch it "
                            f"(python -m stages.clip_fetch sync --project <name>)")


def apply_clips_to_shots(shots: list, assignments: list[tuple[str, ClipEntry]],
                         narration: dict | None = None, *, log=print) -> list[str]:
    """Stamp each assigned clip onto the shot(s) of its beat (see PLAYBACK RULES). Mutates
    `shots` in place (it may insert split shots; shot_ids are renumbered positionally).
    Whole-scene keys are applied first so a fragment key on the same scene overrides them.
    Returns the beat_keys that reached no shot (stale keys) — logged, never raised: clips
    are opt-in test material, and a missing clip just leaves the panel in place."""
    if not assignments or not shots:
        return []
    by_beat: dict[str, list[ClipEntry]] = {}
    for bk, e in assignments:
        by_beat.setdefault(bk, []).append(e)
    order = sorted(by_beat, key=lambda bk: (":" in bk or bk in ("intro", "outro")))
    owner: dict[int, str] = {}       # id(shot) → beat_key that stamped it in this pass
    missed: list[str] = []
    split_any = False
    for bk in order:
        entries = by_beat[bk]
        idxs = _target_indices(shots, bk, narration)
        if not idxs:
            missed.append(bk)
            log(f"[stage5] clips: beat {bk} has no shot (stale key after a re-narrate?) — "
                f"{[e.id for e in entries]} skipped")
            continue
        # Two fragment keys on one merged shot: cut the shot where this fragment starts so
        # both clips get screen time (same fix the custom-image pass uses).
        if len(idxs) == 1 and ":" in bk and owner.get(id(shots[idxs[0]]), bk) != bk:
            prev_owner = owner[id(shots[idxs[0]])]
            if ":" in prev_owner:
                sid_s, _, fi_s = bk.partition(":")
                keep_ci = shots[idxs[0]].custom_image
                new_i = _sh._split_shot_at_fragment(
                    shots, idxs[0], _sh._fragment_text(narration, int(sid_s), int(fi_s)))
                if new_i is not None:
                    shots[new_i].custom_image = keep_ci
                    idxs = [new_i]
                    split_any = True
                else:
                    log(f"[stage5] clips: beat {bk} shares a shot with beat {prev_owner} and "
                        f"it could not be split — {prev_owner}'s clip is replaced")
        # More clips than shots → halve the longest shot until each clip has one.
        while len(idxs) < len(entries):
            longest = max(idxs, key=lambda k: shots[k].duration_seconds)
            new_i = _split_in_time(shots, longest)
            if new_i is None:
                break
            split_any = True
            idxs = sorted([k if k <= longest else k + 1 for k in idxs] + [new_i])
        if len(idxs) < len(entries):
            dropped = [e.id for e in entries[len(idxs):]]
            log(f"[stage5] clips: beat {bk} is too short for {len(entries)} clips — "
                f"{dropped} skipped")
            entries = entries[:len(idxs)]
        groups = _partition([shots[k].duration_seconds for k in idxs], len(entries))
        for e, grp in zip(entries, groups):
            t = e.start
            for pos in grp:
                sh = shots[idxs[pos]]
                _stamp(sh, e, t)
                owner[id(sh)] = bk
                t += float(sh.duration_seconds)
    if split_any:
        for k, s in enumerate(shots):
            s.shot_id = k
    n = sum(1 for s in shots if s.clip_path)
    log(f"[stage5] clips: {n} shot(s) carry a clip "
        f"({len(by_beat) - len(missed)}/{len(by_beat)} beat(s) placed)")
    return missed


def apply_clip_manifest(shots: list, project: str | None, narration: dict, *,
                        custom_beats: set[str] | None = None, log=print) -> list:
    """build_shots hook. No manifest → returns `shots` untouched (same object)."""
    entries = load_manifest(project, log=log)
    if not entries:
        return shots
    claimed = set(custom_beats or ())
    try:
        from ..review_gate import load_state
        locks = (load_state(project) or {}).get("locks") or {}
        claimed |= {k for k, v in locks.items() if isinstance(v, dict) and v}
    except Exception:
        pass
    assignments = resolve_clip_assignments(entries, narration, claimed_beats=claimed, log=log)
    apply_clips_to_shots(shots, assignments, narration, log=log)
    return shots


def prefetch_clips(project: str | None, *, log=print) -> int:
    """Download every manifest entry that has a source_url but no local file yet, and write the
    file path back into clips.json. Returns how many were fetched. Never raises — an entry that
    cannot be fetched simply renders its panel (and says why in shots.json)."""
    if not project:
        return 0
    from ..review_gate import _load_json, _project_root
    root = _project_root(project)
    p = manifest_path(root)
    if not p.exists():
        return 0
    raw = _load_json(p)
    items = raw.get("clips") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return 0
    todo = [it for it in items if isinstance(it, dict) and it.get("enabled", True) is not False
            and str(it.get("source_url") or "").strip()
            and not (str(it.get("file") or "").strip()
                     and _abs(root, it["file"]).is_file())]
    if not todo:
        return 0
    from .. import clip_fetch
    fetched = 0
    for it in todo:
        url = str(it["source_url"]).strip()
        try:
            res = clip_fetch.fetch_clip(url, root / CLIP_DIR_REL, sheet=False, log=log)
        except Exception as exc:
            log(f"[stage5] clips: fetch failed for {url} ({exc}) — that beat keeps its panel")
            continue
        f = Path(res["file"])
        it["file"] = (f.relative_to(root) if f.is_relative_to(root) else f).as_posix()
        fetched += 1
    if fetched:
        from utils.atomic_json import write_json_atomic
        write_json_atomic(p, raw)
        log(f"[stage5] clips: fetched {fetched} clip(s) into {CLIP_DIR_REL}")
    return fetched


def _abs(root: Path, f) -> Path:
    p = Path(str(f))
    return p if p.is_absolute() else root / p


# ─── render ─────────────────────────────────────────────────────────────────────

def _ffprobe() -> str:
    ff = Path(_sh._require_ffmpeg())
    sib = ff.with_name("ffprobe" + ff.suffix)
    if sib.is_file():
        return str(sib)
    p = shutil.which("ffprobe")
    if not p:
        raise FileNotFoundError("ffprobe not found next to ffmpeg or on PATH")
    return p


def probe_video(path: Path) -> dict:
    """{"width","height","duration"} of the first video stream (display orientation — a 90°
    rotation tag swaps w/h, since ffmpeg autorotates on decode). Raises when there is none."""
    res = subprocess.run(
        [_ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:stream_side_data=rotation:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {(res.stderr or '').strip()[-300:]}")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    if not streams or not streams[0].get("width"):
        raise ValueError(f"no video stream in {path}")
    st = streams[0]
    w, h = int(st["width"]), int(st["height"])
    rot = 0
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"])) % 180
    if rot == 90:
        w, h = h, w
    dur = _num((data.get("format") or {}).get("duration")) or 0.0
    return {"width": w, "height": h, "duration": dur}


def crop_region(iw: int, ih: int, crop: dict | None) -> tuple[int, int, int, int]:
    """(x, y, w, h) in source pixels for a parsed crop hint; the full frame when there is none."""
    if not crop:
        return 0, 0, iw, ih
    if "cx" in crop:
        target = _sh.TARGET_ASPECT
        w, h = (int(round(ih * target)), ih) if iw / ih > target else (iw, int(round(iw / target)))
        x = int(round(crop["cx"] * iw - w / 2))
        y = int(round(crop.get("cy", 0.5) * ih - h / 2))
    else:
        x, y = int(round(crop["x"] * iw)), int(round(crop["y"] * ih))
        w, h = int(round(crop["w"] * iw)), int(round(crop["h"] * ih))
    w, h = max(2, min(w, iw)), max(2, min(h, ih))
    x, y = max(0, min(x, iw - w)), max(0, min(y, ih - h))
    return x, y, w, h


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def clip_filter_graph(iw: int, ih: int, crop: dict | None, hold_seconds: float, *,
                      speed: float = 1.0, logo: bool = False) -> str:
    """filter_complex turning input 0 into a 9:16 (OUTPUT_W x OUTPUT_H) CFR stream labelled
    [v]: optional subject crop → speed change [0.8, 1.25] → contain+blur → hold the last
    frame `hold_seconds` (tpad) → corner logo."""
    W, H, fps = _sh.OUTPUT_W, _sh.OUTPUT_H, _sh.FPS
    x, y, w, h = crop_region(iw, ih, crop)
    if crop and "cx" in crop and max(W / w, H / h) > CLIP_MAX_UPSCALE * (1 + _SNAP_FRAC):
        print(f"[stage5] clip crop {crop} needs {max(W / w, H / h):.2f}x on a {iw}x{ih} source "
              f"(> CLIP_MAX_UPSCALE {CLIP_MAX_UPSCALE}) — full-frame contain instead")
        x, y, w, h = 0, 0, iw, ih
    pre = f"crop={w}:{h}:{x}:{y}," if (x, y, w, h) != (0, 0, iw, ih) else ""
    spd = f"setpts={(1.0 / speed):.4f}*PTS," if abs(speed - 1.0) > 1e-4 else ""
    scale = min(min(W / w, H / h), CLIP_MAX_UPSCALE)
    fw, fh = min(W, _even(w * scale)), min(H, _even(h * scale))
    # setsar=0 leaves the aspect ratio unsignalled, as the panel shots do: the H.264 SPS then
    # matches theirs bit for bit, which is what _concat's stream copy reuses for every segment.
    tail = f"tpad=stop_mode=clone:stop_duration={hold_seconds:.3f},format=yuv420p,setsar=0"
    out = "[vc]" if logo else "[v]"
    if abs(fw - W) <= W * _SNAP_FRAC and abs(fh - H) <= H * _SNAP_FRAC:
        g = f"[0:v]{pre}{spd}fps={fps},scale={W}:{H}:flags=lanczos,{tail}{out}"
    else:
        # Blur at quarter size: same look as a full-size sigma-40 blur, a fraction of the cost.
        bw, bh = max(2, W // 4), max(2, H // 4)
        g = (f"[0:v]{pre}{spd}fps={fps},setsar=1,split=2[cb][cf];"
             f"[cb]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
             f"gblur=sigma=10,scale={W}:{H}[bg];"
             f"[cf]scale={fw}:{fh}:flags=lanczos[fg];"
             f"[bg][fg]overlay=({W}-{fw})/2:({H}-{fh})/2,{tail}{out}")
    if logo:
        g += ";[vc][1:v]overlay=W-w-36:36[v]"
    return g


def verify_shot_contract(path: Path, frames: int) -> None:
    """Raise unless `path` matches the shot contract _concat's `-c copy` relies on: exactly one
    stream, h264 yuv420p, OUTPUT_W x OUTPUT_H, FPS, exactly `frames` frames, no audio."""
    res = subprocess.run(
        [_ffprobe(), "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,nb_read_packets",
         "-of", "json", str(path)],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {(res.stderr or '').strip()[-300:]}")
    streams = json.loads(res.stdout or "{}").get("streams") or []
    got = [(s.get("codec_type"), s.get("codec_name"), s.get("width"), s.get("height"),
            s.get("pix_fmt"), s.get("r_frame_rate"), int(s.get("nb_read_packets") or 0))
           for s in streams]
    want = [("video", "h264", _sh.OUTPUT_W, _sh.OUTPUT_H, "yuv420p", f"{_sh.FPS}/1", frames)]
    if got != want:
        raise ValueError(f"clip shot breaks the shot contract: got {got}, want {want}")


def render_clip_shot(shot: Shot, out_path: Path, *, corner_logo: Path | None = None,
                     progress: Callable[[str], None] | None = None) -> Path:
    """Render one clip shot to `out_path` (see PLAYBACK RULES); raises on any problem so the
    caller (shots.render_shot) can fall back to the panel."""
    ff = _sh._require_ffmpeg()
    src = Path(shot.clip_path)
    if not src.is_file():
        raise FileNotFoundError(f"clip file missing: {src}")
    info = probe_video(src)
    clip_in = max(0.0, float(shot.clip_in))
    if info["duration"] and clip_in >= info["duration"] - 1.0 / _sh.FPS:
        raise ValueError(f"in-point {clip_in:.2f}s is past the end of {src.name} "
                         f"({info['duration']:.2f}s)")
    duration = max(0.4, float(shot.duration_seconds))
    frames = max(1, int(round(duration * _sh.FPS)))
    clip_out = float(shot.clip_out or 0.0)

    # Fit math: trim -> extend into source -> speed [0.8, 1.25] -> hold <= 0.3s
    src_dur = float(info.get("duration") or 0.0)
    avail_src = max(0.0, src_dur - clip_in) if src_dur > 0 else duration

    from config import CLIP_SPEED_MIN, CLIP_SPEED_MAX, CLIP_MAX_HOLD

    if clip_out and clip_in > clip_out - 1.0 / _sh.FPS:
        clip_in = max(0.0, clip_out - 1.0 / _sh.FPS)
        nominal_span = 1.0 / _sh.FPS
    elif clip_out > clip_in:
        nominal_span = clip_out - clip_in
    else:
        nominal_span = min(duration, avail_src)

    if nominal_span >= duration:
        # Step 1: trim
        used_span = duration
        speed = 1.0
        hold = 0.0
    else:
        # Step 2: extend into source
        extended_span = min(duration, avail_src)
        if extended_span >= duration:
            used_span = duration
            speed = 1.0
            hold = 0.0
        else:
            # Step 3: speed 0.8-1.25
            req_speed = extended_span / duration if duration > 0 else 1.0
            speed = max(CLIP_SPEED_MIN, min(CLIP_SPEED_MAX, req_speed))
            dur_after_speed = extended_span / speed
            # Step 4: hold frame to fill remaining gap (hold <= CLIP_MAX_HOLD)
            shortfall = max(0.0, duration - dur_after_speed)
            if shortfall > CLIP_MAX_HOLD + 1e-4:
                raise ValueError(
                    f"clip shortfall {shortfall:.2f}s exceeds CLIP_MAX_HOLD {CLIP_MAX_HOLD:.2f}s "
                    f"(duration={duration:.2f}s, speed={speed:.2f}x)"
                )
            hold = shortfall
            used_span = extended_span

    inputs = ["-ss", f"{clip_in:.3f}"]
    if used_span:
        inputs += ["-t", f"{used_span:.3f}"]
    inputs += ["-i", str(src)]
    logo = corner_logo is not None
    if logo:
        inputs += ["-i", str(corner_logo)]
    graph = clip_filter_graph(info["width"], info["height"], shot.clip_crop, hold,
                              speed=speed, logo=logo)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ff, "-y", *inputs, "-filter_complex", graph, "-map", "[v]",
           "-frames:v", str(frames), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(_sh.FPS), "-an", str(out_path)]
    if progress:
        progress(f"[stage5] shot {shot.shot_id:03d} (scene {shot.scene_id}, CLIP {shot.clip_id} "
                 f"@{clip_in:.2f}s, {duration:.2f}s, speed={speed:.2f}x, hold={hold:.2f}s)")
    _sh._run(cmd)
    verify_shot_contract(out_path, frames)
    return out_path


def frozen_tail_seconds(shot) -> float:
    """How long the last frame is held because the clip is shorter than the shot (0 when the
    clip covers it, or is open-ended — the source's own end is only known at render time)."""
    out, cin = float(getattr(shot, "clip_out", 0) or 0), float(getattr(shot, "clip_in", 0) or 0)
    if not out:
        return 0.0
    return round(max(0.0, float(shot.duration_seconds) - max(0.0, out - cin)), 3)


def shot_log_entry(shot) -> dict | None:
    """The "clip" block shots.json carries for a shot that wanted a clip; None otherwise (so a
    project without clips writes exactly the shots.json it always did)."""
    if not (getattr(shot, "clip_path", "") or getattr(shot, "clip_fallback", "")):
        return None
    fb = getattr(shot, "clip_fallback", "") or ""
    return {
        "id": shot.clip_id,
        "file": shot.clip_path,
        "in": shot.clip_in,
        "out": shot.clip_out or None,
        "crop": shot.clip_crop or None,
        "source_url": shot.clip_source_url,
        "rendered": not fb,
        "fallback_reason": fb or None,
        "frozen_tail_seconds": frozen_tail_seconds(shot),
    }


def copy_clip_fields(dst, src) -> None:
    """Point dst's clip at src's (loop-close echo)."""
    for k in ("clip_path", "clip_in", "clip_out", "clip_id", "clip_source_url", "clip_fallback"):
        setattr(dst, k, getattr(src, k))
    dst.clip_crop = copy.deepcopy(getattr(src, "clip_crop", {}) or {})


def render_clip_preview(
    clip_path: Path,
    start: float,
    beat_duration: float,
    out_path: Path,
    crop: dict | None = None,
) -> Path:
    """Render a fast 9:16 vertical preview clip matching beat duration."""
    ff = _sh._require_ffmpeg()
    info = probe_video(clip_path)
    frames = max(1, int(round(beat_duration * _sh.FPS)))
    graph = clip_filter_graph(info["width"], info["height"], crop, beat_duration)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ff, "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{beat_duration:.3f}",
        "-i", str(clip_path),
        "-filter_complex", graph, "-map", "[v]",
        "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(_sh.FPS), "-an",
        str(out_path),
    ]
    _sh._run(cmd)
    return out_path

