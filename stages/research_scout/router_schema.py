"""Type-safe Pydantic schemas for the Media Source Router."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

_ISSUE_RE = re.compile(r"^\s*#?\s*(\d{1,4})\s*$")
_YEAR_RE = re.compile(r"^\s*\(?\s*((?:18|19|20)\d{2})\s*\)?\s*$")


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
    comic_series: str | None = Field(
        default=None,
        description=(
            "Series (or event/mini-series) title of the COMIC this item originates from or "
            "adapts, if one exists — even when the search results only mention the screen work. "
            "Null only when the item has no comic counterpart at all."
        ),
    )
    comic_issue: int | None = Field(
        default=None,
        description="Issue number of that comic, only if you are sure of it. Null when unsure — never guess.",
    )
    comic_year: int | None = Field(
        default=None,
        description="Year the comic series/volume started publishing (4 digits), if known.",
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

    @field_validator("comic_series", mode="before")
    @classmethod
    def _blank_series_is_none(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("comic_issue", mode="before")
    @classmethod
    def _issue_from_text(cls, v: Any) -> int | None:
        # An unsure model answers "unknown"/"1-3"/"N/A" — that must read as "no issue
        # known" (None), not as a validation error that burns a retry, and never as #1.
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, int):
            return v if 0 <= v <= 9999 else None
        m = _ISSUE_RE.match(str(v))
        return int(m.group(1)) if m else None

    @field_validator("comic_year", mode="before")
    @classmethod
    def _year_from_text(cls, v: Any) -> int | None:
        if isinstance(v, bool) or v is None:
            return None
        m = _YEAR_RE.match(str(v))
        return int(m.group(1)) if m else None


class QuestionRouteResponse(BaseModel):
    question: str = Field(description="The original user question")
    items: list[RoutedItem] = Field(
        default_factory=list,
        description="List of routed answer items or story events for the question",
    )
