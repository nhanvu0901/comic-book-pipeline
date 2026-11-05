"""Overlay the Rook mascot on an existing rendered Short (keeps its audio untouched).

usage: python3 overlay_current.py <input final.mp4> <output.mp4>
Choreography below is keyed to this video's scene cuts (ffmpeg scene > 0.25):
2.00 4.33 5.87 8.93 12.00 14.43 16.50 23.17 25.73 28.27 30.17
"""
import os, subprocess, sys, wave
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo  # noqa: E402  (reuses POSES, mascot_state, draw_mascot)

# Feet on the bottom edge of the frame: feet sit at 93.4% of the pose image, so the box top is
# 1920 - 0.934*440 = 1509; minus the 7 px idle float keeps the feet between 1906 and 1920.
demo.HOME = (30, 1502)

W, H, FPS = 1080, 1920, 30
src, dst = sys.argv[1], sys.argv[2]

dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "csv=p=0", src]).decode().strip())

# voice/music level per frame for the talk bounce
tmp_wav = dst + ".level.wav"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", "48000", tmp_wav], check=True)
with wave.open(tmp_wav) as w:
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
os.remove(tmp_wav)
hop = 48000 // FPS
env = np.array([np.sqrt(np.mean(pcm[i:i + hop] ** 2)) for i in range(0, len(pcm) - hop, hop)])
env = np.convolve(env / (env.max() + 1e-9), np.ones(3) / 3, mode="same")

PLAN = [
    dict(t0=0.20, t1=5.55, poses=[(0.0, "point"), (2.05, "think")],
         enter="rise", exit="sink", accents=[("hop", 1.25), ("tilt", 2.20)]),
    dict(t0=12.05, t1=16.25, poses=[(12.0, "think")],
         enter="peek", exit="sink", accents=[("tilt", 13.6)]),
    dict(t0=23.20, t1=dur - 0.55, poses=[(23.2, "point"), (25.76, "shock"), (28.30, "think"), (30.20, "point")],
         enter="rise", exit="slide_right", accents=[("hop", 23.75), ("shake", 25.76), ("hop", 30.25)]),
]

dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       stdout=subprocess.PIPE)
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                        "-r", str(FPS), "-i", "-", "-i", src, "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                        "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", dst],
                       stdin=subprocess.PIPE)
f = 0
while True:
    buf = dec.stdout.read(W * H * 3)
    if len(buf) < W * H * 3:
        break
    frame = Image.frombuffer("RGB", (W, H), buf)
    st = demo.mascot_state(f / FPS, PLAN, float(env[min(f, len(env) - 1)]))
    if st:
        frame = frame.copy()
        demo.draw_mascot(frame, st)
    enc.stdin.write(frame.tobytes())
    f += 1
enc.stdin.close(); enc.wait(); dec.wait()
print("frames", f, "->", dst)
