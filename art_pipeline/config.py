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

# Text-LLM backend is the single repo-wide switch `config.FREE_MODEL` (comic
# root config): False → Claude SDK only (raise on failure), True → OpenRouter
# chain. Art text calls `stages.stage_3._llm.call_with_chain` directly; there
# is no art-specific flag. VLM region proposal always stays on OpenRouter.

# ── Calm / sleep voice (2026-06-13, research/reports/2026-06-13-*) ───────────
# Soothing delivery for relaxation / "chill / easy to fall asleep" videos. All
# art-side: comic Stage 4 accepts emotion/speed/volume/post_atempo via
# synthesize_project kwargs (no comic edit), and audio_fx shapes frequency on
# the finished WAV (length-preserving → no A/V drift). post_atempo < 1.0 SLOWS
# the pace (comic default 1.1 = faster); it runs BEFORE scene_timings, so sync
# is preserved. Override any value via env.
ART_VOICE_EMOTION = os.getenv("ART_VOICE_EMOTION", "peaceful")  # Cartesia: peaceful/serene/calm
ART_VOICE_SPEED = float(os.getenv("ART_VOICE_SPEED", "0.9"))    # Cartesia speed (0.6–1.2 usable)
ART_VOICE_VOLUME = float(os.getenv("ART_VOICE_VOLUME", "0.85"))
ART_POST_ATEMPO = float(os.getenv("ART_POST_ATEMPO", "0.95"))   # <1 slows; pitch-preserving
# Voice identity for art renders (a calm Cartesia narrator, distinct from the
# comic default). Rupert - Caring Dad: warm, mature, reassuring → fits the
# "story behind the painting" tone. Override via env to A/B other voices.
ART_VOICE_ID = os.getenv("ART_VOICE_ID", "0ad65e7f-006c-47cf-bd31-52279d487913")  # Rupert - Caring Dad
ART_CALM_AUDIO = os.getenv("ART_CALM_AUDIO", "true").lower() in ("true", "1", "yes")
ART_CALM_LOWPASS_HZ = int(os.getenv("ART_CALM_LOWPASS_HZ", "4000"))
ART_CALM_BASS_GAIN_DB = float(os.getenv("ART_CALM_BASS_GAIN_DB", "5"))
ART_CALM_DEESS_GAIN_DB = float(os.getenv("ART_CALM_DEESS_GAIN_DB", "-6"))
ART_CALM_LUFS = float(os.getenv("ART_CALM_LUFS", "-18"))

# ── Region framing (2026-06-14) — fix "zoom too close, hard to see" ──────────
# VLM regions are often 4–8% of the canvas → cropping straight to them needs
# 2–4x upscale = too tight + blurry + no sense of WHERE the detail sits.
# Pad each region around its centre to keep CONTEXT and cap the upscale so the
# crop stays sharp. Crop-only → no effect on durations / A/V sync.
ART_REGION_CONTEXT_MARGIN = float(os.getenv("ART_REGION_CONTEXT_MARGIN", "0.3"))  # +30% each side
ART_REGION_MAX_UPSCALE = float(os.getenv("ART_REGION_MAX_UPSCALE", "1.4"))        # never upscale past this

# ── Visual polish (2026-06-14) — "make it more interesting" (art-side) ───────
# Shot-scale variety: alternate ESTABLISH (wide, lots of context) and DETAIL
# (tighter on the region) crops so the eye gets rhythm instead of all-wide.
# DETAIL upscale stays <=1.8 (Met sources are ~4K → still sharp).
ART_REGION_SCALE_VARIETY = os.getenv("ART_REGION_SCALE_VARIETY", "true").lower() in ("true", "1", "yes")
ART_REGION_ESTABLISH_MARGIN = float(os.getenv("ART_REGION_ESTABLISH_MARGIN", "0.5"))
ART_REGION_ESTABLISH_UPSCALE = float(os.getenv("ART_REGION_ESTABLISH_UPSCALE", "1.1"))
ART_REGION_DETAIL_MARGIN = float(os.getenv("ART_REGION_DETAIL_MARGIN", "0.12"))
ART_REGION_DETAIL_UPSCALE = float(os.getenv("ART_REGION_DETAIL_UPSCALE", "1.8"))
# Crossfade (dissolve) between shots — softer, more pro than hard cuts.
ART_CROSSFADE = os.getenv("ART_CROSSFADE", "true").lower() in ("true", "1", "yes")
ART_CROSSFADE_SEC = float(os.getenv("ART_CROSSFADE_SEC", "0.5"))
# Film look on the final encode: subtle vignette + warm tone.
ART_FILM_LOOK = os.getenv("ART_FILM_LOOK", "true").lower() in ("true", "1", "yes")

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


# ── Long-form mode (v3, spec 2026-06-12) ─────────────────────────────────────
# 8-12 min chaptered videos. Shorts constants above are untouched.
ART_LF_MODES = ("painting_story", "artist_journey")
ART_LF_CHAPTER_ROLES_5 = ("cold_open", "backfill", "evidence", "twist", "resolution")
ART_LF_CHAPTER_ROLES_4 = ("cold_open", "backfill_evidence", "twist", "resolution")
ART_LF_TARGET_WORDS_MIN = 1600      # measured 2026-06-12: words×0.36s ≈ duration; 0.85 band floor × 1600 = 1360 words ≈ 8:10 worst case
ART_LF_TARGET_WORDS_MAX = 1700      # 5 chapters x 340 ceiling; ~1700x0.36s ≈ 10:12 max
ART_LF_CHAPTER_WORDS_MIN = 150      # per-chapter target_words sanity band
ART_LF_CHAPTER_WORDS_MAX = 340      # measured pace ≈15 w/scene → 22-scene ceiling ≈330; 340 keeps 2-scene slack at floor ceil(340/17)=20 (e2e round 7)
ART_LF_CHAPTER_WORDS_BAND = (0.75, 1.5)  # per-chapter sanity only — 0.85 failed chapters 5 words short (e2e r8); the 8-min guarantee moved to ART_LF_TOTAL_WORDS_FLOOR
ART_LF_TOTAL_WORDS_FLOOR = 1360     # hard end-of-narrate gate: 1360 words x 0.36 s/word ≈ 8:10; one auto re-loop, then error
ART_LF_SCENES_PER_CHAPTER_MIN = 14
ART_LF_SCENES_PER_CHAPTER_MAX = 22
ART_LF_SCENE_MAX_WORDS = 32         # hard validator cap (prompt asks 8-22)
ART_LF_CHAPTER_GAP_S = 1.0          # stitched silence between chapters (micro-pause)
ART_LF_OUTPUT_W = 1920              # 16:9 landscape (runtime override of stage_5 shots)
ART_LF_OUTPUT_H = 1080
ART_LF_REHOOK_POSITIONS = (2, 3)    # 1-based chapter positions that must END with a re-hook
ART_LF_REGION_REUSE_WINDOW = 6      # same region may not appear twice within any 6 consecutive scenes (long-form)


# ── Anti-repetition (long-form, 2026-06-14) ──────────────────────────────────
# Long-form writes one chapter at a time; each chapter re-describes the same
# painting → near-verbatim repeats (Toledo: the brushstroke line appeared 3x).
# Layer 1: feed prior chapters' sentences into the prompt (truncated). Layer 2:
# embedding near-dup guard + surgical rewrite (dedupe.py).
ART_LF_SAID_LINES_MAX = int(os.getenv("ART_LF_SAID_LINES_MAX", "60"))
ART_LF_DEDUP_THRESHOLD = float(os.getenv("ART_LF_DEDUP_THRESHOLD", "0.86"))
ART_LF_DEDUP_MAX_PASSES = int(os.getenv("ART_LF_DEDUP_MAX_PASSES", "2"))

# ── Chapter title cards (long-form, 2026-06-14) ──────────────────────────────
# Full-screen card (fade-to-black) before chapters 2..N so viewers know which
# section they are in. The card sits inside the inter-chapter silence — to make
# room, that silence is widened from ART_LF_CHAPTER_GAP_S to CARD_SEC. Because
# longform_tts folds the silence into every later scene's offset, scene_timings
# stays consistent → zero A/V drift.
ART_LF_CHAPTER_CARDS = os.getenv("ART_LF_CHAPTER_CARDS", "true").lower() in ("true", "1", "yes")
ART_LF_CHAPTER_CARD_SEC = float(os.getenv("ART_LF_CHAPTER_CARD_SEC", "2.6"))
ART_CARD_BG = os.getenv("ART_CARD_BG", "#0d1b2a")        # midnight blue
ART_CARD_ACCENT = os.getenv("ART_CARD_ACCENT", "#c9a44a")  # muted gold
ART_CARD_FONT = os.getenv("ART_CARD_FONT", str(_REPO_ROOT / "fonts" / "Anton-Regular.ttf"))


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
