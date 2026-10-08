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


@pytest.mark.anyio
async def test_moments_review_html_has_youtube_links(monkeypatch):
    mock_candidates = [
        {
            "id": "abc123",
            "url": "https://www.youtube.com/watch?v=abc123",
            "title": "Action Clip",
            "channel": "Channel",
            "duration": 50.0,
            "embeddable": True,
            "moments": [{"start": 12.0, "end": 15.0, "label": "Impact"}],
        }
    ]
    monkeypatch.setattr(clip_fetch, "moment_search", lambda q, **kw: mock_candidates)

    html = await web_routes.moments_review(project="", beat="1:1", q="Test query")
    assert "Xem trên YouTube" in html
    assert "Dùng từ đây cho Beat 1:1" in html
    assert "https://www.youtube.com/iframe_api" in html
    assert "getCurrentTime" in html


@pytest.mark.anyio
async def test_moments_review_no_fake_rickroll_on_empty(monkeypatch):
    """MAJOR M5: When search returns empty or fails, no fake dQw4w9WgXcQ video is inserted."""
    monkeypatch.setattr(clip_fetch, "moment_search", lambda q, **kw: [])

    html = await web_routes.moments_review(project="", beat="1:1", q="Nothing matches this")
    assert "dQw4w9WgXcQ" not in html
    assert "Không tìm thấy video nào phù hợp" in html


def test_process_clip_pick_rebased_and_preview(tmp_path, monkeypatch):
    """
    BLOCKER B3: On pick, fetch_clip_section downloads the window,
    render_clip_preview creates vertical preview, and manifest has rebased start 0.0.
    """
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "test_proj"
    proj_dir.mkdir(parents=True)

    clips_dir = proj_dir / "review" / "clips"
    clips_dir.mkdir(parents=True)
    fake_section_file = clips_dir / "vid1_15.0_3.2.mp4"
    fake_section_file.write_text("fake video content")

    fetch_calls = []
    def mock_fetch_section(url, out_dir, start, beat_duration, **kwargs):
        fetch_calls.append({"url": url, "start": start, "beat_duration": beat_duration})
        return fake_section_file

    preview_calls = []
    def mock_render_preview(clip_path, start, beat_duration, out_path, **kwargs):
        preview_calls.append({"clip_path": clip_path, "start": start, "beat_duration": beat_duration, "out_path": out_path})
        out_path.write_text("fake preview")
        return out_path

    monkeypatch.setattr(clip_fetch, "fetch_clip_section", mock_fetch_section)
    monkeypatch.setattr(clips, "render_clip_preview", mock_render_preview)

    entry = web_routes.process_clip_pick(
        project_root=proj_dir,
        beat="1:1",
        video_id="vid1",
        start=15.0,
        beat_duration=3.2,
    )

    # 1. verify fetch_clip_section was called with original start
    assert len(fetch_calls) == 1
    assert fetch_calls[0]["start"] == 15.0
    assert fetch_calls[0]["beat_duration"] == 3.2

    # 2. verify render_clip_preview was called with rebased start 0.0
    assert len(preview_calls) == 1
    assert preview_calls[0]["start"] == 0.0
    assert preview_calls[0]["beat_duration"] == 3.2

    # 3. verify manifest entry rebased to start 0.0
    manifest_path = clips_dir / "clips.json"
    assert manifest_path.exists()
    m_data = json.loads(manifest_path.read_text("utf-8"))
    assert len(m_data["clips"]) == 1
    clip = m_data["clips"][0]
    assert clip["beat"] == "1:1"
    assert clip["id"] == "vid1"
    assert clip["start"] == 0.0  # rebased!
    assert clip["end"] == 3.2
    assert clip["source_start"] == 15.0
    assert clip["file"] == "review/clips/vid1_15.0_3.2.mp4"
    assert clip["preview_file"] == "review/clips/preview_1_1.mp4"
