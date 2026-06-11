"""Art-pipeline configuration. Shares OpenRouter/VLM/SDK settings by importing
the root comic config read-only; everything art-specific lives here so comic
constants are never touched."""
import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

ART_PROJECTS_ROOT = _REPO_ROOT / "art_projects"
ART_PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
ART_CANDIDATES_CSV = _REPO_ROOT / "art_candidates.csv"

# ── The Met Open Access (REST JSON, no auth, no scraping) ───────────────────
MET_API_BASE = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_USER_AGENT = "art-pipeline/0.1 (personal research project)"
MET_MIN_IMAGE_SHORT_SIDE = 1200  # documented for the art-scout agent; not imported by code

# ── A3 region proposal ───────────────────────────────────────────────────────
REGION_MIN_COUNT = 3      # fewer survivors than this → grid fallback
REGION_MAX_COUNT = 8
REGION_MIN_AREA_PCT = 2.0
REGION_MAX_AREA_PCT = 90.0
REGION_IOU_DEDUP = 0.65

# ── A4a grounding ────────────────────────────────────────────────────────────
ART_GROUNDING_MIN_CHARS = int(os.getenv("ART_GROUNDING_MIN_CHARS", "600"))
ART_SDK_MIN_STORY_CHARS = 200  # SDK result below this (or no source_url) = rejected

# ── A4b narration — art keeps its OWN copies of word budgets (spec §6) ──────
ART_TARGET_WORDS_MIN = 165
ART_TARGET_WORDS_MAX = 270
ART_SCENE_MAX_WORDS = 28   # educational register runs slightly longer than comic's 24
ART_MIN_SCENES = 10
ART_WORDS_PER_SEC = 2.88   # measured 1.1-atempo pace, same voice as comic


# ── A4.5 visual hunt + A6 assembler (spec 2026-06-11 narration-driven visuals) ─
VISUAL_MIN_SHORT_SIDE = 600    # px; smaller downloads are rejected
ART_SHOT_SPLIT_SEC = 5.0       # scene ≥ this long → 2 shots (mirrors comic SCENE_SECOND_PANEL_MIN_DUR)
ART_MAX_STATIC_SEC = 4.0       # no shot may hold a static frame longer than this


@dataclass(frozen=True)
class ArtMode:
    key: str
    label: str
    description: str


ART_MODES: list[ArtMode] = [
    ArtMode(
        "painting_deep_dive", "Painting Deep Dive",
        "One artwork: hook → historical context → 3-5 region reveals (the zoom "
        "targets) → what it means → outro naming artwork and museum.",
    ),
    ArtMode(
        "themed_listicle", "Themed Listicle",
        "5-7 artworks around one theme; 1-2 scenes per work, each scene names "
        "the work and lands one verified fact.",
    ),
    ArtMode(
        "artist_journey", "Artist Journey",
        "3-6 works of one artist in chronological order; the biography told "
        "through the paintings.",
    ),
]
ART_MODES_BY_KEY = {m.key: m for m in ART_MODES}

_CONNECTIVES = (
    "But", "So", "However", "When", "After", "Then", "Eventually", "As",
    "Instead", "With", "Now", "Until", "Meanwhile", "Soon", "Yet", "Here",
)


def get_art_project_path(name: str) -> Path:
    p = ART_PROJECTS_ROOT / name
    p.mkdir(parents=True, exist_ok=True)
    return p
