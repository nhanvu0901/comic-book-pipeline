"""ffmpeg filter options split on ':' and unescape '\\' — a raw Windows path broke the
chapter-card and outro-card drawtext filters on the server (exit -22)."""
from utils.ffmpeg_filter import filter_path


def test_windows_path_gets_forward_slashes_and_an_escaped_drive_colon():
    assert filter_path(r"D:\code\comic-book-pipeline\fonts\Anton-Regular.ttf") == \
        r"D\:/code/comic-book-pipeline/fonts/Anton-Regular.ttf"


def test_posix_path_is_unchanged_apart_from_quotes():
    assert filter_path("/Users/x/fonts/Anton.ttf") == "/Users/x/fonts/Anton.ttf"
    assert filter_path("/tmp/it's.txt") == r"/tmp/it\'s.txt"


def test_has_filter_reads_ffmpeg_filter_column(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from utils.ffmpeg_filter import has_filter
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="Filters:\n T.. scale V->V Scale the input video\n"))
    assert has_filter("ffmpeg", "scale")
    assert not has_filter("ffmpeg", "drawtext")
