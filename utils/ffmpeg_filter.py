"""Quoting a file path for use inside an ffmpeg filter argument (drawtext fontfile=,
textfile=, subtitles=...).

ffmpeg's option parser treats ':' as the option separator and '\\' as an escape, so a
Windows path like D:\\code\\fonts\\Anton.ttf breaks the filter ("Error parsing
filterchain", exit -22): the drive colon ends the option and the backslashes vanish.
Forward slashes are valid on every platform, and the drive colon must be escaped.
"""
from __future__ import annotations

from pathlib import Path
import subprocess


def filter_path(path: str | Path) -> str:
    """Path text safe to put between single quotes in a filter option."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def has_filter(ffmpeg: str, name: str) -> bool:
    """Whether this FFmpeg build exposes a named libavfilter filter."""
    try:
        result = subprocess.run([ffmpeg, "-filters"], capture_output=True,
                                text=True, check=False)
    except OSError:
        return False
    if result.returncode != 0:
        return False
    return any(len(parts := line.split()) > 1 and parts[1] == name
               for line in result.stdout.splitlines())
