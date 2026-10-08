import json
import pytest
from fastapi.testclient import TestClient

from ui.web_routes import router, add_moment_picked_listener


def test_routes_mounted_and_respond():
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

    # 2. POST /api/pick_moment broadcasts payload
    received_events = []
    add_moment_picked_listener(lambda data: received_events.append(data))

    payload = {
        "project": "demo",
        "beat": "1:1",
        "video_id": "dQw4w9WgXcQ",
        "start": 42.0,
    }
    res_post = client.post("/api/pick_moment", json=payload)
    assert res_post.status_code == 200
    res_json = res_post.json()
    assert res_json["status"] == "ok"
    assert len(received_events) == 1
    assert received_events[0]["video_id"] == "dQw4w9WgXcQ"
    assert received_events[0]["start"] == 42.0

    # 3. GET /api/beat_tts_status returns JSON
    res_status = client.get("/api/beat_tts_status?project=demo")
    assert res_status.status_code == 200
    assert "beat_durations" in res_status.json()


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

