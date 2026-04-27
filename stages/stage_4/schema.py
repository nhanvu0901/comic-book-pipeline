"""
Stage 4 output schemas.
"""
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class WordTiming:
    word: str
    start: float     # seconds from audio start
    end: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneTiming:
    scene_id: int
    text: str
    start: float
    end: float
    page_ref: int
    panel_ref: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaptionChunk:
    """A display-ready caption chunk: 1 sentence or 6-8 word phrase."""
    text: str
    start: float
    end: float
    scene_id: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TTSResult:
    audio_path: str
    audio_duration_seconds: float
    voice_id: str
    model: str
    speed: float
    word_timestamps: list[WordTiming] = field(default_factory=list)
    scene_timings: list[SceneTiming] = field(default_factory=list)
    caption_chunks: list[CaptionChunk] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
