"""
Shared configuration for the Comic Video Pipeline.
All paths, constants, and settings in one place.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── LLM (OpenRouter, OpenAI-compatible) ────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# Text LLM for Stage 1 agent / Stage 3 narration synthesis.
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-haiku-4-5")
# Vision LLM for Stage 2 page preprocessing. Default is a free MoE vision model on OpenRouter.
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemma-4-26b-a4b-it:free")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ─── TTS (Cartesia) ─────────────────────────────────────────────────────────
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
CARTESIA_MODEL = os.getenv("CARTESIA_MODEL", "sonic-2")
# "Comic Vocal" — cloned from /Users/nhanvu/Desktop/comic.wav (private to this org).
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "f7248031-b419-4004-b447-2e9bf32f6b5e")

# ─── Comic Scraper ──────────────────────────────────────────────────────────
ENABLE_COMIC_SCRAPER = os.getenv("ENABLE_COMIC_SCRAPER", "true").lower() in ("true", "1", "yes")
# headless=False opens a visible Chrome window — much more reliable against Cloudflare
COMIC_SCRAPER_HEADLESS = os.getenv("COMIC_SCRAPER_HEADLESS", "false").lower() in ("true", "1", "yes")

# ─── Project Storage (falls back to local ./projects) ───────────────────────
_gdrive_env = os.getenv("GDRIVE_BASE", "")

if _gdrive_env:
    GDRIVE_BASE = Path(os.path.expanduser(_gdrive_env))
else:
    GDRIVE_BASE = Path(__file__).parent / "projects"

GDRIVE_BASE.mkdir(parents=True, exist_ok=True)


def get_project_path(project_name: str) -> Path:
    """Return the project folder path, creating it if needed."""
    p = GDRIVE_BASE / project_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_project_dirs(project_name: str) -> dict:
    """Return base project folder. Sub-folders are created by stages as they need them."""
    base = get_project_path(project_name)
    return {"root": base}
