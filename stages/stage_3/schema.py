"""
JSON schema for a narration script (Stage 3 output).

A narration is a list of scenes. Each scene is one compound sentence of
narration tagged with the (page, panel) it should visually map to in the
final video. The chunker can later split long scenes into caption chunks,
but the scene granularity drives both TTS pacing and the Ken Burns panel
cuts in Stage 5.
"""
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Scene:
    scene_id: int
    text: str
    page_ref: int                  # page_number from Stage 2 preprocessed JSON
    panel_ref: int                 # panel index within that page (-1 = whole page)
    word_count: int = 0
    target_seconds: float = 0.0    # estimated narration duration
    connective: str | None = None  # required for scene_id >= 2 (But/However/As/...)
    beat_id: int = 0               # links back to Beat.id from Phase A


@dataclass
class Beat:
    id: int
    function: str                  # COLD_OPEN | SETUP | COMPLICATION | ESCALATION | MIDPOINT | CLIMAX | LANDING
    name: str
    page_refs: list[int] = field(default_factory=list)
    key_panels: list[dict] = field(default_factory=list)  # [{"page": int, "panel": int}]
    summary: str = ""
    characters_active: list[str] = field(default_factory=list)


@dataclass
class CharacterEntry:
    canonical_name: str
    epithets: list[str] = field(default_factory=list)
    pronouns: list[str] = field(default_factory=list)
    intro_line_hint: str = ""


@dataclass
class Glossary:
    characters: dict[str, CharacterEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"characters": {k: asdict(v) for k, v in self.characters.items()}}


@dataclass
class Narration:
    mode: str                      # one of MODES_BY_KEY
    title: str                     # short title for the short video
    hook: str                      # the opening line of scene 1 (also used as thumbnail text)
    scenes: list[Scene] = field(default_factory=list)
    total_word_count: int = 0
    estimated_duration_seconds: float = 0.0
    words_per_second: float = 3.4  # ComicsUnlocked-calibrated pacing target
    source_project: str = ""
    llm_model: str = ""
    beats: list[Beat] = field(default_factory=list)
    glossary: Glossary | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "mode": self.mode,
            "title": self.title,
            "hook": self.hook,
            "scenes": [asdict(s) for s in self.scenes],
            "total_word_count": self.total_word_count,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "words_per_second": self.words_per_second,
            "source_project": self.source_project,
            "llm_model": self.llm_model,
            "beats": [asdict(b) for b in self.beats],
            "glossary": self.glossary.to_dict() if self.glossary else None,
        }
        return d


@dataclass
class ProposedMode:
    mode: str
    hook: str
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
