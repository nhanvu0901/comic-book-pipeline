"""Localhost-only UI server for the P3-visual Playwright test (port from P3V_PORT, default 8561).
Runs the REAL ui.__main__._run() (--lan, ENABLE_VIDEO_CLIPS=1 => FastAPI wraps Flet) with uvicorn's
host forced to 127.0.0.1, against a private projects_ui/ folder."""
import json, os, runpy, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import config
PR = ROOT / "projects_ui"
PR.mkdir(exist_ok=True)
config.PROJECTS_ROOT = PR          # BEFORE ui/stages import: they bind it by value
assert config.ENABLE_VIDEO_CLIPS, "needs ENABLE_VIDEO_CLIPS=1"

name = "ui_screen"
proj = PR / name
if not proj.exists():
    fx = ROOT / "tests" / "fixtures" / "screen_qa_project"
    (proj / "review" / "clips").mkdir(parents=True)
    (proj / "review" / "custom").mkdir(parents=True)
    shutil.copy(fx / "narration.json", proj / "narration.json")
    shutil.copy(fx / "screen_context.json", proj / "screen_context.json")
    (proj / "state.json").write_text(json.dumps(
        {"project_name": name, "current_stage": 5, "pipeline_mode": "screen_qa"}))
    cands = [{"id": f"vid{i}AAAAAA", "url": f"https://www.youtube.com/watch?v=vid{i}AAAAAA",
              "title": f"Fake candidate {i}", "channel": "Test", "duration": 120.0,
              "embeddable": False, "moments": [{"start": 10.0 * i, "end": 14.0 * i, "label": f"peak {i}"}]}
             for i in (1, 2, 3)]
    for beat in ("1_0", "1_1", "2_0", "2_1", "3_0", "3_1"):
        (proj / "review" / "clips" / f"search_{beat}.json").write_text(json.dumps(cands))

import uvicorn
_orig = uvicorn.run
def _local(app, host=None, port=None, **kw):
    print(f"[p3v] uvicorn forced to 127.0.0.1:{port}", flush=True)
    return _orig(app, host="127.0.0.1", port=port, **kw)
uvicorn.run = _local
sys.argv = ["ui", "--lan", "--port", os.environ.get("P3V_PORT", "8561")]
runpy.run_module("ui", run_name="__main__", alter_sys=True)
