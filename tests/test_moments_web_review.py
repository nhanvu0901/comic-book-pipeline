import time
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from stages import clip_fetch
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
        # Record time and sleep briefly
        call_times.append(time.time())
        time.sleep(0.05)
        return {"title": "Title", "channel": "Ch", "duration": 120, "playable_in_embed": True}, []

    t0 = time.time()
    results = clip_fetch.moment_search("Iron Man fights Hulk", limit=8, metadata=mock_metadata, use_cache=False)
    total_time = time.time() - t0

    assert len(results) == 8
    # If sequential, 8 * 0.05s = 0.40s. With 8 workers in parallel, total time is around ~0.06-0.15s.
    assert total_time < 0.35, f"Expected 8 parallel workers to finish well under sequential time: {total_time:.3f}s"


def test_metadata_caching_by_video_id(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache_meta"
    monkeypatch.setattr(clip_fetch, "_META_CACHE_DIR", cache_dir)

    raw_calls = []
    fake_info = {"title": "Test Title", "duration": 60, "subtitles": {}}

    def fake_subprocess_run(cmd, *args, **kwargs):
        raw_calls.append(cmd)
        m = MagicMock()
        import json
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
async def test_moments_review_html_has_youtube_links():
    html = await web_routes.moments_review(project="demo_proj", beat="1:1", q="Test query")
    assert "Xem trên YouTube" in html
    assert "Dùng từ đây cho Beat 1:1" in html
