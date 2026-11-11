import sys, time
sys.path.insert(0, "/tmp/p1_work")
from ui import Session
with Session() as s:
    p = s.flet_page(); s.enable_semantics(p)
    for _ in range(40):
        p.mouse.move(600, 500); p.mouse.wheel(0, -1500); time.sleep(0.1)
    p.mouse.click(430, 330); time.sleep(0.6)
    p.keyboard.press("Meta+A"); p.keyboard.type("Logan reverted to his feral Winter Soldier state again,", delay=15); time.sleep(0.8)
    p.mouse.click(1190, 195); time.sleep(2.5)     # first click blurs the field (commits the text)
    p.mouse.click(1190, 195); time.sleep(3.5)     # second click saves
    s.shot(p, "flagoff_edit_after_save.png")
    print("saved")
