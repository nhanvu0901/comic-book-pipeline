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

# Creative writing chain — separate from LLM_MODELS, used only by Stage 3 phase C
# (write_scenes + retry_fix). Other phases keep LLM_MODELS.
#
# Selection criteria for creative narrative writing:
#   1. Creative-tuned / instruction-following (sentence variance, storyteller voice)
#   2. Low hallucination (won't invent characters/events not in panel data)
#   3. Reliable JSON output (some models leak chain-of-thought as text)
#
# Avoid: NVIDIA Nemotron Super (leaks CoT instead of JSON), DeepSeek V4 Flash
# (returns empty content under load), gpt-oss-120b (hallucinates), Owl Alpha
# (hidden identity, unpredictable).
_DEFAULT_CREATIVE_CHAIN = (
    "nousresearch/hermes-3-llama-3.1-405b,"   # Hermes line is creative-storytelling tuned
    "meta-llama/llama-3.3-70b-instruct,"      # Reliable instruction-follower, low halluc
    "qwen/qwen3-next-80b-a3b-instruct,"       # Modern alternative, accurate
    "google/gemini-2.5-flash-lite"            # Paid fallback, guaranteed working
)
CREATIVE_LLM_MODELS: list[str] = [
    m.strip() for m in os.getenv("CREATIVE_LLM_MODELS", _DEFAULT_CREATIVE_CHAIN).split(",")
    if m.strip()
]
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

# Multi-image-capable subset for batched (multi-page) VLM calls.
# Probe (scripts/probe_multi_image.py 2026-05) confirmed:
#   ✓ google/gemini-2.5-flash-lite      — fast (~6s), most accurate, ~$0.0002/page
#   ✓ google/gemma-4-31b-it:free        — works but free-tier queues 4+ HOURS under load
#   ✗ qwen/qwen2.5-vl-72b-instruct:free — endpoint 404 (pulled)
#   ✗ nvidia/nemotron-nano-12b-v2-vl:free — hallucinates image count on multi-image
# Order: paid-but-fast first, free fallback only when paid hits 429/transient errors.
_DEFAULT_VLM_BATCH_CHAIN = (
    "google/gemini-2.5-flash-lite,"
    "google/gemma-4-31b-it:free"
)
VLM_MODELS_BATCH: list[str] = [
    m.strip() for m in os.getenv("VLM_MODELS_BATCH", _DEFAULT_VLM_BATCH_CHAIN).split(",") if m.strip()
]
VLM_BATCH_SIZE = int(os.getenv("VLM_BATCH_SIZE", "3"))  # pages per VLM call

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

_DEFAULT_FANDOM_CHAIN = "marvel.fandom.com,dc.fandom.com,imagecomics.fandom.com"
FANDOM_DOMAINS: list[str] = [
    d.strip() for d in os.getenv("FANDOM_DOMAINS", _DEFAULT_FANDOM_CHAIN).split(",") if d.strip()
]

# ─── TTS (Cartesia) ─────────────────────────────────────────────────────────
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
CARTESIA_MODEL = os.getenv("CARTESIA_MODEL", "sonic-3-2026-01-12")
CARTESIA_API_VERSION = os.getenv("CARTESIA_API_VERSION", "2026-03-01")
# Kyle — Cartesia "Emotive" preset voice; deep male storyteller, responds well to emotion tags.
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "c961b81c-a935-4c17-bfb3-ba2239de8c2f")

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
