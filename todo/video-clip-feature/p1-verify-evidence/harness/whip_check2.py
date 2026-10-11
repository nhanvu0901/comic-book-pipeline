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
from stages.stage_5.schema import Shot
js = json.loads((Path("/tmp/p1_evidence/qa_e2e_rollback1/shots.json")).read_text())
shot_objs = [Shot(shot_id=s["shot_id"], scene_id=s["scene_id"], duration_seconds=s["duration_seconds"], panel_bbox={}, source_image="", motion="zoom_in") for s in js]
whip = new._pick_whip_boundaries("qa_e2e", shot_objs)
groups = new._group_shots_only(shot_objs)
pairs = [(groups[b][1][-1].shot_id, groups[b + 1][1][0].shot_id) for b in sorted(whip)]
print("whip boundaries chosen for project 'qa_e2e' (scene groups i|i+1):", sorted(whip), "-> shot pairs", pairs)
old = load_pipeline("/tmp/p0_pre")
rows = []
for a, b in pairs:
    prev, nxt = shots_dir / f"shot_{a:03d}.mp4", shots_dir / f"shot_{b:03d}.mp4"
    o = EV / f"pair_{a}_{b}_old.mp4"; n_ = EV / f"pair_{a}_{b}_new.mp4"
    old._build_whip_bridge(prev, nxt, o, 0.24)
    sys.path.insert(0, "/Users/nhanvu/.ao/data/worktrees/comic-book-pipeline/comic-book-pipeline-18")
    for m in [m for m in sys.modules if m == "config" or m.startswith(("stages", "utils"))]: del sys.modules[m]
    import os; os.chdir("/Users/nhanvu/.ao/data/worktrees/comic-book-pipeline/comic-book-pipeline-18")
    newmod = importlib.import_module("stages.stage_5.pipeline"); sys.path.pop(0)
    newmod._build_whip_bridge(prev, nxt, n_, 0.24)
    for m in [m for m in sys.modules if m == "config" or m.startswith(("stages", "utils"))]: del sys.modules[m]
    sys.path.insert(0, "/tmp/p0_pre"); os.chdir("/tmp/p0_pre"); old = importlib.import_module("stages.stage_5.pipeline"); sys.path.pop(0)
    so, sn = streak(o), streak(n_)
    mo, mn = max(max(x, y) for x, y in so), max(max(x, y) for x, y in sn)
    rows.append((a, b, mo, mn)); print(f"  pair shot {a}->{b}: longest identical-to-border band  pre-fix {mo:4d} rows | fixed {mn:3d} rows")
print("ALL pairs: fixed <= pre-fix:", all(mn <= mo for _, _, mo, mn in rows), "| total pre-fix", sum(r[2] for r in rows), "vs fixed", sum(r[3] for r in rows))
