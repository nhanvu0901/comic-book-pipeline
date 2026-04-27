"""
Font resolution for caption rendering.

Looks for Anton first (the locked-in MrBeast-style display font), then
falls back to common bold sans-serif system fonts on macOS.

Drop Anton-Regular.ttf into assets/fonts/ (or set CAPTION_FONT_PATH env)
to use the intended font.
"""
import os
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CANDIDATE_PATHS: list[Path | str] = [
    # 1. Explicit override via env
    os.getenv("CAPTION_FONT_PATH", ""),
    # 2. Project-local Anton
    _PROJECT_ROOT / "assets" / "fonts" / "Anton-Regular.ttf",
    _PROJECT_ROOT / "assets" / "fonts" / "Anton.ttf",
    # 3. Common macOS system bolds
    "/Library/Fonts/Anton-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/Library/Fonts/Arial Black.ttf",
    # 4. Last resort — any system font discovered by PIL
]


def resolve_font_path() -> str:
    """Return a path to a usable TTF/OTF/TTC font, or raise FileNotFoundError."""
    for p in _CANDIDATE_PATHS:
        if not p:
            continue
        pth = Path(p)
        if pth.exists() and pth.is_file():
            return str(pth)
    raise FileNotFoundError(
        "No caption font found. Drop Anton-Regular.ttf into assets/fonts/ "
        "or set CAPTION_FONT_PATH to a TTF path."
    )
