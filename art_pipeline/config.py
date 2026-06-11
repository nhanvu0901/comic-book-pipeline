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
ART_MIN_SCENES = 6
ART_WORDS_PER_SEC = 2.88   # measured 1.1-atempo pace, same voice as comic


# ── A4.5 visuals — scene-related web images (spec 2026-06-11) ────────────────
# Whitelist is config so it can be tightened later (e.g. drop "by-sa").
VISUAL_LICENSE_WHITELIST = ("pd", "cc0", "by", "by-sa")
VISUAL_KEEP_THRESHOLD = 0.65   # measured (circus-sideshow): reveal scenes sim 0.73-0.96, context scenes 0.56-0.60 — MiniLM same-domain baseline is ~0.5, not ~0.2
VISUAL_MATCH_MIN = 0.45        # web image must score at least this vs scene text
VISUAL_MIN_SHORT_SIDE = 600    # px; smaller downloads are rejected
VISUAL_MAX_PER_VIDEO = 6       # cap so the painting stays the star
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"


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
