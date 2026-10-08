"""Record the background TTS filling in beat durations: server status file vs. what the review screen shows."""
import json, subprocess, sys, time
sys.path.insert(0, "/tmp/p1_work")
from ui import Session, EVID

STATUS = r"D:\code\cbp-video-test-p1\repo\projects\qa_e2e\review\tts_status.json"
log = open(EVID / "tts_progress.log", "a")

def server_status():
    r = subprocess.run(["/tmp/p1_ssh.sh", f"type {STATUS}"], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return None

def say(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True); log.write(line + "\n"); log.flush()

def worker_pids():
    r = subprocess.run(["/tmp/p1_ssh.sh", r"powershell -NoProfile -ExecutionPolicy Bypass -File D:\code\cbp-video-test-p1\workers.ps1"], capture_output=True, text=True)
    return sorted(set(x.strip() for x in r.stdout.split() if x.strip().isdigit()))

seen_workers = set()
last = None
with Session() as s:
    p = s.open_flet()
    t0 = time.time()
    while time.time() - t0 < float(sys.argv[1]) if len(sys.argv) > 1 else 900:
        st = server_status() or {}
        wp = worker_pids(); seen_workers |= set(wp)
        sig = (st.get("chunks_done"), json.dumps(st.get("beat_durations"), sort_keys=True), st.get("completed"), st.get("error"))
        if sig != last:
            last = sig
            chips = [x["label"].split("\n") for x in s.labels(p) if "Thời lượng beat theo giọng đọc" in x["label"]]
            ui_chips = []
            for c in chips:
                ui_chips.append([t for t in c if t.endswith("s") and (t[:-1].replace(".", "").isdigit() or t == "…s")])
            say(f"server: chunks {st.get('chunks_done')}/{st.get('chunks_total')} completed={st.get('completed')} running={st.get('running')} "
                f"error={st.get('error')} worker_pids_now={wp} distinct_workers_so_far={len(seen_workers)} beat_durations={st.get('beat_durations')}")
            say(f"   ui duration chips (in card order): {[c[0] if c else '?' for c in ui_chips]}")
            s.shot(p, f"tts_progress_{int(time.time() - t0):04d}s_chunks{st.get('chunks_done')}.png")
        if st.get("completed") or st.get("error"):
            break
        time.sleep(4)
say(f"watch ended: distinct chatterbox worker processes launched = {len(seen_workers)} {sorted(seen_workers)}")
