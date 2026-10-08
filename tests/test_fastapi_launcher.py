"""ui/__main__.py --lan: the stable Flet launcher with the flag OFF, FastAPI wrapping Flet with it ON.

The routes must be registered BEFORE Flet's catch-all mount("/"): a "/" mount swallows every path
registered after it. That is tested against the app _run() really builds (Flet itself replaced by a
tiny ASGI app that answers "FLET"), not against a hand-assembled copy of the wiring."""
import json

import pytest
from fastapi.testclient import TestClient

import config
from stages import clip_fetch


def _fake_flet_asgi():
    async def app(scope, receive, send):
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"FLET " + scope["path"].encode()})
    return app


@pytest.fixture
def launched_app(tmp_path, monkeypatch):
    """The FastAPI app ui.__main__._run() builds with the flag ON — uvicorn.run and ft.run stubbed."""
    import flet as ft
    import uvicorn
    import ui.__main__ as ui_main

    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "demo" / "review").mkdir(parents=True)
    got = {}
    monkeypatch.setattr(ft, "run", lambda *a, **k: got.setdefault("ft", (a, k)) and _fake_flet_asgi())
    monkeypatch.setattr(uvicorn, "run", lambda app, *a, **k: got.update(app=app, uvicorn=k))
    monkeypatch.setattr("sys.argv", ["ui", "--lan", "--port", "8560"])
    ui_main._run()
    got["tmp"] = tmp_path
    return got


def test_clip_routes_win_over_the_flet_catch_all_mount(launched_app, monkeypatch):
    calls = []
    monkeypatch.setattr(clip_fetch, "moment_search", lambda q, **kw: calls.append(q) or [])   # no network
    client = TestClient(launched_app["app"])

    page = client.get("/moments_review?project=demo&beat=1:0&q=hello")
    assert page.status_code == 200 and "Chọn khoảnh khắc" in page.text and calls == ["hello"], \
        "the clip route must answer, not the Flet mount"
    assert client.get("/api/beat_tts_status?project=demo").json()["beat_durations"] == {}
    assert client.post("/api/bump_beat_tts", json={"project": "demo", "beat": "1:0"}).status_code == 200
    # everything else still belongs to Flet
    assert client.get("/").text == "FLET /"
    assert client.get("/some/flet/asset.js").text == "FLET /some/flet/asset.js"

    # ... and the order is what makes that true: a router included AFTER the mount is shadowed
    from fastapi import FastAPI
    from ui.web_routes import router
    wrong = FastAPI()
    wrong.mount("/", _fake_flet_asgi())
    wrong.include_router(router)
    assert TestClient(wrong).get("/api/beat_tts_status?project=demo").text.startswith("FLET")


def test_the_launcher_wraps_flet_and_serves_projects_as_assets(launched_app):
    (_a, ft_kwargs) = launched_app["ft"]
    assert ft_kwargs.get("export_asgi_app") is True and "assets_dir" in ft_kwargs
    assert launched_app["uvicorn"].get("port") == 8560 and launched_app["uvicorn"].get("host") == "0.0.0.0"
    # first route that claims each request, exactly as Starlette dispatches: the clip routes before
    # the catch-all Flet Mount (version-agnostic — FastAPI wraps include_router differently over time)
    from starlette.routing import Match, Mount

    def first_route(path, method="GET"):
        scope = {"type": "http", "path": path, "method": method, "root_path": "", "query_string": b"",
                 "headers": []}
        return next(r for r in launched_app["app"].routes if r.matches(scope)[0] == Match.FULL)

    for path, method in [("/moments_review", "GET"), ("/api/pick_moment", "POST"), ("/api/pick_status", "GET"),
                         ("/api/clip_preview", "GET"), ("/api/beat_tts_status", "GET"), ("/api/bump_beat_tts", "POST")]:
        assert not isinstance(first_route(path, method), Mount), path
    assert isinstance(first_route("/"), Mount) and isinstance(first_route("/anything/else"), Mount)


def test_no_post_ever_writes_outside_the_temp_projects_root(launched_app, monkeypatch):
    """The POST of the old test wrote to the real PROJECTS_ROOT/demo; here nothing is downloaded and
    the only directory touched is the temp one."""
    from stages.stage_5 import clips
    tmp = launched_app["tmp"]
    monkeypatch.setattr(clip_fetch, "fetch_clip_section", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no download")))
    client = TestClient(launched_app["app"])
    # an unknown beat duration + a 0s wait: the job ends in "error" without touching yt-dlp
    from ui import web_routes
    monkeypatch.setattr(web_routes, "WAIT_TTS_SECONDS", 0.0)
    from stages.stage_4 import background_tts
    monkeypatch.setattr(background_tts, "bump_priority_beat", lambda p, b: None)
    r = client.post("/api/pick_moment?sync=true", json={"project": "demo", "beat": "1:0",
                                                        "video_id": "abcDEF12345", "start": 3.0})
    assert r.json()["status"] == "error"
    assert not (tmp / "demo" / "review" / "clips" / "clips.json").exists()
    assert not (config.PROJECTS_ROOT.parent / "demo").exists()


def test_path_traversal_and_invalid_beat_rejected(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from ui.web_routes import router
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "legit_proj").mkdir()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/moments_review?project=../../etc&beat=1:1").status_code in (400, 404)
    assert client.post("/api/pick_moment", json={"project": "../../etc", "beat": "1:1",
                                                 "video_id": "abcDEF12345", "start": 0.0}).status_code in (400, 404)
    assert client.post("/api/pick_moment", json={"project": "legit_proj", "beat": "../injected_path",
                                                 "video_id": "abcDEF12345", "start": 0.0}).status_code == 400
    # a project that merely resolves to PROJECTS_ROOT itself is not a project either
    assert client.get("/moments_review?project=.&beat=1:1").status_code == 400


def test_lan_flag_off_uses_exact_old_ft_run_and_never_imports_fastapi(monkeypatch):
    import flet as ft
    import sys
    import ui.__main__ as ui_main

    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    called = []
    monkeypatch.setattr(ft, "run", lambda *a, **k: called.append((a, k)))
    monkeypatch.setattr("sys.argv", ["ui", "--lan", "--port", "8550"])
    for m in [m for m in sys.modules if m == "ui.web_routes"]:
        monkeypatch.delitem(sys.modules, m)
    ui_main._run()

    assert len(called) == 1
    args, kwargs = called[0]
    assert kwargs.get("view") == ft.AppView.WEB_BROWSER and kwargs.get("port") == 8550
    assert kwargs.get("host") == "0.0.0.0" and "assets_dir" in kwargs
    assert "export_asgi_app" not in kwargs
    assert "ui.web_routes" not in sys.modules, "flag OFF must not even load the clip routes"
