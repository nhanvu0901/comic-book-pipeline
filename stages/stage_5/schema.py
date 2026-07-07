"""Stage 5 output schemas."""
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Shot:
    shot_id: int
    scene_id: int
    duration_seconds: float
    panel_bbox: dict[str, int]
    source_image: str
    motion: str
    # Text-block bboxes (page coords) overlapping this panel — used by Stage 5 to
    # inpaint the comic's own speech-bubble text out before rendering.
    text_bboxes: list[dict] = field(default_factory=list)
    # The caption words actually spoken over THIS shot (the clause-slice text).
    # Stored so shots.json labels each shot correctly — shot count no longer
    # equals caption-chunk count, so indexing caption_chunks[shot_id] is wrong.
    caption_text: str = ""
    # Skip the horizontal mirror for this panel. Set when the panel contains
    # story-critical readable text baked into the art (a gravestone, a sign, a
    # nameplate) — mirroring would reverse the letters and break the reveal
    # (e.g. the 'PETER' gravestone payoff in Weapon VIII).
    no_mirror: bool = False
    # The cold-open / hook shot. Stage 5 renders it with a stronger, faster camera
    # push (energy in the first seconds) so the opening doesn't read as a slow hold.
    is_intro: bool = False
    # The NARRATION scene this shot belongs to, when scene_id is synthetic. The Q&A
    # locked builder gives every shot a unique scene_id (so the assembler dissolves
    # between them) — beat_id preserves the real story boundary so effects that should
    # only fire between beats (e.g. XFADE_ROTATE transition variety) don't fire between
    # two panels of the SAME answer item. None (recap) = scene_id is already the beat.
    beat_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AssemblyResult:
    final_path: str
    duration_seconds: float
    shot_count: int
    scene_count: int
    caption_path: str
    silent_video_path: str
    audio_mixed_path: str
    shots_dir: str
    bgm_used: str | None = None
    shots: list[Shot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
