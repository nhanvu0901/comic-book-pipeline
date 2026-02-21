"""
Shared configuration for the Comic Video Pipeline.
All paths, constants, and settings in one place.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── API Keys ───────────────────────────────────────────────────────────────
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_BASE_URL = "https://api.z.ai/api/anthropic"
GLM_MODEL = "glm-4.7"

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

# ─── Google Drive Bridge (falls back to local ./projects) ───────────────────
_gdrive_env = os.getenv("GDRIVE_BASE", "")

if _gdrive_env:
    GDRIVE_BASE = Path(os.path.expanduser(_gdrive_env))
else:
    # No Google Drive configured — use local projects folder next to this file
    GDRIVE_BASE = Path(__file__).parent / "projects"

# Create if doesn't exist
GDRIVE_BASE.mkdir(parents=True, exist_ok=True)

# ─── Video Output Settings ──────────────────────────────────────────────────
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
MAX_DURATION = 120  # seconds

# ─── Ken Burns Effect ───────────────────────────────────────────────────────
KB_ZOOM_RANGE = (1.0, 1.15)   # zoom from 100% to 115%
KB_PAN_SPEED = 0.02            # pan speed as fraction of image size

# ─── Audio / Music ──────────────────────────────────────────────────────────
BGM_VOLUME = 0.15              # background music volume (0.0 - 1.0)
NARRATION_VOLUME = 1.0         # narration volume
AUDIO_FADE_IN = 1.0            # BGM fade in duration (seconds)
AUDIO_FADE_OUT = 2.0           # BGM fade out duration (seconds)

# ─── Subtitles ──────────────────────────────────────────────────────────────
SUB_FONT_SIZE = 42
SUB_FONT_COLOR = "white"
SUB_STROKE_COLOR = "black"
SUB_STROKE_WIDTH = 2
SUB_POSITION = ("center", "bottom")
SUB_MARGIN_BOTTOM = 60         # pixels from bottom edge

# ─── Transition ─────────────────────────────────────────────────────────────
CROSSFADE_DURATION = 0.3       # seconds between scenes

# ─── Image Search ───────────────────────────────────────────────────────────
IMAGE_SEARCH_MAX_RESULTS = 12  # candidates per scene
IMAGE_OUTPUT_FORMAT = "jpg"
IMAGE_QUALITY = 95


def get_project_path(project_name: str) -> Path:
    """Get the Google Drive project folder path."""
    p = GDRIVE_BASE / project_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_project_dirs(project_name: str) -> dict:
    """Create and return all subdirectory paths for a project."""
    base = get_project_path(project_name)
    dirs = {
        "root": base,
        "images": base / "images",
        "audio": base / "audio",
        "output": base / "output",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs
