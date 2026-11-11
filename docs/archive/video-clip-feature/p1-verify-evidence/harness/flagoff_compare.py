"""One real project, three renders of the SAME inputs (no clips): HEAD flag ON, HEAD flag OFF, commit 6788212's code.
Compares video_silent.mp4 and every shot file byte for byte (shots.json embeds the checkout's path, so it is compared
with that path normalised)."""
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path
PY = r"D:\code\comic-book-pipeline\.venv\Scripts\python.exe"
HEAD = Path(r"D:\code\cbp-video-test-p1\repo")
OLD = Path(r"D:\code\cbp-video-test-p1\p0_6788212")
NAME = "qa_e2e"
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(repo, flag, label):
    env = dict(os.environ, ENABLE_VIDEO_CLIPS=str(flag), PYTHONUTF8="1", PYTHONUNBUFFERED="1",
               CHATTERBOX_VENV=r"D:\code\comic-book-pipeline\.venv-chatterbox")
    r = subprocess.run([PY, "-u", "-m", "stages.stage_5", "--project", NAME, "--force"], cwd=repo, env=env, capture_output=True, text=True)
    print(f"[{label}] rc={r.returncode}", [l for l in r.stdout.splitlines() if "whip" in l.lower() or "clips:" in l or "assembling" in l][:6], flush=True)
    if r.returncode: print(r.stderr[-1500:]); raise SystemExit(1)
    proj = repo / "projects" / NAME
    out = {"video_silent": sha(proj / "video_silent.mp4"),
           "shots": {p.name: sha(p) for p in sorted((proj / "shots").glob("shot_*.mp4"))},
           "shots_json_normalised": hashlib.sha256(((proj / "shots.json").read_text(encoding="utf-8").replace(str(repo), "<REPO>").replace(str(repo).replace("\\", "\\\\"), "<REPO>")).encode()).hexdigest(),
           "whip_bridges": sorted(p.name for p in (proj / "_whip_bridges").glob("*.mp4")) if (proj / "_whip_bridges").exists() else []}
    return out
# the old checkout renders a COPY of the very same project (inputs identical: audio, timings, locks, pages)
dst = OLD / "projects" / NAME
if dst.exists(): shutil.rmtree(dst)
shutil.copytree(HEAD / "projects" / NAME, dst, ignore=shutil.ignore_patterns("shots", "final.mp4", "video_silent.mp4", "_whip_bridges", "audio_mixed.wav", "concat_list.txt", "shots.json"))
res = {"HEAD flag ON": run(HEAD, 1, "HEAD flag ON"), "HEAD flag OFF": run(HEAD, 0, "HEAD flag OFF"), "6788212": run(OLD, 0, "6788212")}
for k, v in res.items():
    print(f"{k:14} video_silent {v['video_silent'][:16]}  shots.json(norm) {v['shots_json_normalised'][:16]}  {len(v['shots'])} shot files  whip bridge files: {len(v['whip_bridges'])}")
base = res["6788212"]
for k in ("HEAD flag ON", "HEAD flag OFF"):
    v = res[k]
    print(f"{k} == 6788212 :: video_silent {v['video_silent'] == base['video_silent']} | every shot file {v['shots'] == base['shots']} | shots.json {v['shots_json_normalised'] == base['shots_json_normalised']}")
json.dump(res, open(r"D:\code\cbp-video-test-p1\flagoff_compare.json", "w"), indent=1)
