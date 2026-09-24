"""File and folder removal that behaves on Windows, where the app actually runs.

shutil.rmtree alone goes wrong there in two ordinary situations. A read-only file stops
it; clearing the bit fixes that. A file another program still holds open (a player, an
Explorer preview, a render, antivirus scanning something just written) stops it HALF
WAY, with everything listed before that file already gone. For a project that can mean
comic_context.json went first: the folder drops out of the picker while most of it is
still on disk, and nothing in the app can see or delete it any more.

So a folder is first moved into a hidden `.deleting` folder next to it. Windows refuses
that move while any file inside is open, which makes it an all-or-nothing check: if it
fails, nothing was touched and the caller gets a readable error naming the open file; if
it succeeds, the folder is already out of every listing (nothing scans inside
`.deleting`) and the real delete runs on the moved copy. Brief locks are retried either
way, and whatever a delete could not finish is swept by the next one.
"""
from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path
from uuid import uuid4

from stages.user_errors import UserFacingError

_ASIDE_DIR = ".deleting"
_CLOSE_IT = ("Close whatever is using it (a video or audio player, an Explorer preview, "
             "a render that is still running) and try again.")


class FileInUseError(UserFacingError, PermissionError):
    """A file stayed open in another program, so it could not be deleted."""


def remove_tree(path: Path | str, *, attempts: int = 4, delay: float = 0.5) -> None:
    """Delete `path` and everything under it, or raise FileInUseError naming what is open.
    When it raises, nothing under `path` was deleted."""
    target = Path(path)
    aside_root = target.parent / _ASIDE_DIR
    _sweep(aside_root)
    if not target.exists():
        return
    aside_root.mkdir(exist_ok=True)
    aside = aside_root / f"{target.name}-{uuid4().hex[:8]}"
    try:
        _with_retries(lambda: os.replace(target, aside), attempts, delay)
    except PermissionError as exc:
        _sweep(aside_root)
        open_file = next((p for p in target.rglob("*") if p.is_file() and _is_open(p)), None)
        if open_file is None:
            reason = (exc.strerror or str(exc)).rstrip(".") + "."
        else:
            reason = f"'{open_file.name}' is still open in another program. {_CLOSE_IT}"
        raise FileInUseError(f"Could not delete {target.name}: {reason} Nothing was deleted.") from exc
    try:
        _with_retries(lambda: shutil.rmtree(aside, onexc=_clear_readonly_and_retry),
                      attempts, delay)
    except OSError as exc:
        # The folder is already gone from its place; only a straggler is left inside
        # .deleting, and the next delete sweeps it.
        print(f"[fs_remove] {target.name}: left {exc.filename or aside} for the next sweep ({exc})")
    _sweep(aside_root)


def remove_file(path: Path | str, *, attempts: int = 4, delay: float = 0.5) -> None:
    """Delete one file (missing is fine), clearing read-only and retrying brief locks, or
    raise FileInUseError naming it."""
    target = Path(path)

    def _unlink() -> None:
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
        except PermissionError as exc:
            _clear_readonly_and_retry(os.unlink, target, exc)

    try:
        _with_retries(_unlink, attempts, delay)
    except PermissionError as exc:
        if _is_open(target):
            reason = f"it is still open in another program. {_CLOSE_IT}"
        else:
            reason = (exc.strerror or str(exc)).rstrip(".") + "."
        raise FileInUseError(f"Could not delete {target.name}: {reason}") from exc


def _with_retries(action, attempts: int, delay: float) -> None:
    for attempt in range(attempts):
        try:
            action()
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(delay)


def _clear_readonly_and_retry(func, path, exc) -> None:
    if isinstance(exc, FileNotFoundError):
        return      # already gone on its own between listing and removal
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _sweep(aside_root: Path) -> None:
    """Best effort: finish earlier deletes that left something behind, then drop the
    `.deleting` folder once it is empty."""
    if not aside_root.is_dir():
        return
    for leftover in aside_root.iterdir():
        try:
            shutil.rmtree(leftover, onexc=_clear_readonly_and_retry)
        except OSError:
            pass
    try:
        aside_root.rmdir()
    except OSError:
        pass


def _is_open(path: Path) -> bool:
    """Whether another program holds `path` open. Renaming a file onto itself changes
    nothing, but on Windows it fails for exactly the kind of open handle that blocks a
    delete or a move of its folder."""
    try:
        os.replace(path, path)
    except PermissionError:
        return True
    except OSError:
        return False
    return False
