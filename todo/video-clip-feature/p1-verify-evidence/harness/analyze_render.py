"""Verify a rendered Q&A project (flag ON, clips) against its own inputs — measured on decoded frames/audio.
usage: analyze_render.py <local copy of the project> [--video video_silent.mp4]"""
import json, math, subprocess, sys, wave
from pathlib import Path
import numpy as np

FPS = 30
root = Path(sys.argv[1])
out = {"checks": [], "info": {}}

def check(name, ok, detail=""):
    out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), "-", name, "|", detail, flush=True)

def info(name, value):
    out["info"][name] = value
    print("   info:", name, "=", value, flush=True)

def probe(path, entries, stream="v:0"):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-count_packets", "-show_entries", entries, "-of", "json", str(path)],
                       capture_output=True, text=True)
    return json.loads(r.stdout or "{}")

def frames_gray(path, w=108, h=192):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(raw) // (w * h)
    return np.frombuffer(raw[: n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)

def psnr(a, b):
    mse = float(np.mean((a - b) ** 2))
    return 99.0 if mse < 1e-9 else 10 * math.log10(255 * 255 / mse)

shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
status = json.loads((root / "review" / "tts_status.json").read_text(encoding="utf-8"))
manifest = json.loads((root / "review/clips/clips.json").read_text())["clips"] if (root / "review/clips/clips.json").exists() else []
video = root / (sys.argv[sys.argv.index("--video") + 1] if "--video" in sys.argv else "video_silent.mp4")
win = status.get("beat_windows") or {}

# frames each shot file really holds (the video is their concatenation) — NOT the rounded seconds in shots.json
nfr = [int(probe(root / "shots" / f"shot_{s['shot_id']:03d}.mp4", "stream=nb_read_packets")["streams"][0]["nb_read_packets"]) for s in shots]
cum = [0]
for n in nfr: cum.append(cum[-1] + n)
vinfo = probe(video, "stream=nb_read_packets,r_frame_rate,width,height,codec_name,pix_fmt")["streams"][0]
check("video is 1080x1920 30fps h264 yuv420p", (vinfo["width"], vinfo["height"], vinfo["r_frame_rate"], vinfo["codec_name"], vinfo["pix_fmt"]) == (1080, 1920, "30/1", "h264", "yuv420p"), str(vinfo))
info("shots / sum of shot-file frames / video frames", f"{len(shots)} / {cum[-1]} / {vinfo['nb_read_packets']}")
vid = frames_gray(video)

clip_idx = [i for i, s in enumerate(shots) if (s.get("clip") or {}).get("rendered")]
fb_idx = [i for i, s in enumerate(shots) if s.get("clip") and not s["clip"]["rendered"]]
check("every manifest clip landed on a shot, none fell back", len(clip_idx) == len(manifest) and not fb_idx,
      f"{len(manifest)} manifest entr(ies), rendered shots {clip_idx}, fallbacks {[(i, shots[i]['clip']['fallback_reason']) for i in fb_idx]}")

# ── 1. contract of every clip shot file
for i in clip_idx:
    f = root / "shots" / f"shot_{shots[i]['shot_id']:03d}.mp4"
    st = probe(f, "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_read_packets,color_space,color_range")["streams"]
    audio = probe(f, "stream=codec_type", "a")["streams"]
    want = max(1, round(max(0.4, shots[i]["duration_seconds"]) * FPS))
    ok = (len(st) == 1 and (st[0]["codec_name"], st[0]["pix_fmt"], st[0]["width"], st[0]["height"], st[0]["r_frame_rate"]) == ("h264", "yuv420p", 1080, 1920, "30/1")
          and int(st[0]["nb_read_packets"]) == want and not audio and st[0].get("color_space") in (None, "unknown"))
    check(f"clip shot {i} ({shots[i]['clip']['id']}) meets the contract: h264 yuv420p 1080x1920 30fps, {want} frames, muted, untagged", ok, str(st[0]) + f" audio={audio}")

# ── 2. the clip plays verbatim, and it is a HARD cut on both sides
def shot_gray(i): return frames_gray(root / "shots" / f"shot_{shots[i]['shot_id']:03d}.mp4")
measured = {}
for i in clip_idx:
    sg = shot_gray(i)
    exp = cum[i]
    # locate the clip's first frame within a few frames of where the shot files add up to
    cand = range(max(0, exp - 4), min(len(vid) - len(sg), exp + 5) + 1)
    b0 = max(cand, key=lambda k: psnr(vid[k], sg[0]))
    ps = [psnr(vid[b0 + k], sg[k]) for k in range(len(sg))]
    measured[i] = (b0, b0 + len(sg))
    check(f"clip shot {i}: all {len(sg)} frames [{b0},{b0+len(sg)}) are the clip shot's own frames, in order", min(ps) > 30, f"min PSNR {min(ps):.1f} dB, mean {np.mean(ps):.1f}; shot files add up to frame {exp}")
    if b0 > 0:
        pg = shot_gray(i - 1)
        step = float(np.abs(vid[b0] - vid[b0 - 1]).mean())
        around = [float(np.abs(vid[k] - vid[k - 1]).mean()) for k in range(b0 - 3, b0 + 4)]
        check(f"clip shot {i}: HARD CUT in — frame {b0-1} is the previous shot's last frame, frame {b0} the clip's first", psnr(vid[b0 - 1], pg[-1]) > 28 and psnr(vid[b0], sg[0]) > 30 and step > 8,
              f"PSNR prev-last {psnr(vid[b0-1], pg[-1]):.1f} dB / clip-first {psnr(vid[b0], sg[0]):.1f} dB; frame-to-frame steps around the cut {[round(a,1) for a in around]}")
    e = b0 + len(sg)
    if i + 1 < len(shots) and e < len(vid):
        ng = shot_gray(i + 1)
        around = [float(np.abs(vid[k] - vid[k - 1]).mean()) for k in range(e - 3, min(e + 4, len(vid)))]
        check(f"clip shot {i}: HARD CUT out — frame {e-1} is the clip's last frame, frame {e} the next shot's first", psnr(vid[e - 1], sg[-1]) > 30 and psnr(vid[e], ng[0]) > 28,
              f"PSNR clip-last {psnr(vid[e-1], sg[-1]):.1f} dB / next-first {psnr(vid[e], ng[0]):.1f} dB; steps around the cut {[round(a,1) for a in around]}")
# control: a boundary between two panel scenes outside the clip runs is a dissolve (a RAMP of blended frames, not one step)
pairs = [a for a in range(len(shots) - 1) if a not in clip_idx and a + 1 not in clip_idx and shots[a]["scene_id"] != shots[a + 1]["scene_id"] and cum[a + 1] + 8 < len(vid)]
if pairs:
    b = cum[pairs[0] + 1]
    steps = [round(float(np.abs(vid[k] - vid[k - 1]).mean()), 1) for k in range(b - 2, b + 10)]
    info(f"control: panel→panel boundary at frame {b}, frame-to-frame steps (a dissolve = a plateau, not one spike)", steps)

# ── 2b. the review preview is what Stage 5 renders (same section file, same fit; only the encoder preset differs)
for i in clip_idx:
    bt = shots[i]["clip"]["file"].replace("\\", "/")
    pv = [m for m in manifest if m["file"].split("/")[-1] == bt.split("/")[-1]]
    if pv and pv[0].get("preview_file"):
        pf = root / pv[0]["preview_file"]
        a, b = frames_gray(pf), shot_gray(i)
        n = min(len(a), len(b))
        ps = [psnr(a[k], b[k]) for k in range(n)]
        check(f"clip shot {i}: the 9:16 review preview shows the same frames as the rendered shot", len(a) == len(b) and np.mean(ps) > 30,
              f"{len(a)} vs {len(b)} frames, mean PSNR {np.mean(ps):.1f} dB, min {min(ps):.1f}")

# ── 3. frame-level sync against the CACHED AUDIO's beat windows
check("status is complete and carries a window for every review beat", status.get("completed") and len(win) > 0, f"{len(win)} windows")
info("beat windows (s)", {k: [round(v[0], 3), round(v[1], 3)] for k, v in win.items()})
boundaries = sorted({round(v[0] * FPS, 3) for v in win.values()} | {round(v[1] * FPS, 3) for v in win.values()})
# the stable loop-close (SEAMLESS_LOOP) appends an echo of the FIRST shot after the last beat: not an audio boundary
loop_echo = [i for i in range(1, len(shots)) if i == len(shots) - 1 and shots[i].get("source_image") == shots[0].get("source_image")
             and shots[i].get("panel_bbox") == shots[0].get("panel_bbox")]
info("loop-close echo shots excluded from the beat-boundary check", loop_echo)
errs = []
for i in range(1, len(shots)):
    if i in loop_echo:
        continue
    c = cum[i]
    nb = min(boundaries, key=lambda x: abs(x - c))
    errs.append((i, c, round(nb, 2), round(c - nb, 2)))
worst = max(abs(e[3]) for e in errs)
info("shot cut frame vs nearest beat boundary in the audio (shot idx, video frame, audio frame, error in frames)", errs)
check("every shot cut lands within 1 frame of a beat boundary of the cached audio", worst <= 1.0, f"worst error {worst:.2f} frames = {worst/FPS*1000:.0f} ms")
for i in clip_idx:
    key_candidates = [k for k, v in win.items() if abs(v[0] * FPS - measured[i][0]) <= 1.5]
    k = key_candidates[0] if key_candidates else None
    if k is None:
        check(f"clip shot {i} starts on a beat boundary", False, f"video frame {measured[i][0]} matches no beat start"); continue
    s_err = measured[i][0] - win[k][0] * FPS
    e_err = measured[i][1] - win[k][1] * FPS
    check(f"clip shot {i} == beat {k}: appears at audio {win[k][0]:.3f}s (frame {win[k][0]*FPS:.1f}) and leaves at {win[k][1]:.3f}s — measured frames {measured[i]}",
          abs(s_err) <= 1.0 and abs(e_err) <= 1.0, f"start error {s_err:+.2f} frames ({s_err/FPS*1000:+.0f} ms), end error {e_err:+.2f} frames ({e_err/FPS*1000:+.0f} ms)")
wav = root / "audio.wav"
with wave.open(str(wav), "rb") as wf: adur = wf.getnframes() / wf.getframerate()
check("audio.wav is exactly the cached audio (its length = the last beat window's end)", abs(adur - max(v[1] for v in win.values())) < 0.01, f"{adur:.4f}s")
info("video_silent vs audio (the stable loop-close adds a short tail; the final encode is -shortest)", f"video {len(vid)/FPS:.3f}s, audio {adur:.3f}s")

# ── 4. final.mp4: its audio IS the cached audio, aligned at 0
fin = root / "final.mp4"
if fin.exists():
    def env(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "4000", "-f", "s16le", "-"], capture_output=True).stdout
        a = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32)); k = 40
        return np.convolve(a, np.ones(k) / k, mode="same")
    ea, eb = env(wav), env(fin)
    n = min(len(ea), len(eb)); ea, eb = ea[:n] - ea[:n].mean(), eb[:n] - eb[:n].mean()
    m = int(0.5 * 4000); L = 1 << (2 * n - 1).bit_length()
    X = np.fft.irfft(np.fft.rfft(eb, L) * np.conj(np.fft.rfft(ea, L)), L)
    lag = int(np.argmax(np.concatenate([X[-m:], X[: m + 1]]))) - m
    check("final.mp4's audio track is the cached audio, offset 0 (envelope cross-correlation, ±0.5 s search)", abs(lag / 4000) <= 1.0 / FPS, f"offset {lag/4000*1000:.1f} ms")
    ai, vi = probe(fin, "stream=duration", "a:0")["streams"][0], probe(fin, "stream=duration,nb_read_packets", "v:0")["streams"][0]
    check("final.mp4 audio and video end together (within a frame)", abs(float(ai["duration"]) - float(vi["duration"])) <= 1.5 / FPS, f"audio {float(ai['duration']):.3f}s video {float(vi['duration']):.3f}s ({vi['nb_read_packets']} frames)")

(root / "analysis.json").write_text(json.dumps(out, indent=2, default=str))
fails = [c for c in out["checks"] if not c["ok"]]
print(f"\n{len(out['checks']) - len(fails)}/{len(out['checks'])} checks passed")
sys.exit(1 if fails else 0)
