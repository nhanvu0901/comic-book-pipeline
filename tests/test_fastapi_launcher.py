import json
import pytest
from fastapi.testclient import TestClient

import config
from stages import clip_fetch
from ui.web_routes import router, add_moment_picked_listener, clear_moment_picked_listeners


def test_routes_mounted_and_respond(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir(parents=True)

    # Mock moment_search so GET doesn't hit the network
    mock_candidates = [
        {
            "id": "vid_sample",
            "url": "https://www.youtube.com/watch?v=vid_sample",
            "title": "Sample Clip",
            "channel": "Sample Channel",
            "duration": 60.0,
            "embeddable": True,
            "moments": [{"start": 5.0, "end": 10.0, "label": "Sample"}],
        }
    ]
    monkeypatch.setattr(clip_fetch, "moment_search", lambda q, **kw: mock_candidates)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. GET /moments_review returns HTML
    res = client.get("/moments_review?project=demo&beat=1:1")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Chọn khoảnh khắc" in res.text
    assert "1:1" in res.text
    assert "Sample Clip" in res.text

    # 2. POST /api/pick_moment broadcasts payload
    clear_moment_picked_listeners()
    received_events = []
    add_moment_picked_listener(lambda data: received_events.append(data))

    payload = {
        "project": "demo",
        "beat": "1:1",
        "video_id": "vid_sample",
        "start": 42.0,
    }
    res_post = client.post("/api/pick_moment", json=payload)
    assert res_post.status_code == 200
    res_json = res_post.json()
    assert res_json["status"] == "ok"
    assert len(received_events) == 1
    assert received_events[0]["id"] == "vid_sample" or received_events[0].get("video_id") == "vid_sample"

    # 3. GET /api/beat_tts_status returns JSON
    res_status = client.get("/api/beat_tts_status?project=demo")
    assert res_status.status_code == 200
    assert "beat_durations" in res_status.json()


def test_path_traversal_and_invalid_beat_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "legit_proj").mkdir()

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Path traversal in project
    res1 = client.get("/moments_review?project=../../etc&beat=1:1")
    assert res1.status_code in (400, 404)

    # Path traversal in POST
    res2 = client.post("/api/pick_moment", json={
        "project": "../../etc",
        "beat": "1:1",
        "video_id": "test",
        "start": 0.0,
    })
    assert res2.status_code in (400, 404)

    # Invalid beat key
    res3 = client.post("/api/pick_moment", json={
        "project": "legit_proj",
        "beat": "../injected_path",
        "video_id": "test",
        "start": 0.0,
    })
    assert res3.status_code == 400


def test_lan_flag_uses_exact_old_ft_run_when_flag_off(monkeypatch):
    import config
    import ui.__main__ as ui_main

    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)

    called_ft_run = []
    def fake_ft_run(*args, **kwargs):
        called_ft_run.append((args, kwargs))

    import flet as ft
    monkeypatch.setattr(ft, "run", fake_ft_run)
    monkeypatch.setattr("sys.argv", ["ui", "--lan", "--port", "8550"])

    ui_main._run()

    assert len(called_ft_run) == 1
    args, kwargs = called_ft_run[0]
    assert kwargs.get("view") == ft.AppView.WEB_BROWSER
    assert kwargs.get("port") == 8550
    assert "export_asgi_app" not in kwargs


def test_lan_flag_uses_fastapi_when_flag_on(monkeypatch):
    import config
    import ui.__main__ as ui_main

    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)

    called_ft_run = []
    fake_asgi = object()
    def fake_ft_run(*args, **kwargs):
        called_ft_run.append((args, kwargs))
        return fake_asgi

    called_uvicorn_run = []
    import uvicorn
    def fake_uvicorn_run(app, *args, **kwargs):
        called_uvicorn_run.append((app, args, kwargs))

    import flet as ft
    monkeypatch.setattr(ft, "run", fake_ft_run)
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr("sys.argv", ["ui", "--lan", "--port", "8560"])

    ui_main._run()

    assert len(called_ft_run) == 1
    _, kwargs = called_ft_run[0]
    assert kwargs.get("export_asgi_app") is True

    assert len(called_uvicorn_run) == 1
    app, args, kwargs = called_uvicorn_run[0]
    assert kwargs.get("port") == 8560
