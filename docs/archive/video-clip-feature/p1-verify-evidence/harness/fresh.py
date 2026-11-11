import sys, time
sys.path.insert(0, "/tmp/p1_work")
from ui import Session, BASE
with Session() as s:
    for p in list(s.ctx.pages):
        if p.url.startswith(BASE):
            p.close()
    p = s.ctx.new_page()
    p.set_viewport_size({"width": 1400, "height": 1000})
    t0 = time.time()
    while True:
        try:
            p.goto(BASE + "/", wait_until="load", timeout=30000); break
        except Exception as e:
            if time.time() - t0 > 120: raise
            time.sleep(3)
    s.enable_semantics(p); time.sleep(6)
    print("fresh session ready;", len(s.labels(p)), "labels")
