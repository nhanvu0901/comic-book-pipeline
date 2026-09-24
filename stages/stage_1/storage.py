"""
Save comic_context and conversation log to the project folder.
"""
import json
import re
from .ui import print_success, Colors


def save_comic_context(comic_context: dict, project_name: str, get_project_dirs) -> str:
    """Save comic_context JSON to the project's folder."""
    dirs = get_project_dirs(project_name)
    path = str(dirs["root"] / "comic_context.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(comic_context, f, indent=2, ensure_ascii=False)

    print_success(f"Comic context saved: {path}")
    return path



def slugify(text: str) -> str:
    """Convert text to a filesystem-safe project name."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s]+", "_", slug)
    return slug[:60]


_WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul",
                         *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


def project_folder_name(text: str) -> str:
    """A project name typed by hand, made safe to be its folder's name.

    A name that already is one (word characters and hyphens) is kept whole: slugify also
    cuts at 60 characters, which would send an existing long slug to a new project.
    Anything else is slugified, since Windows refuses : * ? " < > | and a '/' nests
    folders. A Windows device name (CON, NUL, COM1...) cannot be a folder at all.
    Returns "" when nothing usable is left; the caller decides what to do then."""
    text = (text or "").strip()
    name = text if re.fullmatch(r"[\w-]+", text) else slugify(text).strip("_")
    return f"{name}_project" if name.lower() in _WINDOWS_DEVICE_NAMES else name
