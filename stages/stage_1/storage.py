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
