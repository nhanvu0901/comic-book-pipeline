"""ffmpeg filter options split on ':' and unescape '\\' — a raw Windows path broke the
chapter-card and outro-card drawtext filters on the server (exit -22)."""
from utils.ffmpeg_filter import filter_path


def test_windows_path_gets_forward_slashes_and_an_escaped_drive_colon():
    assert filter_path(r"D:\code\comic-book-pipeline\fonts\Anton-Regular.ttf") == \
        r"D\:/code/comic-book-pipeline/fonts/Anton-Regular.ttf"


def test_posix_path_is_unchanged_apart_from_quotes():
    assert filter_path("/Users/x/fonts/Anton.ttf") == "/Users/x/fonts/Anton.ttf"
    assert filter_path("/tmp/it's.txt") == r"/tmp/it\'s.txt"
