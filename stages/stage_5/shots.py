"""Shot list construction and per-shot ffmpeg Ken Burns rendering."""
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter

from .schema import Shot


OUTPUT_W = 1080
OUTPUT_H = 1920
TARGET_ASPECT = OUTPUT_W / OUTPUT_H
FPS = 30

# ── Semantic text↔panel alignment ───────────────────────────────────────────
# Cosine similarity from the shared embedding backend (Azure text-embedding-3-large
# when configured, else local mxbai-embed-large-v1). Far more reliable than lexical
# overlap ("reverted to mortal form" vs "FIZAPPT"). One model + cache across stages.
from .._embedding import semantic_sim as _semantic_sim  # noqa: E402
from .._panel_index import panel_embed_text, panel_dialog, page_dialog  # noqa: E402

PADDING_PCT = 0.05
# Small (non-highlight) panels are often cropped a touch tight by the detector and
# look cramped/cut on screen. Give any panel smaller than a highlight (< 40% of its
# page — matches stage-3 _BIG_SHOT_FRAC) a WIDER crop margin (30% per side) so the
# whole panel shows with breathing room. Highlights (big splashes) already fill the
# frame, so they keep the tight PADDING_PCT.
SMALL_PANEL_FRAC = 0.40
SMALL_PANEL_PAD_PCT = 0.30


def _pad_pct_for(w: int, h: int, iw: int, ih: int) -> float:
    """Crop margin (per side, as a fraction of panel size): 30% for a small panel,
    tight 5% for a highlight. 'Small' = panel area < SMALL_PANEL_FRAC of the page."""
    page_area = max(1, int(iw) * int(ih))
    frac = (max(0, int(w)) * max(0, int(h))) / page_area
    return SMALL_PANEL_PAD_PCT if frac < SMALL_PANEL_FRAC else PADDING_PCT


# Erase the comic's own speech-bubble text from panels before render (the video
# already carries narration + burned captions, so on-art dialogue is clutter).
INPAINT_BUBBLE_TEXT = True
# Horizontally flip each panel before render (the cleaned, mirrored frame is no
# longer a pixel-identical copy of the source page).
# The mirror reverses any on-art text the bubble-inpaint did not fully remove.
# That is safe for a TIGHT single panel (its 1-2 bubbles get cleaned), but NOT for
# a WHOLE-PAGE render (many panels + heavy dialogue the inpaint can't all clear →
# the whole page renders with BACKWARDS lettering). `no_mirror` is still set for
# whole-page / no-panel renders (see render_shots), for panels whose art carries
# readable text (_panel_has_critical_text), and UNCONDITIONALLY for the cold-open
# (is_intro). DEFAULT IS NOW OFF (config.MIRROR_PANELS, 2026-07-05): the competitor
# pixel autopsy caught OUR mid-video frames with backwards lettering the guards
# missed — the mirror's dedup value no longer outweighs its AI-slop risk. Kept as a
# plain module attribute so art_pipeline/tests can still runtime-override
# `shots.MIRROR_PANELS` directly.
from config import MIRROR_PANELS  # noqa: F401  (env MIRROR_PANELS=true re-enables)
from config import PANEL_UPSCALE, REALESRGAN_BIN, REALESRGAN_MODEL
# Hints (in a panel's VLM description) that the art contains story-critical
# readable text baked into the image — a gravestone, a sign, a nameplate. Mirroring
# such a panel reverses the letters and breaks the reveal (e.g. the 'PETER'
# gravestone payoff in Weapon VIII), so we keep these panels un-mirrored.
_CRITICAL_TEXT_HINTS = (
    "gravestone", "tombstone", "headstone", "grave marker",
    "engraved", "engraving", "carved", "carving", "inscribed", "inscription",
    "etched", "chiseled", "nameplate", "name plate", "plaque",
    "dog tag", "dogtag", "name tag",
    "a sign reading", "sign that reads", "sign reads",
    "reading '", "reads '", "spelled out",
    # On-screen readouts / labelled surveillance feeds bake location text into the
    # art (e.g. "BARBARA HOUSE" / "GORDON HOME" on a security monitor) that a
    # horizontal mirror reverses into gibberish.
    "monitor", "surveillance", "security camera", "security feed", "cctv",
    "screen showing", "screen shows", "display showing", "readout", "label",
)


def _panel_has_critical_text(panel: dict | None) -> bool:
    """True when a panel's description signals readable text baked into the art
    (gravestone/sign/nameplate) that mirroring would reverse. Used to skip the
    horizontal flip for that one panel so the lettering stays legible."""
    if not panel:
        return False
    desc = str(panel.get("description") or "").lower()
    return any(h in desc for h in _CRITICAL_TEXT_HINTS)


def _prepare_corner_logo(logo_src, out_png: Path, *, width: int, alpha: float) -> Path | None:
    """Scale the channel logo to `width` px and bake a uniform alpha into it,
    saving an RGBA PNG for overlay. Returns None if the source can't be read."""
    try:
        with Image.open(logo_src) as im:
            im = im.convert("RGBA")
            w, h = im.size
            new_h = max(1, int(round(h * (width / w))))
            im = im.resize((width, new_h), Image.LANCZOS)
            a = im.split()[3].point(lambda p: int(p * max(0.0, min(1.0, alpha))))
            im.putalpha(a)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            im.save(out_png)
        return out_png
    except (FileNotFoundError, OSError, ValueError):
        return None


MOTION_CYCLE = ("zoom_in", "pan_down", "pan_up", "pan_right")  # no zoom_out — one-direction only (Master 2026-07-11)

# Two strategies:
#   "scene"          — one continuous Ken-Burns per scene (ComicsUnlocked: 4-5 shots, 10-30s each)
#   "caption_chunk"  — one shot per caption chunk (TheComicCivilian: 35-40 shots, ~1.5s each,
#                      visual changes EVERY time the on-screen text changes)
SHOT_STRATEGY = "caption_chunk"

# Seamless loop (default ON, 2026-07-09: Master wants loop for BOTH recap + Q&A — replay =
# view): make the video's LAST frame close back onto the OPENING (cold-open) panel so
# YouTube's auto-replay reads as a continuous loop (the strongest verified retention lever).
# The recap matcher already reuses the cold-open panel for the is_outro scene, but the Q&A
# locked builder bookends intro/outro with DIFFERENT subject panels (loop never closes) and
# a no-outro narration doesn't close at all. _close_loop (a mode-agnostic post-step run LAST
# on the final Shot list, after any subject-panel outro assignment — see build_shots) points
# the LAST shot's VISUAL at the FIRST shot's; the outro's zoom_out ends at z=1.0 — exactly
# where the intro's zoom_in starts — so the seam frame matches. Only panel/framing changes:
# duration, caption, audio-sync, word_timestamps and caption timing are all untouched. Set
# SEAMLESS_LOOP=0 to restore the old non-looping behavior.
SEAMLESS_LOOP = os.getenv("SEAMLESS_LOOP", "1").strip().lower() not in ("0", "false", "no", "")

SHOTS_PER_SCENE = 1            # used when SHOT_STRATEGY == "scene"
SHOT_TARGET_SECONDS = 3.0   # ~1 panel / 3s — cuts were racing AHEAD of the narration
                            # (panel changed mid-sentence). 3s holds each panel long
                            # enough to track the voiceover. Raise further for calmer.
SHOT_MIN_SECONDS = 0.6         # caption-chunk mode: ~0.5-2s per shot
SILENCE_GAP_THRESHOLD = 0.2
SNAP_WINDOW_SECONDS = 0.5

# ── MOTION CORE (2026-07-04 overhaul) ────────────────────────────────────────
# Driven by a competitor pixel-autopsy (1.4M-3.2M-view channels) + our renderer probe:
#   • their avg shot is 1.2-1.8s, push-in ~8-12%/s, ONE panel FILLS the 9:16 frame;
#   • ours HELD 4-6s at 5% total zoom (<1%/s ≈ freeze) and mid-video frames often showed
#     a whole page / letterbox → tiny subjects.
# Fixes: pre-upscale 4× (probe: cuts zoompan velocity jitter ±33%→±14%, 2.3× smoother,
# +~0.5s/shot), zoom amplitude 5%→10-15%, and SUB-SHOT SPLIT (a long hold becomes several
# ~1.5s hard-cut sub-shots on the SAME panel with tightening framings).

# zoompan rounds the crop x/y to whole pixels every frame; on a 2× pre-scale that jitter
# is ±33% of velocity, and 2.3× smoother at 4× (probe-measured). The 4× frame also keeps
# the tighter push_top/push_detail sub-shot framings sharp (source region stays > output).
PRE_UPSCALE_FACTOR = int(os.getenv("PRE_UPSCALE_FACTOR", "4"))
# Adaptive upscale (perf, 2026-07-06): the 4× pre-scale is only needed for the TIGHT
# sub-shot framings (push_top/push_detail crop into a small region → need the extra
# resolution to stay sharp). A full-frame zoom/pan moves slowly over the whole panel,
# so a smaller pre-scale is enough there and the intermediate frame is far cheaper
# (3× = ~56% the pixels of 4×). Drop to 2 for max speed if an A/B shows no shake.
PRE_UPSCALE_FACTOR_FULL = int(os.getenv("PRE_UPSCALE_FACTOR_FULL", "3"))
_TIGHT_MOTIONS = ("push_top", "push_detail")
# Push-in amplitude over ONE shot: calm 1.00→1.10, action/intro 1.00→1.15. With the shorter
# sub-shots below this lands at the competitors' ~6-10%/s feel. NEVER near-static (freeze).
ZOOM_AMPLITUDE = float(os.getenv("ZOOM_AMPLITUDE", "0.06"))
ZOOM_AMPLITUDE_ACTION = float(os.getenv("ZOOM_AMPLITUDE_ACTION", "0.13"))
assert ZOOM_AMPLITUDE >= 0.06 and ZOOM_AMPLITUDE_ACTION >= 0.06, \
    "zoom amplitude < 0.06 reads as a frozen frame — raise ZOOM_AMPLITUDE(_ACTION)"
# Pan crop-zoom, HELD CONSTANT during a pan (Master 2026-07-11). At 1.15 the viewport is
# iw/1.15 wide → ~13% of the panel is "excess" for the camera to sweep across, a real
# one-directional pan (ComicCut/Cosmo autopsy) instead of the old 3-6% near-static nudge. The
# sweep is LINEAR (constant velocity, on/d — no ease) and ends EXACTLY on the far edge at the
# last frame. Lower it for a shorter sweep, raise it for more travel (and a tighter crop).
PAN_ZOOM = float(os.getenv("PAN_ZOOM", "1.15"))
assert PAN_ZOOM > 1.0, "PAN_ZOOM must exceed 1.0 or a pan has no excess region to travel"
# Any shot longer than this is split into ~SUBSHOT_TARGET_SECONDS hard-cut sub-shots on the
# SAME panel (competitor cadence). Sub-shots sum EXACTLY to the original so scene_timings /
# -shortest audio-sync (pipeline.assemble) is untouched.
MAX_SHOT_SECONDS = float(os.getenv("MAX_SHOT_SECONDS", "9999"))  # Master 2026-07-05: giữ pacing cũ — split OFF by default (set ~2.6 to re-enable competitor pacing)
SUBSHOT_TARGET_SECONDS = float(os.getenv("SUBSHOT_TARGET_SECONDS", "1.6"))
# Sub-shot framing cadence on the same panel: each hard cut steps to a TIGHTER framing
# (wide establish → face/upper-third → detail), ALL push-in so energy only builds — NEVER a
# zoom_out pull-back (Master 2026-07-11: competitors go one direction; a zoom-in-then-out on the
# same panel reads as indecision). push_top/push_detail render (in _zoompan_expr) as a higher
# base-zoom framing centered up (faces) / near-center (detail); the cycle alternates the two
# tight framings so a long hold stays varied without ever widening back out.
_SUBSHOT_FRAMINGS = ("zoom_in", "push_top", "push_detail", "push_top", "push_detail")
# push_top / push_detail base zoom (how tight the CUT lands) + vertical center fraction
# (faces sit high → push_top frames the upper third). Sharp because the frame is 4×-upscaled.
_SUBSHOT_TOP_ZOOM, _SUBSHOT_TOP_YC = 1.5, 0.34
_SUBSHOT_DETAIL_ZOOM, _SUBSHOT_DETAIL_YC = 1.8, 0.46
# A landscape panel wider than this cover-crops to FILL the 9:16 frame (competitors fill it;
# letterboxing shrinks the subject — the measured mid-video defect). Only a MORE extreme strip
# (where a centered cover-crop would show a meaningless sliver) or a >2.5× blow-up falls back
# to contain+blur. Env-tunable: lower it to letterbox more wide panels (keep more context).
LANDSCAPE_COVER_MAX_ASPECT = float(os.getenv("LANDSCAPE_COVER_MAX_ASPECT", "1.7"))  # raised 1.2→1.7 2026-07-12, R3 — panel ngang ≤1.7 giờ cover-crop thay vì contain+blur; A/B preview đã duyệt

# ── MICRO_MOMENT_V2 render knobs (env; default = OLD behavior, byte-identical) ───────────────
# All four default to the pre-v2 behavior; only a caller (answer_pipeline/micro) that SETS the
# env changes anything. The recap/Q&A render is unaffected unless the env is set.
#
# SHOT_MAX_SECONDS (default 0 = OFF): when > 0, any built shot longer than this is TIME-SPLIT
# (post-build, EVERY builder path) into consecutive fragments on the SAME panel, each ≤ the cap,
# cycling motion/crop-window (see _SUBSHOT_FRAMINGS) so a long hold reads as a moving cut, never
# a freeze. Durations sum EXACTLY to the original — captions/audio ride word_timestamps, not the
# shot count, so -shortest sync is untouched. Competitor autopsy: their shots are 2.5-3.8s; ours
# held ~9s (freeze). Micro sets ~3.5.
SHOT_MAX_SECONDS = float(os.getenv("SHOT_MAX_SECONDS", "0"))
# When SHOT_MAX_SECONDS is active AND the loop is on, keep _close_loop's opening-panel echo to a
# SHORT tail (this many seconds) instead of a full ≤cap hold — the seam is a quick replay-cue,
# and the opening panel must not eat a chunk of a 35-60s micro Short (intro < ~15% runtime).
LOOP_TAIL_SECONDS = float(os.getenv("LOOP_TAIL_SECONDS", "1.0"))
# PANEL_FIT_MODE (default "contain" = OLD): "fill" cover-crops a LANDSCAPE panel to fill the 9:16
# frame (center-weighted, sides cropped) instead of the contain+blur letterbox. Exceptions keep
# contain: a panel with critical baked text (_panel_has_critical_text, threaded via Shot.keep_contain)
# and a too-flat strip where the crop would discard > FILL_MAX_AREA_LOSS of the panel (logged).
PANEL_FIT_MODE = os.getenv("PANEL_FIT_MODE", "contain").strip().lower()
FILL_MAX_AREA_LOSS = float(os.getenv("FILL_MAX_AREA_LOSS", "0.60"))

# ── Pure-vector matcher (2026-06-26, validated: cosine on the richer panel embed beats
# the lexical hybrid — the embed already encodes chars/dialog/emotion, so lexical
# double-counts and over-rewards crowded talking-head panels, starving action/climax
# panels). _match_panels scores each (unit, panel) as:
#     W_COS·cos(chunk,panel) + W_COS_SCENE·cos(scene,panel) + render_adjust
# cosine comes from the Stage-2 Qdrant vectors (no re-embed) when available, else an
# in-memory embed. A unit whose best panel's RAW cosine is below PANEL_COS_FLOOR HOLDS
# the previous panel rather than showing a wrong one. Tune via PANEL_* env vars.
W_COS = float(os.getenv("PANEL_W_COS", "7.0"))
W_COS_SCENE = float(os.getenv("PANEL_W_COS_SCENE", "2.0"))
PANEL_COS_FLOOR = float(os.getenv("PANEL_COS_FLOOR", "0.38"))
# Order-FREE content match: each unit takes its best-content panel even if out of page
# order (fixes narration that interleaves present + backstory, where the depicting panel
# sits out of order). PANEL_FWD_BIAS is the AMPLITUDE of a per-unit PAGE-ANCHORED prior: a
# Gaussian bump centred on each unit's OWN page_ref (the Stage-3 beat anchor), added to
# content. It pulls a line toward the PAGE it is about — forward for a chronological line,
# BACKWARD for a backstory/twist line. 0 disables (pure content).
# PANEL_PRIOR_SIGMA_PAGES = bump spread in pages (1.0 → same page 1.0, ±1pg 0.61, ±2pg 0.14).
PANEL_FWD_BIAS = float(os.getenv("PANEL_FWD_BIAS", "1.0"))
PANEL_PRIOR_SIGMA_PAGES = float(os.getenv("PANEL_PRIOR_SIGMA_PAGES", "1.0"))
# Grounded-panel anchor (C2, 2026-07-02): Stage 3's `_ground_beat_panels` already
# content-matched a beat's SUMMARY (a richer, stable sentence) to one specific
# panel; a unit whose scene carries that (page_ref, panel_ref) gets a bonus added
# to its score for exactly that panel. 2.5 ≈ outweighs a cosine deficit up to
# ~0.35 (W_COS=7) — the grounded panel wins unless the chunk-level cosine STRONGLY
# disagrees. Still soft: Hungarian/greedy assignment and VLM rerank can override.
PANEL_ANCHOR_BONUS = float(os.getenv("PANEL_ANCHOR_BONUS", "2.5"))
# Anchor BINDING (Fix 2, 2026-07-02): the bonus above is a SCORE, and the rest of the
# heuristic stack (render_adjust salience swings, the PANEL_COS_FLOOR hold, VLM rerank)
# can and did outweigh it — a whole-page checklist panel beat a cosine-rank-1 anchor.
# An anchor is an AUTHORIAL decision, not a hint: when ON (default), an anchored unit's
# panel is PRE-ASSIGNED before Hungarian/greedy runs and skips the cosine floor, VLM
# rerank, and every tie-break entirely. OFF falls back to the old soft PANEL_ANCHOR_BONUS
# scoring path (safety valve).
PANEL_ANCHOR_BIND = os.getenv("PANEL_ANCHOR_BIND", "1").strip().lower() not in ("0", "false", "no", "")
# Anchor TRUST (Feature C, 2026-07-03): an anchor is only as trustworthy as the PAGE
# DESCRIPTION that produced it. Stage 2's DESC_VERIFY gate writes a page-level
# `desc_verified` (False = descriptions still mismatched their own pixels after a
# re-describe) and the dialog check writes a panel-level `dialog_mismatch` (True = the
# VLM dialog contradicts Magi OCR). On doom-rocket-raccoon scene 13 a FABRICATED
# description made Stage 3 anchor a beat to the WRONG panel; ANCHOR_BIND faithfully
# rendered it and only a human eye caught it. When ON (default) we DON'T hard-bind an
# anchor whose target panel is UNTRUSTED — we leave the unit un-anchored so it flows
# through normal content matching AND becomes VLM-rerank-eligible (Feature D). OFF
# restores the old always-bind behaviour (safety valve; also identical output on old
# projects that carry no flags at all).
ANCHOR_TRUST = os.getenv("ANCHOR_TRUST", "1").strip().lower() not in ("0", "false", "no", "")
# Anchor RE-CHECK (Feature E): a bound anchor normally skips the VLM safety net entirely.
# But a WRONG Stage-3 page_ref (from positional scene→beat drift) binds a panel whose cosine
# is far below the best-content panel's — invisible to the net. When the gap
# max(cosine) − anchor cosine exceeds ANCHOR_DISAGREE_MARGIN, let the VLM re-check the bind
# too (agreement stays trusted, no SDK call). SDK absent → VLM no-ops → the anchor is kept.
PANEL_ANCHOR_RECHECK = os.getenv("PANEL_ANCHOR_RECHECK", "1").strip().lower() not in ("0", "false", "no", "")
ANCHOR_DISAGREE_MARGIN = float(os.getenv("ANCHOR_DISAGREE_MARGIN", "0.18"))
# FIX 2: spread a scene's fragment siblings across DISTINCT panels of its anchor page (instead of
# collapsing all onto key_panels[0]). Locked fragments bypass the matcher, so this only touches the
# unlocked recap fan-out. 0 restores the collapse-to-one behavior.
FRAGMENT_SPREAD = os.getenv("FRAGMENT_SPREAD", "1").strip().lower() not in ("0", "false", "no", "")
# ONE_SHOT_PER_LINE: collapse each narration SENTENCE (scene) into ONE held shot (one panel, one
# continuous motion) instead of one shot per visual-beat clause. Fixes "panels change faster than
# the voice" — the cut lands only when the line's speech ends. Uses the scene's FIRST pinned panel
# (matcher fills if unpinned). Default off → byte-identical to the per-clause behaviour.
ONE_SHOT_PER_LINE = os.getenv("ONE_SHOT_PER_LINE", "0").strip().lower() not in ("0", "false", "no", "")
# render_adjust (panel size / text-coverage) biases toward bigger / highlight panels and
# away from tiny/text-wall ones. The old 1.5 clamp kept it a weak near-tie break because a
# strong size bonus turned the few big panels into "magnets" → wrong matches AND duplicates.
# The duplicate half is now handled independently by PANEL_UNIQUE (Hungarian 1:1 assignment),
# so we can favor bigger panels harder without the duplicate blow-up: cap 3.0 + PANEL_SALIENCE_W
# 3.0 (measured: on size-varied deadpool-batman this lifts median chosen panel area 0.44→0.72
# while changing only 2/16 picks; on splash-heavy motorstorm it changes 0). Content (W_COS·cos,
# swing ~3) still leads — size only decides among content-similar panels. 0 disables render_adjust.
PANEL_RENDER_ADJ_CAP = float(os.getenv("PANEL_RENDER_ADJ_CAP", "3.0"))
# Big/highlight-panel salience weight fed to _render_adjust: a >=50%-of-page (splash) panel adds
# +PANEL_SALIENCE_W to its score (then clamped by PANEL_RENDER_ADJ_CAP). Raise to favor larger
# panels harder; lower toward 0 to make size irrelevant.
PANEL_SALIENCE_W = float(os.getenv("PANEL_SALIENCE_W", "3.0"))
# Soft no-reuse: each prior use of a panel subtracts this from its content score for later
# units. Small enough that a unit with NO good alternative still reuses (two lines about the
# same moment both hold that panel), large enough that several similar lines (e.g. three
# "Galactus devours..." beats) spread across DISTINCT near-tie panels instead of repeating
# one. 0 = unlimited reuse.
PANEL_REUSE_PENALTY = float(os.getenv("PANEL_REUSE_PENALTY", "3.0"))
# Hard no-reuse: assign each STORY scene a DISTINCT panel via optimal (Hungarian)
# assignment on the (content+page-prior) score — no panel is shown for two different
# scenes. Beats the soft PANEL_REUSE_PENALTY, which a strong "magnet" panel can overpower
# (it won 4 scenes on Motorstorm). Falls back to the greedy soft path when scenes > panels
# (uniqueness impossible) or PANEL_UNIQUE=0. The PANEL_COS_FLOOR weak-match HOLD still
# applies after assignment, so a scene with no good DISTINCT panel holds the previous one.
PANEL_UNIQUE = os.getenv("PANEL_UNIQUE", "1").strip().lower() not in ("0", "false", "no", "")
# #6 — VLM panel rerank (Claude SDK vision). When the matcher's best panel for a unit is a
# LOW-confidence match (raw cos < PANEL_RERANK_COS_CEIL → cosine pick unreliable), a Claude
# vision judge looks at a shortlist (top-K by score ∪ panels on the unit's page_ref page),
# reads the cropped panel images, and picks the one that best depicts the line — or NONE →
# hold. Gated to the few weak units (~3-5 SDK calls). PANEL_RERANK=0 disables.
PANEL_RERANK = os.getenv("PANEL_RERANK", "1").strip().lower() not in ("0", "false", "no", "")
PANEL_RERANK_COS_CEIL = float(os.getenv("PANEL_RERANK_COS_CEIL", "0.66"))
PANEL_RERANK_TOPK = int(os.getenv("PANEL_RERANK_TOPK", "5"))
# Big-shot tie-break: among panels whose biased score is within this many points of the
# best (i.e. content-similar), prefer the LARGER panel — a big/splash shot renders sharper
# and reads as a highlight. Content still decides which panels are in the near-tie set, so
# this never overrides a clear content winner; it only restores visual punch on ties.
PANEL_SIZE_TIE_MARGIN = float(os.getenv("PANEL_SIZE_TIE_MARGIN", "0.8"))


def _apply_review_locks(narration: dict, project: str) -> None:
    """Override a scene's (page_ref, panel_ref) from review/locks.json BEFORE matching, so
    Master's hand-picked panel flows through the existing PANEL_ANCHOR_BIND path. No-op when
    there are no locks. (A lock on a DESC_VERIFY-untrusted page won't hard-bind — ANCHOR_TRUST
    still routes it through content-match + rerank, but the page-prior keeps it on the locked
    page; rare, and the smallest patch. Upgrade path: pass locked scene ids to bypass trust.)"""
    try:
        from ..review_gate import load_state, lock_panels
    except Exception:
        return
    locks = (load_state(project) or {}).get("locks") or {}
    if not locks:
        return
    applied = 0
    for s in narration.get("scenes") or []:
        # lock_panels normalises BOTH the v2 multi-panel shape ({"panels":[...]}) and the old
        # single {"page","panel"} shape → [{"page","panel"}, ...]. The single-anchor bind path
        # uses the FIRST locked panel; the sentence path (_build_shots_per_sentence, Q&A) is
        # what actually spreads the full 1-5 set across a scene's sentences. Without this,
        # int(lk.get("page")) crashed on a v2 lock (page is None).
        panels = lock_panels(locks.get(str(s.get("scene_id"))))
        if not panels:
            continue
        s["page_ref"] = int(panels[0]["page"])
        s["panel_ref"] = int(panels[0]["panel"])
        applied += 1
    if applied:
        print(f"[stage5] review-gate: applied {applied} panel lock(s) from review/locks.json")


def _apply_visual_beat_locks(narration: dict, locks: dict) -> None:
    """Pin Master's locked panels onto visual-beat FRAGMENTS in-memory from review/locks.json,
    BEFORE the (unchanged) _build_shots_per_chunk builder runs — so the writer-picks-panel flow
    reads Master's approved (page, panel) via _vb_pin (each fragment its own shot). Works for
    micro_moment (fragments already {"text",...} dicts) AND recap (STRING fragments → normalised
    to {"text",...} dicts here). Keys (review_gate.build_candidates):
      • "<sid>:<frag_idx>" → pin scene <sid>'s fragment <frag_idx>
      • "<sid>"            → pin EVERY fragment of scene <sid> to the lock's first panel
    The "intro" key is handled by build_shots (→ narration.cold_open_lock), not here. Each lock
    uses its FIRST panel. No-op on unmatched/empty keys.
    ponytail: a scene-level lock pins every fragment to ONE panel (matches the real 1-panel-lock
    case); for a multi-panel scene use per-fragment "<sid>:<frag>" keys to spread the pool."""
    from ..review_gate import lock_panels
    scenes = narration.get("scenes") or []
    by_id = {int(s.get("scene_id") or i): s for i, s in enumerate(scenes, start=1)}
    applied = 0

    def _pin(vbs: list, i: int, pg: int, pn: int) -> None:
        b = vbs[i] if isinstance(vbs[i], dict) else {"text": _vb_text(vbs[i])}
        b["page"], b["panel"] = pg, pn
        vbs[i] = b

    for key, lock in locks.items():
        if key == "intro":
            continue
        panels = lock_panels(lock)
        if not panels:
            continue
        pg, pn = int(panels[0]["page"]), int(panels[0]["panel"])
        sid_s, _, frag_s = str(key).partition(":")
        try:
            sc = by_id.get(int(sid_s))
        except ValueError:
            continue
        vbs = (sc or {}).get("visual_beats") or []
        if frag_s:   # "<sid>:<frag_idx>" — one fragment
            try:
                fi = int(frag_s)
            except ValueError:
                continue
            if 0 <= fi < len(vbs):
                _pin(vbs, fi, pg, pn)
                applied += 1
        else:        # "<sid>" — whole scene: pin every fragment to the lock's first panel
            for i in range(len(vbs)):
                _pin(vbs, i, pg, pn)
                applied += 1
    if applied:
        print(f"[stage5] review-gate: applied {applied} visual-beat lock(s) from review/locks.json")


def build_shots(
    narration: dict,
    *,
    scene_timings: list[dict] | None = None,
    word_timestamps: list[dict] | None = None,
    caption_chunks: list[dict] | None = None,
    pages_by_number: dict[int, dict] | None = None,
    cluster_to_name: dict[int, str] | None = None,
    project: str | None = None,
) -> list[Shot]:
    """Split each narration scene into shots.

    Strategy depends on SHOT_STRATEGY:
      • "scene"         — one shot per scene (one continuous Ken-Burns)
      • "caption_chunk" — one shot per caption chunk (visual changes WITH the text,
                           cycling through the page's panels for visual variety)
    """
    if project:
        _apply_review_locks(narration, project)
    # Review locks reach the render mode-specifically (Master 2026-07-14 — review-lock is now a
    # HARD GATE for ALL modes, panels chosen after narrate):
    #   • Q&A (answer_research)     → the legacy chunk-locked builder (unchanged, via _qa_locks).
    #   • recap/micro WITH visual_beats → pin Master's locked panels onto the fragments, then the
    #                                 UNCHANGED per-chunk builder reads them via _vb_pin (each
    #                                 fragment its own shot; unlocked scenes still fragment-split
    #                                 via the free matcher). A scene locked to ONE panel keeps its
    #                                 fragment shots on that panel with varied motion.
    #   • recap WITHOUT visual_beats (legacy) → the chunk-locked builder (unchanged) so all 2-5
    #                                 locked panels per scene appear.
    #   • "intro" lock (recap+micro) → narration.cold_open_lock, honored by the cold-open scorer.
    # No locks → every path below is byte-for-byte identical to before (recap/micro unaffected).
    mode = str(narration.get("mode") or "")
    micro = mode == "micro_moment"
    has_vb = any(s.get("visual_beats") for s in (narration.get("scenes") or [])
                 if not s.get("is_intro") and not s.get("is_outro"))
    try:
        from ..review_gate import _plot_source, lock_panels
        qa = bool(project) and _plot_source(project) == "answer_research"
    except Exception:
        qa, lock_panels = False, None
    qa_locks = _qa_locks(project)
    review_locks = _review_locks(project) if project else {}
    if review_locks and not qa and lock_panels is not None:
        intro_lock = lock_panels(review_locks.get("intro"))
        if intro_lock:
            narration["cold_open_lock"] = [int(intro_lock[0]["page"]), int(intro_lock[0]["panel"])]
        if micro or has_vb:
            _apply_visual_beat_locks(narration, review_locks)
    # Legacy recap with NO visual_beats keeps the chunk-locked builder; a visual_beats recap takes
    # the pin path above + the per-chunk builder (so its fragment split survives the locks).
    recap_locked = bool(
        review_locks and not micro and not qa and not has_vb
        and caption_chunks and pages_by_number is not None and lock_panels is not None
        and any(lock_panels(lk) for kk, lk in review_locks.items() if kk != "intro")
    )
    if qa_locks and caption_chunks and pages_by_number is not None:
        shots = _build_shots_per_chunk_locked(
            narration, caption_chunks, pages_by_number, scene_timings or [],
            locks=qa_locks, cluster_to_name=cluster_to_name or {}, project=project,
        )
    elif recap_locked:
        # recap with Master-locked panels: the "intro" key is ignored by the builder (no scene_id
        # matches it) — it was already folded into cold_open_lock above.
        shots = _build_shots_per_chunk_locked(
            narration, caption_chunks, pages_by_number, scene_timings or [],
            locks=review_locks, cluster_to_name=cluster_to_name or {}, project=project,
        )
    # (legacy) Q&A sentence path: when the headless sentence-match step has written
    # review/sentence_panels.json, drive one shot PER NARRATION SENTENCE from it. Kept as a
    # fallback for a Q&A project that somehow has that file but no locks; the chunk-locked path
    # above supersedes it for every approved Q&A project.
    elif (sentence_panels := _load_sentence_panels(project)) is not None and pages_by_number is not None:
        shots = _build_shots_per_sentence(
            narration, sentence_panels, pages_by_number, scene_timings or [],
            cluster_to_name=cluster_to_name or {}, project=project,
        )
    elif SHOT_STRATEGY == "caption_chunk" and caption_chunks and pages_by_number is not None:
        shots = _build_shots_per_chunk(
            narration, caption_chunks, pages_by_number, scene_timings or [],
            word_timestamps=word_timestamps,
            cluster_to_name=cluster_to_name or {}, project=project,
        )
    else:
        shots = _build_shots_per_scene(narration, scene_timings, word_timestamps)

    # Custom images (Master-added, review UI): OVERRIDE whatever the matcher/builder above
    # picked for a beat that got a custom image (locked by Master, or argmax-assigned by
    # cosine — see assign_custom_images). No-op (byte-identical) for any project with no
    # review/custom/custom_images.json.
    custom_map = _resolve_custom_images(project, narration) if project else {}
    if custom_map:
        _apply_custom_images_to_shots(shots, custom_map)
        print(f"[stage5] custom-image: assigned {len(custom_map)} beat(s) -> "
              f"{sorted(custom_map)}")

    # SHOT_MAX_SECONDS (micro_moment v2): cap held shots so no panel freezes. No-op (returns the
    # list unchanged) when the knob is 0 → byte-identical to the old behavior. Runs BEFORE
    # _close_loop so the loop-clone lands on the FINAL fragment (a short ~LOOP_TAIL_SECONDS tail).
    #
    # FRAGMENT/MICRO GATE (Master 2026-07-11, ComicCut autopsy): 1 beat = 1 panel = 1 CONTINUOUS
    # shot — a panel is held up to ~5s with one-direction motion, NEVER hard-cut mid-panel. The
    # fragment builder (_fragment_units) already emits one unit per clause at exactly that
    # granularity, so time-splitting a held fragment would only re-chop it into sub-shots that
    # SHARE the caption + panel ("1 scene hiện nhiều lần"). Force it off for micro_moment; every
    # other mode still honors SHOT_MAX_SECONDS via env (recap/Q&A byte-identical when unset).
    shots = _time_split_shots(shots, 0.0 if micro else SHOT_MAX_SECONDS,
                              loop_tail=LOOP_TAIL_SECONDS if SEAMLESS_LOOP else 0.0)
    if SEAMLESS_LOOP:
        _close_loop(shots)
    return shots


def _plan_split_durations(dur: float, max_seconds: float, tail: float = 0.0) -> list[float]:
    """Split `dur` into fragments each ≤ max_seconds, summing EXACTLY to `dur`.
    `tail` > 0 reserves a final fragment of ~tail seconds (for the loop-close clone) and
    even-splits the rest; tail == 0 → all fragments roughly equal (last absorbs rounding).
    Returns [dur] when a single fragment already fits and no tail is requested."""
    import math
    if tail > 0.0 and dur > tail + 1e-6:
        return _plan_split_durations(dur - tail, max_seconds) + [round(tail, 3)]
    if dur <= max_seconds + 1e-6:
        return [round(dur, 3)]
    n = max(2, math.ceil(dur / max_seconds - 1e-6))
    step = round(dur / n, 3)
    durs = [step] * (n - 1)
    durs.append(round(dur - step * (n - 1), 3))   # last absorbs the remainder → exact sum
    return durs


def _time_split_shots(shots: list[Shot], max_seconds: float, *, loop_tail: float = 0.0) -> list[Shot]:
    """SHOT_MAX_SECONDS: split any shot longer than `max_seconds` into consecutive fragments on
    the SAME panel (same page/bbox/source/text_bboxes/no_mirror/is_intro/beat_id), each ≤ the cap,
    cycling motion through _SUBSHOT_FRAMINGS so the held panel never freezes. shot_ids are
    renumbered contiguously (fragments would otherwise collide on shot_NNN.mp4). Fragments keep
    the ORIGINAL scene_id so _group_shots_by_scene treats them as sub-shots (hard-cut between
    them, transition only between scenes) — same mechanism as the existing MAX_SHOT_SECONDS
    sub-shots. `loop_tail` > 0 makes the LAST shot's final fragment ~loop_tail seconds so the
    subsequent _close_loop echo is a short tail. No-op (returns the input list unchanged) when
    max_seconds <= 0 AND loop_tail <= 0 → byte-identical to the old behavior. When max_seconds
    <= 0 but loop_tail > 0 (micro_moment + SEAMLESS_LOOP), no shot is time-split for length —
    only the LAST shot is carved into [head, tail] on the SAME panel so the loop still gets its
    short echo tail without re-chopping every held panel (see FRAGMENT/MICRO GATE above)."""
    from dataclasses import replace
    if max_seconds <= 0:
        if loop_tail <= 0 or not shots:
            return shots
        last = shots[-1]
        dur = float(last.duration_seconds)
        if dur < 0.5:
            return shots
        tail_eff = round(min(loop_tail, dur * 0.6), 3)
        head = round(dur - tail_eff, 3)
        out = list(shots[:-1])
        for k, d in enumerate((head, tail_eff)):
            out.append(replace(
                last, shot_id=len(shots) - 1 + k, duration_seconds=d,
                panel_bbox=dict(last.panel_bbox),
                text_bboxes=list(getattr(last, "text_bboxes", None) or []),
            ))
        return out
    tail = min(loop_tail, max_seconds) if loop_tail > 0 else 0.0
    out: list[Shot] = []
    nid = 0
    last_i = len(shots) - 1
    for i, s in enumerate(shots):
        want_tail = tail if (i == last_i and tail > 0.0) else 0.0
        durs = _plan_split_durations(float(s.duration_seconds), max_seconds, want_tail)
        for k, d in enumerate(durs):
            motion = s.motion if len(durs) == 1 else _SUBSHOT_FRAMINGS[k % len(_SUBSHOT_FRAMINGS)]
            out.append(replace(
                s, shot_id=nid, duration_seconds=d, motion=motion,
                panel_bbox=dict(s.panel_bbox),
                text_bboxes=list(getattr(s, "text_bboxes", None) or []),
            ))
            nid += 1
    return out


def _close_loop(shots: list[Shot]) -> None:
    """SEAMLESS_LOOP: point the final shot's VISUAL at the first (cold-open) shot's so the
    video ends where it began → YouTube auto-replay reads as a seamless loop. Only the panel
    /framing is cloned; the last shot keeps its own duration + caption (audio sync untouched).
    Motion is forced to zoom_out, which ends at z=1.0 == the intro zoom_in's first frame → the
    seam frame matches. No-op unless the first shot is the cold-open (is_intro) and there are
    >=2 shots.

    Call this AFTER the builder has fully assigned every shot (see build_shots: it runs as the
    LAST step on the finished list) so it wins any earlier bookend assignment. In particular
    the Q&A locked builder (_build_shots_per_chunk_locked) points the outro scene at a subject
    panel (e.g. subject_seq[QA_INTRO_SUBJECT_PANELS], the "next unused" subject panel after the
    intro's); with the loop on, this clone OVERWRITES that outro subject panel with the intro's
    cold-open panel instead. That is the desired behavior, not a bug — the last frame must match
    the first frame for the loop seam, so the subject-outro panel simply never renders when
    SEAMLESS_LOOP is on.

    ponytail: assumes the last shot is the outro/closing line (our writer always emits one); a
    video with no outro + SEAMLESS_LOOP on would show the opening panel under its final line —
    opt-in only, documented. 2026-07-13: the title banner no longer burns (moved to title.txt),
    so the seam now matches the intro frame exactly."""
    if len(shots) < 2 or not getattr(shots[0], "is_intro", False):
        return
    first, last = shots[0], shots[-1]
    if first is last:
        return
    last.panel_bbox = dict(first.panel_bbox)
    last.source_image = first.source_image
    last.text_bboxes = list(getattr(first, "text_bboxes", None) or [])
    last.no_mirror = getattr(first, "no_mirror", False)
    last.custom_image = getattr(first, "custom_image", "")
    last.motion = "zoom_out"


def _build_shots_per_scene(
    narration: dict,
    scene_timings: list[dict] | None,
    word_timestamps: list[dict] | None,
) -> list[Shot]:
    """ComicsUnlocked-style: one continuous Ken-Burns shot per scene."""
    scenes = narration.get("scenes") or []
    timings_by_scene = {int(t.get("scene_id", 0) or 0): t for t in (scene_timings or [])}
    shots: list[Shot] = []
    shot_id = 0
    prev_scene_end = 0.0  # Each scene "owns" from the previous scene's end → its own end,
                          # so inter-scene silence (TTS gaps between sentences) is absorbed
                          # into the visual timeline. Without this, ffmpeg -shortest clips
                          # the audio tail.
    for s in scenes:
        scene_id = int(s.get("scene_id") or len(shots) + 1)
        # Prefer ACTUAL scene duration from Stage 4 word-alignment over Stage 3's
        # words-per-second estimate — the LLM estimate underpredicts when TTS uses
        # a slower emotion (e.g. "contemplative"), causing audio truncation under
        # ffmpeg's `-shortest` flag. scene_timings.json is the source of truth.
        timing = timings_by_scene.get(scene_id)
        if timing and float(timing.get("end", 0)) > float(timing.get("start", 0)):
            target = float(timing["end"]) - prev_scene_end
            prev_scene_end = float(timing["end"])
        else:
            target = float(s.get("target_seconds") or 0.0)
        bbox = s.get("panel_bbox") or {}
        source_image = str(s.get("source_image") or "")
        if not source_image or target <= 0.0:
            continue

        if SHOTS_PER_SCENE <= 1:
            # ONE continuous motion per scene — matches ComicsUnlocked's 10-30s
            # held-panel-with-Ken-Burns style. Motion cycles per-scene for variety,
            # not per-sub-shot (which created hard cuts mid-scene).
            durations = [target]
        else:
            durations = _plan_durations(
                scene_id=scene_id,
                target=target,
                scene_timing=timings_by_scene.get(scene_id),
                word_timestamps=word_timestamps,
            )
        for i, dur in enumerate(durations):
            # Never static — a held still frame reads as a freeze. Always rotate
            # through MOTION_CYCLE (all entries move) so every shot has motion.
            if SHOTS_PER_SCENE <= 1:
                # Rotate motion based on scene_id so consecutive scenes differ.
                motion = MOTION_CYCLE[(scene_id - 1) % len(MOTION_CYCLE)]
            else:
                motion = MOTION_CYCLE[i % len(MOTION_CYCLE)]
            shots.append(Shot(
                shot_id=shot_id,
                scene_id=scene_id,
                duration_seconds=max(0.4, dur),
                panel_bbox={"x": int(bbox.get("x", 0)), "y": int(bbox.get("y", 0)),
                            "w": int(bbox.get("w", 0)), "h": int(bbox.get("h", 0))},
                source_image=source_image,
                motion=motion,
            ))
            shot_id += 1
    return shots


def _chunks_grouped_by_scene(
    caption_chunks: list[dict],
    scene_timings: list[dict],
    scenes_by_id: dict[int, dict],
) -> list[tuple[dict, list[tuple[str, float, float]]]]:
    """Assign each caption chunk to its scene (by time midpoint), absorb the silence up to the
    next chunk FORWARD into its duration, and group CONSECUTIVE same-scene chunks. Returns
    [(scene, [(text, start, dur), ...]), ...] preserving the exact audio timeline. Shared by the
    recap per-chunk builder and the Q&A locked per-chunk builder (identical chunk→scene math)."""
    def find_scene_for_chunk(c):
        c_mid = (float(c.get("start", 0)) + float(c.get("end", 0))) / 2
        for st in scene_timings:
            if float(st.get("start", 0)) <= c_mid < float(st.get("end", 1e9)):
                return scenes_by_id.get(int(st.get("scene_id", 0)))
        return scenes_by_id.get(1) if scenes_by_id else None

    scene_max_end = max((float(st.get("end", 0)) for st in scene_timings), default=0.0)
    enriched: list[tuple[dict, str, float, float]] = []
    for i, chunk in enumerate(caption_chunks):
        scene = find_scene_for_chunk(chunk)
        if scene is None:
            continue
        c_start = float(chunk.get("start", 0))
        c_end = float(chunk.get("end", c_start + 1.0))
        if i + 1 < len(caption_chunks):
            c_end = max(c_end, float(caption_chunks[i + 1].get("start", c_end)))
        else:
            c_end = max(c_end, scene_max_end)
        enriched.append((scene, str(chunk.get("text", "")), c_start, max(0.4, c_end - c_start)))

    groups: list[tuple[dict, list[tuple[str, float, float]]]] = []
    for scene, text, c_start, dur in enriched:
        sid = int(scene.get("scene_id") or 1)
        if groups and int(groups[-1][0].get("scene_id") or 1) == sid:
            groups[-1][1].append((text, c_start, dur))
        else:
            groups.append((scene, [(text, c_start, dur)]))
    return groups


def _vb_text(b) -> str:
    """A visual-beat's narration words. WRITER-PICKS-PANEL (micro_moment) beats are
    {"text","page","panel"} dicts; recap/legacy beats are plain strings — either way return
    the text."""
    return str(b.get("text", "")).strip() if isinstance(b, dict) else str(b).strip()


def _vb_pin(b) -> tuple[int, int] | None:
    """The (page, panel) a visual-beat dict pins for WRITER-PICKS-PANEL, else None (string
    beat / no pin / malformed). ONLY micro_moment emits dict beats with pins; recap/Q&A beats
    are strings → always None → every unit flows through the matcher (byte-identical path)."""
    if not isinstance(b, dict):
        return None
    pg, pn = b.get("page"), b.get("panel")
    if pg is None or pn is None:
        return None
    try:
        return int(pg), int(pn)
    except (TypeError, ValueError):
        return None


def _build_shots_per_chunk(
    narration: dict,
    caption_chunks: list[dict],
    pages_by_number: dict[int, dict],
    scene_timings: list[dict],
    *,
    word_timestamps: list[dict] | None = None,
    cluster_to_name: dict[int, str] | None = None,
    project: str | None = None,
) -> list[Shot]:
    """TheComicCivilian-style: one shot per caption chunk, with SMART panel
    selection scoring each candidate panel against the chunk text. Pool spans
    the scene's page ±1 adjacent pages; never repeats within a scene; falls
    back to widest pool only when exhausted."""
    scenes = narration.get("scenes") or []
    scenes_by_id = {int(s.get("scene_id") or i): s for i, s in enumerate(scenes, start=1)}
    groups = _chunks_grouped_by_scene(caption_chunks, scene_timings, scenes_by_id)

    # ── Narration-driven panel matching ─────────────────────────────────────
    # Flatten the scenes into ordered narration UNITS (one per visual beat), then
    # match each unit to its best-content panel via _match_panels.
    units: list[tuple[dict, list, str, tuple | None]] = []   # (scene, slice_members, match_text, pin)
    for scene, members in groups:
        scene_text = str(scene.get("text", "") or "")
        if scene.get("is_intro") or scene.get("is_outro"):
            # OUTRO: honor an explicit (page_ref, panel_ref) as the PIN so a chosen closing panel
            # (e.g. the mirror) renders, instead of the matcher's cold-open reuse (loop bookend).
            _opin = None
            if scene.get("is_outro") and int(scene.get("page_ref") or 0) > 0 \
                    and int(scene.get("panel_ref", -1) if scene.get("panel_ref") is not None else -1) >= 0:
                _opin = (int(scene["page_ref"]), int(scene["panel_ref"]))
            clause_texts, slices, pins = [scene_text], [members], [_opin]
        elif ONE_SHOT_PER_LINE:
            # 1 sentence = 1 held panel + 1 motion (no clause-fragmenting). First pinned panel wins;
            # unpinned → matcher. Cut lands exactly at the line's speech end.
            _rb = scene.get("visual_beats") or []
            clause_texts, slices, pins = [scene_text], [members], [_vb_pin(_rb[0]) if _rb else None]
        else:
            # Panel changes track the narration's SEMANTIC subject, NOT audio time-slices.
            # A scene with real visual_beats (multiple drawn moments) → one panel per beat;
            # a single-subject scene → ONE panel held for the whole sentence (the design's
            # "hold-while-same-subject"). No time-split: a panel changing faster than the
            # narration outruns it (Master, Doom 2026-06-27).
            # WRITER-PICKS-PANEL: a beat may be a {"text","page","panel"} dict (micro_moment)
            # that pins the exact panel; recap/Q&A beats are strings → pin is None → matcher.
            raw_beats = scene.get("visual_beats") or []
            beats = [_vb_text(c) for c in raw_beats if _vb_text(c)]
            beat_pins = [_vb_pin(c) for c in raw_beats if _vb_text(c)]
            # PLAN A (micro_moment): when the writer PINNED a panel per beat AND word_timestamps
            # are available, make each fragment its OWN unit — caption = the verbatim fragment,
            # span = the fragment's word-timed slice. The old _split_members_by_clause path
            # buckets ~7-word caption CHUNKS whose boundaries never fall on a clause edge, so a
            # pinned quote gets torn across two panels; aligning to words restores the 1:1 map.
            # Gate: MICRO_MOMENT only. Recap also pins its fragments now (via review-locks) but
            # keeps the _split_members_by_clause path below (consistent caption/timing with its
            # own unlocked scenes; the pin is still carried through as `beat_pins`).
            frag_pinned = any(isinstance(b, dict) and b.get("page") for b in raw_beats)
            if (frag_pinned and word_timestamps and len(beats) > 1
                    and str(narration.get("mode") or "") == "micro_moment"):
                clause_texts, slices, pins = _fragment_units(
                    beats, beat_pins, members, word_timestamps)
            elif len(beats) > 1:
                buckets = _split_members_by_clause(members, beats)   # takes clause TEXTS
                triples = [(c, b, p) for c, b, p in zip(beats, buckets, beat_pins) if b] \
                    or [(scene_text, members, None)]
                clause_texts = [c for c, _b, _p in triples]
                slices = [b for _c, b, _p in triples]
                pins = [p for _c, _b, p in triples]
            else:
                # one held panel per scene; a single pinned micro beat still pins that panel.
                clause_texts, slices = [scene_text], [members]
                pins = [_vb_pin(raw_beats[0]) if raw_beats else None]
        for ct, sl, pin in zip(clause_texts, slices, pins):
            spoken = " ".join(str(m[0]) for m in sl).strip() or ct
            units.append((scene, sl, spoken, pin))

    # PIN-DUP MERGE (Master 2026-07-11): the MICRO writer occasionally PINS two CONSECUTIVE
    # fragments to the SAME (page,panel) — two clauses about one drawn moment. Rendered as-is that
    # is two back-to-back shots on an identical crop (a repeated panel; the ComicCut autopsy shows
    # zero consecutive-repeat panels). Collapse each same-pin run into ONE unit (members + caption
    # + dur joined) → one panel, one continuous shot. MICRO_MOMENT ONLY: a recap's only pins come
    # from Master's review-locks (a scene locked to one panel across its fragments is INTENTIONAL —
    # keep those as separate shots with varied motion, never merge them).
    if str(narration.get("mode") or "") == "micro_moment" and any(
            pin is not None for _s, _sl, _sp, pin in units):
        merged_units: list = []
        for u in units:
            prev = merged_units[-1] if merged_units else None
            if prev is not None and u[3] is not None and prev[3] == u[3] and prev[0] is u[0]:
                merged_units[-1] = (prev[0], prev[1] + u[1],
                                    f"{prev[2]} {u[2]}".strip(), prev[3])
            else:
                merged_units.append(u)
        units = merged_units

    # ── Word-aligned retime (micro_moment) ───────────────────────────────────────────────────
    # Stage-4 caption_chunks / scene_timings can DRIFT a second or two from the actual TTS word
    # timings (a scene's tail words get bucketed under the NEXT scene — the chunker glues the
    # next scene's chunk TEXT onto this scene's trailing-word TIMESTAMPS). Both _fragment_units
    # (window bounded by the drifted scene span) and the caption-chunk member durations then
    # inherit that drift, so every panel cut after it lands EARLY and the video races ahead of
    # the voiceover (measured: -1.7s by scene 2, -4.8s by scene 5). word_timestamps.json is the
    # ground truth. Re-time each render unit so its shot STARTS exactly when its first spoken
    # word is heard: the ordered unit captions are a verbatim partition of the narration, so
    # align them to the full word stream with ONE forward pointer and set each unit's video span
    # to [this-word-start, next-word-start] (unit 0 owns the lead-in from t=0; the last unit runs
    # to the final word). MICRO-ONLY so recap/Q&A stay byte-identical (their units carry joined
    # caption CHUNKS retimed by the unchanged clause/locked paths — asserted elsewhere). No-op if
    # word_timestamps is missing or the two token streams diverge (safe fallback to old spans).
    if str(narration.get("mode") or "") == "micro_moment" and word_timestamps:
        _retime_units_to_words(units, word_timestamps)

    # WRITER-PICKS-PANEL: a unit whose beat pinned a valid (page,panel) is assigned that panel
    # DIRECTLY (the writer authored the 1:1 narration↔panel map, so skip the cosine matcher for
    # it). Unpinned units — and a pin to a panel not in the pool — flow through the normal
    # content matcher. Recap/Q&A beats are strings → every pin is None → matcher_units == units
    # in original order → _match_panels sees exactly the old input → byte-identical output.
    pool_by_key: dict | None = None
    assigned: list = [None] * len(units)
    matcher_idx: list[int] = []
    matcher_units: list[tuple[dict, str]] = []
    for idx, (scene, _sl, spoken, pin) in enumerate(units):
        if pin is not None:
            if pool_by_key is None:
                pool_by_key = {key: (panel, src)
                               for key, panel, src, _tb in _panel_pool(pages_by_number or {})}
            hit = pool_by_key.get(pin)
            if hit is not None:
                assigned[idx] = hit
                continue
            print(f"[stage5] micro: visual-beat pin p{pin[0]}/{pin[1]} not in panel pool "
                  f"— matcher fallback")
        matcher_idx.append(idx)
        matcher_units.append((scene, spoken))
    if matcher_units:
        matched = _match_panels(matcher_units, pages_by_number or {}, cluster_to_name or {},
                                project=project, narration=narration)
        for k, idx in enumerate(matcher_idx):
            assigned[idx] = matched[k]

    shots: list[Shot] = []
    shot_id = 0
    audit_whole = []
    seq = 0   # per-UNIT motion-rotation counter — variety BETWEEN scenes for single shots
    # micro_moment fills the 9:16 frame (cover-crop landscape) rather than contain+blur, gated by
    # the narration's own mode — no env needed; guards live in _should_blur_bg.
    fit_fill = str(narration.get("mode") or "") == "micro_moment"
    for (scene, slice_members, _spoken, _pin), (panel, source_image) in zip(units, assigned):
        slice_dur = sum(m[2] for m in slice_members)
        is_intro = bool(scene.get("is_intro"))
        is_outro = bool(scene.get("is_outro"))
        is_whole = bool(panel is not None and panel.get("_whole_page"))
        if is_whole and not is_intro:
            audit_whole.append(int(scene.get("scene_id") or 0))
        text_bboxes: list[dict] = []
        if panel is None:
            bbox = scene.get("panel_bbox") or {}
            source_image = source_image or str(scene.get("source_image") or "")
        else:
            bbox = panel.get("bbox") or {}
            text_bboxes = _panel_text_bboxes(panel, pages_by_number or {})
        panel_bbox = {"x": int(bbox.get("x", 0)), "y": int(bbox.get("y", 0)),
                      "w": int(bbox.get("w", 0)), "h": int(bbox.get("h", 0))}
        caption_text = " ".join(str(m[0]) for m in slice_members).strip()
        scene_id = int(scene.get("scene_id") or 1)
        # Skip the mirror when it would reverse legible text: a whole-page or a no-panel
        # render shows uncleaned bubbles, and a panel whose art carries readable text
        # (sign/monitor/nameplate) would flip into gibberish. The COLD-OPEN (is_intro) is
        # ALWAYS un-mirrored: frame 1 is too retention-critical to risk backwards lettering,
        # and _panel_has_critical_text provably misses small in-panel dialogue strips (the
        # spider-man "ONE I'M SLYDE" opener mirrored = instant AI-slop). A cold-open frame
        # gains nothing from the flip's dedup purpose anyway (there is no earlier frame).
        no_mirror = (panel is None or is_whole
                     or _panel_has_critical_text(panel) or is_intro)
        keep_contain = _panel_has_critical_text(panel)

        def _emit(dur: float, motion: str) -> None:
            nonlocal shot_id
            shots.append(Shot(
                shot_id=shot_id, scene_id=scene_id, duration_seconds=dur,
                panel_bbox=dict(panel_bbox), source_image=source_image, motion=motion,
                text_bboxes=text_bboxes, caption_text=caption_text,
                no_mirror=no_mirror, keep_contain=keep_contain, is_intro=is_intro,
                fit_fill=fit_fill,
            ))
            shot_id += 1

        # Intro (cold-open hook), outro, and whole-page (slow reveal) are each ONE deliberate
        # framing: never sub-split, never reframed. All get a one-directional zoom_in — the
        # loop-close zoom_out is applied ONLY by _close_loop (post-build), so no normal shot
        # ever pulls back (Master 2026-07-11).
        if is_intro or is_outro or is_whole:
            _emit(max(0.4, slice_dur), "zoom_in")
            seq += 1
            continue
        if panel is None:
            # No matched panel → rendering the scene's fallback bbox (often a whole page):
            # don't split or reframe an unreliable region. One shot, rotating motion.
            _emit(max(0.4, slice_dur), _choose_motion(panel, slice_dur, seq=seq))
            seq += 1
            continue

        # Normal story shot: split a long hold into competitor-cadence sub-shots. They share
        # scene_id, so _group_shots_by_scene hard-cuts between them (dissolve only BETWEEN
        # scenes). Each sub-shot is a tightening framing on the same panel; durations sum
        # EXACTLY to slice_dur so audio sync is unchanged.
        sub_durs = _split_shot_durations(max(0.4, slice_dur))
        multi = len(sub_durs) > 1
        for k, sub_dur in enumerate(sub_durs):
            motion = (_SUBSHOT_FRAMINGS[k % len(_SUBSHOT_FRAMINGS)] if multi
                      else _choose_motion(panel, slice_dur, seq=seq))
            _emit(sub_dur, motion)
        seq += 1
    if audit_whole:
        print(f"[stage5] panel-match: {len(audit_whole)} scene(s) → whole-page "
              f"fallback (scene_ids {audit_whole})")
    return shots


# ── Q&A caption-chunk render, restricted to Master's locked panels ───────────
# Minimum on-screen time for a Q&A shot AFTER merging (below this, a shot is absorbed into a
# same-scene neighbor). Sub-1s hard-cut Ken-Burns shots read as "jump like crazy" (Master v3).
QA_MIN_SHOT_SECONDS = float(os.getenv("QA_MIN_SHOT_SECONDS", "1.5"))

# How many top-ranked subject panels the Q&A intro cycles through (multi-panel hook of
# the QUESTION'S subject character). The outro then uses the next unused subject panel.
QA_INTRO_SUBJECT_PANELS = max(1, int(os.getenv("QA_INTRO_SUBJECT_PANELS", "3")))


def _review_locks(project: str | None) -> dict:
    """The per-key locks map ({"<key>": lock_dict}) from review/locks.json for ANY mode, or {}
    when the project is missing / has no lock with >=1 panel. Keys are "<scene_id>" (recap/Q&A
    scene rows), "<scene_id>:<frag_idx>" (micro fragment rows), or "intro" (cold-open row) — see
    review_gate.build_candidates. Never raises. This is the mode-agnostic reader; _qa_locks is the
    answer_research-only wrapper that gates the legacy Q&A locked path."""
    if not project:
        return {}
    try:
        from ..review_gate import load_state, lock_panels
        locks = (load_state(project) or {}).get("locks") or {}
    except Exception:
        return {}
    return locks if any(lock_panels(lk) for lk in locks.values()) else {}


def _qa_locks(project: str | None) -> dict:
    """GATE for the Q&A chunk-level locked render. Returns _review_locks ONLY for an
    answer_research (Q&A) project (else {}), so the Q&A locked builder path is byte-identical to
    before this became a wrapper — recap/micro never route through it. Never raises."""
    if not project:
        return {}
    try:
        from ..review_gate import _plot_source
        if _plot_source(project) != "answer_research":
            return {}
    except Exception:
        return {}
    return _review_locks(project)


def _qa_drawable_moments(project: str | None, pages_by_number: dict[int, dict],
                         scenes: list[dict]) -> dict[int, str]:
    """Per-scene drawable_moment (the answer item's precise VISUAL target), resolved through the
    SAME page_ref→issue→item map review_gate/sentence_match use, so the Q&A chunk query can blend
    it in (parity with the sentence matcher). {} on any error → queries fall back to narration."""
    if not project:
        return {}
    try:
        from ..review_gate import _beat_source, _load_json, _project_root
        root = _project_root(project)
        comic_ctx = _load_json(root / "comic_context.json")
        answer_ctx = _load_json(root / "answer_context.json")
        page_to_issue = {int(p.get("page_number", 0) or 0): str(p.get("issue_label", "") or "")
                         for p in pages_by_number.values()}
        multi_issue = len({v for v in page_to_issue.values() if v}) > 1
        out: dict[int, str] = {}
        for s in scenes:
            issue = page_to_issue.get(int(s.get("page_ref", 0) or 0), "") if multi_issue else ""
            dm = _beat_source(s, comic_ctx, answer_ctx, issue_label=issue).get("drawable_moment", "")
            out[int(s.get("scene_id") or 0)] = str(dm or "")
        return out
    except Exception:
        return {}


# ─── Custom images (Master-added; certain to appear, cosine only picks the BEAT) ──────────
# Design (Master-approved): an image Master adds himself in the review UI is GUARANTEED to
# show up somewhere in the video — unlike a matched comic panel, it is NEVER filtered out by
# a cosine floor. Cosine only decides WHICH BEAT it lands on (assign_custom_images), and a
# Master hand-lock ({"custom_image": path} in locks.json) skips that argmax entirely for that
# one image. This whole block is a no-op (returns {} / [] immediately) for any project with no
# review/custom/custom_images.json — so a project that never used this feature renders on the
# EXACT same path as before it existed.

def _load_custom_images(project: str | None) -> list[dict]:
    """review/custom/custom_images.json → its "images" list ([] if missing/project None/
    malformed). Never raises. Written by ui/custom_image.add_custom_image; read here as
    plain JSON (stages/ never imports ui/ — same decoupling as candidates.json/locks.json)."""
    if not project:
        return []
    try:
        from ..review_gate import _load_json, _project_root
        p = _project_root(project) / "review" / "custom" / "custom_images.json"
        return list(_load_json(p).get("images") or [])
    except Exception:
        return []


def _custom_locks(project: str | None) -> dict[str, str]:
    """{beat_key: custom-image file} for every beat Master hand-locked to a custom image.
    Reads locks.json DIRECTLY (not the panel-only _review_locks gate, which would report {}
    for a project whose ONLY locks are custom-image ones — lock_panels() is [] for that
    shape). {} on any error/missing project. Never raises."""
    if not project:
        return {}
    try:
        from ..review_gate import lock_custom_image, load_state
        locks = (load_state(project) or {}).get("locks") or {}
    except Exception:
        return {}
    out: dict[str, str] = {}
    for key, lock in locks.items():
        ci = lock_custom_image(lock)
        if ci:
            out[str(key)] = ci
    return out


def _load_custom_image_vectors(project: str | None) -> dict:
    """{"review/custom/<file>": np.ndarray} SigLIP vectors for every custom:true point in the
    project's per-project IMAGE collection — the argmax fallback for a custom image whose VLM
    describe never completed (no desc → no text cosine). {} on any failure/missing collection/
    Qdrant down/SigLIP unavailable. Never raises. Loaded lazily by _resolve_custom_images only
    when some image actually needs it (most runs have a desc and never touch Qdrant here)."""
    if not project:
        return {}
    try:
        import numpy as np
        from .. import _img_index, _qdrant
        c = _qdrant.client()
        name = _img_index._img_collection_name(project)
        if not c.collection_exists(name):
            return {}
        out: dict = {}
        offset = None
        while True:
            recs, offset = c.scroll(name, limit=256, with_payload=True, with_vectors=True,
                                    offset=offset)
            for p in recs:
                pl = p.payload or {}
                if pl.get("custom") and p.vector is not None:
                    out[str(pl.get("image_path", ""))] = np.asarray(p.vector, dtype="float32")
            if offset is None:
                break
        return out
    except Exception:
        return {}


def assign_custom_images(beats: list[tuple[str, str]], images: list[dict],
                         locked: dict[str, str], *, score_fn: Callable) -> dict[str, str]:
    """Pure greedy assignment: decide which BEAT each custom image lands on.

    `beats` = [(beat_key, text), ...] in story order. `images` = the custom_images.json
    "images" list (each a dict with at least "file"; may carry "desc"). `locked` =
    {beat_key: file} from Master's hand-locks (_custom_locks) — resolved DIRECTLY, no argmax.
    `score_fn(beat_text, image_dict) -> float` scores every remaining (beat, image) pair
    (real caller: cosine on the image's VLM desc, SigLIP-vector fallback — see
    _score_custom_image; tests inject a stub for determinism, same idiom as this repo's
    _panel_content_score stubs).

    Every UNLOCKED image is greedily assigned to its best still-free beat, highest score
    first — so when two images both want the SAME beat, the higher-cosine one wins it and
    the other falls through to its next-best free beat ("nhiều ảnh tranh 1 beat"). An image
    that scores 0.0 everywhere (empty beats / totally unavailable embeddings) still gets
    assigned something if any beat remains free — a custom image is NEVER dropped, only its
    beat placement can be a coin-flip in the worst case (Master added it → it WILL appear).

    Returns {beat_key: file} — every beat that ends up with a custom image, locked ∪
    argmax-assigned. {} when there are no images or no beats.

    # ponytail: O(images × beats) greedy sort, not Hungarian/scipy — a project has a handful
    # of custom images at most, so this is plenty; upgrade to
    # scipy.optimize.linear_sum_assignment only if that ever stops being true.
    """
    out: dict[str, str] = {}
    claimed_beats: set[str] = set()
    unlocked: list[dict] = []
    for img in images:
        f = img.get("file")
        if not f:
            continue
        locked_beat = next((bk for bk, lf in locked.items() if lf == f), None)
        if locked_beat is not None:
            out[locked_beat] = f
            claimed_beats.add(locked_beat)
        else:
            unlocked.append(img)
    if not unlocked or not beats:
        return out

    pairs: list[tuple[float, str, str]] = []   # (score, file, beat_key)
    for img in unlocked:
        f = str(img.get("file"))
        for bk, text in beats:
            pairs.append((float(score_fn(text, img)), f, bk))
    pairs.sort(key=lambda p: p[0], reverse=True)

    assigned_images: set[str] = set()
    for _score, f, bk in pairs:
        if f in assigned_images or bk in claimed_beats:
            continue
        out[bk] = f
        assigned_images.add(f)
        claimed_beats.add(bk)
    return out


def _score_custom_image(beat_text: str, image: dict, *, project: str | None,
                        siglip_vecs: dict) -> float:
    """Real scoring for assign_custom_images: cosine(beat_text, image's VLM desc) via the
    shared text-embed backend (Qwen/Gemini/local, whatever Stage 5 already uses); falls back
    to SigLIP image-vector · SigLIP text-embed(beat_text) when the image has no desc yet
    (enrich pending/failed). 0.0 if neither signal is available — never raises."""
    desc = str(image.get("desc") or "").strip()
    if desc:
        try:
            from .._embedding import semantic_sim
            return semantic_sim(beat_text, desc)
        except Exception:
            return 0.0
    vec = siglip_vecs.get(str(image.get("file", "")))
    if vec is None:
        return 0.0
    try:
        import numpy as np
        from .. import _img_index
        txt_vecs = _img_index.embed_texts([beat_text])
        return float(np.dot(vec, txt_vecs[0])) if txt_vecs is not None else 0.0
    except Exception:
        return 0.0


def _beat_rows_for_custom(narration: dict) -> list[tuple[str, str]]:
    """[(beat_key, text), ...] for every beat Master can lock a custom image to, in the SAME
    beat_key scheme review_gate.build_candidates writes to locks.json ("intro" | "<scene_id>"
    | "<scene_id>:<frag_idx>" for a micro fragment) — so a lock written by the review UI and
    the argmax pool here always agree on identity."""
    scenes = narration.get("scenes") or []
    micro = str(narration.get("mode") or "") == "micro_moment"
    rows: list[tuple[str, str]] = []
    intro = next((s for s in scenes if s.get("is_intro")), None)
    if intro is not None:
        rows.append(("intro", str(intro.get("text", "") or "")))
    for s in scenes:
        if s.get("is_intro") or s.get("is_outro"):
            continue
        sid = int(s.get("scene_id") or 0)
        raw_beats = (s.get("visual_beats") or []) if micro else []
        frags = [b for b in raw_beats if _vb_text(b)]
        if micro and frags:
            for fi, b in enumerate(frags):
                rows.append((f"{sid}:{fi}", _vb_text(b)))
        else:
            rows.append((str(sid), str(s.get("text", "") or "")))
    return rows


def _resolve_custom_images(project: str | None, narration: dict) -> dict[str, str]:
    """{beat_key: ABSOLUTE file path} for every beat that gets a custom image this run.
    {} (no-op) when the project has no custom images at all — the common case, and the
    contract that keeps every project without this feature byte-identical."""
    images = _load_custom_images(project)
    if not images:
        return {}
    from ..review_gate import _project_root
    root = _project_root(project)
    locked = _custom_locks(project)
    beats = _beat_rows_for_custom(narration)
    siglip_vecs: dict = {}
    loaded_siglip = False

    def _score(text, img):
        nonlocal loaded_siglip, siglip_vecs
        if not str(img.get("desc") or "").strip() and not loaded_siglip:
            siglip_vecs = _load_custom_image_vectors(project)
            loaded_siglip = True
        return _score_custom_image(text, img, project=project, siglip_vecs=siglip_vecs)

    by_key = assign_custom_images(beats, images, locked, score_fn=_score)
    return {bk: str(root / f) for bk, f in by_key.items()}


def _apply_custom_images_to_shots(shots: list, custom_map: dict[str, str]) -> None:
    """Stamp each assigned beat's custom image onto its shot(s) — OVERRIDING whatever the
    matcher picked for that beat (render_shot then loads the file directly instead of
    cropping panel_bbox out of source_image; see Shot.custom_image). Grouping mirrors
    review_gate's beat_key scheme: "intro" → the is_intro shot; "<scene_id>" → EVERY shot of
    that scene (a scene's sub-shots/time-splits all share one panel already, same as an
    ordinary review lock); "<scene_id>:<frag_idx>" → the frag_idx-th shot of that scene, in
    render order (the FRAGMENT/MICRO GATE invariant is 1 fragment = 1 shot). Unmatched/out-
    of-range keys are skipped, never raised — a stale lock from a re-narrated project should
    not crash the render.

    # ponytail: this runs AFTER the strategy-specific builder + matcher, as a flat override
    # pass — same end state as pre-empting the matcher (the spec's "TRƯỚC matcher" framing),
    # far smaller diff than threading a bypass through all 4 builder code paths.
    """
    if not custom_map or not shots:
        return
    groups: dict[int, list[int]] = {}
    for i, sh in enumerate(shots):
        sid = sh.beat_id if getattr(sh, "beat_id", None) is not None else sh.scene_id
        groups.setdefault(int(sid), []).append(i)
    intro_idx = next((i for i, sh in enumerate(shots) if getattr(sh, "is_intro", False)), None)

    for beat_key, abs_path in custom_map.items():
        if beat_key == "intro":
            if intro_idx is not None:
                shots[intro_idx].custom_image = abs_path
            continue
        sid_s, _, frag_s = beat_key.partition(":")
        try:
            sid = int(sid_s)
        except ValueError:
            continue
        idxs = groups.get(sid)
        if not idxs:
            continue
        if frag_s:
            try:
                fi = int(frag_s)
            except ValueError:
                continue
            if 0 <= fi < len(idxs):
                shots[idxs[fi]].custom_image = abs_path
        else:
            for i in idxs:
                shots[i].custom_image = abs_path


def _seg_bbox_key(panel: dict | None) -> tuple:
    """A panel's bbox as a hashable key so consecutive chunks on the SAME rendered panel merge."""
    bb = (panel or {}).get("bbox") or {}
    return (int(bb.get("x", 0) or 0), int(bb.get("y", 0) or 0),
            int(bb.get("w", 0) or 0), int(bb.get("h", 0) or 0))


def _partition_chunks(members: list, k: int, min_seconds: float) -> list[tuple[str, float, float]]:
    """Split a beat's caption chunks into AT MOST K CONTIGUOUS time-groups of roughly EQUAL
    duration (each group targets its even share of the time left, floored at `min_seconds`).
    The old rule closed a group the moment it hit `min_seconds` and dumped the whole remainder
    on the LAST group — measured on penance-stare scene 5 (14.2s beat, 3 locked panels) that
    gave [3.35, 2.06, 8.77]s: two quick cuts then a near-9s freeze on the final panel of every
    item. Even-share targets give ≈[4.7, 4.7, 4.7]s at the same chunk boundaries. A short
    leading/trailing chunk still folds into its neighbor (never a sub-min group → no jump).
    Returns [(concat_text, start, dur), ...] (len ≤ K); order preserved — chunks are
    audio-locked, never reordered."""
    if k <= 1 or len(members) <= 1:
        return ([(" ".join(m[0] for m in members).strip(),
                  members[0][1], sum(m[2] for m in members))] if members else [])
    total = sum(m[2] for m in members)
    groups: list[list] = []
    cur: list = []
    acc = 0.0
    done = 0.0
    for m in members:
        cur.append(m)
        acc += m[2]
        # Even share of the REMAINING time across the REMAINING groups (recomputed each
        # close, so a chunk-boundary overshoot in one group shrinks the next targets).
        target = max(min_seconds, (total - done) / (k - len(groups)))
        if acc >= target and len(groups) < k - 1:
            groups.append(cur)
            done += acc
            cur, acc = [], 0.0
    if cur:
        if groups and acc < min_seconds:                 # short tail → fold into the previous group
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    return [(" ".join(x[0] for x in g).strip(), g[0][1], sum(x[2] for x in g)) for g in groups]


def _merge_locked_segments(segs: list[dict], min_seconds: float) -> list[dict]:
    """Collapse the per-chunk Q&A picks into shots (fixes the "jump like crazy" from a panel
    repeated across many sub-1s hard-cut chunks):
      1. merge ADJACENT chunks on the SAME (scene, panel) → one hold (dur summed, caption joined);
      2. absorb any segment shorter than `min_seconds` into its LONGER SAME-SCENE neighbor (that
         neighbor's panel wins — a <1.5s flash of a different panel is exactly the jump we remove).
    Never merges across a narration-scene boundary (different Q&A items must stay separate).
    Mutates copies; returns the merged list. ponytail: O(n²) restart loop, n≈chunks (~33) so trivial."""
    merged: list[dict] = []
    for s in segs:
        if (merged and merged[-1]["sid"] == s["sid"] and merged[-1]["src"] == s["src"]
                and _seg_bbox_key(merged[-1]["panel"]) == _seg_bbox_key(s["panel"])):
            merged[-1]["dur"] += s["dur"]
            merged[-1]["text"] = f"{merged[-1]['text']} {s['text']}".strip()
        else:
            merged.append(dict(s))

    changed = True
    while changed:
        changed = False
        for i, seg in enumerate(merged):
            if seg["dur"] >= min_seconds or len(merged) == 1:
                continue
            sid = seg["sid"]
            left = merged[i - 1] if i - 1 >= 0 and merged[i - 1]["sid"] == sid else None
            right = merged[i + 1] if i + 1 < len(merged) and merged[i + 1]["sid"] == sid else None
            if left is None and right is None:
                continue                      # only shot in its scene → leave (can't extend w/o desync)
            if left and right:
                target = left if left["dur"] >= right["dur"] else right
            else:
                target = left or right
            target["dur"] += seg["dur"]
            target["text"] = (f"{target['text']} {seg['text']}".strip() if target is left
                              else f"{seg['text']} {target['text']}".strip())
            del merged[i]
            changed = True
            break
    return merged


def _qa_vlm_rerank(picks: list[dict], texts: list[str], cands: list, *, log) -> None:
    """Feature-D-style VLM rerank for the Q&A LOCKED pool. For each chunk whose chosen locked
    panel is a WEAK cosine match (score < PANEL_RERANK_COS_CEIL), a Claude vision judge looks at
    the scene's locked panel ART and picks the one that best depicts the chunk — overriding the
    cosine pick (fixes "scene not really match": cosine picks within the lock set but doesn't
    UNDERSTAND the line). The pool is tiny (2-5 locked panels) so this is cheap. Mutates `picks`
    in place. No-op when the SDK is unavailable (_vlm_rerank → None), or when a pick has no score.
    Reuses _match_panels' own _vlm_rerank helper — no new rerank logic."""
    if not cands:
        return
    rerank_cands = [(j, cands[j][2], cands[j][1], cands[j][3]) for j in range(len(cands))]
    for i, p in enumerate(picks):
        sc = p.get("score")
        if sc is None or float(sc) >= PANEL_RERANK_COS_CEIL:
            continue                          # strong (or unscored) match → trust cosine
        pick = _vlm_rerank(texts[i], rerank_cands, log=log)
        if pick is None or pick == -1:
            continue                          # undecided / "none" → keep cosine (panel is Master-locked)
        key = cands[pick][0]
        p["page"], p["panel"] = int(key[0]), int(key[1])


_SUBJECT_STOPWORDS = frozenset((
    "The", "This", "That", "There", "They", "With", "From", "Doctor", "Man",
    "And", "But", "His", "Her", "Him", "She", "You", "Who", "What", "When",
    "One", "Two", "For", "Are", "Was", "Has", "Now", "All", "Not", "Panel",
))


def _qa_subject_panels(locked_cands: dict) -> list[tuple]:
    """The question's SUBJECT is the name recurring across Master's locked panels (in a
    "who beat X" video, X appears in every answer beat). Return that subject's locked
    panels as (panel, src), biggest-first — used to bookend the video (intro/outro) with
    an on-subject, Master-approved panel instead of a free-matcher spectacle pick. []
    when no clear recurring name."""
    import collections
    names: collections.Counter = collections.Counter()
    for cands in locked_cands.values():
        for (_key, panel, _src, _tb) in cands:
            desc = str(panel.get("description", ""))
            for tok in set(re.findall(r"\b[A-Z][a-z]{2,}\b", desc)):   # unique per panel
                if tok not in _SUBJECT_STOPWORDS:
                    names[tok] += 1
    if not names:
        return []
    subject, hits = names.most_common(1)[0]
    if hits < 2:                       # not recurring enough to trust as "the subject"
        return []
    out: list[tuple] = []
    for cands in locked_cands.values():
        for (_key, panel, src, _tb) in cands:
            if subject.lower() in str(panel.get("description", "")).lower():
                bb = panel.get("bbox") or {}
                area = int(bb.get("w", 0) or 0) * int(bb.get("h", 0) or 0)
                out.append((area, panel, src))
    out.sort(key=lambda t: t[0], reverse=True)
    return [(panel, src) for (_area, panel, src) in out]


def _qa_subject_sequence(project: str | None, entry_by_key: dict,
                         exclude: set) -> list[tuple]:
    """Ranked subject panels from projects/<slug>/subject_panels.json (built at preprocess
    from the question's derived subject), resolved to (panel, src) via the reading-order
    pool, score-best first, SKIPPING any panel already locked to a body beat (no-reuse) and
    any not present in the pool. [] when the file is missing/empty → the caller falls back
    to _qa_subject_panels. Never raises. This is the PRE-RANKED source the Q&A bookends
    prefer; _qa_subject_panels (recurring-name over locked panels) is the legacy fallback.

    EXCEPTION to no-reuse: a row flagged `force_intro: true` (set by the money-shot funnel's
    `_pin_money_intro` on the confirmed payoff panel) is NEVER dropped by `exclude`, even when
    it's also locked to a body beat — the ComicCut hook formula: the money/payoff panel is
    ALLOWED to spoiler the intro AND still play out in its body beat. `_pin_money_intro`
    inserts that row FIRST, so it naturally comes out first here too.

    FRAME-1 GATE (R2 2026-07-12): the returned order is force_intro-money > clean subject panels
    > the rest, so the bookend (esp. the frame-1 pick, subject_seq[0]) is NEVER a wide letterboxed
    strip or a tiny inset (batcave's manual row[0] p86/0 = 1920×860 aspect 2.23 → contain+blur
    band was the measured 3s swipe-away opener). A `force_intro` money panel STAYS first (money-bind
    beats the gate, matching _cold_open_panel's precedence); Master's manual ordering is preserved
    WITHIN each group. When every subject panel would letterbox/is tiny, the original ranked order is
    returned unchanged (byte-identical fallback). Gate here covers letterbox + tiny only (no page_tb
    for the bubble check); use COLD_OPEN_LOCK to force a specific frame-1 regardless."""
    if not project:
        return []
    try:
        from ..subject_panels import load_subject_panels
        ranked = (load_subject_panels(project) or {}).get("panels") or []
    except Exception:
        return []
    forced: list[tuple] = []   # force_intro money panels (gate-exempt, keep front)
    clean: list[tuple] = []    # gate-passing subject panels (good frame-1)
    dirty: list[tuple] = []    # letterbox / tiny subject panels (last resort)
    seen: set = set()
    for row in ranked:
        try:
            key = (int(row["page"]), int(row["panel"]))
        except (KeyError, TypeError, ValueError):
            continue
        if key in seen or key not in entry_by_key:
            continue
        if key in exclude and not row.get("force_intro"):
            continue
        seen.add(key)
        entry = entry_by_key[key]
        if row.get("force_intro"):
            forced.append(entry)
        elif _cold_open_gate_ok(entry[0], int(entry[0].get("_page_area", 0) or 0)):
            clean.append(entry)
        else:
            dirty.append(entry)
    return forced + clean + dirty


def _build_shots_per_chunk_locked(
    narration: dict,
    caption_chunks: list[dict],
    pages_by_number: dict[int, dict],
    scene_timings: list[dict],
    *,
    locks: dict,
    cluster_to_name: dict[int, str] | None = None,
    project: str | None = None,
) -> list[Shot]:
    """Q&A (answer_research) render restricted to each scene's Master-LOCKED panels. Each beat is
    SEGMENTED into K contiguous time-groups where K = min(#locked panels, #chunks, floor(beat_dur /
    QA_MIN_SHOT_SECONDS)) — so every shot lasts ≥ ~QA_MIN_SHOT_SECONDS and a beat shows at most as
    many panels as Master locked. Each group's text (blended with the scene's drawable_moment) is
    matched to a DISTINCT locked panel (Hungarian no-reuse via _match_sentences), and a WEAK match
    is re-judged by a Claude vision rerank over the same tiny locked pool (Feature-D parity via
    _vlm_rerank, PANEL_RERANK) — fixing "scene not really match". This replaces the old
    one-shot-per-chunk output (many sub-1s hard-cut Ken-Burns frames = "jump like crazy", Master
    v3) with a few ≥1.5s distinct-panel holds. Each emitted shot gets a UNIQUE scene_id so the
    assembler dissolves between them (same mechanism as inter-scene dissolve; XFADE_TRANSITION
    default "dissolve").

    Scenes with no usable lock — and any is_intro/is_outro scene — fall back to the normal
    per-scene _match_panels pick (cold-open / outro-loop / content), held across the whole beat."""
    scenes = narration.get("scenes") or []
    scenes_by_id = {int(s.get("scene_id") or i): s for i, s in enumerate(scenes, start=1)}
    groups = _chunks_grouped_by_scene(caption_chunks, scene_timings, scenes_by_id)

    from .. import _img_index
    from .._panel_index import load_vectors
    from ..review_gate import QA_PANEL_IMG_WEIGHT, lock_panels
    from ..sentence_match import _match_sentences

    # pool as 4-tuples for _match_sentences / rerank cands; (page,panel)->(panel,src) for the emit.
    pool = _panel_pool(pages_by_number or {})
    cand_by_key = {key: (key, panel, src, tb) for (key, panel, src, tb) in pool}
    entry_by_key = {key: (panel, src) for (key, panel, src, _tb) in pool}
    panel_vecs = load_vectors(project) if project else {}
    drawable_by_sid = _qa_drawable_moments(project, pages_by_number or {}, scenes)

    # Which scenes have usable locks (>=1 locked panel present in the preprocessed pool). Pool a
    # scene's locks from BOTH its scene-level key "<sid>" AND any per-fragment keys "<sid>:<frag>"
    # (recap/Q&A now emit visual_beats → the review UI locks a panel per fragment). De-dup, keep
    # order (scene lock first). A pure scene-lock project is byte-identical to before.
    locked_cands: dict[int, list] = {}
    for sid, sc in scenes_by_id.items():
        if sc.get("is_intro") or sc.get("is_outro"):
            continue
        sid_prefix = f"{sid}:"
        lock_entries = [locks.get(str(sid))] + [
            lk for kk, lk in locks.items() if str(kk).startswith(sid_prefix)]
        keys: list[tuple[int, int]] = []
        for le in lock_entries:
            for p in lock_panels(le):
                k = (int(p["page"]), int(p["panel"]))
                if k not in keys:
                    keys.append(k)
        cands = [cand_by_key[k] for k in keys if k in cand_by_key]
        if cands:
            locked_cands[sid] = cands

    # Fallback per-scene pick (cold-open / outro-loop / content) for the non-locked scenes,
    # in one _match_panels batch so the intro cold-open + outro loop-close still work.
    fb_scenes = [sc for sid, sc in scenes_by_id.items() if sid not in locked_cands]
    fb_units = [(sc, str(sc.get("text", "") or "")) for sc in fb_scenes]
    fb_assigned = (_match_panels(list(fb_units), pages_by_number or {},
                                 cluster_to_name or {}, project=project, narration=narration)
                   if fb_units else [])
    fb_pair = {int(sc.get("scene_id") or 0): pair for (sc, _t), pair in zip(fb_units, fb_assigned)}

    # Intro/outro should feature the QUESTION'S subject (the character the whole video is
    # about — "Juggernaut"), not whatever splash the free matcher liked. NEW path: the
    # pre-ranked subject_panels.json (built at preprocess from the question's subject) →
    # a MULTI-PANEL subject intro (top-N) + a subject outro. When that file is absent
    # (old projects), fall back to the legacy single-panel bookend from the recurring-
    # name-over-locked-panels pick — byte-identical to the pre-feature output. Exclude
    # panels already locked to a body beat so a bookend never re-shows one (no-reuse).
    #
    # The subject bookend is a Q&A concept (the video is ABOUT one character). A recap routed
    # through this same locked builder (Master 2026-07-14) has no "question subject", so it
    # SKIPS the bookend entirely and keeps its intro/outro on the cold-open scorer /
    # cold_open_lock via fb_pair — leaving the Q&A path byte-identical (qa_bookend True there).
    qa_bookend = False
    try:
        from ..review_gate import _plot_source
        qa_bookend = bool(project) and _plot_source(project) == "answer_research"
    except Exception:
        qa_bookend = False
    body_locked_keys = {c[0] for cands in locked_cands.values() for c in cands}
    subject_seq = _qa_subject_sequence(project, entry_by_key, exclude=body_locked_keys) if qa_bookend else []
    intro_panels: list[tuple] = []
    if subject_seq:
        intro_panels = subject_seq[:QA_INTRO_SUBJECT_PANELS]          # multi-panel hook
        outro_pair = subject_seq[min(QA_INTRO_SUBJECT_PANELS, len(subject_seq) - 1)]  # next unused
        for sid, sc in scenes_by_id.items():
            if sc.get("is_outro"):
                fb_pair[sid] = outro_pair
            # intro: handled specially in the loop below (multi-panel split)
    elif qa_bookend:
        subj_panels = _qa_subject_panels(locked_cands)                # legacy single-panel bookend
        if subj_panels:
            io_sids = sorted(sid for sid, sc in scenes_by_id.items()
                             if sc.get("is_intro") or sc.get("is_outro"))
            for i, sid in enumerate(io_sids):
                fb_pair[sid] = subj_panels[min(i, len(subj_panels) - 1)]

    # Segment each beat into K contiguous time-groups (K bounded by #locked panels, #chunks, and
    # beat_dur/min so every shot lasts ≥ ~min), match each group to a DISTINCT locked panel
    # (Hungarian no-reuse), then VLM-rerank the weak group picks. Trust the SigLIP image blend
    # more for the visual drawable_moment query (same bump as build_sentence_panels).
    segs: list[dict] = []
    _orig_img_w = _img_index.PANEL_IMG_WEIGHT
    _img_index.PANEL_IMG_WEIGHT = QA_PANEL_IMG_WEIGHT
    try:
        for scene, members in groups:
            sid = int(scene.get("scene_id") or 1)
            is_intro = bool(scene.get("is_intro"))
            is_outro = bool(scene.get("is_outro"))
            cands = locked_cands.get(sid)
            if is_intro and intro_panels:
                # Multi-panel subject hook: split the intro beat into ≤N contiguous
                # time-groups (K bounded by beat_dur/min like the body) and show a
                # distinct top-ranked subject panel in each — a moving intro of the
                # question's subject instead of one static splash.
                beat_dur = sum(m[2] for m in members)
                k = max(1, min(len(intro_panels), len(members),
                               int(beat_dur / QA_MIN_SHOT_SECONDS) or 1))
                for (text, _st, dur), (panel, src) in zip(
                        _partition_chunks(members, k, QA_MIN_SHOT_SECONDS), intro_panels):
                    segs.append({"sid": sid, "scene": scene, "panel": panel, "src": src,
                                 "text": text, "dur": max(0.0, dur),
                                 "is_intro": True, "is_outro": False})
                continue
            if not cands:
                # Non-locked scene (or outro): one held shot over the whole beat.
                pair = fb_pair.get(sid)
                panel, src = pair if pair else (None, "")
                segs.append({"sid": sid, "scene": scene, "panel": panel, "src": src,
                             "text": " ".join(m[0] for m in members).strip(),
                             "dur": sum(m[2] for m in members),
                             "is_intro": is_intro, "is_outro": is_outro})
                continue
            # A scene that emitted VISUAL BEATS (recap/Q&A now do) splits by FRAGMENT, not by an
            # even time-share: the number of shots follows the fragments (semantic seams), and each
            # fragment draws a panel from the locked pool — matched below, reused when Master locked
            # fewer panels than fragments (still distinct SHOTS, different motion). A scene with no
            # visual_beats keeps the byte-identical time-partition path. _split_members_by_clause is
            # verbatim word-position bucketing; frag_idx (the bucket's position BEFORE empty buckets
            # are dropped == its index into the scene's own visual_beats) rides along on each part so
            # a PER-FRAGMENT review lock ("<sid>:<frag_idx>") can be matched back to the exact part
            # it pins (see frag_pin below).
            frag_texts = [t for t in (_vb_text(b) for b in (scene.get("visual_beats") or [])) if t]
            parts: list[tuple[str, float, float, int | None]] = []   # (text, start, dur, frag_idx)
            if len(frag_texts) > 1:
                parts = [(" ".join(m[0] for m in b).strip(), b[0][1], sum(m[2] for m in b), fi)
                         for fi, b in enumerate(_split_members_by_clause(members, frag_texts)) if b]
            if not parts:
                beat_dur = sum(m[2] for m in members)
                k = max(1, min(len(cands), len(members), int(beat_dur / QA_MIN_SHOT_SECONDS) or 1))
                parts = [(t, s, d, None)
                         for t, s, d in _partition_chunks(members, k, QA_MIN_SHOT_SECONDS)]

            # PER-FRAGMENT PIN (bug fix, 2026-07-21): the review UI can lock ONE panel per FRAGMENT
            # ("<sid>:<frag_idx>" keys) — Master's own binding, not a hint. The Hungarian match below
            # is a free re-assignment over the scene's whole locked pool and has no idea a fragment
            # was individually pinned, so it can (and did, on mephisto-defeated) swap two fragments'
            # panels. Resolve each part's pin directly; only a key that maps into THIS scene's own
            # preprocessed pool counts (a stale/foreign key is ignored — matcher fills that fragment
            # instead, same as an unlocked one).
            frag_pin: dict[int, tuple[int, int]] = {}
            for _t, _s, _d, fi in parts:
                if fi is None:
                    continue
                ps = lock_panels(locks.get(f"{sid}:{fi}"))
                if not ps:
                    continue
                pkey = (int(ps[0]["page"]), int(ps[0]["panel"]))
                if pkey in cand_by_key:
                    frag_pin[fi] = pkey

            if frag_pin and all(fi in frag_pin for _t, _s, _d, fi in parts):
                # EVERY fragment is individually pinned — Master's per-fragment picks are final.
                # Skip the matcher, the VLM rerank, AND the no-reuse guard below entirely: those are
                # heuristics for a FREE assignment and must never override an explicit hand pick
                # (Master pinning the same panel twice in a row is deliberate, not a duplicate to
                # dedupe away).
                for text, _st, dur, fi in parts:
                    _key, panel, src, _tb = cand_by_key[frag_pin[fi]]
                    segs.append({"sid": sid, "scene": scene, "panel": panel, "src": src,
                                 "text": text, "dur": max(0.0, dur),
                                 "is_intro": is_intro, "is_outro": is_outro})
                print(f"[stage5] qa-locked: scene {sid} pinned per-fragment "
                      f"({len(parts)} shots, no matcher)")
                continue

            texts = [p[0] for p in parts]
            spans = [(p[1], p[1] + p[2]) for p in parts]
            if frag_pin:
                # PARTIAL pin: some fragments are individually pinned, the rest are not. Pin those
                # directly; run the matcher (+ rerank + no-reuse guard) only on the UNPINNED
                # fragments, over the locked panels NOT already spent on a pin.
                pinned_idx = {i: frag_pin[fi] for i, (_t, _s, _d, fi) in enumerate(parts)
                              if fi in frag_pin}
                free_idx = [i for i in range(len(parts)) if i not in pinned_idx]
                used_by_pins = set(pinned_idx.values())
                sub_cands = [c for c in cands if c[0] not in used_by_pins] or cands
                sub_texts = [texts[i] for i in free_idx]
                sub_spans = [spans[i] for i in free_idx]
                sub_picks = _match_sentences(
                    sub_texts, sub_spans, scene, sub_cands, panel_vecs, project or "",
                    log=print, drawable_moment=drawable_by_sid.get(sid, ""),
                    always_assign=True) if free_idx else []
                if PANEL_RERANK and free_idx:
                    _qa_vlm_rerank(sub_picks, sub_texts, sub_cands, log=print)
                picks: list = [None] * len(parts)
                for i, (pg, pn) in pinned_idx.items():
                    picks[i] = {"page": pg, "panel": pn}
                for i, p in zip(free_idx, sub_picks):
                    picks[i] = p
                locked_keys = [c[0] for c in sub_cands]
                used_keys: set = set(used_by_pins)
                for i in free_idx:
                    p = picks[i]
                    key = (p.get("page"), p.get("panel"))
                    if key in used_keys:
                        for lk in locked_keys:
                            if lk not in used_keys:
                                p["page"], p["panel"] = int(lk[0]), int(lk[1])
                                key = lk
                                break
                    used_keys.add(key)
                print(f"[stage5] qa-locked: scene {sid} partial pin "
                      f"({len(pinned_idx)}/{len(parts)} fragments)")
            else:
                picks = _match_sentences(texts, spans, scene, cands, panel_vecs, project or "",
                                         log=print, drawable_moment=drawable_by_sid.get(sid, ""),
                                         always_assign=True)
                if PANEL_RERANK:
                    _qa_vlm_rerank(picks, texts, cands, log=print)
                # No-reuse across THIS beat: the VLM rerank can collapse two groups onto one
                # locked panel, and a NON-adjacent repeat (A-B-A) slips past _merge_locked_segments
                # (adjacent-only) → the same panel renders twice = duplicate scene. Reassign any
                # duplicate pick to a locked panel not yet used in this beat (K ≤ #locked, so an
                # unused one always exists) → Master's N locked panels yield up to N DISTINCT shots.
                locked_keys = [c[0] for c in cands]
                used_keys = set()
                for p in picks:
                    key = (p.get("page"), p.get("panel"))
                    if key in used_keys:
                        for lk in locked_keys:
                            if lk not in used_keys:
                                p["page"], p["panel"] = int(lk[0]), int(lk[1])
                                key = lk
                                break
                    used_keys.add(key)

            for (text, _st, dur, _fi), p in zip(parts, picks):
                pair = entry_by_key.get((p["page"], p["panel"])) if p["page"] is not None else None
                panel, src = pair if pair else (None, "")
                segs.append({"sid": sid, "scene": scene, "panel": panel, "src": src,
                             "text": text, "dur": max(0.0, dur),
                             "is_intro": is_intro, "is_outro": is_outro})
    finally:
        _img_index.PANEL_IMG_WEIGHT = _orig_img_w

    # Safety net: merge any same-panel adjacency (e.g. the VLM collapsed two groups onto one panel)
    # and absorb a group that still fell under the minimum. Usually a no-op — K already bounds it.
    segs = _merge_locked_segments(segs, QA_MIN_SHOT_SECONDS)

    # Emit one shot per merged segment. UNIQUE scene_id per shot → the assembler treats each as
    # its own clip and dissolves between them (XFADE_TRANSITION default "dissolve").
    shots: list[Shot] = []
    for k, seg in enumerate(segs):
        panel, src = seg["panel"], seg["src"]
        is_intro, is_outro = seg["is_intro"], seg["is_outro"]
        is_whole = bool(panel is not None and panel.get("_whole_page"))
        pb, src2, text_bboxes, no_mirror, keep_contain = _shot_fields(
            panel, src, seg["scene"], is_intro, pages_by_number or {})
        if is_intro or is_outro or is_whole:
            motion = "zoom_in"   # loop-close zoom_out is applied only by _close_loop (no normal pull-back)
        else:
            motion = _choose_motion(panel, seg["dur"], seq=k)
        shots.append(Shot(
            shot_id=k, scene_id=k + 1, duration_seconds=max(0.4, seg["dur"]),
            panel_bbox=pb, source_image=src2, motion=motion,
            text_bboxes=text_bboxes, caption_text=seg["text"],
            no_mirror=no_mirror, keep_contain=keep_contain, is_intro=is_intro,
            beat_id=int(seg["sid"]),   # real narration scene → beat-boundary-only effects
        ))
    return shots


# ── Q&A sentence-driven render (explore_answer only; legacy fallback) ─────────
def _load_sentence_panels(project: str | None) -> dict | None:
    """GATE for the Q&A sentence-driven render. Returns the parsed
    review/sentence_panels.json ONLY for an explore_answer project
    (comic_context.plot_source == "answer_research") that actually carries the file.
    Every other project — recaps, or a Q&A project before the headless sentence-match
    step has run — returns None, so build_shots keeps its unchanged per-chunk/per-scene
    path (byte-for-byte identical output)."""
    if not project:
        return None
    from config import PROJECTS_ROOT
    root = PROJECTS_ROOT / project
    ctx_path = root / "comic_context.json"
    sp_path = root / "review" / "sentence_panels.json"
    if not (ctx_path.exists() and sp_path.exists()):
        return None
    try:
        ctx = json.loads(ctx_path.read_text())
        if str(ctx.get("plot_source") or "") != "answer_research":
            return None
        sp = json.loads(sp_path.read_text())
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    return sp if sp.get("scenes") else None


def _shot_fields(panel: dict | None, source_image: str, scene: dict, is_intro: bool,
                 pages_by_number: dict[int, dict]) -> tuple[dict, str, list[dict], bool, bool]:
    """The render fields (panel_bbox, source_image, text_bboxes, no_mirror, keep_contain) for a
    chosen panel — the SAME derivation _build_shots_per_chunk uses, factored so the locked/
    sentence builders share it verbatim. panel=None → render the scene's fallback bbox/source."""
    is_whole = bool(panel is not None and panel.get("_whole_page"))
    text_bboxes: list[dict] = []
    if panel is None:
        bbox = scene.get("panel_bbox") or {}
        source_image = source_image or str(scene.get("source_image") or "")
    else:
        bbox = panel.get("bbox") or {}
        text_bboxes = _panel_text_bboxes(panel, pages_by_number)
    panel_bbox = {"x": int(bbox.get("x", 0)), "y": int(bbox.get("y", 0)),
                  "w": int(bbox.get("w", 0)), "h": int(bbox.get("h", 0))}
    no_mirror = (panel is None or is_whole
                 or _panel_has_critical_text(panel) or is_intro)
    keep_contain = _panel_has_critical_text(panel)
    return panel_bbox, source_image, text_bboxes, no_mirror, keep_contain


def _build_shots_per_sentence(
    narration: dict,
    sentence_panels: dict,
    pages_by_number: dict[int, dict],
    scene_timings: list[dict],
    *,
    cluster_to_name: dict[int, str] | None = None,
    project: str | None = None,
) -> list[Shot]:
    """Q&A (explore_answer) render: ONE shot per narration SENTENCE, each showing the
    panel the headless sentence-match step chose in review/sentence_panels.json.

    Per sentence: panel = its (page, panel) resolved through the same reading-order
    panel pool the per-scene anchor path uses; duration tiles to the next sentence's
    start (silence absorbed forward, like the per-chunk path — keeps the visuals synced
    to the burned captions/audio, which are at absolute word times); caption = the
    sentence text. A null/unresolvable panel is SPARSE → it REUSES the previous shot's
    panel (a first-ever sparse sentence seeds from the scene's own anchor, else the
    cold-open). The intro (is_intro) and any scene the sentence-match step didn't cover
    fall back to the normal per-scene pick (cold-open for the intro, content match /
    outro-loop otherwise) via _match_panels. Motion, framing and pacing defaults are the
    SAME as the per-scene path — only PANEL, DURATION and CAPTION are sentence-driven."""
    scenes = narration.get("scenes") or []
    sp_by_scene: dict[int, list] = {}
    for sc in (sentence_panels.get("scenes") or []):
        sid = sc.get("scene_id")
        if sid is not None:
            sp_by_scene[int(sid)] = list(sc.get("sentences") or [])
    timing_by_scene = {int(t.get("scene_id", 0) or 0): t for t in (scene_timings or [])}

    # (page, panel_idx) → (panel_dict, source_image), the SAME resolution the per-scene
    # anchor path uses (pool key = (page_number, enumerate index within the page)).
    pool = _panel_pool(pages_by_number or {})
    key_to_entry = {key: (panel, src) for (key, panel, src, _tb) in pool}

    def _is_sentence_scene(sc: dict) -> bool:
        # A scene is sentence-driven iff the match step covered it AND it is not the intro
        # (the intro always keeps its cold-open).
        sid = int(sc.get("scene_id") or 0)
        return (not sc.get("is_intro")) and bool(sp_by_scene.get(sid))

    # Fallback scenes (intro / outro / any uncovered scene) → the normal per-scene matcher
    # in one batch: it handles the cold-open, the outro loop-close, and content matching.
    fb_units = [(sc, str(sc.get("text", "") or "")) for sc in scenes if not _is_sentence_scene(sc)]
    fb_assigned = (_match_panels(list(fb_units), pages_by_number or {},
                                 cluster_to_name or {}, project=project, narration=narration)
                   if fb_units else [])
    fb_panel: dict[int, tuple] = {}
    for (sc, _t), pair in zip(fb_units, fb_assigned):
        fb_panel[int(sc.get("scene_id") or 0)] = pair   # (panel, src)

    _cold: dict = {"pair": None, "done": False}   # lazily resolved cold-open seed

    def _cold_open() -> tuple:
        if not _cold["done"]:
            _cold["pair"] = _cold_open_panel(pages_by_number or {}, narration=narration, project=project)
            _cold["done"] = True
        return _cold["pair"]

    def _resolve_sentence_panel(sent: dict) -> tuple | None:
        page, panel_idx = sent.get("page"), sent.get("panel")
        if page is None or panel_idx is None:
            return None                          # sparse → reuse previous
        return key_to_entry.get((int(page), int(panel_idx)))   # None if not in pool → reuse

    # Pass 1: flatten scenes into ordered segments (one per sentence, one per fallback
    # scene), resolving panels + sparse reuse. prev_pair tracks the last emitted panel.
    segments: list[dict] = []
    prev_pair: tuple | None = None
    for sc in scenes:
        sid = int(sc.get("scene_id") or (len(segments) + 1))
        is_intro = bool(sc.get("is_intro"))
        is_outro = bool(sc.get("is_outro"))
        tim = timing_by_scene.get(sid) or {}
        t_start = float(tim.get("start", 0.0) or 0.0)
        t_end = float(tim.get("end", t_start) or t_start)
        if _is_sentence_scene(sc):
            for sent in sp_by_scene[sid]:
                pair = _resolve_sentence_panel(sent)
                if pair is None:                         # sparse
                    if prev_pair is not None:
                        pair = prev_pair
                    else:                                # first-ever sparse → anchor, else cold-open
                        pref = int(sc.get("page_ref", 0) or 0)
                        aref = sc.get("panel_ref")
                        aref = int(aref) if aref is not None else -1
                        pair = key_to_entry.get((pref, aref)) or _cold_open()
                panel, src = pair if pair else (None, "")
                s0 = float(sent.get("start", t_start) or t_start)
                s1 = float(sent.get("end", s0) or s0)
                segments.append({"scene": sc, "scene_id": sid, "is_intro": False,
                                 "is_outro": is_outro, "panel": panel, "src": src,
                                 "caption": str(sent.get("text", "") or ""),
                                 "start": s0, "end": s1})
                prev_pair = (panel, src)
        else:
            panel, src = fb_panel.get(sid) or (None, "")
            segments.append({"scene": sc, "scene_id": sid, "is_intro": is_intro,
                             "is_outro": is_outro, "panel": panel, "src": src,
                             "caption": str(sc.get("text", "") or ""),
                             "start": t_start, "end": t_end})
            prev_pair = (panel, src)

    # Pass 2: motion (rotates per shot like the per-scene path) + durations (tile to the
    # next segment's start; the last shot uses its own end and pipeline.assemble extends it
    # to cover the audio under -shortest).
    shots: list[Shot] = []
    n = len(segments)
    for k, seg in enumerate(segments):
        panel = seg["panel"]
        is_intro, is_outro = seg["is_intro"], seg["is_outro"]
        is_whole = bool(panel is not None and panel.get("_whole_page"))
        end = segments[k + 1]["start"] if k + 1 < n else seg["end"]
        dur = max(0.4, end - float(seg["start"]))
        if is_intro or is_outro or is_whole:
            motion = "zoom_in"   # loop-close zoom_out is applied only by _close_loop (no normal pull-back)
        else:
            motion = _choose_motion(panel, dur, seq=k)
        pb, src, text_bboxes, no_mirror, keep_contain = _shot_fields(
            panel, seg["src"], seg["scene"], is_intro, pages_by_number or {})
        shots.append(Shot(
            shot_id=k, scene_id=seg["scene_id"], duration_seconds=dur,
            panel_bbox=pb, source_image=src, motion=motion,
            text_bboxes=text_bboxes, caption_text=seg["caption"],
            no_mirror=no_mirror, keep_contain=keep_contain, is_intro=is_intro,
        ))
    return shots


# ── Visual-beat panel slicing ────────────────────────────────────────────────
# The panel unit is a VISUAL BEAT — scene["visual_beats"], verbatim fragments computed
# by the LLM beat-splitter at the END OF STAGE 3 (stages/stage_3/beat_split.py). A panel
# changes at each new visual moment, never mid-phrase. (Replaced the spaCy clause splitter,
# which almost never fired: it required the token right after a comma to be the subject,
# but that token is nearly always a determiner/conjunction/compound-name.) When a scene
# has no visual_beats it stays one panel — still synced.


def _split_members_by_clause(members: list, clauses: list[str]) -> list[list]:
    """Bucket a scene's caption-chunk members into one group PER CLAUSE, ALIGNED 1:1
    with `clauses` (a bucket may be empty — the caller zips clauses↔buckets and drops
    empty pairs so clause text, panel, and chunks stay aligned). Assignment is by WORD
    position: chunks are word-fragments in reading order, so each chunk goes to the
    clause covering its midpoint word. members = (text, start, dur). Returns [members]
    (single group) when there's nothing to split (1 clause / <=1 chunk)."""
    if len(clauses) <= 1 or len(members) <= 1:
        return [members]
    clause_of_word: list[int] = []
    for ci, cl in enumerate(clauses):
        clause_of_word.extend(ci for _ in cl.split())
    nwords = len(clause_of_word)
    if not nwords:
        return [members]
    buckets: list[list] = [[] for _ in clauses]
    wptr = 0
    for m in members:
        wc = max(1, len(str(m[0]).split()))
        wi = min(nwords - 1, int(wptr + wc / 2.0))
        buckets[clause_of_word[wi]].append(m)
        wptr += wc
    return buckets   # ALIGNED to clauses (may include empty buckets)


def _align_fragments_to_words(
    fragments: list[str], words: list[dict]
) -> list[tuple[float, float]]:
    """Map each verbatim fragment to its (start, end) span by walking `words`
    ([{word,start,end}], a scene's word_timestamps) with ONE linear pointer. The fragments are a
    verbatim partition of the scene narration, so after normalizing away quotes/dashes/case and
    punctuation on BOTH sides (the TTS glues .",/ onto tokens and emits stray punctuation-only
    tokens the text lacks) the two token streams line up one-to-one. Returns one (start,end) per
    fragment. If the streams DON'T line up (a rare tokenizer disagreement — hyphenated compound,
    spelled-out number) it falls back to an EVEN split of the word window across the fragments.
    Never raises."""
    def _norm(s: str) -> str:
        return re.sub(r"[^0-9a-z]+", "", str(s).lower())

    cw = [(_norm(w.get("word", "")), float(w.get("start", 0.0)), float(w.get("end", 0.0)))
          for w in (words or [])]
    cw = [t for t in cw if t[0]]                    # drop punctuation-only / empty tokens

    def _even() -> list[tuple[float, float]]:
        n = max(1, len(fragments))
        s0 = cw[0][1] if cw else 0.0
        s1 = cw[-1][2] if cw else 0.0
        step = (s1 - s0) / n if s1 > s0 else 0.0
        return [(round(s0 + i * step, 3), round(s0 + (i + 1) * step, 3)) for i in range(n)]

    if not cw:
        return _even()
    spans: list[tuple[float, float]] = []
    ptr = 0
    for frag in fragments:
        ftoks = [t for t in (_norm(x) for x in str(frag).split()) if t]
        if not ftoks or ptr + len(ftoks) > len(cw):
            return _even()
        start = ptr
        for ft in ftoks:
            if cw[ptr][0] != ft:
                return _even()                      # streams diverged → split evenly, no raise
            ptr += 1
        spans.append((round(cw[start][1], 3), round(cw[ptr - 1][2], 3)))
    return spans


def _fragment_units(
    fragments: list[str], pins: list, members: list, word_timestamps: list[dict]
) -> tuple[list[str], list[list], list]:
    """PLAN A (micro_moment): turn a scene's visual-beat fragments into one render UNIT each,
    timed from word_timestamps. Returns (texts, slices, pins) in the SAME shape the
    _split_members_by_clause path yields (slices = lists of (text,start,dur) members) so the
    rest of _build_shots_per_chunk is unchanged — but here every slice is a SINGLE synthetic
    member whose caption is the VERBATIM fragment. Fragment starts come from aligning the text to
    the words; the outer edges are the scene's own [start, start+total], so the durations sum
    EXACTLY to the scene total (first fragment absorbs any lead silence, last the trailing tail)
    → global audio sync is byte-identical to the old path."""
    seg_start = float(members[0][1]) if members else 0.0
    seg_total = sum(float(m[2]) for m in members) if members else 0.0
    seg_end = seg_start + seg_total
    win = [w for w in (word_timestamps or [])
           if float(w.get("end", 0.0)) > seg_start and float(w.get("start", 0.0)) < seg_end]
    spans = _align_fragments_to_words(fragments, win or word_timestamps or [])
    cuts = [seg_start]
    for s, _e in spans[1:]:                          # internal cut points = fragment starts
        cuts.append(min(max(float(s), cuts[-1]), seg_end))
    cuts.append(seg_end)
    slices = [[(frag, round(cuts[i], 3), max(0.0, round(cuts[i + 1] - cuts[i], 3)))]
              for i, frag in enumerate(fragments)]
    return list(fragments), slices, list(pins)


def _retime_units_to_words(units: list, word_timestamps: list[dict]) -> None:
    """Overwrite each unit's slice-member timing IN PLACE from word_timestamps (ground truth),
    so a shot starts exactly when its first spoken word is heard — bypassing drifted Stage-4
    caption_chunks / scene_timings. Units are (scene, slice_members, spoken, pin); the ordered
    unit captions (" ".join member texts) are a verbatim partition of the narration, so align
    them to the full normalized word stream with ONE forward pointer. Each unit gets a single
    synthetic member (SAME caption text, corrected start/dur): unit i spans [start_i, start_{i+1}]
    with unit 0 pinned to t=0 (owns the lead-in) and the last unit running to the final word end
    → cumulative start of unit i == its audio start for every i>=1. No-op (leaves units untouched)
    if the streams diverge, so a tokenizer disagreement degrades to the old caption-chunk spans
    instead of raising."""
    def _norm(s: str) -> str:
        return re.sub(r"[^0-9a-z]+", "", str(s).lower())

    cw = [(_norm(w.get("word", "")), float(w.get("start", 0.0)), float(w.get("end", 0.0)))
          for w in (word_timestamps or [])]
    cw = [t for t in cw if t[0]]
    if not cw or not units:
        return
    caps = [" ".join(str(m[0]) for m in sl) for _sc, sl, _sp, _pin in units]
    starts: list[float] = []
    ptr = 0
    for cap in caps:
        ftoks = [t for t in (_norm(x) for x in cap.split()) if t]
        if not ftoks:
            return
        found = None
        p = ptr
        while p + len(ftoks) <= len(cw):
            if all(cw[p + i][0] == ftoks[i] for i in range(len(ftoks))):
                found = p
                break
            p += 1
        if found is None:
            return                       # streams diverged → keep original spans (no raise)
        starts.append(cw[found][1])
        ptr = found + len(ftoks)
    audio_end = cw[-1][2]
    for i, (sc, _sl, sp, pin) in enumerate(units):
        b0 = 0.0 if i == 0 else starts[i]
        b1 = starts[i + 1] if i + 1 < len(starts) else max(audio_end, starts[i])
        units[i] = (sc, [(caps[i], round(b0, 3), max(0.0, round(b1 - b0, 3)))], sp, pin)


def _panel_text_bboxes(panel: dict, pages_by_number: dict[int, dict]) -> list[dict]:
    """Return the page-coordinate bboxes of every text block (speech/narration)
    belonging to this panel — matched by panel_index on the panel's page. Used to
    build the inpaint mask that erases the comic's own dialogue."""
    pn = int(panel.get("_page_number", 0) or 0)
    page = pages_by_number.get(pn) or {}
    out: list[dict] = []
    for tb in panel_dialog(panel, page.get("text_blocks")):
        b = tb.get("bbox") or {}
        if b.get("w") and b.get("h"):
            out.append({"x": int(b["x"]), "y": int(b["y"]),
                        "w": int(b["w"]), "h": int(b["h"])})
    return out


def _choose_motion(panel: dict | None, dur: float, seq: int = 0) -> str:
    """Content-aware motion picker. NEVER returns "static": a still frame (z=1.00
    held for seconds) reads as a FREEZE / glitch in a motion comic — half this
    project's shots were static and visibly froze. Every shot now gets a gentle
    Ken-Burns move (the calm zoom is a subtle 1.05 push, see _zoompan_expr, which
    spans the FULL duration so there is no static tail).

    The FIRST shot (cold open, seq 0) gets the epic slow zoom_in. Every later shot
    ROTATES through the motions that actually read on its panel's shape — so a run of
    similarly-shaped panels (e.g. many big splashes) never collapses to all-zoom_in
    (Master, 2026-06-28: a whole video of identical zoom_in reads as monotonous).
    Zooms always read; a vertical pan needs height to travel (tall or square), a
    horizontal pan needs width (wide or square). Big/splash panels rotate too —
    variety beats a uniform push, and a pan across a full-page splash is cinematic."""
    bbox = (panel or {}).get("bbox") or {}
    w = int(bbox.get("w", 0) or 0)
    h = int(bbox.get("h", 0) or 0)
    # Cold open → epic slow zoom_in, whatever the shape.
    if seq <= 0:
        return "zoom_in"
    ar = (h / w) if w else 1.0
    # NEVER zoom_out on a normal shot (Master 2026-07-11): the only pull-back allowed is the
    # loop-close (_close_loop), which ends at z=1.0 to match the intro's first frame. Variety
    # here comes from zoom_in + the aspect-appropriate one-directional pans.
    cands = ["zoom_in"]
    if ar >= 0.9:          # tall or square → up↕down reveal reads
        cands += ["pan_down", "pan_up"]
    if ar <= 1.15:         # wide or square → left↔right reveal reads
        cands.append("pan_right")
    return cands[seq % len(cands)]


def _split_shot_durations(dur: float) -> list[float]:
    """Competitor pacing (measured): a long held shot becomes several ~SUBSHOT_TARGET_SECONDS
    hard-cut sub-shots (their avg shot 1.2-1.8s; ours held 4-6s). Each sub-shot is capped at
    MAX_SHOT_SECONDS. Durations sum EXACTLY to `dur` (the last absorbs the remainder) so the
    scene_timings totals and the -shortest audio-sync math in pipeline.assemble are untouched.
    dur <= MAX_SHOT_SECONDS → a single shot (no split)."""
    if dur <= MAX_SHOT_SECONDS:
        return [dur]
    n = max(2, round(dur / SUBSHOT_TARGET_SECONDS))
    while dur / n > MAX_SHOT_SECONDS:       # keep every sub-shot under the cap
        n += 1
    step = round(dur / n, 3)
    durs = [step] * (n - 1)
    durs.append(dur - sum(durs))            # last sub-shot absorbs the remainder → exact sum
    return durs


def _render_adjust(panel: dict, page_text_blocks: list[dict] | None,
                   *, salience_w: float = 4.0) -> float:
    """Render-quality adjustments (NOT content): penalize tiny panels that must be
    heavily upscaled, reward big/splash panels as a tie-break, penalize text-wall
    panels (cluttered + smear under inpaint). Used by the pure-vector matcher
    (_panel_content_score) to pick the better-RENDERING panel among content-similar
    candidates."""
    import math
    bbox = panel.get("bbox", {}) or {}
    _pw = int(bbox.get("w", 0) or 0)
    _ph = int(bbox.get("h", 0) or 0)
    score = 0.0
    # Small-panel penalty — upscale factor to fill 1080×1920; >2.5× → blurry giant.
    panel_scale = max(OUTPUT_W / _pw, OUTPUT_H / _ph) if (_pw > 0 and _ph > 0) else 99.0
    if panel_scale > 2.5:
        score -= 3.0 * (panel_scale - 2.5)
    # Visual salience — page-relative: a >=50% splash gets the full weight.
    area = _pw * _ph
    page_area = int(panel.get("_page_area", 0) or 0)
    if page_area > 0:
        score += salience_w * min(1.0, (area / page_area) / 0.5)
    elif area > 50000:
        score += (salience_w * 0.75) * min(1.0, math.log(area / 50000) / 3.0)
    # Text-coverage penalty — avoid text-wall panels.
    _dlg = panel_dialog(panel, page_text_blocks)
    if area > 0 and _dlg:
        px, py = int(bbox.get("x", 0) or 0), int(bbox.get("y", 0) or 0)
        text_area = 0
        for tb in _dlg:
            tbb = tb.get("bbox") or {}
            tx, ty = int(tbb.get("x", 0) or 0), int(tbb.get("y", 0) or 0)
            tw, th = int(tbb.get("w", 0) or 0), int(tbb.get("h", 0) or 0)
            ix0, iy0 = max(px, tx), max(py, ty)
            ix1, iy1 = min(px + _pw, tx + tw), min(py + _ph, ty + th)
            if ix1 > ix0 and iy1 > iy0:
                text_area += (ix1 - ix0) * (iy1 - iy0)
        coverage = text_area / area
        if coverage > 0.15:
            score -= min(8.0, 30.0 * (coverage - 0.15))
    return score


def _is_skip_page(page: dict) -> bool:
    """Detect non-story pages that slipped through page_type classification.
    Filters credit/title pages and back-cover promo/ad pages by 3 heuristics:
      1. page_type tag (cover/credits/ads/promo/title)
      2. page_summary keywords (promotional/advertisement)
      3. text_block content (creator role names: WRITER/ARTIST/COLORIST/LETTERER/EDITOR)
    """
    pt = (page.get("page_type") or "").lower()
    if pt in ("cover", "credits", "ads", "promo", "title", "skip"):
        return True
    # Stage 2 marks ads / back-matter as is_story_page=False (page_type "skip");
    # honour that flag too so reclassified pages never get picked for a shot.
    if page.get("is_story_page") is False and pt != "cover":
        return True
    summary = (page.get("page_summary") or "").lower()
    # A page whose OWN summary calls itself a cover is a cover, even when page_type
    # was mis-tagged "story" (real case: a recap/collage cover with the title logo +
    # licensing credits → became a content magnet that matched every narration line).
    if summary.startswith("cover"):
        return True
    summary_markers = (
        "promotional or advertisement",
        "promotional page",
        "advertisement page",
        "creator credits",
        "licensing credits",        # cover/credits page (e.g. "...Toho licensing credits")
        "stylized collage",         # cover montage
        "collage depicting",        # cover montage
        "title logo",               # cover/title page
        "table of contents",
        "next issue",
        "upcoming comic book",
        "on sale now",
        "sets the stage for",       # title/recap page
        "pivotal question is posed",
        "opening with a quote",
    )
    if any(m in summary for m in summary_markers):
        return True
    # Credit/title page detector — text_blocks contain creator role labels.
    role_words = ("WRITER", "ARTIST", "COLORIST", "LETTERER", "COVER ARTIST",
                  "EDITOR-IN-CHIEF", "PRODUCTION", "VARIANT COVER")
    text_corpus = " ".join(str(tb.get("text", "")).upper()
                           for tb in page_dialog(page))
    role_hits = sum(1 for w in role_words if w in text_corpus)
    if role_hits >= 2:  # ≥2 role labels = unambiguously a credits/title page
        return True
    # House-ad detector — same idea on the page's own OCR text. Real case: a
    # trailing BOOM! ad ("ON-SALE NOW!", "— IGN", "DISCOVER YOURS") slipped
    # through as page_type="story" with a hallucinated story summary.
    ad_patterns = (r"\bON[- ]SALE\b", r"\bIN STORES\b", r"\bAVAILABLE NOW\b",
                   r"\bDISCOVER YOURS\b", r"\bVOLUMES?\s+[\dI]", r"\bSUBSCRIBE\b",
                   r"\bENTERTAINMENT WEEKLY\b", r"\bIGN\b", r"\.COM\b", r"\bISBN\b",
                   r"\bNEXT ISSUE\b", r"\bFREE PREVIEW\b", r"\bGRAPHIC NOVEL\b")
    ad_hits = sum(1 for p in ad_patterns if re.search(p, text_corpus))
    if ad_hits >= 2:  # ≥2 distinct ad markers = house ad / promo page
        return True
    return False


# ── Cold-open frame-1 gates (R2 2026-07-12) ──────────────────────────────────────────────
# Frame 1 is the single most retention-critical pixel; an audience audit found 50-60% of the
# swipe-aways happen in the first 3s, and the batcave Q&A opened on a WIDE letterboxed strip
# (aspect 2.23 → contain+blur band). These HARD-gate frame-1: a candidate the renderer would
# LETTERBOX, a TINY panel (< min area frac of its page), or a BUBBLE-CHOKED panel is removed
# from contention whenever ≥1 clean candidate exists; if NONE pass, the old scorer runs
# unchanged (never crash / never None when panels exist). COLD_OPEN_MONEY_BIND gates the
# subject_panels.json money-bind step. Defaults chosen from batcave's real area_frac spread
# (median 0.13; the genuinely tiny insets cluster < 0.12, the hero portraits sit ~0.9).
COLD_OPEN_MONEY_BIND = os.getenv("COLD_OPEN_MONEY_BIND", "1").strip() not in ("0", "false", "False", "")
COLD_OPEN_MIN_AREA_FRAC = float(os.getenv("COLD_OPEN_MIN_AREA_FRAC", "0.12"))
COLD_OPEN_MAX_BUBBLE_FRAC = float(os.getenv("COLD_OPEN_MAX_BUBBLE_FRAC", "0.15"))
# Cold-open score weights — frame-1 must READ INSTANTLY (retention is won or lost in the first
# ~3s). char reward raised 0.20→0.25 and caption-clutter penalty 0.30→0.40 so a clean
# single-subject panel beats a caption-choked one; env-tunable to retune without a code edit.
COLD_OPEN_W_CHAR = float(os.getenv("COLD_OPEN_W_CHAR", "0.25"))
COLD_OPEN_W_DIALOG = float(os.getenv("COLD_OPEN_W_DIALOG", "0.40"))
# Crowd penalty: many named characters = no single readable subject (the "busy establishing shot"
# frame-1 defect). Fires only past CROWD_MIN. ponytail: len(characters) is a coarse crowd proxy —
# the schema has no per-figure bbox; upgrade to figure-area share if character bboxes ever land.
COLD_OPEN_W_CROWD = float(os.getenv("COLD_OPEN_W_CROWD", "0.15"))
COLD_OPEN_CROWD_MIN = int(os.getenv("COLD_OPEN_CROWD_MIN", "3"))


def _cold_open_bubble_frac(panel: dict, page_tb) -> float:
    """Fraction of the panel's bbox area covered by its own speech/narration bboxes. Same
    bubble-rect source `_choose_crop_offset`/`_panel_text_bboxes` use (panel_dialog → tb.bbox).
    Overlapping bubbles double-count → a slight OVER-estimate, fine for a >15% reject. 0.0 when
    a panel carries no bubble bbox data (old projects) → the bubble gate then never fires."""
    bb = panel.get("bbox") or {}
    px, py = int(bb.get("x", 0) or 0), int(bb.get("y", 0) or 0)
    pw, ph = int(bb.get("w", 0) or 0), int(bb.get("h", 0) or 0)
    parea = pw * ph
    if parea <= 0:
        return 0.0
    covered = 0.0
    for tb in panel_dialog(panel, page_tb):
        b = tb.get("bbox") or {}
        bw, bh = int(b.get("w", 0) or 0), int(b.get("h", 0) or 0)
        if bw <= 0 or bh <= 0:
            continue
        tx, ty = int(b.get("x", 0) or 0), int(b.get("y", 0) or 0)
        ix0, iy0 = max(px, tx), max(py, ty)
        ix1, iy1 = min(px + pw, tx + bw), min(py + ph, ty + bh)
        if ix1 > ix0 and iy1 > iy0:
            covered += (ix1 - ix0) * (iy1 - iy0)
    return min(1.0, covered / parea)


def _cold_open_gate_ok(panel: dict, parea: int, page_tb=None) -> bool:
    """Is `panel` an ACCEPTABLE frame-1? Rejects (a) a panel the renderer would LETTERBOX —
    blurry-giant upscale (cover > _BLUR_FALLBACK_SCALE) OR a strip at/over LANDSCAPE_COVER_MAX_ASPECT,
    the SAME predicate _prepare_panel_frame/_should_blur_bg use — (b) a TINY panel (< COLD_OPEN_MIN_AREA_FRAC
    of its page area), and (c) a BUBBLE-CHOKED panel (> COLD_OPEN_MAX_BUBBLE_FRAC text coverage, only
    when page_tb is supplied). Shared by _cold_open_panel's hard gate and the Q&A subject-intro bookend
    order so both frame-1 selectors reject the wide-blur / tiny-inset opener."""
    bb = panel.get("bbox") or {}
    w, h = int(bb.get("w", 0) or 0), int(bb.get("h", 0) or 0)
    if w <= 0 or h <= 0:
        return False
    aspect = w / h
    cover = max(OUTPUT_W / w, OUTPUT_H / h)
    if cover > _BLUR_FALLBACK_SCALE or aspect >= LANDSCAPE_COVER_MAX_ASPECT:
        return False                                   # would letterbox (contain+blur band)
    if parea > 0 and (w * h) / parea < COLD_OPEN_MIN_AREA_FRAC:
        return False                                   # tiny inset
    if page_tb is not None and _cold_open_bubble_frac(panel, page_tb) > COLD_OPEN_MAX_BUBBLE_FRAC:
        return False                                   # bubble-choked
    return True


def _cold_open_money_panel(project, pages_by_number, exclude_keys):
    """The VLM-confirmed MONEY panel to open on, from projects/<slug>/subject_panels.json: the
    first row flagged `force_intro`/`money` (written by review_gate._pin_money_intro after the
    money-shot VLM confirm) that resolves in the reading-order pool. A `force_intro` row bypasses
    exclude_keys — it MAY double as a body beat (the ComicCut spoiler-hook: the payoff opens AND
    plays out). Returns (panel, src) or None (no file / no money row / unresolved) → the caller
    then falls through to the gated scorer. A MANUAL subject file carries no such flag, so a
    hand-ordered file is NOT money-bound here — its ordering is honored by the Q&A subject-intro
    path (_qa_subject_sequence) instead."""
    try:
        from ..subject_panels import load_subject_panels
        rows = (load_subject_panels(project) or {}).get("panels") or []
    except Exception:
        return None
    entry = {k: (p, s) for (k, p, s, _tb) in _panel_pool(pages_by_number or {})}
    for row in rows:
        if not (row.get("force_intro") or row.get("money")):
            continue
        try:
            key = (int(row["page"]), int(row["panel"]))
        except (KeyError, TypeError, ValueError):
            continue
        if key not in entry:
            continue
        if key in (exclude_keys or set()) and not row.get("force_intro"):
            continue
        return entry[key]
    return None


def _cold_open_lock(narration: dict | None) -> tuple[int, int] | None:
    """COLD_OPEN_LOCK knob: manually PIN the cold-open to an exact (page, panel) instead
    of the scorer below — for the rare case where the right frame-1 is a specific payoff
    panel discovered later in the story (e.g. so SEAMLESS_LOOP's _close_loop opens AND
    closes the video on that exact panel). Priority: env COLD_OPEN_LOCK="page,panel" wins,
    else narration.json's optional `cold_open_lock` field, accepting either "page,panel"
    or [page, panel]. Returns None on anything missing/unparsable — the caller then falls
    back to the normal scorer, so an unset knob is byte-identical to the old behavior."""
    raw = os.environ.get("COLD_OPEN_LOCK") or (narration or {}).get("cold_open_lock")
    if not raw:
        return None
    try:
        page_s, panel_s = raw.split(",") if isinstance(raw, str) else raw
        return int(page_s), int(panel_s)
    except (ValueError, TypeError):
        return None


def _cold_open_panel(pages_by_number, exclude_keys=None, narration: dict | None = None,
                     project: str | None = None):
    """COLD-OPEN: pick a striking STORY panel to open the video on instead of the
    cover. Frame 1 is the single most retention-critical moment, so the pick is by a
    SCORE — not raw area. Largest-area alone landed on the two worst openers seen in
    shipped videos: (a) a WIDE overhead establishing shot (Doom tiny at a dinner table,
    ~12 empty speech bubbles) that mismatches the hook, and (b) a LANDSCAPE strip that
    _prepare_panel_frame letterboxes into a thin blurred band. Both waste the 9:16 frame.

    Frame-1 precedence: COLD_OPEN_LOCK (Master's hand-pin) > money-bind (the VLM-confirmed
    money panel from subject_panels.json, COLD_OPEN_MONEY_BIND) > the gated scorer below.
    COLD_OPEN_LOCK (see _cold_open_lock) bypasses everything when set and the locked (page,
    panel) exists in the pool — used to hand-pick frame 1 (e.g. a hidden payoff panel). A lock
    pointing nowhere logs a warning and falls through. Then, for a Q&A project whose funnel
    confirmed a money panel, that panel opens the video (see _cold_open_money_panel).

    HARD GATE (below): whenever ≥1 candidate is a clean frame-1 (not letterbox, not tiny, not
    bubble-choked — see _cold_open_gate_ok), the scorer picks ONLY among those; a letterbox/
    tiny candidate is removed from contention, not merely penalized. If NONE pass, the old
    full-pool scorer runs unchanged, so an all-wide / all-tiny opening still returns a panel.

    Rank opening-third candidates by (all terms 0..1):
        score = 0.40*area_frac      # still reward a big panel …
              + 0.35*aspect_fit     # … but a PORTRAIT/near-9:16 panel FILLS the frame;
                                     #   a landscape panel (letterboxed) scores ~0 here
              + 0.25*has_character   # a clear face/figure opens stronger than empty scenery
              - 0.40*dialog_load     # a CLEAN splash beats a caption/bubble-cluttered panel on
                                     #   a HELD opening frame (empty bubbles read as slop)
              - 0.15*crowd           # many named figures = no single subject the eye lands on
                                     #   in <1s (busy establishing shot); past COLD_OPEN_CROWD_MIN
              - 0.60*will_letterbox  # HARD penalty when _prepare_panel_frame would letterbox
                                     #   this panel (contain+blur wide strip OR blurry-giant
                                     #   upscale) — the shipped frame-1 defect. Uses the SAME
                                     #   two triggers the renderer uses (general, not a magic
                                     #   aspect constant)

    Skips cover/credits/ad pages, the final 2 story pages (no ending spoiler), and
    `exclude_keys` = (page, idx) panels already assigned to story scenes (the intro must
    never duplicate one — it would play as the same image twice within seconds). Falls
    back to the largest-area candidate when nothing scores positively, so an all-wide /
    all-cluttered opening still returns a panel (never None when panels exist). Returns
    (panel, src) or (None, '')."""
    lock = _cold_open_lock(narration)
    if lock is not None:
        page, idx = lock
        entry = {k: (p, s) for (k, p, s, _tb) in _panel_pool(pages_by_number or {})}.get((page, idx))
        if entry is not None:
            print(f"[stage5] cold-open: locked p{page}/{idx}")
            return entry
        print(f"[stage5] cold-open: lock p{page}/{idx} not found in panel pool — falling back to scorer")
    exclude_keys = exclude_keys or set()
    if COLD_OPEN_MONEY_BIND and project:
        mb = _cold_open_money_panel(project, pages_by_number, exclude_keys)
        if mb is not None:
            print(f"[stage5] cold-open: money-bind p{mb[0].get('_page_number')}/{mb[0].get('index')}")
            return mb
    story_pns = sorted(pn for pn, pg in (pages_by_number or {}).items()
                       if pg and not _is_skip_page(pg))
    if not story_pns:
        return None, ""
    ending = set(story_pns[-2:])   # exclude the last 2 story pages (ending/outro splash)
    # Only the OPENING third (≥3 pages): the hook is about the SETUP, so frame 1 should
    # come from where the premise is established — not the globally-largest panel, which
    # was landing on a mid-story action splash unrelated to the opening line.
    opening = story_pns[:max(3, len(story_pns) // 3)]
    best = None          # (score, panel_dict, src) — best over ALL candidates (old behavior)
    biggest = None       # (area, panel_dict, src)  — old largest-area fallback
    best_gated = None    # (score, panel_dict, src) — best among GATE-PASSING candidates
    biggest_gated = None # (area, panel_dict, src)  — largest gate-passing (score-≤0 fallback)
    for pn in opening:
        if pn in ending:
            continue
        page = pages_by_number.get(pn) or {}
        src = str(page.get("source_image") or "")
        dims = page.get("image_dimensions") or {}
        parea = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
        page_tb = page.get("text_blocks")   # None on new schema → panel_dialog uses nested
        for idx, panel in enumerate(page.get("panels") or []):
            if (pn, idx) in exclude_keys:
                continue
            bb = panel.get("bbox") or {}
            w, h = int(bb.get("w", 0) or 0), int(bb.get("h", 0) or 0)
            area = w * h
            if area <= 0 or h <= 0:
                continue
            pw = dict(panel)
            pw["_page_number"] = pn
            pw["index"] = idx
            if biggest is None or area > biggest[0]:
                biggest = (area, pw, src)
            gate_ok = _cold_open_gate_ok(pw, parea, page_tb)
            if gate_ok and (biggest_gated is None or area > biggest_gated[0]):
                biggest_gated = (area, pw, src)
            area_frac = area / parea if parea > 0 else 0.0
            # aspect_fit: 1.0 for a portrait/square panel (cover-crops to fill 9:16),
            # decaying as it widens past square. A landscape panel (w/h ≳ 1.2) is exactly
            # what _prepare_panel_frame renders as a letterboxed strip — the spider-man
            # cold-open defect — so it scores near 0 and loses to a portrait candidate.
            aspect = w / h
            aspect_fit = 1.0 if aspect <= 1.0 else max(0.0, 1.0 - (aspect - 1.0))
            # Bubble/text load on a HELD frame: ~12 empty bubbles (Doom dinner table) is
            # instant AI-slop. panel_dialog handles both schemas (nested + old page-level).
            dialog_load = min(len(panel_dialog(panel, page_tb)), 8) / 8.0
            cast = panel.get("characters") or []
            has_char = 1.0 if cast else 0.0
            # crowd: 0 at ≤CROWD_MIN named characters, ramping to 1 as the panel fills with
            # figures — a busy establishing shot has no single subject the eye lands on in <1s.
            crowd = min(1.0, max(0, len(cast) - COLD_OPEN_CROWD_MIN) / 3.0)
            # Will _prepare_panel_frame LETTERBOX this frame-1 (contain+blur → tiny subject in
            # a blurred band)? Same two triggers the renderer uses, on the panel's own bbox:
            # a too-small panel blown up past _BLUR_FALLBACK_SCALE, OR a strip at/over
            # LANDSCAPE_COVER_MAX_ASPECT. Penalized hard so a portrait, subject-filled panel
            # wins; the biggest-area fallback still fires if EVERY opening panel would letterbox.
            cover = max(OUTPUT_W / w, OUTPUT_H / h)
            will_letterbox = cover > _BLUR_FALLBACK_SCALE or aspect >= LANDSCAPE_COVER_MAX_ASPECT
            score = (0.40 * area_frac + 0.35 * aspect_fit
                     + COLD_OPEN_W_CHAR * has_char - COLD_OPEN_W_DIALOG * dialog_load
                     - COLD_OPEN_W_CROWD * crowd
                     - (0.60 if will_letterbox else 0.0))
            if best is None or score > best[0]:
                best = (score, pw, src)
            if gate_ok and (best_gated is None or score > best_gated[0]):
                best_gated = (score, pw, src)
    # Gate-passers win outright whenever ≥1 exists — a letterbox/tiny/bubble-choked candidate is
    # REMOVED from contention, not just penalized (the frame-1 hard gate). biggest_gated is set
    # whenever best_gated is, so a clean opening always resolves to a clean panel.
    if best_gated is not None:
        return (best_gated[1], best_gated[2]) if best_gated[0] > 0 else (biggest_gated[1], biggest_gated[2])
    # No clean candidate at all (every opening panel would letterbox / is tiny) → OLD full-pool
    # scorer, byte-identical to the pre-gate behavior (never crash / never None when panels exist).
    if best is not None and best[0] > 0:
        return best[1], best[2]
    if biggest is not None:
        return biggest[1], biggest[2]
    return None, ""


def _outro_panel(pages_by_number):
    """OUTRO: pick a striking FOCAL panel for the thematic closing line — the largest
    NON-whole-page, low-text STORY panel in the CLOSING third. Mirrors _cold_open_panel
    (which opens on a striking panel). Avoids landing the outro on the final whole-page
    splash, which renders cluttered with no clear subject. Returns (panel, src) or
    (None, '') to fall back to the content match."""
    story_pns = sorted(pn for pn, pg in (pages_by_number or {}).items()
                       if pg and not _is_skip_page(pg))
    if not story_pns:
        return None, ""
    closing = story_pns[-max(3, len(story_pns) // 3):]
    best = None  # (score, panel, src)
    for pn in closing:
        page = pages_by_number.get(pn) or {}
        src = str(page.get("source_image") or "")
        dims = page.get("image_dimensions") or {}
        parea = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
        for idx, panel in enumerate(page.get("panels") or []):
            bb = panel.get("bbox") or {}
            area = int(bb.get("w", 0) or 0) * int(bb.get("h", 0) or 0)
            if area <= 0:
                continue
            if parea > 0 and area / parea >= 0.85:   # skip whole-page / near-full splash
                continue
            ndlg = len(panel.get("dialog") or [])
            score = area - ndlg * 40000              # big + low-text wins
            if best is None or score > best[0]:
                pw = dict(panel)
                pw["_page_number"] = pn
                pw["index"] = idx
                best = (score, pw, src)
    return (best[1], best[2]) if best else (None, "")


def _panel_pool(pages_by_number: dict) -> list:
    """All non-skip panels in READING ORDER (page asc, panel idx asc) across every
    page/issue: [(key, panel_dict, source_image, page_text_blocks)]. Each panel dict
    carries _page_number/_page_area/index for the content scorer + render tie-break."""
    pool = []
    for pn in sorted(pages_by_number or {}):
        page = pages_by_number.get(pn)
        if not page or _is_skip_page(page):
            continue
        src = str(page.get("source_image") or "")
        page_tb = page.get("text_blocks") or []
        dims = page.get("image_dimensions") or {}
        parea = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
        # Stage-2 trust flag for the whole page (DESC_VERIFY gate). Stashed on every
        # panel so _panel_untrusted can read it from the pool entry alone. get() → None
        # when the gate never ran (old projects) → treated as trusted, identical output.
        page_dv = page.get("desc_verified")
        for idx, panel in enumerate(page.get("panels") or []):
            pw = dict(panel)              # copies the panel's own dialog_mismatch flag too
            pw["_page_number"] = pn
            pw["_page_area"] = parea
            pw["index"] = idx
            pw["_page_desc_verified"] = page_dv
            pool.append(((pn, idx), pw, src, page_tb))
    return pool


def _panel_untrusted(panel: dict) -> bool:
    """A pool panel is UNTRUSTED when Stage 2 flagged it: its PAGE failed DESC_VERIFY
    (page dict `desc_verified` == False — explicit False only; absent/True = trusted, so
    old projects with no flag stay trusted) OR the panel's own VLM dialog contradicts Magi
    OCR ground truth (`dialog_mismatch` truthy). An untrusted panel's (page,idx) anchor is
    only as reliable as the description that produced it — a fabricated description silently
    mis-anchored doom-rocket-raccoon scene 13 and ANCHOR_BIND rendered the wrong panel with
    no VLM check. Feature C uses this to refuse the hard bind; Feature D forces the VLM."""
    return panel.get("_page_desc_verified") is False or bool(panel.get("dialog_mismatch"))


def _panel_content_score(panel, panel_vec, chunk_vec, scene_vec, page_tb,
                         *, chunk_text, scene_text):
    """PURE-VECTOR content match + render tie-break. Returns (score, sim_chunk).
    cosine comes from the persisted Qdrant vector (panel_vec, no re-embed) when
    available, else an in-memory embed of the richer panel text. The lexical hybrid
    (char/dialog/emotion) is deliberately gone — validated worse, see W_COS notes."""
    import numpy as np
    if panel_vec is not None and chunk_vec is not None:
        sim_chunk = max(0.0, float(np.dot(chunk_vec, panel_vec)))
        sim_scene = (max(0.0, float(np.dot(scene_vec, panel_vec)))
                     if scene_vec is not None else 0.0)
    else:
        ptext = panel_embed_text(panel, page_tb)
        sim_chunk = _semantic_sim(chunk_text, ptext)
        sim_scene = _semantic_sim(scene_text, ptext)
    score = W_COS * sim_chunk + W_COS_SCENE * sim_scene
    # render_adjust biases toward bigger/highlight panels; bounded by PANEL_RENDER_ADJ_CAP so it
    # never overrides content, only decides among content-similar panels.
    radj = _render_adjust(panel, page_tb, salience_w=PANEL_SALIENCE_W)
    score += max(-PANEL_RENDER_ADJ_CAP, min(PANEL_RENDER_ADJ_CAP, radj))
    return score, sim_chunk


def _blend_image_content(content, pool: list, units: list, project: str | None) -> None:
    """Feature A — blend a desc-FREE SigLIP IMAGE signal into the `content` matrix IN PLACE.

    WHY: `content` (text cosine on the VLM description) trusts the VLM's WORDS, and the VLM
    fabricates descriptions from story context — a poisoned description scores a FAKE-HIGH
    text cosine for the WRONG panel (doom-rocket-raccoon #13). The image cosine (narration
    line vs the panel's ART pixels, both in SigLIP's joint space) never reads those words, so
    it can veto a poisoned pick.

    Blend: content[i][j] = (1-w)*text + w*img_mapped, w = PANEL_IMG_WEIGHT.
      • text stays RAW so a CONFIDENT text lead keeps its magnitude (image is a MINORITY vote
        at w=0.35 — it flips near-ties / low-confidence picks, not a confidently-agreeing text
        lead). Min-maxing the text instead would erase confidence (a coin-flip near-tie would
        masquerade as maximally certain and starve the image signal exactly when it matters).
      • img_mapped = per-unit min-max of the RAW image cosine (SigLIP cosines cluster in a
        narrow band, so min-max spreads them) linearly mapped into THIS unit's text span
        [tmin, tmax]. Mapping onto the text scale keeps the blend on `content`'s ~0-10 point
        scale so the page prior / anchor bonus / tie-break / reuse-penalty POINTS downstream
        keep their calibrated magnitude.
      • Panels with no stored image vector stay NEUTRAL (img == their own text value → the
        blend leaves them unchanged) rather than being pushed by a bogus 0 cosine.

    Degrades to EXACTLY the text-only path (content untouched) when the channel is off, no
    image vectors exist, the SigLIP text tower is unavailable, or dims don't line up. `sim`
    (raw TEXT cosine) is intentionally NOT passed in / NOT touched: the PANEL_COS_FLOOR
    cascade-hold guard must keep its text semantics (image cosines live on a different scale).

    ponytail: min-max maps onto the text span, so a unit where ALL text scores tie (span≈0)
    gets no image push. That's the backend-down degenerate case the cascade-hold guard already
    catches; per-unit ties on real runs are rare. Revisit only if flat-text units show up.
    """
    from .. import _img_index   # module import → attributes late-bound (monkeypatchable in tests)
    if not project or not _img_index.PANEL_IMG_EMBED or not _img_index.img_embed_available():
        return
    img_vecs = _img_index.load_image_vectors(project)
    if not img_vecs:
        print("[stage5] img-match: no image vectors — text-only")
        return
    unit_txt = _img_index.embed_texts([txt for _sc, txt in units])
    if unit_txt is None:
        print("[stage5] img-match: SigLIP text tower unavailable — text-only")
        return

    import numpy as np
    n, m = content.shape
    w = float(_img_index.PANEL_IMG_WEIGHT)
    stored_dim = len(next(iter(img_vecs.values())))
    if int(unit_txt.shape[1]) != int(stored_dim):
        # A SigLIP swap since indexing would crash np.dot — skip rather than blend garbage.
        print(f"[stage5] img-match: text-dim {unit_txt.shape[1]} != stored img-dim {stored_dim} — text-only")
        return

    keys = [pool[j][0] for j in range(m)]
    have = np.array([k in img_vecs for k in keys])
    panel_mat = np.zeros((m, int(stored_dim)), dtype="float32")
    for j, k in enumerate(keys):
        if have[j]:
            panel_mat[j] = img_vecs[k]
    img_cos = unit_txt @ panel_mat.T                 # [n, m] cosine (both L2-normed)

    blended = 0
    for i in range(n):
        row = content[i]
        tmin, tmax = float(row.min()), float(row.max())
        span = tmax - tmin
        if span <= 0 or int(have.sum()) < 2:
            continue                                 # nothing to reorder / too few image vecs
        ic = img_cos[i]
        imin = float(ic[have].min()); imax = float(ic[have].max())
        ispan = imax - imin
        if ispan <= 0:
            continue                                 # image can't discriminate this unit
        # img in [0,1] over present panels → mapped into [tmin, tmax]; absent panels neutral.
        img_mapped = np.where(have, tmin + (ic - imin) / ispan * span, row)
        content[i] = (1.0 - w) * row + w * img_mapped
        blended += 1
    if blended:
        print(f"[stage5] img-match: blended SigLIP image signal into {blended}/{n} units (w={w})")


def _vlm_rerank(line: str, cands: list, *, log=print) -> int | None:
    """#6 VLM judge: crop each candidate panel to PNG and ask a Claude vision agent (Read
    tool reads the images) which best depicts `line`. cands = [(pool_idx, src, panel,
    page_tb)]. Returns the chosen pool_idx (override the cosine pick), -1 for "none of these"
    (caller holds the previous panel), or None when the judge is unavailable / undecided
    (caller keeps the cosine pick). Never raises."""
    import re as _re
    import tempfile
    import shutil
    from .._claude_sdk import sdk_available, sdk_complete_vision
    if not cands or not sdk_available():
        return None
    tmpdir = Path(tempfile.mkdtemp(prefix="panel_rerank_"))
    try:
        listed = []   # (display_n, pool_idx, png_path)
        for n, (pidx, src, panel, _page_tb) in enumerate(cands, start=1):
            outp = tmpdir / f"cand_{n}.png"
            try:
                _crop_panel(str(src), panel.get("bbox") or {}, outp, skip_mirror=True)
            except Exception as exc:
                log(f"[stage5] rerank crop failed cand {n}: {exc}")
                continue
            if outp.exists():
                listed.append((n, pidx, outp))
        if len(listed) < 2:
            return None
        system = ("You are a comic panel-matching judge. You look at panel ART and decide "
                  "which single panel best depicts a narration line. When MULTIPLE panels "
                  "depict the line about equally well, prefer the LARGER / more dramatic "
                  "(splash) panel — it reads as a highlight. Output ONLY a number.")
        body = "\n".join(f"{n}. {p}" for n, _pidx, p in listed)
        user = (
            f'Narration line: "{line}"\n\n'
            f"Below are {len(listed)} candidate comic panels as image files. Open (Read) EVERY "
            f"image, then decide which ONE panel best depicts the narration line. If several "
            f"fit about equally, pick the LARGER / more dramatic one.\n\n{body}\n\n"
            "Reply with ONLY a single integer: the number of the best-matching panel, or 0 if "
            "NONE of them depict the line. No other text."
        )
        resp = sdk_complete_vision(system, user, log=log)
        if not resp:
            return None
        mt = _re.search(r"-?\d+", resp)
        if not mt:
            return None
        pick = int(mt.group())
        if pick <= 0:
            return -1
        for n, pidx, _p in listed:
            if n == pick:
                return pidx
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _match_panels(units: list, pages_by_number: dict, cluster_to_name: dict,
                  *, project: str | None = None,
                  candidates_out: list | None = None, candidates_k: int = 12,
                  narration: dict | None = None) -> list:
    """Align the narration sequence to the panel sequence by ORDER-FREE content match:
    each unit takes its BEST-content panel independently (reuse allowed), with a per-unit
    PAGE-ANCHORED prior — a Gaussian bump centred on the unit's OWN page_ref (the Stage-3
    beat anchor) — pulling a line toward the page it depicts (forward for a chronological
    beat, BACKWARD for a backstory/twist beat). Content is scored PURE-VECTOR by
    _panel_content_score (cosine on the richer panel embed, from the Stage-2 Qdrant vectors
    when present). A unit whose best panel's raw cosine is below PANEL_COS_FLOOR HOLDS the
    previous panel rather than showing a wrong one. A unit whose scene carries a grounded/
    hand (page_ref, panel_ref) anchor is BOUND to that panel when PANEL_ANCHOR_BIND is on
    (default) — pre-assigned before Hungarian/greedy, bypassing the cosine floor, VLM
    rerank, and tie-breaks entirely. Cold-open for the intro, outro panel for the closing
    line. units=[(scene,text)] in audio order → [(panel,src)]."""
    pool = _panel_pool(pages_by_number)
    n = len(units)
    if n == 0:
        return []
    if not pool:
        return [(None, "")] * n
    m = len(pool)

    import numpy as np
    from .._embedding import embed_batch as _embed_batch
    from .._panel_index import load_vectors

    # Persisted panel vectors (Stage 2 → Qdrant); {} → fall back to in-memory embed.
    panel_vecs = load_vectors(project) if project else {}
    unit_vecs = _embed_batch([txt for _sc, txt in units])
    scene_text_list = [str(sc.get("text", "") or "") for sc, _txt in units]
    scene_vecs = _embed_batch(scene_text_list)
    if panel_vecs:
        print(f"[stage5] panel-match: PURE-VECTOR via {len(panel_vecs)} Qdrant vectors")
    else:
        print("[stage5] panel-match: PURE-VECTOR via in-memory embed (no Qdrant index)")

    content = np.full((n, m), -1.0e9, dtype="float64")
    sim = np.zeros((n, m), dtype="float64")
    for i, (scene, text) in enumerate(units):
        cv, sv = unit_vecs[i], scene_vecs[i]
        for j, (key, panel, _src, page_tb) in enumerate(pool):
            sc, sc_sim = _panel_content_score(
                panel, panel_vecs.get(key), cv, sv, page_tb,
                chunk_text=text, scene_text=scene_text_list[i])
            content[i][j] = sc
            sim[i][j] = sc_sim

    # Feature A: blend the desc-free SigLIP image signal into `content` BEFORE the page prior
    # (below). No-op — content untouched → EXACTLY the text-only path — when the image channel
    # is unavailable. `sim` (raw TEXT cosine) is left alone so the cosine-floor guard keeps its
    # text semantics.
    _blend_image_content(content, pool, units, project)

    # Cascade-HOLD guard: if the embedding backend is down/misconfigured, every
    # sim is ~0.0 → every story unit falls below PANEL_COS_FLOOR → every scene
    # silently HOLDs the cold-open panel (a video that shows one panel throughout).
    # Fail loud instead of shipping a broken render.
    story_units = [i for i, (sc, _t) in enumerate(units)
                   if not (sc.get("is_intro") or sc.get("is_outro"))]
    if len(story_units) >= 5:
        n_under_floor = sum(1 for i in story_units if float(np.max(sim[i])) < PANEL_COS_FLOOR)
        frac_under_floor = n_under_floor / len(story_units)
        if frac_under_floor >= 0.6:
            raise RuntimeError(
                f"panel-match: {n_under_floor}/{len(story_units)} story scenes have no panel "
                f"above cosine floor ({PANEL_COS_FLOOR}) — embedding backend likely down/"
                f"misconfigured; refusing to render a video where every scene shows the same panel")

    # CONTENT order: each unit takes its BEST-content panel independently —
    # order-FREE and REUSE-ALLOWED. A backstory line gets the backstory panel even when
    # it sits out of page order (Doom fix); two consecutive lines about the SAME moment
    # both hold that panel instead of no-reuse forcing a wrong one onto one of them.
    # Gemini's discriminative cosine makes a "magnet" panel (top for many different
    # subjects) unlikely. Per-unit PAGE-ANCHORED prior: each unit's bias is a Gaussian
    # bump centred on ITS OWN page_ref in PAGE space, so a line is pulled toward the page
    # it depicts — forward for a chronological beat, BACKWARD for a backstory/twist beat.
    # A unit with no page_ref (0) gets no positional bias → pure content decides (safe
    # fallback).
    biased = content.copy()
    if PANEL_FWD_BIAS > 0.0 and PANEL_PRIOR_SIGMA_PAGES > 0.0:
        two_sig2 = 2.0 * PANEL_PRIOR_SIGMA_PAGES * PANEL_PRIOR_SIGMA_PAGES
        panel_pages = [int(pool[j][0][0]) for j in range(m)]
        for i, (scene, _t) in enumerate(units):
            pref = int(scene.get("page_ref", 0) or 0)
            if pref <= 0:
                continue
            for j in range(m):
                d = panel_pages[j] - pref
                biased[i][j] += PANEL_FWD_BIAS * float(np.exp(-(d * d) / two_sig2))
    if not PANEL_ANCHOR_BIND and PANEL_ANCHOR_BONUS > 0.0:
        pool_key_to_j = {key: j for j, (key, _pan, _src, _tb) in enumerate(pool)}
        for i, (scene, _t) in enumerate(units):
            panel_ref = int(scene.get("panel_ref", -1) if scene.get("panel_ref") is not None else -1)
            if panel_ref < 0:
                continue
            page_ref = int(scene.get("page_ref", 0) or 0)
            j_anchor = pool_key_to_j.get((page_ref, panel_ref))
            if j_anchor is not None:
                biased[i][j_anchor] += PANEL_ANCHOR_BONUS

    # Review-gate export: hand back the matcher's OWN ranked shortlist per unit (biased
    # content+page-prior score, raw cosine), top-`candidates_k`, then RETURN EARLY. The
    # review UI wants the ranked list, not the final single pick — and must not fire the
    # VLM rerank (SDK cost) or the assignment. No-op on the render path (candidates_out None).
    if candidates_out is not None:
        for i in range(n):
            row = []
            for j in [int(x) for x in np.argsort(-biased[i])[:max(1, candidates_k)]]:
                key, panel, src, _tb = pool[j]
                row.append({"page": int(key[0]), "panel_idx": int(key[1]),
                            "score": float(biased[i][j]), "cosine": float(sim[i][j]),
                            "panel": panel, "src": src})
            candidates_out.append(row)
        return []

    # Panel area fraction (size/page) per pool index — for the big-shot tie-break.
    panel_fracs = []
    for _key, _pan, _src, _tb in pool:
        _bb = _pan.get("bbox") or {}
        _pa = int(_pan.get("_page_area", 0) or 0)
        _a = int(_bb.get("w", 0) or 0) * int(_bb.get("h", 0) or 0)
        panel_fracs.append((_a / _pa) if _pa else 0.0)
    # STORY units only — intro/outro are special-picked below (cold-open / outro panel),
    # so they neither need nor should consume a story panel.
    story_rows = [i for i, (sc, _t) in enumerate(units)
                  if not (sc.get("is_intro") or sc.get("is_outro"))]
    story_set = set(story_rows)
    idxs = [0] * n

    # Anchor BIND: pre-assign each anchored unit's panel BEFORE Hungarian/greedy runs, so
    # no later heuristic can override it. The panel is marked consumed so it drops out of
    # the assignment pool for the remaining (un-anchored) rows — PANEL_UNIQUE still holds
    # for them. Two units bound to the SAME panel is a legal authorial repeat: consuming
    # it twice is a no-op, not an error.
    anchored: set[int] = set()
    consumed_panels: set[int] = set()
    # Units whose authorial anchor was REJECTED by Feature C because the target panel is
    # UNTRUSTED (desc_verified=False page or dialog_mismatch panel). They flow through
    # normal content matching below, and Feature D forces them into VLM rerank regardless
    # of cosine (a poisoned description often yields a HIGH fake cosine — see the loop).
    distrusted_units: set[int] = set()
    # Panels already bound to EACH scene's own fragments (FIX 2, see FRAGMENT_SPREAD): a recap
    # scene fans into several fragment units that all carry the scene's ONE (page_ref, panel_ref),
    # so binding every one to key_panels[0] shows the SAME panel 2-4× in a row.
    scene_bound: dict[int, list[int]] = {}   # id(scene) -> panel js already given to its fragments
    if PANEL_ANCHOR_BIND:
        pool_key_to_j = {key: j for j, (key, _pan, _src, _tb) in enumerate(pool)}
        page_js: dict[int, list[int]] = {}   # page_number -> pool indices on that page
        for j, (key, _pan, _src, _tb) in enumerate(pool):
            page_js.setdefault(int(key[0]), []).append(j)
        for i in story_rows:
            scene, text = units[i]
            panel_ref = int(scene.get("panel_ref", -1) if scene.get("panel_ref") is not None else -1)
            if panel_ref < 0:
                continue
            page_ref = int(scene.get("page_ref", 0) or 0)
            j_anchor = pool_key_to_j.get((page_ref, panel_ref))
            if j_anchor is None:
                print(f"[stage5] match u{i}: ANCHOR MISS (page {page_ref}, idx {panel_ref}) not in "
                      f"pool — falling back to content match | {text[:42]!r}")
                continue
            # Feature C: don't hard-bind onto an UNTRUSTED panel. The anchor is only as
            # trustworthy as the page description that produced it, and a fabricated desc
            # silently mis-anchored a scene once (doom-rocket-raccoon #13). Leave the unit
            # un-anchored → normal content match + Feature-D VLM rerank get a say.
            if ANCHOR_TRUST and _panel_untrusted(pool[j_anchor][1]):
                reason = ("desc_verified=False" if pool[j_anchor][1].get("_page_desc_verified") is False
                          else "dialog_mismatch")
                print(f"[stage5] match u{i}: ANCHOR {pool[j_anchor][0]} UNTRUSTED ({reason}) — falling "
                      f"back to content match + rerank | {text[:42]!r}")
                distrusted_units.add(i)
                continue
            taken = scene_bound.setdefault(id(scene), [])
            if FRAGMENT_SPREAD and j_anchor in taken:
                # A sibling fragment already took this exact panel — give THIS fragment a
                # DISTINCT trusted panel on the SAME page, best-matching its own text. Prefer
                # a page-panel no scene has bound yet; fall back to any un-taken same-page one.
                cands = [j for j in page_js.get(page_ref, [])
                         if j not in taken and not (ANCHOR_TRUST and _panel_untrusted(pool[j][1]))]
                fresh = [j for j in cands if j not in consumed_panels]
                pick_from = fresh or cands
                if pick_from:
                    alt = max(pick_from, key=lambda j: content[i][j])
                    print(f"[stage5] match u{i}: ANCHOR spread {pool[j_anchor][0]}→{pool[alt][0]} "
                          f"(sibling fragment, same page) | {text[:42]!r}")
                    j_anchor = alt
                # else: page has no other distinct panel → keep the repeat (nothing better)
            elif j_anchor in consumed_panels:
                print(f"[stage5] match u{i}: ANCHOR {pool[j_anchor][0]} reuses a panel already bound "
                      f"to another scene (authorial repeat, allowed) | {text[:42]!r}")
            idxs[i] = j_anchor
            anchored.add(i)
            consumed_panels.add(j_anchor)
            taken.append(j_anchor)

    free_rows = [i for i in story_rows if i not in anchored]
    free_cols = [j for j in range(m) if j not in consumed_panels]
    if PANEL_UNIQUE and 0 < len(free_rows) <= len(free_cols):
        # Optimal 1:1 assignment: maximise total (content + page-prior) score with NO panel
        # reused across story scenes. A tiny size nudge breaks exact ties toward the larger
        # (splash) panel, mirroring the greedy big-shot tie-break. linear_sum_assignment
        # MINIMISES, so feed the negated score.
        from scipy.optimize import linear_sum_assignment
        score = np.array(
            [[biased[i][j] + 1e-6 * panel_fracs[j] for j in free_cols] for i in free_rows],
            dtype="float64")
        r, c = linear_sum_assignment(-score)
        for ri, cj in zip(r, c):
            idxs[free_rows[ri]] = free_cols[cj]
        for i in range(n):
            if i not in story_set:
                idxs[i] = int(np.argmax(biased[i]))   # placeholder — overridden by cold-open/outro
        print(f"[stage5] panel-match: UNIQUE assignment ({len(free_rows)} scene(s) -> distinct panels, "
              f"{len(anchored)} bound)")
    else:
        # Greedy in narration order with a soft reuse penalty — fallback when scenes > panels
        # (uniqueness impossible) or PANEL_UNIQUE=0. Each prior use docks PANEL_REUSE_PENALTY so
        # similar consecutive lines spread across distinct near-tie panels, while a line with no
        # good alternative still reuses (hold-same-subject). Anchored rows are already assigned
        # above and skipped here.
        used: dict[int, int] = {j: 1 for j in consumed_panels}
        for i in range(n):
            if i in anchored:
                continue
            if i not in story_set:
                # intro/outro: placeholder pick (overridden by cold-open/outro downstream). Do
                # NOT let it consume `used` — its phantom reuse-penalty would dock a real story
                # beat wanting the same panel. The Hungarian branch already excludes non-story
                # from contention; match that so the two paths pick consistently.
                idxs[i] = int(np.argmax(biased[i]))
                continue
            row = biased[i].copy()
            for j, cnt in used.items():
                row[j] -= PANEL_REUSE_PENALTY * cnt
            j = int(np.argmax(row))
            # Big-shot tie-break: among panels within PANEL_SIZE_TIE_MARGIN of the best
            # (content-similar), prefer the LARGER one for visual punch.
            if PANEL_SIZE_TIE_MARGIN > 0.0 and m > 1:
                top = float(row[j])
                near = [k for k in range(m) if float(row[k]) >= top - PANEL_SIZE_TIE_MARGIN]
                if len(near) > 1:
                    j = max(near, key=lambda k: panel_fracs[k])
            idxs[i] = j
            used[j] = used.get(j, 0) + 1

    # #6 — VLM rerank for LOW-confidence units. The page_ref a beat carries is itself an
    # LLM guess (Stage-3 outliner reads lossy panel descriptions), so a WRONG page_ref can
    # bury the correct panel via the prior. Vision is the only ground truth: a Claude judge
    # views a shortlist (top-K by score ∪ page_ref-page panels) and picks the panel that
    # best depicts the line (or NONE → hold). Gate fires on off-ref OR prior-overrode picks
    # (see below) so a wrong page_ref self-corrects — no per-comic page_ref fixes needed.
    # Standalone-safe (SDK throttles under concurrency).
    force_hold: set[int] = set()
    reranked: set[int] = set()
    if PANEL_RERANK:
        # Panels already assigned to OTHER story scenes — kept out of each unit's rerank
        # shortlist so the VLM can't re-pick a used panel and reintroduce a duplicate.
        assigned_now = set(idxs[r] for r in story_rows) if PANEL_UNIQUE else set()
        # Feature E: bound anchors whose panel cosine is far below the best-content panel's
        # (a likely-wrong Stage-3 page_ref) get re-checked by vision too; agreement is trusted.
        recheck_anchor: set[int] = set()
        if PANEL_ANCHOR_RECHECK:
            for i in anchored:
                if float(np.max(sim[i])) - float(sim[i][idxs[i]]) > ANCHOR_DISAGREE_MARGIN:
                    recheck_anchor.add(i)
        for i, (scene, text) in enumerate(units):
            if scene.get("is_intro") or scene.get("is_outro"):
                continue
            if i in anchored and i not in recheck_anchor:
                continue                                   # bound + cosine agrees → trusted, no VLM
            j = idxs[i]
            # Feature D: a unit whose anchor Feature C REJECTED as untrusted MUST get VLM
            # eyes even if its cosine looks strong. A poisoned description scores a HIGH
            # FAKE cosine (the bad text matched the bad panel text), so the "strong match →
            # trust cosine" gate below — and the on-ref/anchor-match trust gate — would skip
            # exactly the picks we least trust. Distrusted units bypass both and always get a
            # shortlist + _vlm_rerank (which still no-ops gracefully when the SDK is absent).
            distrusted = i in distrusted_units
            if not distrusted and float(sim[i][j]) >= PANEL_RERANK_COS_CEIL:
                continue                                   # strong match → trust cosine
            # Low-confidence pick → let the VLM verify, in two cases:
            #   off-ref     : cosine landed OFF the Stage-3 page_ref page, OR
            #   prior_overrode: the page_ref Gaussian prior MOVED the pick off cosine's
            #                   top panel onto a different page (Stage-3's page_ref may be
            #                   wrong — e.g. it anchored "stirred Eternity" to the wrong
            #                   page; the prior then buried the correct cosine-top panel).
            # An on-ref pick where the prior agreed with cosine is trusted (no VLM call).
            ref = int(scene.get("page_ref", 0) or 0)
            on_ref = ref > 0 and int(pool[j][0][0]) == ref
            panel_ref = int(scene.get("panel_ref", -1) if scene.get("panel_ref") is not None else -1)
            anchor_match = panel_ref >= 0 and pool[j][0] == (ref, panel_ref)
            cos_top = int(np.argmax(content[i]))           # cosine winner, BEFORE the prior
            prior_overrode = cos_top != j and int(pool[cos_top][0][0]) != int(pool[j][0][0])
            if not distrusted and i not in recheck_anchor and (on_ref or anchor_match) and not prior_overrode:
                continue
            topk = [int(x) for x in np.argsort(-biased[i])[:PANEL_RERANK_TOPK]]
            ref_js = [jj for jj in range(m) if int(pool[jj][0][0]) == ref]
            cand_js = list(dict.fromkeys(topk + ref_js))
            if PANEL_UNIQUE:
                # Drop panels owned by other scenes (keep this unit's own pick). If <2 remain,
                # _vlm_rerank no-ops (returns None) and the unique cosine pick stands.
                others = assigned_now - {j}
                cand_js = [c for c in cand_js if c not in others]
            cands = [(jj, pool[jj][2], pool[jj][1], pool[jj][3]) for jj in cand_js]
            pick = _vlm_rerank(text, cands)
            if pick is None:
                continue                                   # VLM absent/undecided → keep current pick
                                                           # (a recheck bind stays its trusted anchor)
            if pick == -1:
                force_hold.add(i)
                anchored.discard(i)                        # let the output honor the hold
                print(f"[stage5] rerank u{i}: VLM→NONE (hold) | {text[:42]!r}")
            elif pick != j:
                if PANEL_UNIQUE:
                    assigned_now.discard(j)
                    assigned_now.add(pick)
                idxs[i] = pick
                reranked.add(i)
                anchored.discard(i)                        # VLM overruled the bind → normal output
                print(f"[stage5] rerank u{i}: {pool[j][0]}→{pool[pick][0]} (VLM) | {text[:42]!r}")
            else:
                reranked.add(i)                            # VLM confirmed the cosine/anchor pick

    out = []
    prev: tuple | None = None
    for i, (scene, text) in enumerate(units):
        # Cold-open: the teaser opens on a striking OPENING panel, not a content match.
        # Exclude panels already assigned to story scenes — no duplicate opener.
        if i == 0 and scene.get("is_intro"):
            _story_keys = {pool[idxs[r]][0] for r in story_rows}
            cp, csrc = _cold_open_panel(pages_by_number, exclude_keys=_story_keys, narration=narration,
                                        project=project)
            if cp is not None:
                prev = (cp, csrc)
                out.append((cp, csrc))
                print(f"[stage5] match u{i}: COLD-OPEN | {text[:42]!r}")
                continue
        # Outro: LOOP-CLOSE — reuse the panel the video OPENED on (unit 0: cold-open
        # or first story match) so the last narrated frame ≈ frame 1 and the Short's
        # auto-replay reads as a seamless loop. Falls back to the closing-third focal
        # pick only when there is nothing before the outro.
        if scene.get("is_outro"):
            op, osrc = out[0] if out else _outro_panel(pages_by_number)
            if op is not None:
                prev = (op, osrc)
                out.append((op, osrc))
                print(f"[stage5] match u{i}: OUTRO-LOOP p{op.get('_page_number')} | {text[:42]!r}")
                continue
        j = idxs[i]
        key, panel, src, _tb = pool[j]
        if i in anchored:
            # BOUND: an authorial decision — skip the cosine floor, VLM hold, and every
            # tie-break entirely. It is truth.
            out.append((panel, src))
            prev = (panel, src)
            print(f"[stage5] match u{i}: ANCHOR {key} | {text[:42]!r}")
            continue
        if i in force_hold and prev is not None:
            out.append(prev)                             # VLM judged none depict it → hold
            print(f"[stage5] match u{i}: HOLD(vlm-none) | {text[:42]!r}")
        elif i not in reranked and float(sim[i][j]) < PANEL_COS_FLOOR and prev is not None:
            out.append(prev)                             # weak cosine, not VLM-chosen → hold
            print(f"[stage5] match u{i}: HOLD(weak) {key} cos={float(sim[i][j]):.3f} | {text[:42]!r}")
        else:
            out.append((panel, src))
            prev = (panel, src)
            tag = "ALIGN-VLM" if i in reranked else "ALIGN"
            print(f"[stage5] match u{i}: {tag} {key} cos={float(sim[i][j]):.3f} score={float(content[i][j]):.1f} | {text[:42]!r}")
    return out


def _plan_durations(
    *,
    scene_id: int,
    target: float,
    scene_timing: dict | None,
    word_timestamps: list[dict] | None,
) -> list[float]:
    n_shots = max(1, min(4, round(target / SHOT_TARGET_SECONDS)))
    if n_shots == 1:
        return [target]

    even_step = target / n_shots
    rel_splits = [even_step * i for i in range(1, n_shots)]

    if scene_timing and word_timestamps:
        scene_start = float(scene_timing.get("start", 0.0))
        scene_end = float(scene_timing.get("end", scene_start + target))
        gaps_abs = _silence_gaps_in_window(word_timestamps, scene_start, scene_end)
        rel_splits = [_snap_split_to_gaps(rel, scene_start, gaps_abs) for rel in rel_splits]

    rel_splits = sorted(_clamp_splits(rel_splits, target))
    boundaries = [0.0] + rel_splits + [target]
    return [round(max(0.4, boundaries[i + 1] - boundaries[i]), 3) for i in range(n_shots)]


def _silence_gaps_in_window(
    word_timestamps: list[dict], scene_start: float, scene_end: float
) -> list[float]:
    gaps: list[float] = []
    prev_end = scene_start
    for w in word_timestamps:
        ws = float(w.get("start", 0.0))
        we = float(w.get("end", 0.0))
        if we < scene_start or ws > scene_end:
            continue
        if ws - prev_end >= SILENCE_GAP_THRESHOLD:
            gaps.append((prev_end + ws) / 2.0)
        prev_end = max(prev_end, we)
    return gaps


def _snap_split_to_gaps(rel_split: float, scene_start: float, gaps_abs: list[float]) -> float:
    if not gaps_abs:
        return rel_split
    abs_split = scene_start + rel_split
    best_gap = min(gaps_abs, key=lambda g: abs(g - abs_split))
    if abs(best_gap - abs_split) <= SNAP_WINDOW_SECONDS:
        return max(SHOT_MIN_SECONDS / 2, best_gap - scene_start)
    return rel_split


def _clamp_splits(splits: list[float], total: float) -> list[float]:
    if not splits:
        return splits
    sorted_splits = sorted(splits)
    fixed: list[float] = []
    prev = 0.0
    for s in sorted_splits:
        s = max(prev + SHOT_MIN_SECONDS, min(s, total - SHOT_MIN_SECONDS))
        fixed.append(s)
        prev = s
    return fixed


def render_shot(
    shot: Shot,
    out_path: Path,
    *,
    work_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
    corner_logo: Path | None = None,
) -> Path:
    """Render one Ken Burns shot to MP4."""
    ff = _require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = work_dir or out_path.parent / "_panels"
    work_dir.mkdir(parents=True, exist_ok=True)

    panel_png = work_dir / f"panel_{shot.shot_id:03d}.png"
    avoid: list[dict] = []
    custom_src = getattr(shot, "custom_image", "") or ""
    if custom_src:
        # Master-added image: load it straight as the panel — no page crop (there is no
        # bbox/page for it), no mirror, no bubble-inpaint (no comic dialog to erase).
        # Everything downstream (upscale/frame/motion/ffmpeg) is unchanged.
        _load_custom_panel(custom_src, panel_png)
    else:
        geom: dict = {}
        _crop_panel(shot.source_image, shot.panel_bbox, panel_png,
                    text_bboxes=getattr(shot, "text_bboxes", None),
                    skip_mirror=getattr(shot, "no_mirror", False),
                    geom_out=geom)

        # Bubble rects → CROP-LOCAL coords (mirror-aware) so the 9:16 window can frame
        # the inpainted white blobs out (the frame-1 "empty bubble" slop, audit 2026-07-03).
        if geom:
            gl, gt = geom["left"], geom["top"]
            gw = geom["right"] - gl
            for tb in (getattr(shot, "text_bboxes", None) or []):
                ix0 = max(int(tb.get("x", 0)), gl); iy0 = max(int(tb.get("y", 0)), gt)
                ix1 = min(int(tb.get("x", 0)) + int(tb.get("w", 0)), geom["right"])
                iy1 = min(int(tb.get("y", 0)) + int(tb.get("h", 0)), geom["bottom"])
                if ix1 > ix0 and iy1 > iy0:
                    bx = ix0 - gl
                    if geom.get("mirrored"):
                        bx = gw - (ix1 - gl)
                    avoid.append({"x": bx, "y": iy0 - gt, "w": ix1 - ix0, "h": iy1 - iy0})

    # AI-upscale the crop BEFORE framing when it needs real magnification to fill
    # the frame — _prepare_panel_frame's own `cover` formula picks the same panels
    # its LANCZOS blow-up would otherwise soften. `avoid` was computed in the
    # original crop's pixel coords, so it's rescaled by the ACTUAL output size
    # (not an assumed ×4 — Real-ESRGAN's own rounding may differ slightly).
    frame_src = panel_png
    if PANEL_UPSCALE:
        with Image.open(panel_png) as _pim:
            piw, pih = _pim.size
        if _needs_upscale(piw, pih):
            up_path = _ai_upscale_panel(panel_png)
            if up_path != panel_png:
                with Image.open(up_path) as _uim:
                    uiw, uih = _uim.size
                rw, rh = uiw / piw, uih / pih
                avoid = [{"x": b["x"] * rw, "y": b["y"] * rh,
                          "w": b["w"] * rw, "h": b["h"] * rh} for b in avoid]
                frame_src = up_path

    framed = _prepare_panel_frame(frame_src, panel_png.with_name(panel_png.stem + "_9x16.png"),
                                  avoid_boxes=avoid,
                                  keep_contain=getattr(shot, "keep_contain", False),
                                  fit_mode=("fill" if getattr(shot, "fit_fill", False) else None))

    duration = max(0.4, shot.duration_seconds)
    frames = max(1, int(round(duration * FPS)))

    # BUG #121 fix (shaking): zoompan rounds the crop x/y to whole pixels every
    # frame. On an image already at output size with sub-pixel motion, that rounding
    # jitters the frame ("shake"). Pre-upscaling makes each rounding step a fraction of
    # an output pixel → smooth. Probe: 2× left ±33% velocity jitter, 4× cut it to ±14%
    # (2.3× smoother, +~0.5s/shot). 4× also keeps the tight push_top/push_detail sub-shot
    # framings sharp (their source region stays larger than the 1080×1920 output). Only for
    # moving shots — "static" (never emitted now) has no x/y motion, so skip the extra cost.
    if shot.motion == "static":
        pre = ""
    else:
        # Tight crops (push_top/push_detail) need 4× to stay sharp; full-frame moves
        # get by on the cheaper PRE_UPSCALE_FACTOR_FULL (adaptive upscale, perf).
        factor = PRE_UPSCALE_FACTOR if shot.motion in _TIGHT_MOTIONS else PRE_UPSCALE_FACTOR_FULL
        pre = f"scale={OUTPUT_W * factor}:{OUTPUT_H * factor}:flags=bicubic,"
    # Motion-comic: action/impact panels — and the cold-open hook shot — get a
    # stronger, faster camera push (energy in the opening seconds, not a slow hold).
    from config import MOTION_COMIC
    action = bool(MOTION_COMIC) and (
        _is_action_text(getattr(shot, "caption_text", "")) or getattr(shot, "is_intro", False))
    zp = _zoompan_expr(shot.motion, frames, action=action)

    # Build the filter chain: zoompan → [corner logo] → final.
    inputs = ["-framerate", "1", "-loop", "1", "-t", "1", "-i", str(framed)]
    segs = [f"[0:v]{pre}{zp}[vz]"]
    prev = "vz"
    if corner_logo is not None:
        # logo top-right with a 36px margin; logo PNG already carries its alpha
        inputs += ["-i", str(corner_logo)]
        segs.append(f"[{prev}][1:v]overlay=W-w-36:36[vl]")
        prev = "vl"
    filter_complex = ";".join(segs)

    cmd = [
        ff, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{prev}]",
        "-frames:v", str(frames),
        "-c:v", "libx264",
        # crf 18: intermediate must out-quality final (double-encode chain)
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-an",
        str(out_path),
    ]
    if progress:
        progress(f"[stage5] shot {shot.shot_id:03d} (scene {shot.scene_id}, "
                 f"{shot.motion}, {duration:.2f}s)")
    _run(cmd)
    return out_path


# Action/impact words in a shot's spoken clause → motion-comic push (stronger,
# faster camera). General + comic-agnostic; high-precision impact verbs only so
# calm/talky scenes are not falsely energized.
_ACTION_WORDS = frozenset((
    "punch punches punched smash smashes smashed blast blasts blasted "
    "explode explodes exploded explosion slam slams slammed strike strikes struck "
    "crash crashes crashed rip rips ripped tear tears tore unleash unleashes unleashed "
    "attack attacks attacked charge charges charged lunge lunges lunged slash slashes slashed "
    "clash clashes clashed erupt erupts erupted burst bursts shatter shatters shattered "
    "crush crushes crushed devour devours devoured destroy destroys destroyed "
    "seize seizes seized roar roars rampage leap leaps leaped fight fights fought "
    "kill kills killed claw claws clawed hurl hurls hurled"
).split())


def _is_action_text(text: str) -> bool:
    """True if the spoken clause signals a fight/impact moment (→ stronger camera)."""
    for raw in (text or "").lower().split():
        if raw.strip(".,!?;:'\"()—-") in _ACTION_WORDS:
            return True
    return False


def _zoompan_expr(motion: str, frames: int, action: bool = False) -> str:
    """ffmpeg zoompan expression. CALM panels push 1.00→1.10 (smoothstep). ACTION panels
    (fights/impacts) push 1.00→1.15 with an ease-OUT 'punch' (fast hit, then settle). The
    push amplitude was raised from 5% (measured a near-freeze at <1%/s) to 10-15% — with the
    shorter sub-shots this lands at the competitors' ~6-10%/s feel. push_top / push_detail are
    the sub-shot framings: a higher BASE zoom (a tighter cut) centered up / near-center, plus
    the same push — kept sharp by the 4× pre-upscale.

    Pans (pan_right/pan_down/pan_up) hold zoom at PAN_ZOOM and sweep the viewport across the
    WHOLE excess region (x: 0 → iw-iw/zoom, or the y equivalent) at CONSTANT velocity — linear
    on/d, no ease — ending EXACTLY on the far edge at the last frame (Master 2026-07-11: go one
    direction, full travel, land on the boundary when the scene cuts). No action variance on a
    pan: the travel is fixed by the panel, the velocity by the shot's own duration."""
    s = f"{OUTPUT_W}x{OUTPUT_H}"
    fps = FPS
    d = max(1, frames)
    lin = f"(on/{d})"                            # linear 0->1 (constant velocity) for pans
    if action:
        zamt, hi = f"{ZOOM_AMPLITUDE_ACTION:g}", f"{1 + ZOOM_AMPLITUDE_ACTION:g}"
        ease = f"(1-pow(1-on/{d},2))"            # ease-out: fast hit, then settle (punch)
    else:
        zamt, hi = f"{ZOOM_AMPLITUDE:g}", f"{1 + ZOOM_AMPLITUDE:g}"
        ease = f"pow(on/{d},2)*(3-2*(on/{d}))"   # smoothstep 0->1, eased ends
    # Sub-shot framings: start ALREADY zoomed (a hard cut to a tighter shot), then push. y is
    # clamped so the tight crop never runs off the top/bottom of the frame.
    if motion == "push_top":
        return (
            f"zoompan=z='{_SUBSHOT_TOP_ZOOM:g}+{zamt}*{ease}':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='max(0,min(ih-ih/zoom,ih*{_SUBSHOT_TOP_YC:g}-(ih/zoom/2)))':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "push_detail":
        return (
            f"zoompan=z='{_SUBSHOT_DETAIL_ZOOM:g}+{zamt}*{ease}':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='max(0,min(ih-ih/zoom,ih*{_SUBSHOT_DETAIL_YC:g}-(ih/zoom/2)))':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "zoom_in":
        return (
            f"zoompan=z='1+{zamt}*{ease}':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "zoom_out":
        return (
            f"zoompan=z='{hi}-{zamt}*{ease}':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    # Horizontal pan: hold z=PAN_ZOOM, sweep x across the FULL excess (0 → iw-iw/zoom) linearly,
    # landing on the right edge at the last frame. y stays centered.
    if motion == "pan_right":
        return (
            f"zoompan=z='{PAN_ZOOM:g}':"
            f"x='(iw-iw/zoom)*{lin}':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    # Vertical pans — z=PAN_ZOOM gives vertical room so the y travel stays inside the image
    # (no black bars). Full excess (0 → ih-ih/zoom) at constant velocity: pan_down sweeps
    # TOP→BOTTOM, pan_up sweeps BOTTOM→TOP, each landing exactly on its far edge.
    if motion == "pan_down":
        return (
            f"zoompan=z='{PAN_ZOOM:g}':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='(ih-ih/zoom)*{lin}':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "pan_up":
        return (
            f"zoompan=z='{PAN_ZOOM:g}':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='(ih-ih/zoom)*(1-{lin})':"
            f"d={frames}:s={s}:fps={fps}"
        )
    # static — no motion, slight constant zoom to fill output frame cleanly
    return (
        f"zoompan=z='1.00':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={s}:fps={fps}"
    )


# A panel needing more than this cover-scale factor is "too small" — blowing it
# up to fill the frame makes a blurry giant where you can't read the scene.
_BLUR_FALLBACK_SCALE = 2.5
# Don't upscale the sharp foreground panel beyond this in the blur-bg path.
_FG_MAX_SCALE = 2.0
# Bubble-avoiding cover-crop: don't slide the crop window off-center by more than
# this fraction of the free-axis slack. The window only knows where BUBBLES are
# (text_bboxes) — not where faces are — so a bounded slide trims a bubble edge
# without risking the subject leaving frame. ponytail: character bboxes exist in
# preprocessed pages; thread them in if a slide ever crops a face.
_AVOID_MAX_SHIFT_FRAC = float(os.getenv("AVOID_MAX_SHIFT_FRAC", "0.35"))


def _choose_crop_offset(new_w: int, new_h: int, out_w: int, out_h: int,
                        avoid_boxes: list[dict]) -> tuple[int, int]:
    """Pick the cover-crop window origin. Default = dead center (today's behavior).
    When `avoid_boxes` (inpainted-bubble rects, scaled coords) cover >2% of the
    centered window, slide along the ONE axis with slack — bounded by
    _AVOID_MAX_SHIFT_FRAC — to the candidate that minimises bubble area in frame;
    keep center unless the best candidate cuts that area by ≥25% (hysteresis, so
    clean/near-clean panels never churn). Empty speech bubbles survive inpaint as
    flat white blobs; framing them out is the cheapest slop-killer (frame-1 audit)."""
    cx0 = (new_w - out_w) // 2
    cy0 = (new_h - out_h) // 2
    if not avoid_boxes:
        return cx0, cy0

    def _covered(x0: int, y0: int) -> float:
        area = 0.0
        for b in avoid_boxes:
            ix0 = max(x0, b["x"]); iy0 = max(y0, b["y"])
            ix1 = min(x0 + out_w, b["x"] + b["w"]); iy1 = min(y0 + out_h, b["y"] + b["h"])
            if ix1 > ix0 and iy1 > iy0:
                area += (ix1 - ix0) * (iy1 - iy0)
        return area

    center_cov = _covered(cx0, cy0)
    if center_cov <= 0.02 * out_w * out_h:
        return cx0, cy0
    slack_x, slack_y = new_w - out_w, new_h - out_h
    cands: list[tuple[int, int]] = []
    for frac in (-_AVOID_MAX_SHIFT_FRAC, -_AVOID_MAX_SHIFT_FRAC / 2,
                 _AVOID_MAX_SHIFT_FRAC / 2, _AVOID_MAX_SHIFT_FRAC):
        if slack_x > 0:
            cands.append((min(slack_x, max(0, cx0 + int(slack_x * frac))), cy0))
        if slack_y > 0:
            cands.append((cx0, min(slack_y, max(0, cy0 + int(slack_y * frac)))))
    best = min(cands, key=lambda p: _covered(*p), default=(cx0, cy0))
    return best if _covered(*best) <= 0.75 * center_cov else (cx0, cy0)


def _should_blur_bg(iw: int, ih: int, *, keep_contain: bool = False,
                    fit_mode: str | None = None) -> bool:
    """contain+blur (True) vs cover-fill (False) for an iw×ih panel. Extracted from
    _prepare_panel_frame so the PANEL_FIT_MODE branch is unit-testable without PIL/ffmpeg I/O.

    Default fit_mode ("contain") reproduces the OLD rule EXACTLY: blur a small panel that
    would upscale past _BLUR_FALLBACK_SCALE OR a landscape strip at/over LANDSCAPE_COVER_MAX_ASPECT.
    "fill" instead cover-crops such a LANDSCAPE panel to fill the 9:16 frame — UNLESS keep_contain
    (critical baked text a crop would slice off), a tiny panel fill would blow up into a blurry
    giant (cover > _BLUR_FALLBACK_SCALE), or the crop would discard > FILL_MAX_AREA_LOSS of the
    panel (too-flat strip → meaningless sliver). keep_contain is IGNORED in "contain" mode (a
    blurred panel stays blurred), so the flag never changes the default output."""
    cover = max(OUTPUT_W / iw, OUTPUT_H / ih)
    aspect = iw / ih
    blur = cover > _BLUR_FALLBACK_SCALE or aspect >= LANDSCAPE_COVER_MAX_ASPECT
    if not blur:
        return False
    mode = fit_mode if fit_mode is not None else PANEL_FIT_MODE
    if mode != "fill" or keep_contain:
        return True
    if cover > _BLUR_FALLBACK_SCALE:
        return True                          # tiny panel → fill would be a blurry giant
    visible = TARGET_ASPECT / aspect if aspect > TARGET_ASPECT else 1.0
    if (1.0 - visible) > FILL_MAX_AREA_LOSS:
        return True                          # too-flat strip → cover-crop shows a sliver
    return False                             # fill: cover-crop to fill the frame


# Panels below this real magnification (crop size vs the frame it fills, same
# `cover` formula _should_blur_bg uses) are already big enough — skip the ~2s
# Real-ESRGAN call entirely.
_UPSCALE_MIN_MAGNIFICATION = 1.3


def _needs_upscale(iw: int, ih: int) -> bool:
    """True when an iw×ih crop needs more than _UPSCALE_MIN_MAGNIFICATION× cover-scale
    to fill 1080×1920 — extracted from render_shot so the gate is unit-testable
    without PIL/subprocess I/O (same rationale as _should_blur_bg)."""
    return max(OUTPUT_W / iw, OUTPUT_H / ih) > _UPSCALE_MIN_MAGNIFICATION


def _ai_upscale_panel(panel_png: Path) -> Path:
    """AI-upscale a panel crop with Real-ESRGAN (config.PANEL_UPSCALE) before framing.
    Crops are often 237-500px; _prepare_panel_frame's LANCZOS blow-up to fill
    1080×1920 (up to 8× for a small panel) reads soft — Real-ESRGAN sharpens the
    source first.

    Cached as `<stem>_up4.png` next to the crop; reused while newer than the input
    (the same panel crop reused across shots/re-renders shouldn't cost ~2s twice).
    ANY failure (binary missing, timeout, bad exit, no output file) falls back to
    the original crop — upscale is a quality bonus, never a hard requirement."""
    up = panel_png.with_name(panel_png.stem + "_up4.png")
    if up.exists() and up.stat().st_mtime >= panel_png.stat().st_mtime:
        return up
    model_dir = str(Path(REALESRGAN_BIN).parent / "models")
    try:
        subprocess.run(
            [REALESRGAN_BIN, "-i", str(panel_png), "-o", str(up),
             "-n", REALESRGAN_MODEL, "-m", model_dir, "-s", "4"],
            check=True, capture_output=True, timeout=60,
        )
    except Exception as exc:
        print(f"[stage5] Real-ESRGAN upscale failed ({exc}) — using original panel")
        return panel_png
    if not up.exists():
        print("[stage5] Real-ESRGAN produced no output — using original panel")
        return panel_png
    return up


def _prepare_panel_frame(panel_png: Path, out_path: Path,
                         avoid_boxes: list[dict] | None = None,
                         *, keep_contain: bool = False,
                         fit_mode: str | None = None) -> Path:
    """Fit the panel into 1080×1920.

    `avoid_boxes` — inpainted-bubble rects in the CROP's own pixel coords; the
    cover-crop window slides (bounded) along its free axis to keep those empty
    white blobs out of frame (see _choose_crop_offset). None/[] → dead center,
    byte-identical to the old behavior. The blur-bg path ignores them: the sharp
    foreground is the WHOLE panel (can't crop it) and background bubbles blur out.

    Default = cover-scale (fill frame, crop overflow). Reference channels favor
    this when the panel is large enough to fill the frame without much upscale.

    BUG 1 fix (+ extreme-wide fix): when a panel would need >2.5× cover-scale, the
    cover-crop is bad in TWO ways — a small frame-shaped panel becomes a blurry
    giant, and a wide/tall panel gets cropped down to a meaningless center sliver
    (e.g. a 3.8:1 establishing strip at 6× cover shows only ~15% of its width).
    BOTH are fixed by contain+blur: show the WHOLE panel sharp (capped at 2×
    upscale) centered over a blurred copy of itself filling the frame.

    MOTION CORE 2026-07-04: competitors FILL the 9:16 frame — letterboxing (contain+blur)
    shrinks the subject, the measured mid-video "tiny subject" defect. So a MODERATE landscape
    now cover-crops to fill (centered = the panel's salient region), trading the old whole-panel
    view for a full-frame subject. Triggers for contain+blur are now narrower (either):
      (1) cover-scale > _BLUR_FALLBACK_SCALE (a small panel blown up >2.5× = blurry giant), OR
      (2) an EXTREME strip, aspect ≥ LANDSCAPE_COVER_MAX_ASPECT, where a centered cover-crop
          would show only a meaningless sliver (e.g. a 3.8:1 establishing strip).
    Portrait/tall splashes (ih>iw) stay on cover-scale and fill the frame edge-to-edge."""
    with Image.open(panel_png) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        cover = max(OUTPUT_W / iw, OUTPUT_H / ih)

        aspect = iw / ih
        mode = fit_mode or PANEL_FIT_MODE
        use_blur_bg = _should_blur_bg(iw, ih, keep_contain=keep_contain, fit_mode=mode)
        # fill requested (env or micro fit_fill) but this landscape panel still letterboxes → say why.
        if (mode == "fill" and not keep_contain and use_blur_bg
                and aspect >= LANDSCAPE_COVER_MAX_ASPECT):
            visible = TARGET_ASPECT / aspect if aspect > TARGET_ASPECT else 1.0
            reason = ("blurry-giant upscale" if cover > _BLUR_FALLBACK_SCALE
                      else f"would crop {1 - visible:.0%} of area > {FILL_MAX_AREA_LOSS:.0%}")
            print(f"[stage5] fit=fill kept CONTAIN (panel {iw}x{ih}, aspect {aspect:.2f}: {reason})")
        if not use_blur_bg:
            # ── Cover-scale (original behavior; bubble-aware window) ──
            new_w = max(OUTPUT_W, int(round(iw * cover)))
            new_h = max(OUTPUT_H, int(round(ih * cover)))
            scaled = im.resize((new_w, new_h), Image.LANCZOS)
            scaled_avoid = [
                {"x": int(b["x"] * cover), "y": int(b["y"] * cover),
                 "w": int(b["w"] * cover), "h": int(b["h"] * cover)}
                for b in (avoid_boxes or [])
            ]
            x0, y0 = _choose_crop_offset(new_w, new_h, OUTPUT_W, OUTPUT_H, scaled_avoid)
            frame = scaled.crop((x0, y0, x0 + OUTPUT_W, y0 + OUTPUT_H))
        else:
            # ── Contain + blurred background ──
            # 1. Background: cover-scale the panel, crop to frame, heavy blur.
            bw = max(OUTPUT_W, int(round(iw * cover)))
            bh = max(OUTPUT_H, int(round(ih * cover)))
            bg = im.resize((bw, bh), Image.LANCZOS)
            bx = (bw - OUTPUT_W) // 2
            by = (bh - OUTPUT_H) // 2
            bg = bg.crop((bx, by, bx + OUTPUT_W, by + OUTPUT_H))
            bg = bg.filter(ImageFilter.GaussianBlur(radius=40))
            # 2. Foreground: contain-fit the whole panel, capped at 2× upscale
            #    so it stays sharp. Centered on the blurred background.
            contain = min(OUTPUT_W / iw, OUTPUT_H / ih)
            fg_scale = min(contain, _FG_MAX_SCALE)
            fw = max(1, int(round(iw * fg_scale)))
            fh = max(1, int(round(ih * fg_scale)))
            fg = im.resize((fw, fh), Image.LANCZOS)
            px = (OUTPUT_W - fw) // 2
            py = (OUTPUT_H - fh) // 2
            bg.paste(fg, (px, py))
            frame = bg
    frame.save(out_path, "PNG")
    return out_path


_LAMA = None          # lazy SimpleLama singleton
_LAMA_FAILED = False   # set once if LaMa is unavailable → caller falls back
_CLEAN_PANEL_CACHE: dict[tuple, "Path"] = {}  # (src,bbox,text,mirror) → cleaned PNG


def _lama_clean(img_bgr, text_bboxes, iw: int, ih: int):
    """Erase the comic's own speech-bubble text with LaMa (deep inpainting).

    LaMa (Fast-Fourier-Conv model, via `simple_lama_inpainting`) reconstructs the
    region behind the text *semantically* — continuing the bubble's white or the
    art behind an SFX — so the result is seamless, unlike cv2's pixel-diffusion
    smear. Mask = each text bbox padded a few px (LaMa handles rectangle masks).

    Returns the cleaned BGR ndarray, or None if LaMa can't load (caller falls
    back to a plain cv2 inpaint). First call downloads the ~200 MB model.
    """
    global _LAMA, _LAMA_FAILED
    if _LAMA_FAILED:
        return None
    try:
        import numpy as np
        from PIL import Image
        if _LAMA is None:
            from simple_lama_inpainting import SimpleLama
            _LAMA = SimpleLama()
        mask = np.zeros((ih, iw), np.uint8)
        for tb in (text_bboxes or []):
            x, y = int(tb.get("x", 0)), int(tb.get("y", 0))
            w, h = int(tb.get("w", 0)), int(tb.get("h", 0))
            if w <= 0 or h <= 0:
                continue
            x0, y0 = max(0, x - 4), max(0, y - 4)
            x1, y1 = min(iw, x + w + 4), min(ih, y + h + 4)
            mask[y0:y1, x0:x1] = 255
        if not mask.any():
            return img_bgr
        rgb = Image.fromarray(img_bgr[:, :, ::-1])   # BGR → RGB
        mask_img = Image.fromarray(mask)
        # Cap LaMa input resolution. A double-page-SPREAD crop (e.g. 3960×2299 ≈ 9MP)
        # blows LaMa's memory and HARD-CRASHES the process (SIGKILL, no traceback).
        # The final frame is ≤1080×1920, so inpainting at reduced resolution is
        # lossless for us — downscale image+mask, inpaint, then resize back to (iw,ih).
        _MAX_SIDE = 1600
        if max(iw, ih) > _MAX_SIDE:
            s = _MAX_SIDE / max(iw, ih)
            rgb = rgb.resize((max(1, int(iw * s)), max(1, int(ih * s))))
            mask_img = mask_img.resize(rgb.size)
        out = _LAMA(rgb, mask_img)                    # PIL RGB result
        if out.size != (iw, ih):
            out = out.resize((iw, ih))
        return np.ascontiguousarray(np.array(out)[:, :, ::-1])  # RGB → BGR
    except Exception:
        _LAMA_FAILED = True
        return None


def _load_custom_panel(src_path: str, out_path: Path) -> Path:
    """Load a Master-added custom image straight as the panel PNG for render_shot — no page
    crop (no bbox/page context exists for it), no mirror, no bubble-inpaint (no comic dialog
    to erase off an arbitrary photo). Raises FileNotFoundError if missing, mirroring
    _crop_panel's own contract (render_shot lets that propagate — a stale/deleted custom
    image should fail loud, not silently render a placeholder)."""
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"custom image missing: {src}")
    with Image.open(src) as im:
        im.convert("RGB").save(out_path, "PNG")
    return out_path


def _crop_panel(source_image: str, bbox: dict[str, int], out_path: Path,
                text_bboxes: list[dict] | None = None,
                skip_mirror: bool = False,
                geom_out: dict | None = None) -> Path:
    """Crop one panel from the source page, then (1) inpaint the comic's own
    speech-bubble text out of the CROP and (2) mirror it horizontally. Uses
    OpenCV; falls back to a plain PIL crop (no inpaint/mirror) if cv2 is missing.

    PERF (A): inpaint runs on the small CROP, not the whole page — LaMa is the
    dominant cost and a panel is a fraction of the page (~5× faster). PERF (B):
    the cleaned result is cached by (source, bbox, text, mirror) so a panel shown
    across several shots/scenes is inpainted ONCE, then copied."""
    src = Path(source_image)
    if not src.exists():
        raise FileNotFoundError(f"source image missing: {src}")

    # Crop-window geometry ONCE (PIL header read is cheap) — shared by the cv2 and
    # PIL branches (the pad math was duplicated) and filled into geom_out even on a
    # cache hit, so bubble-aware framing works for reused panels too.
    with Image.open(src) as _im:
        _iw, _ih = _im.size
    _x = int(bbox.get("x", 0)); _y = int(bbox.get("y", 0))
    _w = int(bbox.get("w", 0)); _h = int(bbox.get("h", 0))
    if _w <= 0 or _h <= 0:
        _x, _y, _w, _h = 0, 0, _iw, _ih
    _p = _pad_pct_for(_w, _h, _iw, _ih)
    g_left = max(0, _x - int(_w * _p)); g_top = max(0, _y - int(_h * _p))
    g_right = min(_iw, _x + _w + int(_w * _p)); g_bottom = min(_ih, _y + _h + int(_h * _p))
    if geom_out is not None:
        geom_out.update(left=g_left, top=g_top, right=g_right, bottom=g_bottom,
                        mirrored=bool(MIRROR_PANELS and not skip_mirror))

    # (B) cache — same panel crop reused across shots → inpaint once, copy after.
    cache_key = (
        str(src), int(bbox.get("x", 0)), int(bbox.get("y", 0)),
        int(bbox.get("w", 0)), int(bbox.get("h", 0)),
        bool(MIRROR_PANELS and not skip_mirror),
        bool(INPAINT_BUBBLE_TEXT),
        tuple((int(b.get("x", 0)), int(b.get("y", 0)), int(b.get("w", 0)), int(b.get("h", 0)))
              for b in (text_bboxes or [])),
    )
    cached = _CLEAN_PANEL_CACHE.get(cache_key)
    if cached is not None and Path(cached).exists():
        shutil.copyfile(str(cached), str(out_path))
        return out_path

    try:
        import cv2
        import numpy as np
    except ImportError:
        cv2 = None

    if cv2 is not None:
        # Robust read (handles spaces/unicode in the path better than imread).
        data = np.fromfile(str(src), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)  # BGR full page
        if img is None:
            cv2 = None  # decode failed → fall back to PIL below

    if cv2 is not None:
        # 1. Crop the panel region (with padding) FIRST — so inpaint works on a
        #    small image, not the whole page. Geometry precomputed above.
        left, top, right, bottom = g_left, g_top, g_right, g_bottom
        crop = img[top:bottom, left:right]
        ch, cw = crop.shape[:2]
        # 2. Inpaint text bboxes → erase dialogue. Page-coordinate text bboxes are
        #    translated into CROP-LOCAL coords and clipped to the crop.
        if INPAINT_BUBBLE_TEXT and text_bboxes and cw > 0 and ch > 0:
            local: list[dict] = []
            for tb in text_bboxes:
                tx, ty = int(tb.get("x", 0)), int(tb.get("y", 0))
                tw, th = int(tb.get("w", 0)), int(tb.get("h", 0))
                ix0 = max(tx, left); iy0 = max(ty, top)
                ix1 = min(tx + tw, right); iy1 = min(ty + th, bottom)
                if ix1 > ix0 and iy1 > iy0:
                    local.append({"x": ix0 - left, "y": iy0 - top,
                                  "w": ix1 - ix0, "h": iy1 - iy0})
            if local:
                cleaned = _lama_clean(crop, local, cw, ch)
                if cleaned is not None:
                    crop = cleaned
                else:
                    mask = np.zeros((ch, cw), np.uint8)
                    for tb in local:
                        cx, cy = tb["x"], tb["y"]
                        cv2.rectangle(mask, (max(0, cx - 4), max(0, cy - 4)),
                                      (min(cw, cx + tb["w"] + 4), min(ch, cy + tb["h"] + 4)),
                                      255, -1)
                    if mask.any():
                        crop = cv2.inpaint(crop, mask, 6, cv2.INPAINT_NS)
        # 3. Mirror horizontally — UNLESS this panel has story-critical readable
        #    text baked into the art (gravestone/sign), which a flip would reverse.
        if MIRROR_PANELS and not skip_mirror:
            crop = cv2.flip(crop, 1)
        ok, buf = cv2.imencode(".png", crop)
        if ok:
            buf.tofile(str(out_path))
            _CLEAN_PANEL_CACHE[cache_key] = out_path   # (B) remember for reuse
            return out_path
        # encode failed → fall through to PIL

    # ── PIL fallback (no inpaint, no mirror) — geometry precomputed above ──
    if geom_out is not None:
        geom_out["mirrored"] = False   # this path never mirrors
    with Image.open(src) as im:
        cropped = im.convert("RGB").crop((g_left, g_top, g_right, g_bottom))
        cropped.save(out_path, "PNG")
    return out_path


def _require_ffmpeg() -> str:
    from config import FFMPEG_BIN
    if os.path.isabs(FFMPEG_BIN) and os.path.isfile(FFMPEG_BIN):
        return FFMPEG_BIN
    p = shutil.which(FFMPEG_BIN) or shutil.which("ffmpeg")
    if not p:
        raise FileNotFoundError(f"ffmpeg not found (FFMPEG_BIN={FFMPEG_BIN}). Check .env or PATH.")
    return p


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = (res.stderr or "")[-2000:]
        raise RuntimeError(
            f"ffmpeg failed (exit {res.returncode})\ncmd: {' '.join(cmd)}\nstderr:\n{tail}"
        )
