import json, subprocess, sys, time
sys.path.insert(0, "/tmp/p1_work")
from ui import Session, BASE
def say(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
def ssh(c): return subprocess.run(["/tmp/p1_ssh.sh", c], capture_output=True, text=True).stdout
with Session() as s:
    for p in list(s.ctx.pages):
        if p.url.startswith(BASE): p.close()
    p = s.ctx.new_page(); p.set_viewport_size({"width": 1400, "height": 1000})
    p.goto(BASE + "/", wait_until="load"); s.enable_semantics(p); time.sleep(8)
    for _ in range(40):
        p.mouse.move(600, 500); p.mouse.wheel(0, -1500); time.sleep(0.1)
    labs = s.labels(p)
    cards = [x for x in labs if x["label"].split("\n")[0].isdigit() and len(x["label"].split("\n")[0]) == 2]
    say(f"card header groups visible: {[x['label'].splitlines()[:4] for x in cards][:3]}")
    say("duration chip / TTS tooltip present anywhere: " + str(any("Thời lượng beat" in x["label"] for x in labs)))
    say("'MP4' chips present: " + str(any(x["label"] in ("MP4", "MP4 clip selected") or "\nMP4" in x["label"] for x in labs)))
    s.shot(p, "flagoff_review_gate.png")
    # files the clip layer would have written
    out = ssh(r"dir /b D:\code\cbp-video-test-p1\repo\projects\qa_off\review & dir /b D:\code\cbp-video-test-p1\repo\projects\qa_off\cache 2>&1")
    say("review/ + cache/ after opening the screen for 8s: " + " | ".join(out.split()))
