import subprocess, sys, os
sys.path.insert(0, r"D:\code\cbp-video-test-p1\repo"); os.chdir(r"D:\code\cbp-video-test-p1\repo")
from pathlib import Path
from stages import clip_fetch
out = Path(r"D:\code\cbp-video-test-p1\dl_debug"); out.mkdir(exist_ok=True)
for f in out.glob("*"): f.unlink()
cmd = clip_fetch.ytdlp_section_args("https://www.youtube.com/watch?v=" + sys.argv[1], out, start=float(sys.argv[2]), beat_duration=3.8)
extra = sys.argv[3:]
cmd[-1:-1] = extra
r = subprocess.run(cmd, capture_output=True, text=True)
print("rc", r.returncode); print("STDOUT:", r.stdout[-800:]); err = r.stderr
import re
err = re.sub(r"https://[^ \"\n]*", "<url>", err)
print("STDERR:", err[-5000:])
print(sorted(p.name for p in out.glob("*")))
