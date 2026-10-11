import sys, time
sys.path.insert(0, "/tmp/p1_work")
from ui import Session
scene_no, chip, new_text = sys.argv[1], sys.argv[2], sys.argv[3]
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
        if card and 120 < card["r"][1] < 560: break
        p.mouse.move(600, 500); p.mouse.wheel(0, 300); time.sleep(0.35)
    assert card
    cx, cy, cw, ch = card["r"]
    s.shot(p, "edit_0_before.png")
    p.mouse.click(430, cy + 95); time.sleep(0.6)
    p.keyboard.press("Meta+A"); time.sleep(0.3)
    p.keyboard.type(new_text, delay=15); time.sleep(0.8)
    s.shot(p, "edit_1_typed.png")
    say(f"typed the new fragment text: {new_text!r}")
    p.mouse.click(1190, 195); time.sleep(4)          # "Save narration edits"
    s.shot(p, "edit_2_after_save.png")
    say("clicked 'Save narration edits'")
