"""Crash-safe JSON writes.

`Path.write_text` opens with mode "w", which TRUNCATES the target immediately — so a
crash, a kill, or a full disk anywhere between the open and the flush leaves a 0-byte
file rather than a partial one. Demonstrated 2026-08-10 on a real locks.json:
40 locked beats / 2960 bytes -> 0 bytes -> JSONDecodeError on the next read.

That window matters here because the files written this way are not derived data:
review/locks.json holds every panel Master picked by hand (and is rewritten on EVERY
click), narration.json holds hand-edited script text, and the Stage 2 page cache is
what the whole pipeline reads back instead of re-running Magi.

Writing a sibling temp file and os.replace()-ing it over the target is atomic on POSIX:
a reader sees either the entire old file or the entire new one, never a stump.
"""
import json
import os
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, doc: Any, *, indent: int = 2) -> Path:
    """Serialise `doc` to `path` atomically. Returns `path`.

    The temp file is a sibling so the rename never crosses a filesystem boundary
    (os.replace is only atomic within one). fsync before the rename because the
    rename being atomic says nothing about the CONTENT having reached the disk —
    without it a power loss can leave the new name pointing at empty blocks.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=indent, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt mid-write is exactly the
        # case this module exists for, and it must not leave .tmp litter behind.
        tmp.unlink(missing_ok=True)
        raise
    return path
