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
SCOUT_EVIDENCE_MODEL = os.getenv("SCOUT_EVIDENCE_MODEL", "deepseek/deepseek-v4-flash")
# Cheap planner LLM that fills the Stage 1 ResearchPlan's 4 knobs (see
# stages/research_scout/planner.py + RESEARCH_PLANNER_DESIGN.md). Defaults to
# the evidence model — no separate key/model needed unless split later.
SCOUT_PLANNER_MODEL = os.getenv("SCOUT_PLANNER_MODEL", SCOUT_EVIDENCE_MODEL)
# You.com Research effort for the Stage 1 scout. A/B on 2026-08-21 (same question,
# same schema): deep returned the same item count at the same latency as standard,
# so standard stays the default — deep only costs more.
YOUCOM_RESEARCH_EFFORT = os.getenv("YOUCOM_RESEARCH_EFFORT", "standard")

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
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free,"  # 30B MoE, reasoning, 256K ctx, multimodal
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

# Master 2026-07-24: per-page VLM description extraction (OpenRouter, via
# stages/stage_2/vlm_extract.py) is DISABLED by default. Workflow now: panels are
# hand-picked in review and speech bubbles come from Magi's OCR + bbox, so the
# OpenRouter describe pass is dead cost. DISABLED via this knob, NOT deleted.
#   0 = Magi-only: pick tay + bubble từ Magi OCR/bbox. Panel description empty (later
#       synthesized from dialog/characters), page-sort candidates, no OpenRouter call.
#   1 = re-enable the VLM OpenRouter desc extraction (old behaviour, byte-identical).
VLM_EXTRACT = os.getenv("VLM_EXTRACT", "0").strip().lower() not in ("0", "false", "no", "")

# Master 2026-08-13: the VLM pass that NAMES Magi's character clusters is DISABLED via this
# knob, NOT deleted — same treatment as VLM_EXTRACT above. It is dead cost: reference-counted
# by hand, cluster_to_name.json is loaded in two places, threaded through six signatures in
# stage_5/shots.py, and lands in _match_panels(), which never reads the argument. Nothing maps
# a cluster id to a name anywhere else either — speaker_cluster_id appears only inside Stage 2,
# and Stage 3 never touches clusters. So the names reach no render decision, while the pass
# itself costs ~8 VLM calls per project and was measured naming ALL 8 clusters "Hulk" on
# hulk-smash-asteroid — a book whose dialogue is largely the Leader's.
# CLUSTER_NAMER=1 restores it byte-for-byte.
CLUSTER_NAMER = os.getenv("CLUSTER_NAMER", "0").strip().lower() not in ("0", "false", "no", "")

# ─── Stage 2 perf (2026-07-06) ──────────────────────────────────────────────
# Magi panel-detection is a LOCAL model (Florence-2, float32 on Mac MPS) run once
# per page — the biggest local-compute block of Stage 2. Its API already takes a
# LIST of images, so we detect several pages per forward pass (a pre-pass before
# the VLM-describe loop). Larger = fewer forward launches but more activations
# resident at once → keep modest on a 16GB Mac (OOM risk). 1 = old per-page path.
MAGI_BATCH_SIZE = int(os.getenv("MAGI_BATCH_SIZE", "3"))
# Independent VLM round-trips run concurrently (network-bound, no shared state):
# DESC_VERIFY within a describe-batch, and per-cluster naming at the end. 1 = serial.
VLM_VERIFY_WORKERS = int(os.getenv("VLM_VERIFY_WORKERS", "3"))
CLUSTER_NAME_WORKERS = int(os.getenv("CLUSTER_NAME_WORKERS", "6"))
# extract_page() (single-page front-matter/back-matter/issue-edge path) takes no
# prior_page/running_state — each call is independent, so a contiguous group of these
# pages runs concurrently too. 1 = old strictly-serial per-page loop.
VLM_PAGE_WORKERS = int(os.getenv("VLM_PAGE_WORKERS", "4"))


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
# Which engine Stage 4 uses: "resemble" (default — Resemble AI's Chatterbox, what we ship with)
# | "cartesia" (legacy, no longer used; kept so an old project can be re-rendered on its
# original voice). Switch via the TTS_PROVIDER env var — no code change needed.
# Default flipped cartesia→resemble 2026-07-29 (Master: "we dont use cartesia_tts.py anymore use
# chatterbox"). .env already set resemble, so nothing about the shipped behaviour changes — this
# only stops a fresh checkout from silently reaching for the engine we stopped using.
# Master 2026-08-01: LOCAL Chatterbox is the default engine for every mode. It clones the
# channel voice from CHATTERBOX_VOICE_WAV, so the narrator does not change — what changes is
# that we get a per-chunk emotion knob and stop paying per render. "resemble" (hosted) and
# "cartesia" still work by env override.
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "chatterbox").strip().lower()

# ─── Comic Vine (structured comic DB — issue/character cross-check) ──────────
# Free API key (comicvine.gamespot.com/api). Used to VERIFY a Q&A answer item's exact
# issue exists + names the right character before download. Empty → cross-check skipped.
COMIC_VINE_API_KEY = os.getenv("COMIC_VINE_API_KEY", "")

# ─── TTS (Resemble AI — Chatterbox) ─────────────────────────────────────────
RESEMBLE_API_KEY = os.getenv("RESEMBLE_API_KEY", "")
RESEMBLE_SYNTH_URL = os.getenv("RESEMBLE_SYNTH_URL", "https://f.cluster.resemble.ai/synthesize")
# THE CHANNEL VOICE. Arthur (warm, classic, emotive; measured F0 ≈121 Hz) — pinned by Master
# 2026-07-29 after we measured that voice auto-selection had shipped THREE different narrators
# across five videos (CarlBishop ≈95 Hz, Arthur ≈130 Hz, Rupert ≈88 Hz). A 88-vs-130 Hz gap is a
# different person to the ear, and a channel's voice is its strongest recognition asset — the
# reference channel Master picked runs one voice across its whole catalogue. Story-fit was worth
# far less than identity. See project_audio_inconsistency_diagnosed_2026-07-29.
RESEMBLE_VOICE_UUID = os.getenv("RESEMBLE_VOICE_UUID", "9de11312")

# ffmpeg atempo applied AFTER TTS (pitch-preserving). TWO numbers, because reading pace is a
# property of the FORMAT, not of the voice — Master judged both by ear on 2026-08-01:
#   Shorts   1.30 (~201 wpm) — 45 seconds, pace is what holds the viewer; 1.10 dragged.
#   Longform 1.10 (~183 wpm) — 19 minutes, pace is what tires them; 1.35 read like a race.
# One shared number cannot serve both, which is what the old single 1.35 got wrong in the
# other direction. Stage 3's words-per-second constants are MEASURED AT THESE PACES and must
# move with them, or the writer budgets words for a tempo the render no longer plays.
POST_ATEMPO = float(os.getenv("POST_ATEMPO", "1.30"))
POST_ATEMPO_LONGFORM = float(os.getenv("POST_ATEMPO_LONGFORM", "1.10"))
# Narration modes that take the longform pace. Mirrors review_gate.GATE_EXEMPT_MODES.
LONGFORM_TTS_MODES = ("panel_walk",)
# Per-story voice auto-selection (an SDK call that reads the narration and picks from the catalog
# below). OFF by default — it is what produced the three-narrator problem. Set to 1 only for a
# deliberate one-off; a normal run must use the pinned voice above.
RESEMBLE_AUTO_SELECT_VOICE = os.getenv("RESEMBLE_AUTO_SELECT_VOICE", "0").strip().lower() in (
    "1", "true", "yes")
# Voice catalog (name/uuid/vibe) the Claude SDK reads when auto-selection is explicitly enabled.
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

# Stage 3 TRANSPARENCY / CLARITY CRITIC (runs for ALL 3 modes: recap, micro, Q&A).
# Fires at the convergence point in pipeline.write_script AFTER the writer emits scenes
# and BEFORE narration.json is saved. Flags four clarity failures a first-time viewer
# trips on that no existing critic catches: (1) an undefined proper name (a stranger
# character dropped in with no role clause), (2) a subplot that dilutes the core point,
# (3) an overstuffed 3+-event run-on sentence, (4) an off-focus scene. Default behavior
# is FLAG + LOG only — it never rewrites or blocks (Master still reviews the panel sheet).
# Soft: never raises; skips on any LLM failure (offline/no-embed safe).
TRANSPARENCY_CRITIC = os.getenv("TRANSPARENCY_CRITIC", "true").lower() in ("true", "1", "yes")
# When on AND heavy flags remain (undefined char / subplot / off-target), re-run the
# writer ONCE and keep whichever draft has fewer flags. Default OFF so current single-pass
# behavior is unchanged.
TRANSPARENCY_RETRY = os.getenv("TRANSPARENCY_RETRY", "false").lower() in ("true", "1", "yes")

# Stage 3 GROUNDING CHECK (text <-> SHOWN panel, all 3 modes). SEPARATE from the
# transparency critic: transparency is text-vs-text prose clarity; grounding is
# text-vs-IMAGE. It runs in save_narration AFTER panel enrich (the only point each scene
# carries panel_description) and flags a narration line that ASSERTS a concrete, drawable
# event/place/action the panel it is shown over does not depict — e.g. a hook saying
# "died at a gas station" while every shown panel is the morgue. Abstract meaning/thesis
# lines ("the truth was worse than he imagined") need no panel and are never flagged.
# FLAG + LOG only; never rewrites/blocks. Soft: skips on any LLM failure / missing panel
# metadata (offline / Q&A-before-stage-5 safe). No auto-retry (feedback wiring is later).
GROUNDING_CHECK = os.getenv("GROUNDING_CHECK", "true").lower() in ("true", "1", "yes")

# Stage 3 COLD-VIEWER CRITIC (all 3 modes). SIBLING of the transparency critic, same
# convergence point (pipeline.write_script), same soft FLAG+LOG contract. Plays a viewer
# who NEVER read the comic and asks, per scene: do I know who these people are to each
# other, and do I understand WHY this action matters? Real miss it targets: the
# harley-quinn-25-joker-breakup micro beat the Joker bloody but never said he was Harley's
# ABUSIVE EX — a cold viewer had no idea why any of it landed. Flags relationship/why/context
# gaps ONLY when they cause genuine confusion (never deep/optional lore). Each flag carries a
# <=12-word suggested_clause the writer can weave in. Default ON; never raises (offline-safe).
COLD_VIEWER_CRITIC = os.getenv("COLD_VIEWER_CRITIC", "1").lower() in ("true", "1", "yes")
# When on AND cold-viewer flags remain, re-run the writer ONCE with the flags' suggested
# clauses as a fix block and keep whichever draft has fewer flags. Full-writer rewrite
# (verbatim visual_beats stay consistent — the writer re-emits them) rather than a surgical
# text-edit + re-split, which would risk desyncing visual_beats / per-fragment locks. Default
# OFF so the single-pass behavior is unchanged; flags still log so Master can weave the WHY
# in the review UI (exactly how harley scene S13 was hand-added).
COLD_VIEWER_RETRY = os.getenv("COLD_VIEWER_RETRY", "false").lower() in ("true", "1", "yes")

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
#   "qwen"/"openai"    → tiered chain (see EMBED_PRIMARY below): OpenRouter
#                        `qwen/qwen3-embedding-8b` API (cheap/fast/0 RAM, PRIMARY
#                        since 2026-07-17) → local LM Studio (:1234, model
#                        `text-embedding-qwen3-embedding-8b`, real 4096-dim qwen —
#                        verified 2026-06-29, NOT the 768-dim nomic fallback) →
#                        llama.cpp (:1235, manually-started last resort).
#   "azure"            → force Azure ·  "local" → force local mxbai
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "auto").strip().lower()
EMBED_OPENAI_URL = os.getenv("EMBED_OPENAI_URL", "http://127.0.0.1:1234/v1/embeddings").strip()
EMBED_OPENAI_MODEL = os.getenv("EMBED_OPENAI_MODEL", "text-embedding-qwen3-embedding-8b").strip()
EMBED_OPENAI_DIM = int(os.getenv("EMBED_OPENAI_DIM", "4096"))
# Which tier goes first within the "qwen"/"openai" backend: "openrouter" (default,
# cloud API — no local RAM/server needed) or "local" (LM Studio :1234 first, e.g.
# no OPENROUTER_API_KEY or a session that wants to keep calls offline). llama.cpp
# (:1235) is always the last-resort tier regardless of this knob.
EMBED_PRIMARY = os.getenv("EMBED_PRIMARY", "openrouter").strip().lower()
EMBED_OPENROUTER_MODEL = os.getenv("EMBED_OPENROUTER_MODEL", "qwen/qwen3-embedding-8b").strip()
EMBED_LLAMACPP_URL = os.getenv("EMBED_LLAMACPP_URL", "http://127.0.0.1:1235/v1/embeddings").strip()

# ─── Qdrant vector store (panel↔narration matching) ─────────────────────────
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:8069")

# ─── Panel TEXT-embed master switch (Master 2026-07-24) ──────────────────────
# The workflow now picks panels BY HAND in the Review Beats UI (hard gate, all modes),
# so the Qwen 4096-dim panel TEXT index + Qdrant text collection + cosine ranking no
# longer decide which panel renders. PANEL_TEXT_EMBED=0 (DEFAULT, OFF) turns that whole
# dead machinery off: Stage 2 skips building the panel text index (no embedding API /
# no `panels__<slug>` collection), review_gate.build_candidates lists ALL panels
# page-sorted (no vector query), and Stage 5 assigns unlocked scenes DETERMINISTICALLY
# (first panel of the beat's page_ref). Re-enable the old cosine pipeline with
# PANEL_TEXT_EMBED=1. NOTE: the SigLIP IMAGE index (panels_img__) is separate and stays
# on — it is local, cheap, and feeds custom-image argmax placement.
PANEL_TEXT_EMBED = os.getenv("PANEL_TEXT_EMBED", "0").strip().lower() not in ("0", "false", "no", "")


# ─── Stage 3 vector beat-grounding master switch (Master 2026-07-27) ─────────
# Same manual-first reasoning as PANEL_TEXT_EMBED above: Stage 3's embed pass only
# produces vector page_ref/panel_ref PINS for beat↔panel grounding, and hand-picked
# panels + the review-lock hard gate overwrite those pins before anything renders.
# So it is pure cost — and on 2026-07-27 it was worse than free: an OpenRouter embed
# call blocked a recap run for 31 minutes inside an SSL read (urlopen(timeout=) bounds
# each socket op, not the whole request, so a trickling server never trips it).
# DEFAULT ON (= skip embedding). STAGE3_NO_EMBED=0 restores the old vector grounding.
# Read through this helper, never as a module constant: stage_3/cli.py sets the env var
# AFTER config import when --no-embed is passed, so a constant would miss it.
def stage3_no_embed() -> bool:
    """True when Stage 3 should skip every embedding call (default)."""
    return os.getenv("STAGE3_NO_EMBED", "1").strip().lower() not in ("0", "false", "no", "")


def _azure_embed_ready() -> bool:
    k, e = AZURE_OPENAI_EMBEDDING_API_KEY, AZURE_OPENAI_EMBEDDING_ENDPOINT
    return bool(k) and bool(e) and not k.startswith("<") and not e.startswith("<")

# ─── Stage 5: Video assembly ────────────────────────────────────────────────
# Crossfade dissolve between SCENES. XFADE_TRANSITION="cut" (default) bypasses the
# xfade filter entirely (hard cut, plain concat) — competitor autopsy (1.4M-3.2M view
# Shorts) measured 0.4-0.8 hard cuts/s and near-zero dissolves; our old default dissolved
# EVERY scene boundary, which read as a slideshow. Set XFADE_TRANSITION to a real ffmpeg
# xfade name (e.g. "dissolve") to opt back into a dissolve at every boundary; XFADE_DURATION
# is then the dissolve length. XFADE_DURATION also doubles as the small edge-only dissolve
# length used by XFADE_SOFT_EDGES below.
XFADE_DURATION = float(os.getenv("XFADE_DURATION", "0.25"))
XFADE_TRANSITION = os.getenv("XFADE_TRANSITION", "dissolve")  # Master 2026-07-05: old pacing kept; "cut" = competitor mode

# Master 2026-07-06 "more animation between scenes": instead of the SAME dissolve at
# every scene boundary (monotone), ROTATE through a small curated set of ffmpeg xfade
# transitions — one per boundary — so scene changes feel varied/dynamic. Applies ONLY at
# scene-group boundaries (sub-shots WITHIN a scene stay hard-cut, unchanged), i.e. the
# "large" boundaries. Only active in the dissolve path (XFADE_TRANSITION != "cut"); the
# outer intro/outro edges in cut mode keep a plain dissolve. Empty string = no rotation
# (every boundary uses XFADE_TRANSITION verbatim = pre-2026-07-06 behavior). Any valid
# ffmpeg xfade name works (slideup/slidedown, smoothleft, wipeleft, circleopen, radial…).
XFADE_ROTATE = os.getenv("XFADE_ROTATE", "dissolve,slideleft,slideright")

# In hard-cut mode, still dissolve the two OUTER edges (intro→scene1, last-story→outro
# card) for `XFADE_DURATION`s — a flat hard cut there read as an abrupt slap in review;
# every scene-to-scene cut in between stays a hard cut. No effect when XFADE_TRANSITION
# is not "cut".
XFADE_SOFT_EDGES = os.getenv("XFADE_SOFT_EDGES", "true").lower() in ("true", "1", "yes")

# ─── Stage 5: whip-blur wipe transition (Comicz-style) ──────────────────────
# At each SCENE boundary, a deterministic coin flip (seeded by
# f"{project}:{scene_a}:{scene_b}" — stable across re-renders) decides whether that
# boundary gets a ~TRANSITION_WHIP_SECONDS vertical whip-blur wipe bridge clip instead
# of the normal transition (xfade dissolve or hard cut, whichever XFADE_TRANSITION
# would otherwise use there). 0 = never (byte-identical old behavior); 1 = every
# boundary whose two adjacent shots are both >= 0.6s long.
TRANSITION_WHIP_PROB = float(os.getenv("TRANSITION_WHIP_PROB", "0.5"))
TRANSITION_WHIP_SECONDS = float(os.getenv("TRANSITION_WHIP_SECONDS", "0.24"))

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

# ─── Stage 5: caption style knobs (A/B only — defaults reproduce the current,
# already-approved look byte-for-byte) ───────────────────────────────────────
CAPTION_FONT_SIZE = int(os.getenv("CAPTION_FONT_SIZE", "84"))
CAPTION_ALIGNMENT = int(os.getenv("CAPTION_ALIGNMENT", "2"))
CAPTION_MARGIN_V = int(os.getenv("CAPTION_MARGIN_V", "300"))
CAPTION_OUTLINE = float(os.getenv("CAPTION_OUTLINE", "8"))

# ─── Stage 5: panel mirror ───────────────────────────────────────────────────
# Read by stages/stage_5/shots.py (MIRROR_PANELS there). Default OFF: competitor
# autopsy caught OUR mirror flipping mid-video lettering backwards (house-of-m
# frames) — the dedup value of mirroring no longer outweighs the AI-slop risk.
MIRROR_PANELS = os.getenv("MIRROR_PANELS", "false").lower() in ("true", "1", "yes")

# ─── Stage 5: panel AI upscale (Real-ESRGAN) ────────────────────────────────
# Panel crops are often 237-500px; _prepare_panel_frame's LANCZOS blow-up to fill
# the 1080×1920 frame (up to 8× for a small panel) reads soft. Real-ESRGAN
# (anime model, ~2s/panel measured) sharpens the crop BEFORE that blow-up.
# Read by stages/stage_5/shots.py (_ai_upscale_panel). Any failure (binary
# missing, timeout, bad exit) falls back to the un-upscaled crop — never fatal.
PANEL_UPSCALE = os.getenv("PANEL_UPSCALE", "true").lower() in ("true", "1", "yes")
REALESRGAN_BIN = os.getenv("REALESRGAN_BIN", str(Path(__file__).parent / "tools/realesrgan/realesrgan-ncnn-vulkan"))
REALESRGAN_MODEL = os.getenv("REALESRGAN_MODEL", "realesrgan-x4plus-anime")

# ─── Stage 5: channel branding (Grimframe) ──────────────────────────────────
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "Grimframe")
CHANNEL_HANDLE = os.getenv("CHANNEL_HANDLE", "@grimframe")
_LOGO_RAW = os.getenv("CHANNEL_LOGO_PATH", "assets/branding/grimframe_logo.jpg")
CHANNEL_LOGO_PATH = _LOGO_RAW if os.path.isabs(_LOGO_RAW) else str(Path(__file__).parent / _LOGO_RAW)
ENABLE_CORNER_LOGO = os.getenv("ENABLE_CORNER_LOGO", "true").lower() in ("true", "1", "yes")
# Master 2026-07-07: the 3.5s branding OUTRO CARD is OFF by default for BOTH modes now.
# A branding card at the end reads as "the video is over" and kills the replay — the
# Shorts growth playbook (seamless loop = replay = view) wants the last narration shot
# to cut straight back to the hook. Corner logo (subtle, per-frame) stays ON — it brands
# without breaking the loop. Set ENABLE_OUTRO_CARD=true to bring the card back.
ENABLE_OUTRO_CARD = os.getenv("ENABLE_OUTRO_CARD", "false").lower() in ("true", "1", "yes")
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

# ─── Stage 3: banner title text ─────────────────────────────────────────────
# A short catchy title (narration.banner_title) Stage 3 generates. 2026-07-13:
# Stage 5 no longer burns it into the video (Master writes titles in CapCut);
# Stage 5 exports it to <project>/title.txt instead. Gate kept for Stage 3's
# generation call (write_script.py / micro_moment.py) — off skips the LLM call.
ENABLE_TITLE_BANNER = os.getenv("ENABLE_TITLE_BANNER", "true").lower() in ("true", "1", "yes")

# ─── Stage 5: background music bed ──────────────────────────────────────────
# Inert until a music file actually resolves (see stage_5.pipeline._resolve_bgm): with no
# file the render is narration-only and byte-identical to before this existed.
#
# The numbers below are CRAFT, not standards — measured on 2026-08-12 that no standards body
# (W3C / EBU / ATSC / Netflix) specifies a music-vs-speech level at all; ATSC A/85's own mixer
# guide says "always mix relying on your hearing". See MUSIC_SCORING_RESEARCH_2026-08-12.md.
#
# BG_MUSIC_OFFSET_LU is how far BELOW the narration's own integrated loudness the bed sits, so
# the bed self-calibrates to whatever the TTS delivered instead of assuming a fixed dBFS. That
# also mirrors EBU Tech 3343's advice to set the speech anchor FIRST and place background under
# it. Measured on a real 61s render: the bed moves final integrated loudness by only 0.11 LU at
# -15 and 0.04 LU at -20, so anything in this band is free to tune BY EAR — it cannot break the
# -14 LUFS normalisation.
ENABLE_BG_MUSIC = os.getenv("ENABLE_BG_MUSIC", "true").lower() in ("true", "1", "yes")
# Normal Stage 5 renders own their score after the narration-only MP4 is finished. The
# private MiniMax Space receives the measured final-video duration; failures remain soft —
# narration-only is always renderable.
AUTO_GENERATE_BG_MUSIC = os.getenv("AUTO_GENERATE_BG_MUSIC", "true").lower() in ("true", "1", "yes")

# ─── Music GENRE (per project, chosen in the Review Beats UI) ────────────────
# The style brief a generator is asked for. "minimal dark cinematic" is the default because
# it is the only register that survived a listening test on a real render: six dense beds
# (epic / drill / synthwave / lo-fi / trap / ambient) laid under the same narration at the
# same level were all inaudible, while a SPARSE one came through. Narration here occupies
# almost the whole timeline (measured: one continuous speech region, real silences only
# 0.26-0.75s), so music that leaves gaps is heard and music that fills every moment is not.
# MUSIC_GENRES is only the dropdown's preset list — the field is editable, so any string
# a generator understands is valid. Per-project choice lives in projects/<slug>/music.json.
MUSIC_GENRE = os.getenv("MUSIC_GENRE", "minimal dark cinematic")
# "impressionist solo piano" takes the sparse finding further than the default does: a 70s test
# render (2026-08-21) measured LRA 14.2 LU at -22.3 LUFS integrated — real silence between
# phrases rather than a continuous bed. Its wide dynamics are the trade: under narration the
# quietest phrases can disappear, so it is a preset to choose per project, not a default.
_DEFAULT_MUSIC_GENRES = (
    "minimal dark cinematic,minimal horror piano,impressionist solo piano,dark ambient drone,"
    "sparse dark strings,epic hybrid orchestral,dark trap,uk drill,dark synthwave,dark lo-fi"
)
MUSIC_GENRES: list[str] = [
    g.strip() for g in os.getenv("MUSIC_GENRES", _DEFAULT_MUSIC_GENRES).split(",") if g.strip()
]

# ─── Music generation (stages/music_bed.py → private HF MiniMax Space) ───────
# The LLM reads final narration + beats and writes the complete MiniMax Studio state.
# A CHAIN, not one model:
# measured 5/6 successful calls on deepseek-v4-flash — one returned empty, which config.py
# already warns about above. Called through OpenRouter directly, NOT the SDK, and it does not
# touch the global FREE_MODEL switch — same treatment as BEAT_SPLIT_MODELS.
MUSIC_BRIEF_MODELS: list[str] = [
    m.strip() for m in os.getenv(
        "MUSIC_BRIEF_MODELS",
        "deepseek/deepseek-v4-flash-0731,deepseek/deepseek-v4-flash,google/gemma-4-31b-it:free"
    ).split(",") if m.strip()
]
HF_MUSIC_SPACE = os.getenv("HF_MUSIC_SPACE", "Neopet2001/MiniMax-Music3")
HF_MUSIC_STEPS = int(os.getenv("HF_MUSIC_STEPS", "30"))
HF_MUSIC_GUIDANCE = float(os.getenv("HF_MUSIC_GUIDANCE", "1.7"))
HF_MUSIC_HEADROOM = float(os.getenv("HF_MUSIC_HEADROOM", "0"))
# 8.0 chosen by ear on a real render (Master, 2026-08-13) after A/B-ing 18/14/10/8/6/4/2/0 LU
# under the same narration. Measured at that setting: the bed sits ~23 LU down during the
# gaps and moves the level under speech by 0.07 dB, so it is present without crowding.
BG_MUSIC_OFFSET_LU = float(os.getenv("BG_MUSIC_OFFSET_LU", "8.0"))
# Extra attenuation applied while speech is present. Small on purpose: the bed already sits far
# under the voice, so the duck is a nudge for presence, not a rescue.
BG_MUSIC_DUCK_DB = float(os.getenv("BG_MUSIC_DUCK_DB", "-6.0"))
BG_MUSIC_DUCK_RAMP_S = float(os.getenv("BG_MUSIC_DUCK_RAMP_S", "0.25"))
# Only lift the bed for a gap LONGER than this. Measured: this narration's real silences run
# 0.26-0.75s, and lifting for a 0.3s gap (with a 0.25s ramp each side) never reaches full level
# before ducking again — that is exactly what reads as pumping. Short gaps stay ducked.
BG_MUSIC_DUCK_MIN_GAP_S = float(os.getenv("BG_MUSIC_DUCK_MIN_GAP_S", "0.6"))
# How many lifts the duck curve carries — a LIMIT, not a taste knob, and audio.py clamps it to
# its own hard ceiling regardless of what is set here. Two walls sit behind this, both measured:
# ffmpeg's expression evaluator refuses more than 31 lifts ("Missing ')' or too many args"), and
# Windows' CreateProcess refuses a command line over 32767 chars (WinError 206) — a 19-minute
# long-form has ~380 qualifying gaps, which would build a 44k-character expression. The LONGEST
# pauses are kept, being the ones a listener registers. A 61s Short has ~2 gaps and never
# reaches this at all, so the ceiling only ever bites long-form.
BG_MUSIC_DUCK_MAX_GAPS = int(os.getenv("BG_MUSIC_DUCK_MAX_GAPS", "24"))
# Silence detector threshold for finding the REAL speech/silence structure. Note this reads the
# rendered audio, NOT word_timestamps.json — those timings are interpolated evenly inside each
# sentence (measured: all 170 inter-word gaps are exactly 0.0), so they cannot locate silence.
BG_MUSIC_SILENCE_DB = float(os.getenv("BG_MUSIC_SILENCE_DB", "-35"))

_FFMPEG_BIN_RAW = os.getenv("FFMPEG_BIN", "bin/ffmpeg")
FFMPEG_BIN = _FFMPEG_BIN_RAW if os.path.isabs(_FFMPEG_BIN_RAW) else str(Path(__file__).parent / _FFMPEG_BIN_RAW)

# ─── Comic Scraper ──────────────────────────────────────────────────────────
# headless=False opens a visible Chrome window — much more reliable against Cloudflare

# ─── Project Storage ────────────────────────────────────────────────────────
PROJECTS_ROOT = Path(__file__).parent / "projects"
PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
RESEARCH_SESSIONS_ROOT = Path(__file__).parent / "research_sessions"


def get_project_path(project_name: str) -> Path:
    """Return the project folder path, creating it if needed."""
    p = PROJECTS_ROOT / project_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_project_dirs(project_name: str) -> dict:
    """Return base project folder. Sub-folders are created by stages as they need them."""
    base = get_project_path(project_name)
    return {"root": base}
