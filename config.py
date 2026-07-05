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
    CROSSOVER_SAGA = "crossover_saga"  # ≤5 sequential issues of one series → one Short

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

# Global LLM routing switch — the single repo-wide backend flag for comic + art
# text-LLM calls (everything that goes through stages.stage_3._llm.call_with_chain).
#   FREE_MODEL = False (default)  → Claude Agent SDK ONLY (CLAUDE_SDK_MODEL), for
#       every text phase unconditionally. There is NO OpenRouter fallback: if the
#       SDK is unavailable / rate-limited / returns empty / fails the validator,
#       call_with_chain RAISES so the problem surfaces instead of being masked
#       (unified policy, 2026-06-12). A long run can therefore hard-fail mid-way
#       if the SDK rate-limits — the escape hatch is FREE_MODEL=true. NOTE: under
#       this mode the per-phase chains below (CREATIVE/FIDELITY) and the `models=`
#       / `max_tokens` args are unused — every phase uses the one SDK model.
#   FREE_MODEL = True             → skip the SDK; use the OpenRouter chains
#       (LLM_MODELS / CREATIVE_LLM_MODELS / FIDELITY_LLM_MODELS) with multi-model
#       fallback as before.
# VLM (Stage 2 panel vision) is NOT affected by this — it always uses VLM_MODELS.
FREE_MODEL = os.getenv("FREE_MODEL", "false").lower() in ("true", "1", "yes")

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
# (hidden identity, unpredictable), Hermes 3 405B (verified 2026-05-24 to inject
# fabricated meme/online-personality references — "YuanfenOnline", "Venom szn",
# "It's morphin time" memes — completely unrelated to source comic).
_DEFAULT_CREATIVE_CHAIN = (
    "deepseek/deepseek-v4-flash:free,"        # Primary: NEW V4, 1M ctx — fits wiki+panels+few-shots
    "deepseek/deepseek-v4-flash,"             # Paid backup: $0.10/$0.20 per M, same model
    "qwen/qwen3-next-80b-a3b-instruct,"       # Fallback: prev primary, good variance
    "google/gemini-2.5-flash-lite"            # Last resort paid
)
CREATIVE_LLM_MODELS: list[str] = [
    m.strip() for m in os.getenv("CREATIVE_LLM_MODELS", _DEFAULT_CREATIVE_CHAIN).split(",")
    if m.strip()
]

# Fact-checker chain — Stage 3 Phase D (fidelity) + Phase E (wiki cross-check).
# Use reasoning/thinking models — they analyze claims more rigorously than
# instruction-only models, and are less pedantic about phrasing differences.
_DEFAULT_FIDELITY_CHAIN = (
    "arcee-ai/trinity-large-thinking:free,"        # Primary: reasoning, 262K ctx
    "nvidia/nemotron-3-super-120b-a12b:free,"      # Backup: 120B reasoning, 1M ctx
    "google/gemini-2.5-flash-lite"                  # Last resort paid
)
FIDELITY_LLM_MODELS: list[str] = [
    m.strip() for m in os.getenv("FIDELITY_LLM_MODELS", _DEFAULT_FIDELITY_CHAIN).split(",")
    if m.strip()
]
_DEFAULT_VLM_CHAIN = (
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free,"  # NEW Primary: 30B MoE, reasoning, 256K ctx, multimodal
    "google/gemma-4-31b-it:free,"                           # Backup: 31B, 262K ctx
    "google/gemma-3-27b-it,"                                # Paid backup: $0.08/$0.16 per M, 27B, stable
    "google/gemini-2.5-flash-lite"                          # Last resort paid
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
    "google/gemma-4-31b-it:free,"
    "google/gemma-4-26b-a4b-it:free"
)
VLM_MODELS_BATCH: list[str] = [
    m.strip() for m in os.getenv("VLM_MODELS_BATCH", _DEFAULT_VLM_BATCH_CHAIN).split(",") if m.strip()
]
VLM_BATCH_SIZE = int(os.getenv("VLM_BATCH_SIZE", "3"))  # pages per VLM call

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

_DEFAULT_FANDOM_CHAIN = (
    "marvel.fandom.com,dc.fandom.com,imagecomics.fandom.com,"
    "powerrangers.fandom.com,darkhorse.fandom.com,valiant.fandom.com,"
    "turtlepedia.fandom.com,starwars.fandom.com"
)
FANDOM_DOMAINS: list[str] = [
    d.strip() for d in os.getenv("FANDOM_DOMAINS", _DEFAULT_FANDOM_CHAIN).split(",") if d.strip()
]

# ─── SDK web-research plot fallback ──────────────────────────────────────────
# When fandom + wiki return no/weak plot, a web-enabled Claude SDK agent researches
# the issue's plot from a real source (see stages/stage_1/tools/gather_plot_sdk.py).
ENABLE_SDK_PLOT_FALLBACK = os.getenv("ENABLE_SDK_PLOT_FALLBACK", "true").lower() in ("true", "1", "yes")
# Trigger the fallback when the found plot is shorter than this (catches stubs like
# DCeased: A Good Day to Die ≈475 chars). The SDK result is adopted only if it's longer.
SDK_PLOT_FALLBACK_MIN_CHARS = int(os.getenv("SDK_PLOT_FALLBACK_MIN_CHARS", "600"))

# ─── TTS (Cartesia) ─────────────────────────────────────────────────────────
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
CARTESIA_MODEL = os.getenv("CARTESIA_MODEL", "sonic-3-2026-01-12")
CARTESIA_API_VERSION = os.getenv("CARTESIA_API_VERSION", "2026-03-01")
# Kyle — Cartesia "Emotive" preset voice; deep male storyteller, responds well to emotion tags.
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "c961b81c-a935-4c17-bfb3-ba2239de8c2f")

# ─── TTS provider selector ──────────────────────────────────────────────────
# Which engine Stage 4 uses: "cartesia" (default) | "resemble". Switch freely via
# the TTS_PROVIDER env var — no code change needed.
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "cartesia").strip().lower()

# ─── TTS (Resemble AI — Chatterbox) ─────────────────────────────────────────
RESEMBLE_API_KEY = os.getenv("RESEMBLE_API_KEY", "")
RESEMBLE_SYNTH_URL = os.getenv("RESEMBLE_SYNTH_URL", "https://f.cluster.resemble.ai/synthesize")
RESEMBLE_VOICE_UUID = os.getenv("RESEMBLE_VOICE_UUID", "28f1626c")  # Rupert — fallback when no map/SDK
# Voice catalog (name/uuid/vibe) the Claude SDK reads to auto-pick a narrator per story.
RESEMBLE_VOICE_MAP = os.getenv(
    "RESEMBLE_VOICE_MAP",
    str(Path(__file__).resolve().parent / "voice_samples" / "voice_map.json"),
)

# ─── Stage 3: loop-friendly ending ──────────────────────────────────────────
# Append a short forward-pointing "tease" after the outro closure so the ending
# invites a rewatch (loop signal) without dropping the complete-story payoff.
ENABLE_LOOP_TEASE = os.getenv("ENABLE_LOOP_TEASE", "true").lower() in ("true", "1", "yes")

# Stage 3 narration LOGIC CRITIC (story-editor in a loop with the writer). When on:
#   1. a beat-impact critic drops low-impact beats AFTER outlining (→ fewer, punchier
#      scenes) while preserving the cold-open, climax, landing and the cause→effect spine;
#   2. a clarity critic reviews the written draft for zero-context understandability
#      (missing glosses, logic jumps, a twist that doesn't land) and feeds targeted
#      fix directives back to the writer via the existing retry loop.
# It lets the writer prompt stay light — the critic drives fixes dynamically instead
# of baking heavy static rules into the writer. Faithfulness stays owned by the wiki
# cross-check. Both critics are soft (never raise; skip on any LLM failure).
ENABLE_LOGIC_CRITIC = os.getenv("ENABLE_LOGIC_CRITIC", "true").lower() in ("true", "1", "yes")
# Never let the beat-impact critic gut the story below this many beats.
LOGIC_CRITIC_MIN_BEATS = int(os.getenv("LOGIC_CRITIC_MIN_BEATS", "9"))

# Stage 3 LLM VISUAL-BEAT SPLIT (end of Stage 3). Splits each body scene's sentence into
# VERBATIM visual beats so Stage 5 can show a distinct panel per beat (panel changes at
# each new visual moment, not held static for the whole sentence). Replaces the old spaCy
# clause splitter. Uses a cheap/free OpenRouter model DIRECTLY (not the SDK — this is a
# trivial mechanical task; does NOT touch the global FREE_MODEL switch). Never raises:
# any failure / non-verbatim output falls back to the whole sentence = today's 1-panel
# behavior. Beats must be verbatim (concat == sentence word-for-word) so Stage 5's
# word-position bucketing of caption chunks stays aligned.
ENABLE_LLM_BEAT_SPLIT = os.getenv("ENABLE_LLM_BEAT_SPLIT", "true").lower() in ("true", "1", "yes")
BEAT_SPLIT_MODELS: list[str] = [
    m.strip() for m in os.getenv(
        "BEAT_SPLIT_MODELS",
        "google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it"
    ).split(",") if m.strip()
]

# Stage 3 STORY ARCHITECT (advisory pre-write analysis). When on, one LLM call builds a
# structured story map (structure/timeline/spine/characters+visual_available/omit/framing)
# that grounds the outliner + writer so framed/non-linear stories and plot characters with
# no panel (e.g. the Batman Who Laughs) are handled without manual fixes. Advisory only —
# never deletes beats; degrades to today's behavior when off or on any failure.
ENABLE_STORY_ARCHITECT = os.getenv("ENABLE_STORY_ARCHITECT", "true").lower() in ("true", "1", "yes")

# ─── Embeddings (Azure OpenAI, OpenAI-compatible) ───────────────────────────
# Panel↔narration semantic matching (Stage 3 grounding + Stage 5 panel pick).
# When AZURE_OPENAI_EMBEDDING_API_KEY + _ENDPOINT are set, stages/_embedding.py
# uses Azure text-embedding-3-large; otherwise it degrades to the local
# sentence-transformer (mxbai), and finally to None (callers handle gracefully).
AZURE_OPENAI_EMBEDDING_API_KEY = os.getenv("AZURE_OPENAI_EMBEDDING_API_KEY", "").strip().strip('"')
AZURE_OPENAI_EMBEDDING_ENDPOINT = os.getenv("AZURE_OPENAI_EMBEDDING_ENDPOINT", "").strip().strip('"')
AZURE_OPENAI_EMBEDDING_MODEL_NAME = os.getenv("AZURE_OPENAI_EMBEDDING_MODEL_NAME", "text-embedding-3-large").strip().strip('"')
AZURE_OPENAI_EMBEDDING_MODEL_API_VERSION = os.getenv("AZURE_OPENAI_EMBEDDING_MODEL_API_VERSION", "2023-05-15").strip().strip('"')
# A placeholder like "<your-...-here>" counts as unset.
# ─── Gemini embedding (preferred when set; used while Azure endpoint is blocked) ──
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"')
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")

# ─── Embedding backend switch (easy toggle Google ↔ local Qwen ↔ …) ──────────
# EMBED_BACKEND picks the embedding backend explicitly:
#   "auto"   (default) → Gemini (if GEMINI_API_KEY) → Azure → local mxbai
#   "google"/"gemini"  → force Gemini
#   "qwen"/"openai"    → an OpenAI-compatible /v1/embeddings server (EMBED_OPENAI_URL).
#                        Served by LM Studio (:1234) as model `text-embedding-qwen3-embedding-8b`
#                        — LM Studio DOES serve it correctly under the `text-embedding-` id
#                        (returns real 4096-dim qwen, NOT the 768-dim nomic fallback; verified
#                        2026-06-29). Earlier note that LM Studio routed qwen3→nomic applied
#                        only to the bare `qwen3-embedding-8b` id; the text-embedding- id works.
#   "azure"            → force Azure ·  "local" → force local mxbai
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "auto").strip().lower()
EMBED_OPENAI_URL = os.getenv("EMBED_OPENAI_URL", "http://127.0.0.1:1234/v1/embeddings").strip()
EMBED_OPENAI_MODEL = os.getenv("EMBED_OPENAI_MODEL", "text-embedding-qwen3-embedding-8b").strip()
EMBED_OPENAI_DIM = int(os.getenv("EMBED_OPENAI_DIM", "4096"))

# ─── Qdrant vector store (panel↔narration matching) ─────────────────────────
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:8069")

def _azure_embed_ready() -> bool:
    k, e = AZURE_OPENAI_EMBEDDING_API_KEY, AZURE_OPENAI_EMBEDDING_ENDPOINT
    return bool(k) and bool(e) and not k.startswith("<") and not e.startswith("<")

# ─── Stage 5: Video assembly ────────────────────────────────────────────────
BG_MUSIC_PATH = os.getenv("BG_MUSIC_PATH", "assets/bgm/default.mp3")

# Crossfade dissolve between SCENES. XFADE_TRANSITION="cut" (default) bypasses the
# xfade filter entirely (hard cut, plain concat) — competitor autopsy (1.4M-3.2M view
# Shorts) measured 0.4-0.8 hard cuts/s and near-zero dissolves; our old default dissolved
# EVERY scene boundary, which read as a slideshow. Set XFADE_TRANSITION to a real ffmpeg
# xfade name (e.g. "dissolve") to opt back into a dissolve at every boundary; XFADE_DURATION
# is then the dissolve length. XFADE_DURATION also doubles as the small edge-only dissolve
# length used by XFADE_SOFT_EDGES below.
XFADE_DURATION = float(os.getenv("XFADE_DURATION", "0.25"))
XFADE_TRANSITION = os.getenv("XFADE_TRANSITION", "dissolve")  # Master 2026-07-05: old pacing kept; "cut" = competitor mode

# In hard-cut mode, still dissolve the two OUTER edges (intro→scene1, last-story→outro
# card) for `XFADE_DURATION`s — a flat hard cut there read as an abrupt slap in review;
# every scene-to-scene cut in between stays a hard cut. No effect when XFADE_TRANSITION
# is not "cut".
XFADE_SOFT_EDGES = os.getenv("XFADE_SOFT_EDGES", "true").lower() in ("true", "1", "yes")

# ─── Stage 5: flash-accent cuts ─────────────────────────────────────────────
# A single white flash frame (1 frame @30fps) at a hard cut into an action-classified
# scene (reuses shots.py's _is_action_text impact-verb check) — competitor autopsy
# counted 9-10 single-frame flashes per video at strong beats. Capped per video so it
# stays an accent, not a strobe; prefers the LATEST qualifying cuts (fights cluster
# toward the climax). No-op outside hard-cut assembly (flashes need a real cut to land on).
FLASH_ACCENTS = os.getenv("FLASH_ACCENTS", "false").lower() in ("true", "1", "yes")
FLASH_ACCENTS_MAX = int(os.getenv("FLASH_ACCENTS_MAX", "3"))

# ─── Stage 5: caption entrance pop ──────────────────────────────────────────
# Each caption chunk pops in via an ASS \t scale animation (100%→108%→100% over
# ~120ms) the moment it first appears — competitor captions are themselves a motion
# source, not a static overlay. Off → plain karaoke-fill (no scale animation).
CAPTION_POP = os.getenv("CAPTION_POP", "false").lower() in ("true", "1", "yes")

# ─── Stage 5: panel mirror ───────────────────────────────────────────────────
# Read by stages/stage_5/shots.py (MIRROR_PANELS there). Default OFF: competitor
# autopsy caught OUR mirror flipping mid-video lettering backwards (house-of-m
# frames) — the dedup value of mirroring no longer outweighs the AI-slop risk.
MIRROR_PANELS = os.getenv("MIRROR_PANELS", "false").lower() in ("true", "1", "yes")

# ─── Stage 5: channel branding (Grimframe) ──────────────────────────────────
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "Grimframe")
CHANNEL_HANDLE = os.getenv("CHANNEL_HANDLE", "@grimframe")
_LOGO_RAW = os.getenv("CHANNEL_LOGO_PATH", "assets/branding/grimframe_logo.jpg")
CHANNEL_LOGO_PATH = _LOGO_RAW if os.path.isabs(_LOGO_RAW) else str(Path(__file__).parent / _LOGO_RAW)
ENABLE_CORNER_LOGO = os.getenv("ENABLE_CORNER_LOGO", "true").lower() in ("true", "1", "yes")
ENABLE_OUTRO_CARD = os.getenv("ENABLE_OUTRO_CARD", "true").lower() in ("true", "1", "yes")
OUTRO_CARD_SECONDS = float(os.getenv("OUTRO_CARD_SECONDS", "3.5"))

# ─── Stage 5: motion-comic dynamics ─────────────────────────────────────────
# Action/impact panels (detected from the spoken clause — punch/smash/blast/...)
# get a STRONGER, faster camera push so fights feel dynamic; calm/talky panels
# keep the subtle 1.05 Ken Burns. Off → uniform subtle motion (prior behavior).
MOTION_COMIC = os.getenv("MOTION_COMIC", "true").lower() in ("true", "1", "yes")

# ─── Stage 5: cold-open ─────────────────────────────────────────────────────
# Open the video on a striking STORY panel (a big/splash image) instead of the
# cover/title — the first frame must grab in <1s or the seed pool swipes away
# (measured swipe-away fix). Excludes the final pages so the ending isn't spoiled.
# Off → intro opens on the cover (prior behavior).
COLD_OPEN = os.getenv("COLD_OPEN", "true").lower() in ("true", "1", "yes")

# ─── Stage 5: persistent title banner ───────────────────────────────────────
# A small white-box catchy title (narration.banner_title, generated in Stage 3)
# burned at the top of EVERY narration frame so a scroller instantly gets the
# hook — the technique high-view comic Shorts use. Off → no banner.
ENABLE_TITLE_BANNER = os.getenv("ENABLE_TITLE_BANNER", "true").lower() in ("true", "1", "yes")
TITLE_BANNER_FONTSIZE = int(os.getenv("TITLE_BANNER_FONTSIZE", "40"))
# Show the banner only on hook (is_intro) shots, not pinned for the whole video —
# competitor autopsy flagged the always-on banner as wasted vertical space past
# the opening seconds. false = restore the old always-on behavior.
TITLE_BANNER_HOOK_ONLY = os.getenv("TITLE_BANNER_HOOK_ONLY", "false").lower() in ("true", "1", "yes")

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
