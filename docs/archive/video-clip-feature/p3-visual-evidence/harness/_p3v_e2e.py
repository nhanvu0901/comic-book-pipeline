"""P3-visual Windows E2E: fixture -> picks (real YouTube section, corrupt+backup, still, cards)
-> approval -> Stage 4 (real Chatterbox, CPU) -> Stage 5 screen_qa -> final.mp4 + sync report."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import config
from PIL import Image
from stages import clip_fetch
from stages.stage_5 import screen_selection as sel
from stages.stage_5.pipeline import assemble_project, _wav_duration, _probe_duration

T0 = time.time()
def log(m): print(f"[{time.time()-T0:7.1f}s] {m}", flush=True)

assert config.ENABLE_VIDEO_CLIPS, "run with ENABLE_VIDEO_CLIPS=1"
name = "screen_fixture"
proj = Path(config.PROJECTS_ROOT) / name
if proj.exists():
    shutil.rmtree(proj)
(proj / "review" / "clips").mkdir(parents=True)
(proj / "review" / "custom").mkdir(parents=True)
fx = ROOT / "tests" / "fixtures" / "screen_qa_project"
shutil.copy(fx / "narration.json", proj / "narration.json")
shutil.copy(fx / "screen_context.json", proj / "screen_context.json")
nar = json.loads((proj / "narration.json").read_text())
log(f"project {proj}  seed={config.CHATTERBOX_SEED} POST_ATEMPO={config.POST_ATEMPO}")

ff = Path(subprocess.run(["where", "ffmpeg"], capture_output=True, text=True).stdout.splitlines()[0])
def gen_clip(path, seconds=6.0, color=None):
    src = f"color=c={color}:s=640x360:r=30" if color else "testsrc2=s=640x360:r=30"
    subprocess.run([str(ff), "-y", "-f", "lavfi", "-i", src, "-t", str(seconds), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-an", str(path)], check=True, capture_output=True)

clips = []
# 1:0 — a REAL section from YouTube through P1's fetch_clip_section (yt-dlp + keyframe cut)
URL = os.environ.get("P3V_CLIP_URL", "https://www.youtube.com/watch?v=TcMBFSGVi1c")
START = float(os.environ.get("P3V_CLIP_START", "60"))
beat_secs = sel.beat_seconds(proj, nar)
try:
    sec = clip_fetch.fetch_clip_section(URL, proj / "review" / "clips", start=START,
                                        beat_duration=beat_secs["1:0"], log=log)
    clips.append({"id": "TcMBFSGVi1c", "file": sec.relative_to(proj).as_posix(), "beat": "1:0",
                  "start": 0.0, "end": round(beat_secs["1:0"], 3), "source_url": URL,
                  "source_start": START})
    log(f"real section downloaded: {sec.name}")
except Exception as exc:
    log(f"!! real section download failed ({exc}) — beat 1:0 will fall through the chain")
    clips.append({"id": "TcMBFSGVi1c", "file": "review/clips/TcMBFSGVi1c_missing.mp4", "beat": "1:0",
                  "start": 0.0, "end": 3.0, "source_url": URL, "source_start": START})
# 3:1 — a clip that is shorter than its beat (generated)
gen_clip(proj / "review" / "clips" / "gen_a.mp4", 1.2)
clips.append({"id": "gen_a", "file": "review/clips/gen_a.mp4", "beat": "3:1", "start": 0.0, "end": 1.2})
# 2:1 — corrupt primary + good backup
(proj / "review" / "clips" / "broken.mp4").write_bytes(b"not a video")
gen_clip(proj / "review" / "clips" / "gen_backup.mp4", 6.0, "orange")
clips.append({"id": "bad", "file": "review/clips/broken.mp4", "beat": "2:1", "start": 0.0, "end": 2.0,
              "backup": {"id": "bad-backup", "file": "review/clips/gen_backup.mp4", "start": 0.0, "end": 3.0}})
(proj / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": clips}, indent=2))
# 2:0 — a still (landscape, like a film frame)
Image.linear_gradient("L").resize((1600, 900)).convert("RGB").save(proj / "review" / "custom" / "frame.png")
(proj / "review" / "custom" / "custom_images.json").write_text(json.dumps(
    {"images": [{"file": "review/custom/frame.png", "beat_key": "2:0", "desc": "", "enrich_status": "pending"}]}))
sel.lock_still(proj, "2:0", "review/custom/frame.png")
sel.set_approved(proj, True)
log("picks written, project approved")

# ── Stage 4: real Chatterbox on CPU ────────────────────────────────────────────
from stages.stage_4.pipeline import synthesize_project
res = synthesize_project(name, force=True)
log(f"TTS done: audio {res.audio_duration_seconds:.2f}s, {len(res.scene_timings)} scene timings")

# ── Stage 5: screen_qa ─────────────────────────────────────────────────────────
out = assemble_project(name, force=True, progress=log)
final = Path(out.final_path)
log(f"final: {final}  {out.duration_seconds}s  shots={out.shot_count}")

# ── report ─────────────────────────────────────────────────────────────────────
audio = _wav_duration(proj / "audio.wav")
def probe(p, sel_, entries):
    r = subprocess.run([str(ff).replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error", "-select_streams", sel_,
                        "-show_entries", entries, "-of", "json", str(p)], capture_output=True, text=True)
    return (json.loads(r.stdout).get("streams") or [{}])[0]
v = probe(final, "v:0", "stream=codec_name,profile,width,height,pix_fmt,r_frame_rate")
a = probe(final, "a:0", "stream=codec_name,sample_rate,channels")
shots = json.loads((proj / "shots.json").read_text())
timings = json.loads((proj / "scene_timings.json").read_text())
print("\n=== REPORT ===")
print("video", v); print("audio", a)
print(f"audio.wav={audio:.3f}s  video_silent={_probe_duration(proj/'video_silent.mp4'):.3f}s  final={_probe_duration(final):.3f}s")
cum = 0.0
print("shot  level      start    dur   frames  beats")
for e in shots:
    print(f"{e['shot_id']:>4}  {str(e['level_name']):<10} {cum:6.2f} {e['duration_seconds']:6.2f} {e['frames']:>6}  {e['beats']}"
          + (f"  notes={e['level_notes']}" if e.get('level_notes') else ""))
    cum += e["duration_seconds"]
print("\nscene boundaries (end of scene i  vs  start of next scene's first shot):")
starts, c = {}, 0.0
for e in shots:
    starts.setdefault(e["scene_id"], c); c += e["duration_seconds"]
for i, t in enumerate(timings[:-1]):
    nxt = starts.get(timings[i + 1]["scene_id"])
    print(f"  scene {t['scene_id']} ends {t['end']:.3f}s ; scene {timings[i+1]['scene_id']} first shot starts {nxt:.3f}s ; "
          f"next scene's speech begins {timings[i+1]['start']:.3f}s")
(proj / "_e2e_ok").write_text("ok")
log("E2E OK")
