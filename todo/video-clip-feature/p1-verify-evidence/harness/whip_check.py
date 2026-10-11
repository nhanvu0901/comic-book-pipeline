"""Whip bridges: the fixed (mirrored) bridge vs the pre-fix (stretched) one, built from the SAME two real frames,
plus the bridges the Windows render actually produced."""
import importlib, json, subprocess, sys, shutil
from pathlib import Path
import numpy as np
from PIL import Image
EV = Path("/tmp/p1_evidence/whip")
shots_dir = Path("/tmp/p1_evidence/qa_e2e/shots")
def load_pipeline(repo):
    for m in [m for m in sys.modules if m == "config" or m.startswith(("stages", "utils"))]:
        del sys.modules[m]
    sys.path.insert(0, str(repo)); import os; os.chdir(repo)
    mod = importlib.import_module("stages.stage_5.pipeline")
    assert Path(mod.__file__).resolve().is_relative_to(Path(repo).resolve()), mod.__file__
    sys.path.pop(0)
    return mod
def frames(path):
    d = EV / "_f"; shutil.rmtree(d, ignore_errors=True); d.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), str(d / "f_%03d.png")], check=True)
    return [np.asarray(Image.open(p).convert("RGB")).astype(int) for p in sorted(d.glob("f_*.png"))]
def border_run(fr, top: bool):
    """rows, counted inward from the top/bottom border, that are (nearly) identical to the border row = a stretched band"""
    rows = fr if top else fr[::-1]
    n = 0
    for k in range(1, len(rows)):
        if np.abs(rows[k] - rows[0]).mean() < 1.5: n += 1
        else: break
    return n
def streak(path):
    fs = frames(path); return [(border_run(f, True), border_run(f, False)) for f in fs]
new = load_pipeline("/Users/nhanvu/.ao/data/worktrees/comic-book-pipeline/comic-book-pipeline-18")
prev, nxt = shots_dir / "shot_001.mp4", shots_dir / "shot_002.mp4"    # two real panel shots of the E2E project
out_new = EV / "bridge_new_mirror.mp4"; new._build_whip_bridge(prev, nxt, out_new, 0.24)
old = load_pipeline("/tmp/p0_pre")
out_old = EV / "bridge_old_stretch.mp4"; old._build_whip_bridge(prev, nxt, out_old, 0.24)
sn, so = streak(out_new), streak(out_old)
print("same two real frames, 0.24s whip bridge; for each bridge frame: (rows of an identical-to-the-border-row band at the TOP, at the BOTTOM)")
print("  pre-fix  (stretch):", so)
print("  fixed    (mirror) :", sn)
print(f"  longest stretched band: pre-fix {max(max(a, b) for a, b in so)} rows  vs  fixed {max(max(a, b) for a, b in sn)} rows")
win = [streak(p) for p in sorted(EV.glob("bridge_00*.mp4"))]
print("bridges produced by the Windows render (fixed code), longest border band per bridge:", [max(max(a, b) for a, b in w) for w in win])
# side-by-side montage of the middle frame of each variant
fo, fn = frames(out_old), frames(out_new)
mid = len(fo) // 2
im = np.concatenate([fo[mid], np.full((fo[mid].shape[0], 20, 3), 255), fn[mid]], axis=1).astype(np.uint8)
Image.fromarray(im).resize((im.shape[1] // 3, im.shape[0] // 3)).save(EV / "whip_old_vs_new_midframe.png")
json.dump({"old": so, "new": sn}, open(EV / "whip_metric.json", "w"))
