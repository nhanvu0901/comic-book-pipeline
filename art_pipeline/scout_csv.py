"""art_candidates.csv — APPEND-ONLY (mirrors the comic_candidates.csv workflow).
Rejected candidates are reported in chat, never written here."""
import csv
from pathlib import Path

from .config import ART_CANDIDATES_CSV

COLUMNS = [
    "title", "artist", "year", "object_id", "department", "image_url",
    "wiki_grounding", "story_hook", "yt_coverage", "date_added", "status",
    "longform_angle",
]


def read_candidates(*, path: Path = ART_CANDIDATES_CSV) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_candidates(rows: list[dict], *, path: Path = ART_CANDIDATES_CSV) -> int:
    """Append rows whose object_id is not already present. Returns count appended."""
    existing = {r.get("object_id", "") for r in read_candidates(path=path)}
    fresh = [r for r in rows if str(r.get("object_id", "")) not in existing]
    if not fresh:
        return 0
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if is_new:
            w.writeheader()
        for r in fresh:
            w.writerow({c: str(r.get(c, "")) for c in COLUMNS})
    return len(fresh)
