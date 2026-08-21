"""Pydantic models used by the Stage 1 research scout."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ScoutMode(str, Enum):
    QA = "qa"
    MICRO = "micro"


class SessionState(str, Enum):
    GENERAL_DRAFT = "general_draft"
    GENERAL_REVIEW = "general_review"
    SPECIFIC_REVIEW = "specific_review"
    PRODUCTION_GATES = "production_gates"
    COMPLETE = "complete"
    ARCHIVED = "archived"


class FeedbackNote(BaseModel):
    """A Master note threaded back into a later research prompt.

    ``state`` records the session state the feedback was given IN, not the
    state it produced — the workflow already transitions the session before
    the note is appended, so callers pass the literal review state rather
    than reading it off the (already-advanced) session.
    """

    state: str = ""
    text: str = ""


class ResearchSession(BaseModel):
    id: str
    mode: ScoutMode
    user_intent: str
    state: SessionState = SessionState.GENERAL_DRAFT
    revision: int = 1
    selected_general_candidate_id: str | None = None
    selected_specific_candidate_ids: list[str] = Field(default_factory=list)
    created_project: str | None = None
    # Absent from session.json files written before this field existed — the
    # default keeps those old sessions loading instead of failing validation.
    feedback_log: list[FeedbackNote] = Field(default_factory=list)


class Candidate(BaseModel):
    """A research candidate, kept permissive for versioned scout artifacts."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    series_issue_year: str = ""
    visible_event: str = ""
    evidence_urls: list[str] = Field(default_factory=list)
    reader_url: str | None = None
    title: str | None = None
    character: str | None = None
    angle: str | None = None
    summary: str | None = None
    flags: list[str] = Field(default_factory=list)


class EvidenceGate(BaseModel):
    """Typed result of the evidence review gate."""

    model_config = ConfigDict(extra="allow")

    verdict: str = "inconclusive"
    reason: str = ""
    evidence_urls: list[str] = Field(default_factory=list)
    reader_url: str | None = None
    flags: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    """A user or workflow decision recorded against scout candidates."""

    model_config = ConfigDict(extra="allow")

    approved: bool = False
    candidate_ids: list[str] = Field(default_factory=list)
    feedback: str = ""

