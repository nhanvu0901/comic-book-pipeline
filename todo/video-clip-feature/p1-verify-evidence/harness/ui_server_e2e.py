"""E2E UI server for the P1 test dir: the REAL ui.__main__._run() (--lan wiring), only with the bind host
forced to 127.0.0.1 so it is reachable solely through `ssh -L`. Usage: python -u ui_server_e2e.py <port>"""
import os, sys
REPO = r"D:\code\cbp-video-test-p1\repo"
os.chdir(REPO)
sys.path.insert(0, REPO)
port = sys.argv[1]

import uvicorn
_orig_uv = uvicorn.run
uvicorn.run = lambda app, *a, **k: _orig_uv(app, *a, **{**k, "host": "127.0.0.1"})   # flag ON: FastAPI wrapper

import flet as ft
_orig_ft = ft.run
def _local_ft_run(*a, **k):                                                          # flag OFF: plain ft.run
    if "host" in k:
        k["host"] = "127.0.0.1"
    return _orig_ft(*a, **k)
ft.run = _local_ft_run

sys.argv = ["ui", "--lan", "--port", port]
import config
print(f"[e2e-server] ENABLE_VIDEO_CLIPS={config.ENABLE_VIDEO_CLIPS} CHATTERBOX_SEED={config.CHATTERBOX_SEED} "
      f"POST_ATEMPO={config.POST_ATEMPO} PROJECTS_ROOT={config.PROJECTS_ROOT}", flush=True)
import ui.__main__ as m
m._run()
