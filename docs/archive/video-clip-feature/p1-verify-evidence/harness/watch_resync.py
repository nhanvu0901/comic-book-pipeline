import json, subprocess, sys, time
LOG = open("/tmp/p1_evidence/resync.log", "a")
def say(m):
    l = f"{time.strftime('%H:%M:%S')} {m}"; print(l, flush=True); LOG.write(l + "\n"); LOG.flush()
def ssh(cmd): return subprocess.run(["/tmp/p1_ssh.sh", cmd], capture_output=True, text=True).stdout
dur = float(sys.argv[1]); t0 = time.time(); last = None; seen = set()
while time.time() - t0 < dur:
    try: st = json.loads(ssh(r"type D:\code\cbp-video-test-p1\repo\projects\qa_e2e\review\tts_status.json"))
    except Exception: st = {}
    pids = sorted(set(x.strip() for x in ssh(r"powershell -NoProfile -ExecutionPolicy Bypass -File D:\code\cbp-video-test-p1\workers.ps1").split() if x.strip().isdigit()))
    seen |= set(pids)
    n_wav = len([l for l in ssh(r"dir /b D:\code\cbp-video-test-p1\repo\projects\qa_e2e\cache\tts\*.wav").splitlines() if l.strip().endswith(".wav")])
    sig = (st.get("chunks_done"), st.get("running"), st.get("completed"), n_wav, tuple(pids), json.dumps(st.get("beat_durations"), sort_keys=True))
    if sig != last:
        last = sig
        say(f"status: chunks {st.get('chunks_done')}/{st.get('chunks_total')} running={st.get('running')} completed={st.get('completed')} | cached chunk wavs on disk: {n_wav} | worker pids now: {pids} | beat_durations[3:1]={ (st.get('beat_durations') or {}).get('3:1')}")
    time.sleep(2)
say(f"watch ended; worker processes seen: {sorted(seen)}")
