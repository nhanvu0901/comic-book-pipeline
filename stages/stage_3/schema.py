"""
JSON schema for a narration script (Stage 3 output).

A narration is a list of scenes. Each scene is 1-2 sentences of narration
tagged with the (page, panel) it should visually map to in the final video.
The chunker can later split long scenes into caption chunks, but the scene
granularity drives both TTS pacing and the Ken Burns panel cuts in Stage 5.
"""
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Scene:
    scene_id: int
    text: str
    page_ref: int            # page_number from Stage 2 preprocessed JSON
    panel_ref: int           # panel index within that page (-1 = whole page)
    word_count: int = 0
    target_seconds: float = 0.0   # estimated narration duration


@dataclass
class Narration:
    mode: str                      # one of MODES_BY_KEY
    title: str                     # short title for the short video
    hook: str                      # the opening line of scene 1 (also used as thumbnail text)
    scenes: list[Scene] = field(default_factory=list)
    total_word_count: int = 0
    estimated_duration_seconds: float = 0.0
    words_per_second: float = 2.9  # our default pacing target
    source_project: str = ""
    llm_model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProposedMode:
    mode: str
    hook: str
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
