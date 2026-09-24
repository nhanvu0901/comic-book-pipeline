"""Directory removal that behaves on Windows, where the app actually runs.

shutil.rmtree alone fails there in two ordinary situations: a read-only file (the
attribute blocks unlink) and a file another process still holds open (a player, an
Explorer preview, a render, antivirus scanning a fresh file). The first is fixed by
clearing the bit; the second usually clears within a moment, so a short retry covers it.
When a file really stays locked, the caller gets a readable error naming it, instead of
a raw traceback — or, worse, a half-deleted folder reported as removed.
"""
from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

from stages.user_errors import UserFacingError


class FileInUseError(UserFacingError, PermissionError):
    """A file inside the folder stayed locked by another program."""


def _clear_readonly_and_retry(func, path, exc) -> None:
    if isinstance(exc, FileNotFoundError):
        return      # already gone on its own between listing and removal
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path | str, *, attempts: int = 4, delay: float = 0.5) -> None:
    """Delete `path` and everything under it, or raise FileInUseError naming what is stuck."""
    target = Path(path)
    last: PermissionError | None = None
    for attempt in range(attempts):
        if not target.exists():
            return
        try:
            shutil.rmtree(target, onexc=_clear_readonly_and_retry)
            return
        except PermissionError as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(delay)
    stuck = Path(getattr(last, "filename", "") or target).name
    raise FileInUseError(
        f"Could not delete {target.name}: '{stuck}' is still open in another program. "
        "Close whatever is using it (a video/audio player, an Explorer preview, a running "
        "render) and try again."
    ) from last
