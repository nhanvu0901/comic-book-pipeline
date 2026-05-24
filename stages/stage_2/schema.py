"""
JSON schema for a single preprocessed comic page.

This is the contract consumed by Stage 3 (narration synthesis) and Stage 5
(video assembly). All coordinates are pixels, origin top-left.
"""
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class PanelInfo:
    index: int                      # reading order, 0-based
    bbox: dict                      # {"x": int, "y": int, "w": int, "h": int}
    description: str = ""           # one-sentence VLM description
    characters: list[str] = field(default_factory=list)
    dominant_emotion: str = ""
    # Pipeline v5 Phase 2 — Magi V3 character cluster info
    cluster_ids: list[int] = field(default_factory=list)
    # cluster_id → name mapping is per-project, not per-panel; resolved during
    # Stage 5 scoring via projects/<name>/cluster_to_name.json


@dataclass
class TextBlock:
    panel_index: int                # which panel this text belongs to (-1 if unassigned)
    text: str
    type: str = "speech"            # speech | narration | sfx | caption
    speaker: str | None = None      # None for narration/sfx/caption
    # Pipeline v5 Phase 2 — Magi V3 association
    speaker_cluster_id: int | None = None
    bbox: dict = field(default_factory=dict)  # text bbox from Magi {x,y,w,h}


@dataclass
class PreprocessedPage:
    page_number: int
    source_image: str               # absolute path
    image_dimensions: dict          # {"width": int, "height": int}
    is_story_page: bool             # True only for page_type=="story" (used by Stage 3 narration)
    page_type: str = "story"        # "cover" | "story" | "skip"
    panels: list[PanelInfo] = field(default_factory=list)
    text_blocks: list[TextBlock] = field(default_factory=list)
    page_summary: str = ""
    issue_label: str = ""           # e.g. "#1", "chapter 5"
    vlm_model: str = ""
    vlm_model_used: str = ""        # actual model that produced the result (after fallback chain)
    content_hash: str = ""          # sha256 prefix of the image bytes
    preprocessing_method: str = "magi+vlm"  # "magi+vlm" | "heuristic_skip"
    skip_reason: str = ""           # specific reason when page_type=="skip"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
