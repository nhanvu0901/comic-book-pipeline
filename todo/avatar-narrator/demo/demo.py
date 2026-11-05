"""Mascot demo: one consistent character, whole-image moves only (AnimeRewind-style).

Background: public-domain comic panels (Exciting Comics v3 #1, Wikimedia Commons) with a slow
Ken Burns, contain + blurred fill. Narration: macOS `say` (placeholder voice). Mascot: Rook, three
pose pictures; every move is a transform of the whole picture (rise-in with motion blur, idle
float/breathe, talk bounce from the voice level, hop, pop-swap, shake, slide out).
"""
import math, subprocess, wave, json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
HERE = os.path.dirname(os.path.abspath(__file__))
FONT = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Black.ttf", 64)

# ── story: (panel box on page2 at 975 px wide, narration line, keyword to highlight) ──────────
SHOTS = [
    ((485, 360, 893, 640), "This city's district attorney hides his face behind a mask, and nobody knows it's him.", "mask"),
    ((70, 58, 465, 342), "It all starts with a token from an athletic club that closed years ago.", "token"),
    ((70, 360, 465, 640), "He climbs the wall and knocks out the guard without a sound.", "wall"),
    ((485, 660, 893, 940), "But inside, the gang is already waiting for him.", "waiting"),
    ((70, 958, 465, 1243), "One punch from behind, and the Mask hits the floor.", "punch"),
    ((485, 958, 893, 1243), "Then they drag him out, to make sure he never talks.", "never"),
]
GAP = 0.18  # silence between lines (s)


# ── easing ───────────────────────────────────────────────────────────────────────────────────
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))

def out_back(u, k=1.70158):
    u = clamp(u) - 1
    return 1 + (k + 1) * u ** 3 + k * u ** 2

def in_back(u, k=1.70158):
    u = clamp(u)
    return (k + 1) * u ** 3 - k * u ** 2

def in_out_sine(u):
    return 0.5 - 0.5 * math.cos(math.pi * clamp(u))

def out_quad(u):
    u = clamp(u)
    return 1 - (1 - u) ** 2


# ── narration ────────────────────────────────────────────────────────────────────────────────
def make_audio():
    lines = []
    for i, (_box, text, _kw) in enumerate(SHOTS):
        aiff, wav = f"{HERE}/line{i}.aiff", f"{HERE}/line{i}.wav"
        subprocess.run(["say", "-v", "Daniel", "-r", "185", "-o", aiff, text], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", aiff, "-ar", "48000", "-ac", "1", wav], check=True)
        with wave.open(wav) as w:
            n = w.getnframes(); sr = w.getframerate()
            pcm = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
        lines.append(pcm)
    sr = 48000
    t, pcm_all, timing = 0.35, [np.zeros(int(0.35 * sr), np.float32)], []
    for i, pcm in enumerate(lines):
        dur = len(pcm) / sr
        timing.append((t, t + dur))
        pcm_all += [pcm, np.zeros(int(GAP * sr), np.float32)]
        t += dur + GAP
    pcm_all.append(np.zeros(int(0.8 * sr), np.float32))
    full = np.concatenate(pcm_all)
    with wave.open(f"{HERE}/narration.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(full, -1, 1) * 32767).astype(np.int16).tobytes())
    # voice envelope per video frame, smoothed (drives the talk bounce)
    hop = sr // FPS
    env = np.array([np.sqrt(np.mean(full[i:i + hop] ** 2)) for i in range(0, len(full) - hop, hop)])
    env = env / (env.max() + 1e-9)
    env = np.convolve(env, np.ones(3) / 3, mode="same")
    return timing, len(full) / sr, env


# ── captions: 2–3 word groups spread over each line by character count ─────────────────────
def caption_track(timing):
    out = []
    for (t0, t1), (_box, text, kw) in zip(timing, SHOTS):
        words = text.split()
        groups, cur = [], []
        for w in words:
            cur.append(w)
            if len(cur) == 3 or w.endswith((",", ".")):
                groups.append(cur); cur = []
        if cur:
            groups.append(cur)
        total = sum(len(" ".join(g)) for g in groups)
        t = t0
        for g in groups:
            d = (t1 - t0) * len(" ".join(g)) / total
            out.append((t, t + d, g, kw))
            t += d
    return out


def draw_caption(frame, words, kw, age):
    d = ImageDraw.Draw(frame)
    text = " ".join(words).upper()
    pop = 1.0 + 0.12 * (1 - out_quad(age / 0.12))           # tiny pop when a group appears
    font = FONT.font_variant(size=int(64 * pop))
    widths = [d.textlength(w.upper() + " ", font=font) for w in words]
    x = (W - sum(widths)) / 2
    y = 1000
    for w, wd in zip(words, widths):
        hot = kw.lower() in w.lower().strip(",.'s")
        d.text((x, y), w.upper(), font=font, fill=(255, 214, 0) if hot else (255, 255, 255),
               stroke_width=8, stroke_fill=(0, 0, 0), anchor="ls")
        x += wd


# ── background shots: contain + blurred fill + slow Ken Burns ──────────────────────────────
def prepare_shots(page):
    shots = []
    for i, (box, _t, _k) in enumerate(SHOTS):
        panel = page.crop(box).convert("RGB")
        aspect = 1.15                                   # near-square crop, centred, so the panel fills more of the frame
        cw = min(panel.width, int(panel.height * aspect))
        cx0 = (panel.width - cw) // 2
        panel = panel.crop((cx0, 0, cx0 + cw, panel.height))
        bg = panel.resize((W, int(W * panel.height / panel.width)))
        bg = bg.resize((int(H * bg.width / bg.height), H)).crop(((int(H * bg.width / bg.height) - W) // 2, 0, (int(H * bg.width / bg.height) - W) // 2 + W, H))
        bg = bg.filter(ImageFilter.GaussianBlur(28)).point(lambda v: int(v * 0.45))
        pw = 1010
        ph = int(pw * panel.height / panel.width)
        big = panel.resize((int(pw * 1.12), int(ph * 1.12)), Image.LANCZOS)
        shots.append((bg, big, pw, ph, i % 2))
    return shots


def render_bg(shot, u):
    bg, big, pw, ph, direction = shot
    frame = bg.copy()
    z = 1.0 + 0.10 * in_out_sine(u)                         # 1.00 → 1.10 zoom over the shot
    cw, chh = int(big.width / z / 1.12 * 1.0), int(big.height / z / 1.12 * 1.0)
    # crop window in big-image coords, drifting toward a corner by direction
    sx = (big.width - big.width / z) * (0.5 + (0.3 if direction else -0.3) * in_out_sine(u))
    sy = (big.height - big.height / z) * 0.5
    win = big.crop((int(sx), int(sy), int(sx + big.width / z), int(sy + big.height / z))).resize((pw, ph), Image.BICUBIC)
    border = Image.new("RGB", (pw + 16, ph + 16), (245, 241, 234))
    border.paste(win, (8, 8))
    frame.paste(border, ((W - pw - 16) // 2, 170))
    return frame


# ── mascot timeline: whole-image moves only ───────────────────────────────────────────────
POSES = {p: Image.open(f"{HERE}/rook_{p}.png").convert("RGBA") for p in ("point", "shock", "think")}
BASE_H = 440                       # on-screen height (px) ≈ 23% of the frame
HOME = (30, 1040)                  # top-left of the mascot box: bottom-left, above the Shorts UI


def mascot_plan(timing):
    s = [t for t, _ in timing]
    e = [t for _, t in timing]
    # (start, end, pose_events, enter, exit, accents)
    return [
        dict(t0=s[0] + 0.05, t1=e[1] - 0.1, poses=[(s[0], "point"), (s[1] + 0.2, "think")],
             enter="rise", exit="sink", accents=[("hop", s[0] + 1.9), ("tilt", s[1] + 0.25)]),
        dict(t0=s[3] + 0.05, t1=e[5] + 0.35, poses=[(s[3], "think"), (s[4] + 0.30, "shock"), (s[5] + 0.35, "point")],
             enter="peek", exit="slide_right", accents=[("shake", s[4] + 0.30), ("hop", s[5] + 0.35)]),
    ]


def mascot_state(t, plan, env_val):
    for seg in plan:
        if not (seg["t0"] <= t <= seg["t1"] + 0.45):
            continue
        pose = [p for (ts, p) in seg["poses"] if ts <= t][-1] if any(ts <= t for ts, _ in seg["poses"]) else seg["poses"][0][1]
        x, y = HOME
        sx = sy = 1.0
        rot = 0.0
        alpha = 1.0
        blur = 0.0
        # idle float + breathe (never fully still)
        x += 4 * math.sin(2 * math.pi * t / 3.7)
        y += 7 * math.sin(2 * math.pi * t / 2.6)
        rot += 1.6 * math.sin(2 * math.pi * t / 3.3)
        breathe = 0.012 * math.sin(2 * math.pi * t / 2.2)
        sx += breathe; sy -= breathe
        # talk bounce from the voice level (narrator is "speaking")
        sy += 0.035 * env_val; sx -= 0.015 * env_val
        # enter
        ue = (t - seg["t0"]) / 0.45
        if ue < 1:
            if seg["enter"] == "rise":
                y += 520 * (1 - out_back(ue)); rot += -10 * (1 - out_back(ue)); blur = 10 * (1 - clamp(ue * 1.6))
            elif seg["enter"] == "peek":
                x += -560 * (1 - out_back(ue)); rot += 14 * (1 - out_back(ue)); blur = 10 * (1 - clamp(ue * 1.6))
        # exit
        ux = (t - seg["t1"]) / 0.4
        if ux > 0:
            if seg["exit"] == "sink":
                y += 560 * in_back(ux); blur = 9 * clamp(ux * 1.5)
            elif seg["exit"] == "slide_right":
                x += 1250 * in_back(ux); rot += 12 * in_back(ux); blur = 10 * clamp(ux * 1.5)
            if ux >= 1:
                return None
        # pose swap: pop (scale up then settle) centred on the swap instant
        for ts, _p in seg["poses"][1:]:
            up = (t - ts + 0.08) / 0.32
            if 0 <= up <= 1:
                k = math.sin(math.pi * up) * 0.16
                sx += k; sy += k
        # accents
        for name, ta in seg["accents"]:
            ua = (t - ta) / {"hop": 0.38, "tilt": 0.7, "shake": 0.6}[name]
            if 0 <= ua <= 1:
                if name == "hop":
                    y -= 70 * math.sin(math.pi * ua)
                    if ua > 0.85: sy -= 0.08 * (1 - ua) / 0.15; sx += 0.05 * (1 - ua) / 0.15
                elif name == "tilt":
                    rot += 9 * math.sin(math.pi * ua)
                elif name == "shake":
                    x += 16 * math.sin(2 * math.pi * 16 * ua) * math.exp(-4.5 * ua)
                    rot += 4 * math.sin(2 * math.pi * 11 * ua) * math.exp(-4.5 * ua)
        return dict(pose=pose, x=x, y=y, sx=sx, sy=sy, rot=rot, alpha=alpha, blur=blur)
    return None


def draw_mascot(frame, st):
    src = POSES[st["pose"]]
    h = BASE_H * st["sy"]; w = BASE_H * src.width / src.height * st["sx"]
    im = src.resize((max(1, int(w)), max(1, int(h))), Image.BICUBIC)
    if abs(st["rot"]) > 0.05:
        im = im.rotate(st["rot"], resample=Image.BICUBIC, expand=True)
    if st["blur"] > 0.6:
        im = im.filter(ImageFilter.GaussianBlur(st["blur"]))
    # anchor: feet stay at the bottom-centre of the home box so squash/stretch reads as weight
    bx = st["x"] + BASE_H * src.width / src.height / 2 - im.width / 2
    by = st["y"] + BASE_H - im.height + (im.height - h) / 2 * 0
    frame.paste(im, (int(bx), int(by)), im)


def main():
    timing, total, env = make_audio()
    caps = caption_track(timing)
    page = Image.open(f"{HERE}/page2.jpg")
    shots = prepare_shots(page)
    plan = mascot_plan(timing)
    n = int(total * FPS)
    ff = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", f"{HERE}/narration.wav", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", f"{HERE}/mascot_demo.mp4"], stdin=subprocess.PIPE)
    for f in range(n):
        t = f / FPS
        idx = max([i for i, (t0, _t1) in enumerate(timing) if t0 - GAP <= t] or [0])
        t0 = timing[idx][0] - (0.35 if idx == 0 else GAP)
        t1 = timing[idx + 1][0] - GAP if idx + 1 < len(timing) else total
        frame = render_bg(shots[idx], (t - t0) / max(0.1, t1 - t0))
        for c0, c1, words, kw in caps:
            if c0 <= t < c1:
                draw_caption(frame, words, kw, t - c0)
        st = mascot_state(t, plan, float(env[min(f, len(env) - 1)]))
        if st:
            draw_mascot(frame, st)
        ff.stdin.write(frame.tobytes())
    ff.stdin.close(); ff.wait()
    json.dump({"timing": timing, "total": total}, open(f"{HERE}/timing.json", "w"))
    print("frames", n, "duration", round(total, 2))


if __name__ == "__main__":
    main()
