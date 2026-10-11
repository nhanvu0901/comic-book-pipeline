"""Stand-in for stages/stage_4/_chatterbox_worker.py in tests: same job-file protocol, no torch.

For each chunk it writes out_dir/chunk_<i>.wav (24 kHz mono 16-bit, 0.1s per word of silence) and
prints {"i","sec","sr"}; a chunk whose text contains "FAIL" prints an error line instead. It also
appends the job (plus this process's pid) to $FAKE_WORKER_LOG, one JSON line per invocation, so a
test can count worker launches and read the per-chunk seeds. $FAKE_WORKER_DELAY seconds are slept
before each chunk (lets a test act while a batch is in flight)."""
import json
import os
import sys
import time
import wave
from pathlib import Path


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text())
    log = os.environ.get("FAKE_WORKER_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "job": job}) + "\n")
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"ready": True, "device": "cpu", "sr": 24000}), flush=True)
    delay = float(os.environ.get("FAKE_WORKER_DELAY", "0") or 0)
    for i, ch in enumerate(job["chunks"]):
        if delay:
            time.sleep(delay)
        if "FAIL" in ch["text"]:
            print(json.dumps({"i": i, "error": "RuntimeError('boom')"}), flush=True)
            continue
        sec = 0.1 * max(1, len(ch["text"].split()))
        with wave.open(str(out_dir / f"chunk_{i:05d}.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x00\x00" * int(sec * 24000))
        print(json.dumps({"i": i, "sec": sec, "sr": 24000}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
