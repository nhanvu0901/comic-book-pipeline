"""Open a file or folder with the platform's own handler.

art_ui is a desktop app (art_ui/__main__.py: ft.run(main), no web/LAN mode), so the
person clicking the button sits at the machine running it — unlike ui/, "open" only
exists on macOS, so on Windows a plain subprocess.run(["open", ...]) raised
FileNotFoundError and the button looked dead.

Popen, never run()/wait(): verified on the real Windows server that os.startfile blocks
forever when the app has no desktop session (started over SSH or as a service) — the
click handler must not wait for the child at all. For the same reason its exit code is
never inspected (explorer.exe returns 1 even when it worked), and since we can't know
the window actually appeared, the message says "Opening", not "Opened"."""
import subprocess
import sys
from pathlib import Path


def open_path(path: Path | str) -> str:
    """Best-effort open of `path` in the platform's file manager / default handler.
    Never raises — returns a short message for the caller's status line instead."""
    path = Path(path)
    if not path.exists():
        return f"{path.name} does not exist yet: {path}"

    if sys.platform == "win32":
        cmd = ["explorer", str(path)]
    elif sys.platform == "darwin":
        cmd = ["open", str(path)]
    else:
        cmd = ["xdg-open", str(path)]

    try:
        subprocess.Popen(cmd)
    except OSError as e:
        return f"Could not open {path.name}: {e}"
    return f"Opening {path.name}: {path}"
