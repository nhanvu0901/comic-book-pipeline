"""
Shared configuration for the Comic Video Pipeline.
All paths, constants, and settings in one place.
"""
import os
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


# ─── Pipeline Modes ────────────────────────────────────────────────────────
class PipelineMode(str, Enum):
    NARRATE_1_COMIC = "narrate_1_comic"
    STORY_ARC = "story_arc"
    CHARACTER_FEAT = "character_feat"
    VERSUS = "versus"
    WHAT_IF = "what_if"
    ORIGIN_STORY = "origin_story"
    TOP_MOMENTS = "top_moments"

PIPELINE_MODE = PipelineMode(os.getenv("PIPELINE_MODE", "narrate_1_comic"))

# ─── Agent Behaviour ───────────────────────────────────────────────────────
MAX_PHASE_RETRIES = int(os.getenv("MAX_PHASE_RETRIES", "3"))

# ─── LLM (OpenRouter, OpenAI-compatible) ────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

_DEFAULT_LLM_CHAIN = (
    "minimax/minimax-m2.5:free,"
    "deepseek/deepseek-chat-v3.1:free,"
    "meta-llama/llama-3.3-70b-instruct:free,"
    "google/gemini-2.5-flash-lite"
)
LLM_MODELS: list[str] = [
    m.strip() for m in os.getenv("LLM_MODELS", _DEFAULT_LLM_CHAIN).split(",") if m.strip()
]
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", LLM_MODELS[0])
_DEFAULT_VLM_CHAIN = (
    "google/gemma-4-31b-it:free,"
    "qwen/qwen2.5-vl-72b-instruct:free,"
    "nvidia/nemotron-nano-12b-v2-vl:free,"
    "google/gemini-2.5-flash-lite"
)
VLM_MODELS: list[str] = [
    m.strip() for m in os.getenv("VLM_MODELS", _DEFAULT_VLM_CHAIN).split(",") if m.strip()
]
VLM_MODEL = os.getenv("VLM_MODEL", VLM_MODELS[0])

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

_DEFAULT_FANDOM_CHAIN = "marvel.fandom.com,dc.fandom.com,imagecomics.fandom.com"
FANDOM_DOMAINS: list[str] = [
    d.strip() for d in os.getenv("FANDOM_DOMAINS", _DEFAULT_FANDOM_CHAIN).split(",") if d.strip()
]

# ─── TTS (Cartesia) ─────────────────────────────────────────────────────────
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
CARTESIA_MODEL = os.getenv("CARTESIA_MODEL", "sonic-3-2026-01-12")
CARTESIA_API_VERSION = os.getenv("CARTESIA_API_VERSION", "2026-03-01")
# "Comic Vocal" — cloned from /Users/nhanvu/Desktop/comic.wav (private to this org).
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "f7248031-b419-4004-b447-2e9bf32f6b5e")

# ─── Stage 5: Video assembly ────────────────────────────────────────────────
BG_MUSIC_PATH = os.getenv("BG_MUSIC_PATH", "assets/bgm/default.mp3")
_FFMPEG_BIN_RAW = os.getenv("FFMPEG_BIN", "bin/ffmpeg")
FFMPEG_BIN = _FFMPEG_BIN_RAW if os.path.isabs(_FFMPEG_BIN_RAW) else str(Path(__file__).parent / _FFMPEG_BIN_RAW)

# ─── Comic Scraper ──────────────────────────────────────────────────────────
ENABLE_COMIC_SCRAPER = os.getenv("ENABLE_COMIC_SCRAPER", "true").lower() in ("true", "1", "yes")
# headless=False opens a visible Chrome window — much more reliable against Cloudflare
COMIC_SCRAPER_HEADLESS = os.getenv("COMIC_SCRAPER_HEADLESS", "false").lower() in ("true", "1", "yes")

# ─── Project Storage ────────────────────────────────────────────────────────
PROJECTS_ROOT = Path(__file__).parent / "projects"
PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)


def get_project_path(project_name: str) -> Path:
    """Return the project folder path, creating it if needed."""
    p = PROJECTS_ROOT / project_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_project_dirs(project_name: str) -> dict:
    """Return base project folder. Sub-folders are created by stages as they need them."""
    base = get_project_path(project_name)
    return {"root": base}
