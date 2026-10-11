import io, sys, time
sys.path.insert(0, "/tmp/p1_work")
import numpy as np
from PIL import Image
from ui import Session
scene_no, chip = sys.argv[1], sys.argv[2]
def say(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
def find_card(s, p):
    for g in s.labels(p):
        parts = g["label"].split("\n")
        if parts[0].startswith("Thời lượng beat") and len(parts) > 3 and parts[1] == scene_no and chip in parts:
            return g
with Session() as s:
    p = s.open_flet()
    for _ in range(40):
        p.mouse.move(600, 500); p.mouse.wheel(0, -1500); time.sleep(0.15)
    card = None
    for _ in range(60):
        card = find_card(s, p)
        if card and 120 < card["r"][1] < 650: break
        p.mouse.move(600, 500); p.mouse.wheel(0, 300); time.sleep(0.35)
    assert card, "card not found"
    cx, cy, cw, ch = card["r"]
    say(f"card: {card['label'].splitlines()}")
    s.shot(p, f"rollback_{scene_no}_{chip}_0_before.png")
    arr = np.asarray(Image.open(io.BytesIO(p.screenshot())).convert("L")).astype(int)
    band = arr[cy + 22: cy + 50, 640: cx + cw]
    cols = band.max(axis=0) > 90
    clusters, st = [], None
    for i, on in enumerate(list(cols) + [False]):
        if on and st is None: st = i
        if not on and st is not None: clusters.append((st, i - 1)); st = None
    merged = []
    for a, b in clusters:
        if merged and a - merged[-1][1] < 8: merged[-1] = (merged[-1][0], b)
        else: merged.append((a, b))
    icons = [640 + (a + b) // 2 for a, b in merged if 8 <= b - a <= 30]
    say(f"header icon x positions: {icons}  (add-image, Dùng MP4, UNDO, merge, trash)")
    p.mouse.click(icons[2], cy + 35); time.sleep(3)
    s.shot(p, f"rollback_{scene_no}_{chip}_1_after.png")
    say(f"clicked UNDO at ({icons[2]}, {cy+35})")
