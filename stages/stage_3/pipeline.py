"""
Stage 3 orchestrator: load inputs, propose modes, (CLI or UI picks one), write script.
"""
import json
from pathlib import Path

from config import GDRIVE_BASE, get_project_dirs
from .propose_modes import propose_modes as _propose_modes
from .write_script import write_script as _write_script
from .schema import Narration


def load_inputs(project_name: str) -> tuple[dict, list[dict]]:
    """Load comic_context.json and every preprocessed/*.json."""
    root = GDRIVE_BASE / project_name
    ctx_path = root / "comic_context.json"
    if not ctx_path.exists():
        raise FileNotFoundError(f"comic_context.json missing: {ctx_path}")

    ctx = json.loads(ctx_path.read_text())

    prep_dir = root / "preprocessed"
    if not prep_dir.exists():
        raise FileNotFoundError(f"preprocessed/ missing: {prep_dir}. Run Stage 2 first.")

    pages: list[dict] = []
    for p in sorted(prep_dir.glob("page_*.json")):
        try:
            pages.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    if not pages:
        raise RuntimeError(f"preprocessed/ has no parseable pages in {prep_dir}")

    return ctx, pages


def filter_story_pages(pages: list[dict]) -> list[dict]:
    """Keep only pages marked as story (skip covers, recaps, ads, preview pages)."""
    return [p for p in pages if p.get("is_story_page")]


def propose_modes(project_name: str, n: int = 3):
    ctx, pages = load_inputs(project_name)
    story = filter_story_pages(pages)
    return _propose_modes(ctx, story, n=n)


def write_script(project_name: str, mode: str, hook_hint: str = "") -> Narration:
    ctx, pages = load_inputs(project_name)
    story = filter_story_pages(pages)
    nar = _write_script(ctx, story, mode, hook_hint=hook_hint)
    nar.source_project = project_name
    return nar


def save_narration(narration: Narration, project_name: str) -> Path:
    root = get_project_dirs(project_name)["root"]
    path = root / "narration.json"
    path.write_text(json.dumps(narration.to_dict(), indent=2, ensure_ascii=False))
    return path
