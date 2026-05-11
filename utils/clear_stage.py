from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from config import PROJECTS_ROOT


def _project_dir(project_name: str) -> Path:
    project_dir = PROJECTS_ROOT / project_name
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project folder not found: {project_dir}")
    return project_dir


def _remove_tree(path: Path) -> Path | None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        return path
    return None


def _remove_file(path: Path) -> Path | None:
    if path.exists():
        path.unlink(missing_ok=True)
        return path
    return None


def clear_stage_2(project_name: str, *, raw: bool = False, preprocessed: bool = False) -> list[Path]:
    if not (raw or preprocessed):
        raise ValueError("clear_stage_2 requires raw=True and/or preprocessed=True")
    project_dir = _project_dir(project_name)
    removed: list[Path] = []
    if raw:
        result = _remove_tree(project_dir / "raw_comic")
        if result is not None:
            removed.append(result)
    if preprocessed:
        result = _remove_tree(project_dir / "preprocessed")
        if result is not None:
            removed.append(result)
    return removed


def clear_stage_3(project_name: str) -> list[Path]:
    project_dir = _project_dir(project_name)
    removed: list[Path] = []
    result = _remove_file(project_dir / "narration.json")
    if result is not None:
        removed.append(result)
    return removed


def clear_stage_4(project_name: str, *, alignment_only: bool = False) -> list[Path]:
    project_dir = _project_dir(project_name)
    if alignment_only:
        targets = ["scene_timings.json", "caption_chunks.json"]
    else:
        targets = ["audio.wav", "word_timestamps.json", "scene_timings.json", "caption_chunks.json"]
    removed: list[Path] = []
    for name in targets:
        result = _remove_file(project_dir / name)
        if result is not None:
            removed.append(result)
    return removed


def clear_stage_5(project_name: str) -> list[Path]:
    project_dir = _project_dir(project_name)
    removed: list[Path] = []
    result = _remove_tree(project_dir / "_stage5")
    if result is not None:
        removed.append(result)
    return removed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="utils.clear_stage")
    parser.add_argument("--project", required=True)
    parser.add_argument("--stage", required=True, type=int, choices=[2, 3, 4, 5])
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--preprocessed", action="store_true")
    parser.add_argument("--alignment-only", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if args.stage == 2:
        removed = clear_stage_2(args.project, raw=args.raw, preprocessed=args.preprocessed)
    elif args.stage == 3:
        removed = clear_stage_3(args.project)
    elif args.stage == 4:
        removed = clear_stage_4(args.project, alignment_only=args.alignment_only)
    else:
        removed = clear_stage_5(args.project)
    for path in removed:
        print(path)


if __name__ == "__main__":
    main()
