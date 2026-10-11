import sys, time
sys.path.insert(0, "/tmp/p1_work")
from ui import Session
def say(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
with Session() as s:
    p = s.open_flet()
    s.shot(p, "approve_0_before.png")
    p.mouse.click(1160, 261); time.sleep(2.5)       # the right panel's Approve / Un-approve toggle (fixed layout)
    s.shot(p, "approve_1_after_first_click.png")
    say("clicked the Approve/Un-approve toggle once")
