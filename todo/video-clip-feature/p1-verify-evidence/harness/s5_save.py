import sys, time
sys.path.insert(0, "/tmp/p1_work")
from ui import Session
with Session() as s:
    p = s.open_flet()
    p.mouse.click(1190, 195); time.sleep(4)
    s.shot(p, "edit_3_second_save_click.png")
    print("clicked save again")
