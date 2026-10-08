import json
import time
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

import config
from stages import clip_fetch
from stages.stage_5 import clips
from ui import web_routes


def test_parallel_moment_search_8_workers(monkeypatch, tmp_path):
    monkeypatch.setattr(clip_fetch, "_MOMENTS_CACHE_DIR", tmp_path / "cache_moments")

    # Mock candidate search returning 8 candidate items
    dummy_cands = [
        {"id": f"vid_{i}", "url": f"https://www.youtube.com/watch?v=vid_{i}", "title": f"Video {i}"}
        for i in range(8)
    ]
    monkeypatch.setattr(clip_fetch, "gather_candidates", lambda *args, **kwargs: dummy_cands)
    monkeypatch.setattr(clip_fetch, "moment_queries", lambda desc, extra: [desc])

    call_times = []

    def mock_metadata(url, work_dir, **kwargs):
        call_times.append(time.time())
        time.sleep(0.05)
        return {"title": "Title", "channel": "Ch", "duration": 120, "playable_in_embed": True}, []

    t0 = time.time()
    results = clip_fetch.moment_search("Iron Man fights Hulk", limit=8, metadata=mock_metadata, use_cache=False)
    total_time = time.time() - t0

    assert len(results) == 8
    # With 8 workers in parallel, total time is well under sequential time (8 * 0.05 = 0.40s)
    assert total_time < 0.35, f"Expected 8 parallel workers to finish well under sequential time: {total_time:.3f}s"


def test_metadata_caching_by_video_id(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache_meta"
    monkeypatch.setattr(clip_fetch, "_META_CACHE_DIR", cache_dir)

    raw_calls = []
    fake_info = {"title": "Test Title", "duration": 60, "subtitles": {}}

    def fake_subprocess_run(cmd, *args, **kwargs):
        raw_calls.append(cmd)
        m = MagicMock()
        m.stdout = json.dumps(fake_info)
        m.stderr = ""
        m.returncode = 0
        return m

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)

    url = "https://www.youtube.com/watch?v=abc123xyz00"
    # First call: executes subprocess
    info1, cues1 = clip_fetch.video_metadata(url, tmp_path, use_cache=True)
    assert len(raw_calls) >= 1
    assert (cache_dir / "abc123xyz00.json").exists()

    # Second call: reads from cache without calling subprocess
    first_count = len(raw_calls)
    info2, cues2 = clip_fetch.video_metadata(url, tmp_path, use_cache=True)
    assert len(raw_calls) == first_count, "Second call should use cache, not run subprocess"
    assert info2["title"] == "Test Title"


def test_moment_search_caching(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache_moments"
    monkeypatch.setattr(clip_fetch, "_MOMENTS_CACHE_DIR", cache_dir)

    gather_calls = []

    def mock_gather(*args, **kwargs):
        gather_calls.append(1)
        return [{"id": "vid1", "url": "https://www.youtube.com/watch?v=vid1", "title": "Vid 1"}]

    monkeypatch.setattr(clip_fetch, "gather_candidates", mock_gather)
    monkeypatch.setattr(clip_fetch, "video_metadata", lambda url, wd, **kw: ({"title": "Vid 1", "duration": 30}, []))

    # First call
    res1 = clip_fetch.moment_search("search query", limit=1, use_cache=True)
    assert len(gather_calls) == 1
    assert len(res1) == 1

    # Second call: uses cache
    res2 = clip_fetch.moment_search("search query", limit=1, use_cache=True)
    assert len(gather_calls) == 1, "Should reuse cached shortlist"
    assert res2[0]["id"] == "vid1"


# ─── the picking page + the pick job (no network, no yt-dlp, no real TTS) ────────────────────────

import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stages.stage_4 import tts_status

VID = "abcDEF12345"          # a real-shaped 11-char YouTube id


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(web_routes, "WAIT_TTS_SECONDS", 3.0)
    web_routes._PICKS.clear()
    web_routes.clear_moment_picked_listeners()
    proj = tmp_path / "qa"
    (proj / "review").mkdir(parents=True)
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 1, "text": "Hook line.", "is_intro": True, "visual_beats": ["Hook line."]},
        {"scene_id": 2, "text": "Peter fell, then he rose again.",
         "visual_beats": ["Peter fell,", "then he rose again."]}]}))
    bumps = []
    from stages.stage_4 import background_tts
    monkeypatch.setattr(background_tts, "bump_priority_beat", lambda p, b: bumps.append((p, b)))

    app = FastAPI()
    app.include_router(web_routes.router)

    class W:
        root = proj
        client = TestClient(app)
        bumped = bumps
        fetches: list = []
        previews: list = []

        def status(self, durations, completed=True):
            tts_status.write_status(proj, {"completed": completed, "beat_durations": durations})

        def fake_download(self, *, footage_left=60.0):
            """Mock fetch_clip_section/render_clip_preview. Returns the dict of what they were asked."""
            calls = {"fetch": [], "preview": []}

            def fetch(url, out_dir, *, start, beat_duration, **kw):
                calls["fetch"].append({"url": url, "start": start, "beat_duration": beat_duration})
                f = Path(out_dir) / f"{VID}_{start:.1f}_{beat_duration:.1f}.mp4"
                f.write_bytes(b"section")
                return f

            def preview(clip_path, start, beat_duration, out_path, **kw):
                calls["preview"].append({"clip": Path(clip_path).name, "start": start, "beat_duration": beat_duration})
                if beat_duration > footage_left:
                    raise clips.ClipTooShort(f"shortfall exceeds CLIP_MAX_HOLD ({footage_left:.1f}s of footage)")
                Path(out_path).write_bytes(b"preview")
                return out_path

            monkeypatch.setattr(clip_fetch, "fetch_clip_section", fetch)
            monkeypatch.setattr(clips, "render_clip_preview", preview)
            return calls
    return W()


def _pick(w, beat="2:0", video=VID, start=42.0, sync=True):
    return w.client.post(f"/api/pick_moment{'?sync=true' if sync else ''}",
                         json={"project": "qa", "beat": beat, "video_id": video, "start": start})


def _manifest(w):
    p = w.root / "review" / "clips" / "clips.json"
    return json.loads(p.read_text())["clips"] if p.exists() else []


def test_a_pick_uses_the_real_beat_duration_not_a_default(web):
    """B3: the section is downloaded for the beat's EXACT duration from the TTS status — never a 3.0 default."""
    web.status({"2:0": 2.4, "2:1": 4.15})
    calls = web.fake_download()
    res = _pick(web, "2:1")
    assert res.status_code == 200 and res.json()["status"] == "ok"
    assert calls["fetch"] == [{"url": f"https://www.youtube.com/watch?v={VID}", "start": 42.0, "beat_duration": 4.15}]
    assert calls["preview"] == [{"clip": f"{VID}_42.0_4.2.mp4", "start": 0.0, "beat_duration": 4.15}]
    (entry,) = _manifest(web)
    assert entry["beat"] == "2:1" and entry["start"] == 0.0 and entry["source_start"] == 42.0
    assert entry["file"] == f"review/clips/{VID}_42.0_4.2.mp4" and (web.root / entry["file"]).is_file()
    assert entry["preview_file"] == "review/clips/preview_2_1.mp4"
    assert "end" not in entry, "open-ended: Stage 5's fit may extend into the footage margin"
    assert entry["beat_duration"] == 4.15


def test_nothing_reaches_the_manifest_before_the_file_exists(web, monkeypatch):
    web.status({"2:0": 2.0})
    gate, started = threading.Event(), threading.Event()
    seen = {}

    def slow_fetch(url, out_dir, *, start, beat_duration, **kw):
        started.set()
        seen["manifest_during_download"] = _manifest(web)
        gate.wait(5)
        f = Path(out_dir) / "sec.mp4"
        f.write_bytes(b"x")
        return f

    monkeypatch.setattr(clip_fetch, "fetch_clip_section", slow_fetch)
    monkeypatch.setattr(clips, "render_clip_preview", lambda c, start, beat_duration, out_path, **k: Path(out_path).write_bytes(b"p") or out_path)
    res = _pick(web, sync=False)
    assert res.json()["status"] == "accepted"                       # answered at once, the job runs in the background
    assert started.wait(5)
    assert seen["manifest_during_download"] == [], "no provisional entry pointing at a file that is not there"
    assert web.client.get("/api/pick_status?project=qa&beat=2:0").json()["state"] == "downloading"
    gate.set()
    for _ in range(100):
        if web.client.get("/api/pick_status?project=qa&beat=2:0").json()["state"] == "ready":
            break
        threading.Event().wait(0.05)
    assert [c["beat"] for c in _manifest(web)] == ["2:0"]


def test_a_pick_waits_for_the_tts_then_uses_the_duration_it_computes(web):
    calls = web.fake_download()
    timer = threading.Timer(0.5, lambda: web.status({"2:0": 3.33}, completed=False))
    timer.start()
    res = _pick(web)                                                 # sync: the job waits for the duration
    timer.join()
    assert res.json()["status"] == "ok"
    assert calls["fetch"][0]["beat_duration"] == 3.33
    assert web.bumped == [("qa", "2:0")], "a pick for a beat the TTS has not reached bumps it to the front"


def test_a_pick_gives_up_cleanly_when_the_tts_never_reaches_the_beat(web, monkeypatch):
    monkeypatch.setattr(web_routes, "WAIT_TTS_SECONDS", 1.2)
    calls = web.fake_download()
    res = _pick(web)
    assert res.json()["status"] == "error"
    st = web.client.get("/api/pick_status?project=qa&beat=2:0").json()
    assert st["state"] == "error" and "TTS" in st["message"]
    assert calls["fetch"] == [] and _manifest(web) == []


def test_a_clip_too_short_for_its_beat_is_refused_with_the_reason(web):
    web.status({"2:0": 5.0})
    web.fake_download(footage_left=1.0)                              # only 1s of footage after the picked moment
    res = _pick(web)
    assert res.json()["status"] == "error"
    st = web.client.get("/api/pick_status?project=qa&beat=2:0").json()
    assert st["state"] == "error" and "mốc sớm hơn" in st["message"] and "CLIP_MAX_HOLD" in st["message"]
    assert _manifest(web) == []


def test_a_repick_replaces_the_beats_entry_and_keeps_the_others(web):
    web.status({"2:0": 2.0, "2:1": 3.0})
    web.fake_download()
    _pick(web, "2:0", start=10.0)
    _pick(web, "2:1", start=20.0)
    _pick(web, "2:0", start=99.0)
    by_beat = {c["beat"]: c for c in _manifest(web)}
    assert set(by_beat) == {"2:0", "2:1"} and len(_manifest(web)) == 2
    assert by_beat["2:0"]["source_start"] == 99.0 and by_beat["2:1"]["source_start"] == 20.0


def test_the_pick_event_carries_the_project_so_the_review_screen_can_react(web):
    web.status({"2:0": 2.0})
    web.fake_download()
    got = []
    web_routes.add_moment_picked_listener(got.append)
    _pick(web)
    assert got and got[-1]["project"] == "qa" and got[-1]["beat"] == "2:0" and got[-1]["state"] == "ready"
    got.clear()
    web.fake_download(footage_left=0.5)
    web.status({"2:0": 9.0})
    _pick(web)
    assert got[-1]["state"] == "error" and got[-1]["project"] == "qa"


def test_pick_input_is_validated_before_it_reaches_a_path_or_yt_dlp(web):
    web.status({"2:0": 2.0})
    calls = web.fake_download()
    for bad in [dict(video_id="../../etc/passwd"), dict(video_id="short"), dict(video_id="a" * 12),
                dict(beat="../x"), dict(beat="a/b"), dict(start=-1.0), dict(start=1e12),
                dict(start=float("inf")), dict(project="../qa"), dict(project="nope")]:
        body = {"project": "qa", "beat": "2:0", "video_id": VID, "start": 1.0, **bad}
        try:
            r = web.client.post("/api/pick_moment?sync=true", json=body)
        except ValueError:      # json can't carry inf: the client refuses before the server does
            continue
        assert r.status_code in (400, 404, 422), (bad, r.status_code)
    assert calls["fetch"] == []
    # a client-supplied url is not even a field: the download URL is rebuilt from the id
    r = web.client.post("/api/pick_moment?sync=true", json={"project": "qa", "beat": "2:0", "video_id": VID,
                                                            "start": 1.0, "url": "http://169.254.169.254/x"})
    assert r.status_code == 200 and calls["fetch"][0]["url"] == f"https://www.youtube.com/watch?v={VID}"


def test_clip_preview_is_served_and_cannot_leave_the_project(web):
    web.status({"2:0": 2.0})
    web.fake_download()
    _pick(web)
    r = web.client.get("/api/clip_preview?project=qa&beat=2:0")
    assert r.status_code == 200 and r.content == b"preview" and r.headers["content-type"] == "video/mp4"
    assert web.client.get("/api/clip_preview?project=qa&beat=1").status_code == 404
    (web.root / "secret.txt").write_text("secret")
    mp = web.root / "review" / "clips" / "clips.json"
    doc = json.loads(mp.read_text())
    doc["clips"][0]["preview_file"] = "../secret.txt"
    mp.write_text(json.dumps(doc))
    assert web.client.get("/api/clip_preview?project=qa&beat=2:0").status_code == 404


def test_status_endpoint_reads_the_one_status_file(web):
    web.status({"2:0": 2.5}, completed=False)
    j = web.client.get("/api/beat_tts_status?project=qa").json()
    assert j["beat_durations"] == {"2:0": 2.5} and j["completed"] is False
    assert web.client.get("/api/beat_tts_status?project=nope").json()["beat_durations"] == {}
    assert web.client.get("/api/beat_tts_status?project=../../etc").json()["beat_durations"] == {}


def test_bump_endpoint_reaches_the_registry_runner(web):
    r = web.client.post("/api/bump_beat_tts", json={"project": "qa", "beat": "2:1"})
    assert r.status_code == 200 and web.bumped == [("qa", "2:1")]
    web.client.post("/api/bump_beat_tts", json={"project": "../qa", "beat": "2:1"})
    web.client.post("/api/bump_beat_tts", json={"project": "qa", "beat": "../2"})
    assert web.bumped == [("qa", "2:1")]


CANDS = [{"id": VID, "url": f"https://www.youtube.com/watch?v={VID}", "title": "Action Clip", "channel": "Chan",
          "duration": 50.0, "embeddable": True, "moments": [{"start": 12.0, "end": 15.0, "why": "chapter: Impact"}]},
         {"id": "zzzZZZ99999", "title": "Blocked", "channel": "C", "embeddable": False,
          "moments": [{"start": 3.0, "end": 5.0, "why": "subtitle: hit"}]},
         {"id": "errERR00000", "url": "https://www.youtube.com/watch?v=errERR00000", "error": "private video"}]


def test_moments_page_uses_a_real_player_and_the_beats_own_words(web, monkeypatch):
    queries = []
    loop_thread = {}

    def search(q, **kw):
        queries.append(q)
        loop_thread["t"] = threading.get_ident()
        return CANDS

    monkeypatch.setattr(clip_fetch, "moment_search", search)
    main_thread = threading.get_ident()
    html = web.client.get("/moments_review?project=qa&beat=2:1").text
    assert queries == ["then he rose again."], "default query = the review beat's own narration words (string beats)"
    assert "https://www.youtube.com/iframe_api" in html and "new YT.Player" in html
    assert "getCurrentTime" in html and "enablejsapi=1" in html
    assert "Dùng từ đây cho Beat 2:1" in html and "Xem trực tiếp trên YouTube tại mốc t" in html
    assert "chapter: Impact" in html                                 # the moment's "why" is the button label
    assert "Video này chặn nhúng" in html and 'id="start_zzzZZZ99999"' in html   # number input ONLY when it can't embed
    assert 'id="start_abcDEF12345"' not in html and 'id="cur_abcDEF12345"' in html
    assert "errERR00000" not in html                                 # unavailable results are not offered
    assert "dQw4w9WgXcQ" not in html


def test_moments_page_search_runs_off_the_event_loop(web, monkeypatch):
    """M4: moment_search blocks on the network; it must not run on the thread that serves Flet too."""
    seen = {}
    loop_idents = set()

    async def probe():
        loop_idents.add(threading.get_ident())

    def search(q, **kw):
        seen["thread"] = threading.get_ident()
        return CANDS

    monkeypatch.setattr(clip_fetch, "moment_search", search)
    import asyncio

    async def go():
        await probe()
        await web_routes.moments_review(project="qa", beat="2:0", q="anything")
    asyncio.run(go())
    assert seen["thread"] not in loop_idents


def test_moments_page_shows_search_errors_without_a_fake_video(web, monkeypatch):
    def boom(q, **kw):
        raise RuntimeError("yt-dlp: HTTP 429")
    monkeypatch.setattr(clip_fetch, "moment_search", boom)
    html = web.client.get("/moments_review?project=qa&beat=2:0&q=whatever").text
    assert "HTTP 429" in html and "Không tìm thấy video nào phù hợp" in html
    assert "dQw4w9WgXcQ" not in html and "<iframe" not in html


def test_moments_page_escapes_everything_it_echoes(web, monkeypatch):
    monkeypatch.setattr(clip_fetch, "moment_search", lambda q, **kw: [])
    evil = '"><script>alert(1)</script>'
    html = web.client.get("/moments_review", params={"project": "qa", "beat": "2:0", "q": evil}).text
    assert evil not in html and "<script>alert(1)" not in html
    # project/beat reach the page's JS only as JSON literals
    assert 'const PROJECT = "qa";' in html and 'const BEAT = "2:0";' in html
    assert web.client.get("/moments_review", params={"project": "qa", "beat": "x</script>"}).status_code == 400


def test_moments_page_caches_the_shortlist_per_beat_and_query(web, monkeypatch):
    calls = []
    monkeypatch.setattr(clip_fetch, "moment_search", lambda q, **kw: calls.append(q) or CANDS)
    web.client.get("/moments_review?project=qa&beat=2:0&q=alpha")
    web.client.get("/moments_review?project=qa&beat=2:0&q=alpha")          # same beat+query: cached
    web.client.get("/moments_review?project=qa&beat=2:0&q=beta")           # same beat, another query: a new search
    assert calls == ["alpha", "beta"]


def test_listeners_are_keyed_not_stacked():
    web_routes.clear_moment_picked_listeners()
    a, b = [], []
    web_routes.add_moment_picked_listener(a.append, key="page1")
    web_routes.add_moment_picked_listener(b.append, key="page1")         # same key: replaces
    web_routes._broadcast_moment_picked({"x": 1})
    assert a == [] and b == [{"x": 1}] and web_routes.listener_count() == 1
    web_routes.remove_moment_picked_listener("page1")
    assert web_routes.listener_count() == 0
    cb = a.append
    web_routes.add_moment_picked_listener(cb)                            # a bare callback still works
    assert web_routes.listener_count() == 1
    web_routes.remove_moment_picked_listener(cb)
    assert web_routes.listener_count() == 0
