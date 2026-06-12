"""Sync wrappers + loaders the Art screens call via ui.bridge.run_blocking.

A5/A6 (comic Stage 4/5 reused) log via print() — we capture it with the same
print-redirect pattern ui/bridge.py uses, closing the log-callback gap noted
in the Plan-1 final review. ART_ROOT is a module attribute for test patching."""
import contextlib
import json
from pathlib import Path
from typing import Callable

from art_pipeline.config import ART_PROJECTS_ROOT
from ui.bridge import format_exception, run_blocking  # read-only reuse

ART_ROOT: Path = ART_PROJECTS_ROOT


@contextlib.contextmanager
def _print_to(log: Callable[[str], None]):
    import builtins
    original = builtins.print
    builtins.print = lambda *a, **k: log(" ".join(str(x) for x in a))
    try:
        yield
    finally:
        builtins.print = original


# ── Stage runners (called inside run_blocking worker threads) ───────────────

def _project_length(project: str) -> str:
    sel = ART_ROOT / project / "selection.json"
    if sel.exists():
        return json.loads(sel.read_text()).get("length", "short")
    return "short"


def run_fetch(project: str, ids: list[int], mode: str, theme: str,
              log: Callable[[str], None], *, length: str = "short") -> dict:
    from art_pipeline.fetch import fetch_artworks
    return fetch_artworks(project, ids, mode=mode, theme=theme, length=length, log=log)


def run_regions(project: str, force: bool, log: Callable[[str], None]) -> list[dict]:
    from art_pipeline.regions import process_artworks
    return process_artworks(project, force=force, log=log)


def run_ground(project: str, log: Callable[[str], None]) -> dict:
    from art_pipeline.grounding import build_art_context
    return build_art_context(project, log=log)


def run_narrate(project: str, mode: str | None, log: Callable[[str], None]) -> dict:
    if _project_length(project) == "longform":
        from art_pipeline.outline import write_outline
        from art_pipeline.narrate_longform import write_longform_narration
        # log=log explicitly: these fns default log=print bound at def-time,
        # so _print_to's builtins.print patch would NOT reach them.
        with _print_to(log):
            write_outline(project, mode, log=log)
            return write_longform_narration(project, log=log)
    from art_pipeline.narrate import write_narration
    return write_narration(project, mode, log=log)


def run_hunt(project: str, force: bool, log: Callable[[str], None]) -> dict:
    from art_pipeline.hunt import hunt_visuals
    return hunt_visuals(project, force=force, log=log)


def run_tts(project: str, log: Callable[[str], None]) -> dict:
    if _project_length(project) == "longform":
        from art_pipeline.longform_tts import synthesize_longform
        # log=log explicitly (def-time print bind); _print_to still wraps to
        # catch raw print() from the reused comic stage_4 inside synthesize.
        with _print_to(log):
            return synthesize_longform(project, log=log)
    from art_pipeline.tts import synthesize_art
    with _print_to(log):
        return synthesize_art(project, force=True).to_dict()


def run_video(project: str, log: Callable[[str], None]) -> str:
    from art_pipeline.video import assemble_art
    with _print_to(log):
        result = assemble_art(project, force=True)
    return str(result.final_path)


# ── Loaders ──────────────────────────────────────────────────────────────────

def _root(project: str) -> Path:
    return ART_ROOT / project


def load_art_pages(project: str) -> list[dict]:
    prep = _root(project) / "preprocessed"
    if not prep.exists():
        return []
    out: list[dict] = []
    for p in sorted(prep.glob("page_*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def load_art_context(project: str) -> dict | None:
    return _load_json(_root(project) / "art_context.json")


def load_art_narration(project: str) -> dict | None:
    return _load_json(_root(project) / "narration.json")


def load_manifest(project: str) -> list[dict]:
    return _load_json(_root(project) / "raw_art" / "manifest.json") or []


def load_youtube_description(project: str) -> str:
    p = _root(project) / "youtube_description.txt"
    return p.read_text() if p.exists() else ""


def save_narration_edits(project: str, narration: dict) -> None:
    """Persist scene-text edits; word_count is recomputed so Stage 4 pacing stays honest."""
    for s in narration.get("scenes") or []:
        s["word_count"] = len(str(s.get("text", "")).split())
    p = _root(project) / "narration.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(narration, indent=2, ensure_ascii=False))


def load_candidates() -> list[dict]:
    from art_pipeline.scout_csv import read_candidates
    return read_candidates()
