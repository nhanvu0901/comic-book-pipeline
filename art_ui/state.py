"""Art app state — mirrors ui/state.py but persists under art_projects/.
ART_ROOT is a module attribute (not read from config at call time) so tests
can monkeypatch it."""
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from art_pipeline.config import ART_PROJECTS_ROOT

ART_ROOT: Path = ART_PROJECTS_ROOT

STAGE_NAMES = {
    1: "Select Artwork",
    2: "Fetch from Met",
    3: "Detect Regions",
    4: "Narration",
    5: "TTS Audio",
    6: "Final Video",
}
N_STAGES = 6


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "untitled-art"


@dataclass
class ArtAppState:
    project_name: str = ""
    current_stage: int = 1
    approved: dict[str, bool] = field(default_factory=dict)
    dirty: dict[str, bool] = field(default_factory=dict)
    object_ids: list[int] = field(default_factory=list)
    mode: str = "painting_deep_dive"
    theme: str = ""
    length: str = "short"

    def is_approved(self, stage: int) -> bool:
        return bool(self.approved.get(str(stage), False))

    def is_dirty(self, stage: int) -> bool:
        return bool(self.dirty.get(str(stage), False))

    def mark_approved(self, stage: int) -> None:
        self.approved[str(stage)] = True
        self.dirty[str(stage)] = False

    def mark_dirty(self, stage: int) -> None:
        self.dirty[str(stage)] = True
        for s in range(stage + 1, N_STAGES + 1):
            if self.approved.get(str(s)):
                self.dirty[str(s)] = True

    def reset(self) -> None:
        self.approved = {}
        self.dirty = {}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def state_path(project_name: str) -> Path:
    return ART_ROOT / project_name / "state.json"


def load_state(project_name: str) -> ArtAppState:
    p = state_path(project_name)
    if not p.exists():
        return ArtAppState(project_name=project_name)
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return ArtAppState(project_name=project_name)
    s = ArtAppState(project_name=project_name)
    for k, v in data.items():
        if hasattr(s, k):
            setattr(s, k, v)
    s.project_name = project_name
    return s


def save_state(s: ArtAppState) -> None:
    if not s.project_name:
        return
    p = state_path(s.project_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s.to_dict(), indent=2, ensure_ascii=False))


def list_art_projects() -> list[str]:
    if not ART_ROOT.exists():
        return []
    out: list[str] = []
    for d in sorted(ART_ROOT.iterdir()):
        if d.is_dir() and ((d / "state.json").exists() or (d / "selection.json").exists()):
            out.append(d.name)
    return out
