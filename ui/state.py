"""
App state: which project is loaded, which stage we're on, which stages have
been approved, which are dirty (need regeneration after a back-nav edit).

Persisted to projects/<slug>/state.json. Loaded on app launch; autosaved
after every stage transition.
"""
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from config import PROJECTS_ROOT


# Not a stage — the sentinel a screen passes to its `on_go` callback to ask the app to
# reopen the project picker. `on_go` is the ONE navigation callback already threaded to
# every screen, so widening it costs nothing; a separate callback would have to be added
# to three_col() and all eight builders to reach the same place.
PICKER_STAGE = 0

STAGE_NAMES = {
    1: "Identify Comic",
    2: "Download Comic",
    3: "Preprocess Pages",
    4: "Narration Script",
    5: "Review Beats",
    6: "TTS Audio",
    7: "Review & Edit",
    8: "Final Video",
}


@dataclass
class AppState:
    project_name: str = ""
    current_stage: int = 1
    approved: dict[str, bool] = field(default_factory=dict)  # str keys for JSON
    dirty: dict[str, bool] = field(default_factory=dict)

    # Stage 1
    last_prompt: str = ""
    pipeline_mode: str = "narrate_1_comic"
    # Research Scout sessions live independently of projects, so this identity
    # remains available while Stage 1 is still collecting evidence.
    scout_session_id: str = ""
    scout_mode: str = "qa"
    # Stage 3
    chosen_mode: str = ""
    chosen_hook: str = ""
    # Stage 4
    tts_voice_id: str = ""
    tts_model: str = ""

    def is_approved(self, stage: int) -> bool:
        return bool(self.approved.get(str(stage), False))

    def is_dirty(self, stage: int) -> bool:
        return bool(self.dirty.get(str(stage), False))

    def mark_approved(self, stage: int) -> None:
        self.approved[str(stage)] = True
        self.dirty[str(stage)] = False

    def mark_dirty(self, stage: int) -> None:
        self.dirty[str(stage)] = True
        # Cascade: all later stages are also dirty (output depends on this)
        for s in range(stage + 1, 9):
            if self.approved.get(str(s)):
                self.dirty[str(s)] = True

    def reset(self) -> None:
        self.approved = {}
        self.dirty = {}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def state_path(project_name: str) -> Path:
    return PROJECTS_ROOT / project_name / "state.json"


def load_state(project_name: str) -> AppState:
    p = state_path(project_name)
    if not p.exists():
        return AppState(project_name=project_name)
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return AppState(project_name=project_name)
    # tolerate unknown fields
    s = AppState(project_name=project_name)
    for k, v in data.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


def save_state(s: AppState) -> None:
    if not s.project_name:
        return
    from .bridge import write_json_atomic   # local: state.py must not import bridge at module load
    write_json_atomic(state_path(s.project_name), s.to_dict())


def list_projects() -> list[str]:
    """Scan PROJECTS_ROOT for project directories containing comic_context.json."""
    if not PROJECTS_ROOT.exists():
        return []
    out: list[str] = []
    for d in sorted(PROJECTS_ROOT.iterdir()):
        if d.is_dir() and (d / "comic_context.json").exists():
            out.append(d.name)
    return out
