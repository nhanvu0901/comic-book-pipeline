"""Type-safe Pydantic schemas for the Media Source Router."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class PrimaryMedium(str, Enum):
    COMIC = "comic"
    FILM = "film"
    TV_ANIMATION = "tv_animation"
    GAME = "game"
    MIXED = "mixed"


class VisualSource(str, Enum):
    COMIC = "comic"
    YOUTUBE = "youtube"
    BOTH = "both"


class RoutedItem(BaseModel):
    event: str = Field(description="The specific event or answer element being routed")
    primary_medium: PrimaryMedium = Field(
        description="Primary medium where this canon event originates or predominantly lives"
    )
    visual_source: VisualSource = Field(
        description="Recommended asset source: comic panels, youtube video clips, or both"
    )
    adaptation_title: str | None = Field(
        default=None,
        description="Title and year of screen adaptation or comic run, e.g. 'Avengers: Endgame (2019)' or 'Amazing Spider-Man #121'",
    )
    evidence_urls: list[str] = Field(
        default_factory=list,
        description="URLs from search results supporting this decision",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0"
    )
    reason: str = Field(
        description="Concise rationale explaining the medium determination based strictly on search signals"
    )


class QuestionRouteResponse(BaseModel):
    question: str = Field(description="The original user question")
    items: list[RoutedItem] = Field(
        default_factory=list,
        description="List of routed answer items or story events for the question",
    )
