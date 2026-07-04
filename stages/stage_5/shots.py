"""Shot list construction and per-shot ffmpeg Ken Burns rendering."""
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
# the whole page renders with BACKWARDS lettering). So the mirror stays ON, but
# `no_mirror` is set for whole-page / no-panel renders (see render_shots), for panels
# whose art carries readable text (_panel_has_critical_text), and UNCONDITIONALLY for
# the cold-open (is_intro) — frame 1 is too retention-critical to risk backwards text.
MIRROR_PANELS = True
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


MOTION_CYCLE = ("zoom_in", "pan_down", "zoom_out", "pan_up", "pan_right")

# Two strategies:
#   "scene"          — one continuous Ken-Burns per scene (ComicsUnlocked: 4-5 shots, 10-30s each)
#   "caption_chunk"  — one shot per caption chunk (TheComicCivilian: 35-40 shots, ~1.5s each,
#                      visual changes EVERY time the on-screen text changes)
SHOT_STRATEGY = "caption_chunk"

SHOTS_PER_SCENE = 1            # used when SHOT_STRATEGY == "scene"
SHOT_TARGET_SECONDS = 3.0   # ~1 panel / 3s — cuts were racing AHEAD of the narration
                            # (panel changed mid-sentence). 3s holds each panel long
                            # enough to track the voiceover. Raise further for calmer.
SHOT_MIN_SECONDS = 0.6         # caption-chunk mode: ~0.5-2s per shot
SILENCE_GAP_THRESHOLD = 0.2
SNAP_WINDOW_SECONDS = 0.5

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
    if SHOT_STRATEGY == "caption_chunk" and caption_chunks and pages_by_number is not None:
        return _build_shots_per_chunk(
            narration, caption_chunks, pages_by_number, scene_timings or [],
            cluster_to_name=cluster_to_name or {}, project=project,
        )
    return _build_shots_per_scene(narration, scene_timings, word_timestamps)


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


def _build_shots_per_chunk(
    narration: dict,
    caption_chunks: list[dict],
    pages_by_number: dict[int, dict],
    scene_timings: list[dict],
    *,
    cluster_to_name: dict[int, str] | None = None,
    project: str | None = None,
) -> list[Shot]:
    """TheComicCivilian-style: one shot per caption chunk, with SMART panel
    selection scoring each candidate panel against the chunk text. Pool spans
    the scene's page ±1 adjacent pages; never repeats within a scene; falls
    back to widest pool only when exhausted."""
    scenes = narration.get("scenes") or []
    scenes_by_id = {int(s.get("scene_id") or i): s for i, s in enumerate(scenes, start=1)}

    # Map each chunk to its scene by time
    def find_scene_for_chunk(c):
        c_mid = (float(c.get("start", 0)) + float(c.get("end", 0))) / 2
        for st in scene_timings:
            if float(st.get("start", 0)) <= c_mid < float(st.get("end", 1e9)):
                return scenes_by_id.get(int(st.get("scene_id", 0)))
        return scenes_by_id.get(1) if scenes_by_id else None

    # Pre-compute each chunk's (scene, text, start, extended-duration) so the
    # grouping below preserves the exact audio timeline (silence between chunks
    # is absorbed forward into the preceding chunk, as before).
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

    # Group consecutive chunks that belong to the same scene (E).
    groups: list[tuple[dict, list[tuple[str, float, float]]]] = []
    for scene, text, c_start, dur in enriched:
        sid = int(scene.get("scene_id") or 1)
        if groups and int(groups[-1][0].get("scene_id") or 1) == sid:
            groups[-1][1].append((text, c_start, dur))
        else:
            groups.append((scene, [(text, c_start, dur)]))

    # ── Narration-driven panel matching ─────────────────────────────────────
    # Flatten the scenes into ordered narration UNITS (one per visual beat), then
    # match each unit to its best-content panel via _match_panels.
    units: list[tuple[dict, list, str]] = []   # (scene, slice_members, match_text)
    for scene, members in groups:
        scene_text = str(scene.get("text", "") or "")
        if scene.get("is_intro") or scene.get("is_outro"):
            clause_texts, slices = [scene_text], [members]
        else:
            # Panel changes track the narration's SEMANTIC subject, NOT audio time-slices.
            # A scene with real visual_beats (multiple drawn moments) → one panel per beat;
            # a single-subject scene → ONE panel held for the whole sentence (the design's
            # "hold-while-same-subject"). No time-split: a panel changing faster than the
            # narration outruns it (Master, Doom 2026-06-27).
            beats = [str(c).strip() for c in (scene.get("visual_beats") or []) if str(c).strip()]
            if len(beats) > 1:
                buckets = _split_members_by_clause(members, beats)
                pairs = [(c, b) for c, b in zip(beats, buckets) if b] or [(scene_text, members)]
                clause_texts = [c for c, _ in pairs]
                slices = [b for _, b in pairs]
            else:
                clause_texts, slices = [scene_text], [members]   # one held panel per scene
        for ct, sl in zip(clause_texts, slices):
            spoken = " ".join(str(m[0]) for m in sl).strip() or ct
            units.append((scene, sl, spoken))

    assigned = _match_panels(
        [(sc, txt) for sc, _sl, txt in units],
        pages_by_number or {}, cluster_to_name or {}, project=project,
    )

    shots: list[Shot] = []
    shot_id = 0
    audit_whole = []
    for (scene, slice_members, _spoken), (panel, source_image) in zip(units, assigned):
        slice_dur = sum(m[2] for m in slice_members)
        if panel is not None and panel.get("_whole_page") and not scene.get("is_intro"):
            audit_whole.append(int(scene.get("scene_id") or 0))
        text_bboxes: list[dict] = []
        if panel is None:
            bbox = scene.get("panel_bbox") or {}
            source_image = source_image or str(scene.get("source_image") or "")
        else:
            bbox = panel.get("bbox") or {}
            text_bboxes = _panel_text_bboxes(panel, pages_by_number or {})
        motion = _choose_motion(panel, slice_dur, seq=shot_id)
        if scene.get("is_outro"):
            # LOOP-CLOSE: the outro reuses the opening panel; zoom_out ENDS at z=1.0
            # centered — the exact framing the cold-open zoom_in STARTS from.
            motion = "zoom_out"
        if panel is not None and panel.get("_whole_page"):
            # Whole page: slow reveal of the full page; never a random pan, never static.
            motion = "zoom_out"
        shots.append(Shot(
            shot_id=shot_id,
            scene_id=int(scene.get("scene_id") or 1),
            duration_seconds=max(0.4, slice_dur),
            panel_bbox={"x": int(bbox.get("x", 0)), "y": int(bbox.get("y", 0)),
                        "w": int(bbox.get("w", 0)), "h": int(bbox.get("h", 0))},
            source_image=source_image,
            motion=motion,
            text_bboxes=text_bboxes,
            caption_text=" ".join(str(m[0]) for m in slice_members).strip(),
            # Skip the mirror when it would reverse legible text: a whole-page or a
            # no-panel render shows uncleaned bubbles, and a panel whose art carries
            # readable text (sign/monitor/nameplate) would flip into gibberish.
            # The COLD-OPEN (is_intro) is ALWAYS un-mirrored, unconditionally: frame 1 is
            # too retention-critical to risk backwards lettering, and _panel_has_critical_text
            # provably misses small in-panel dialogue strips (the spider-man "ONE I'M SLYDE"
            # opener rendered mirrored = instant AI-slop). A cold-open frame gains nothing
            # from the flip's dedup purpose anyway (it opens the video; there is no earlier
            # frame to differ from).
            no_mirror=(panel is None or bool(panel.get("_whole_page"))
                       or _panel_has_critical_text(panel)
                       or bool(scene.get("is_intro"))),
            is_intro=bool(scene.get("is_intro")),
        ))
        shot_id += 1
    if audit_whole:
        print(f"[stage5] panel-match: {len(audit_whole)} scene(s) → whole-page "
              f"fallback (scene_ids {audit_whole})")
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
    cands = ["zoom_in", "zoom_out"]
    if ar >= 0.9:          # tall or square → up↕down reveal reads
        cands += ["pan_down", "pan_up"]
    if ar <= 1.15:         # wide or square → left↔right reveal reads
        cands.append("pan_right")
    return cands[seq % len(cands)]


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


def _cold_open_panel(pages_by_number, exclude_keys=None):
    """COLD-OPEN: pick a striking STORY panel to open the video on instead of the
    cover. Frame 1 is the single most retention-critical moment, so the pick is by a
    SCORE — not raw area. Largest-area alone landed on the two worst openers seen in
    shipped videos: (a) a WIDE overhead establishing shot (Doom tiny at a dinner table,
    ~12 empty speech bubbles) that mismatches the hook, and (b) a LANDSCAPE strip that
    _prepare_panel_frame letterboxes into a thin blurred band. Both waste the 9:16 frame.

    Rank opening-third candidates by (all terms 0..1):
        score = 0.45*area_frac      # still reward a big panel …
              + 0.35*aspect_fit     # … but a PORTRAIT/near-9:16 panel FILLS the frame;
                                     #   a landscape panel (letterboxed) scores ~0 here
              + 0.15*has_character   # a face/figure opens stronger than empty scenery
              - 0.25*dialog_load     # a CLEAN splash beats a bubble-cluttered panel on a
                                     #   HELD opening frame (empty bubbles read as slop)

    Skips cover/credits/ad pages, the final 2 story pages (no ending spoiler), and
    `exclude_keys` = (page, idx) panels already assigned to story scenes (the intro must
    never duplicate one — it would play as the same image twice within seconds). Falls
    back to the largest-area candidate when nothing scores positively, so an all-wide /
    all-cluttered opening still returns a panel (never None when panels exist). Returns
    (panel, src) or (None, '')."""
    exclude_keys = exclude_keys or set()
    story_pns = sorted(pn for pn, pg in (pages_by_number or {}).items()
                       if pg and not _is_skip_page(pg))
    if not story_pns:
        return None, ""
    ending = set(story_pns[-2:])   # exclude the last 2 story pages (ending/outro splash)
    # Only the OPENING third (≥3 pages): the hook is about the SETUP, so frame 1 should
    # come from where the premise is established — not the globally-largest panel, which
    # was landing on a mid-story action splash unrelated to the opening line.
    opening = story_pns[:max(3, len(story_pns) // 3)]
    best = None      # (score, panel_dict, src)
    biggest = None   # (area, panel_dict, src) — fallback when nothing scores positively
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
            has_char = 1.0 if (panel.get("characters") or []) else 0.0
            score = (0.45 * area_frac + 0.35 * aspect_fit
                     + 0.15 * has_char - 0.25 * dialog_load)
            if best is None or score > best[0]:
                best = (score, pw, src)
    if best is not None and best[0] > 0:
        return best[1], best[2]
    if biggest is not None:   # nothing scored well (all wide/cluttered) → old largest-area
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
                  *, project: str | None = None) -> list:
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
    if PANEL_ANCHOR_BIND:
        pool_key_to_j = {key: j for j, (key, _pan, _src, _tb) in enumerate(pool)}
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
            if j_anchor in consumed_panels:
                print(f"[stage5] match u{i}: ANCHOR {pool[j_anchor][0]} reuses a panel already bound "
                      f"to another scene (authorial repeat, allowed) | {text[:42]!r}")
            idxs[i] = j_anchor
            anchored.add(i)
            consumed_panels.add(j_anchor)

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
        for i, (scene, text) in enumerate(units):
            if scene.get("is_intro") or scene.get("is_outro"):
                continue
            if i in anchored:
                continue                                   # bound (TRUSTED) — never enters rerank
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
            if not distrusted and (on_ref or anchor_match) and not prior_overrode:
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
                continue
            if pick == -1:
                force_hold.add(i)
                print(f"[stage5] rerank u{i}: VLM→NONE (hold) | {text[:42]!r}")
            elif pick != j:
                if PANEL_UNIQUE:
                    assigned_now.discard(j)
                    assigned_now.add(pick)
                idxs[i] = pick
                reranked.add(i)
                print(f"[stage5] rerank u{i}: {pool[j][0]}→{pool[pick][0]} (VLM) | {text[:42]!r}")
            else:
                reranked.add(i)                            # VLM confirmed the cosine pick

    out = []
    prev: tuple | None = None
    for i, (scene, text) in enumerate(units):
        # Cold-open: the teaser opens on a striking OPENING panel, not a content match.
        # Exclude panels already assigned to story scenes — no duplicate opener.
        if i == 0 and scene.get("is_intro"):
            _story_keys = {pool[idxs[r]][0] for r in story_rows}
            cp, csrc = _cold_open_panel(pages_by_number, exclude_keys=_story_keys)
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
    banner_text: str = "",
) -> Path:
    """Render one Ken Burns shot to MP4."""
    ff = _require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = work_dir or out_path.parent / "_panels"
    work_dir.mkdir(parents=True, exist_ok=True)

    panel_png = work_dir / f"panel_{shot.shot_id:03d}.png"
    _crop_panel(shot.source_image, shot.panel_bbox, panel_png,
                text_bboxes=getattr(shot, "text_bboxes", None),
                skip_mirror=getattr(shot, "no_mirror", False))

    framed = _prepare_panel_frame(panel_png, panel_png.with_name(panel_png.stem + "_9x16.png"))

    duration = max(0.4, shot.duration_seconds)
    frames = max(1, int(round(duration * FPS)))

    # BUG #121 fix (shaking): zoompan rounds the crop x/y to whole pixels every
    # frame. On an image already at output size with sub-pixel motion (zoom only
    # 1.0→1.05), that rounding jitters the frame ("shake"). Pre-upscaling 2×
    # makes each rounding step half an output pixel → smooth. Only for moving
    # shots — static has no x/y motion, so skip the extra encode cost.
    if shot.motion in ("zoom_in", "zoom_out", "pan_right", "pan_down", "pan_up"):
        pre = f"scale={OUTPUT_W * 2}:{OUTPUT_H * 2}:flags=bicubic,"
    else:
        pre = ""
    # Motion-comic: action/impact panels — and the cold-open hook shot — get a
    # stronger, faster camera push (energy in the opening seconds, not a slow hold).
    from config import MOTION_COMIC
    action = bool(MOTION_COMIC) and (
        _is_action_text(getattr(shot, "caption_text", "")) or getattr(shot, "is_intro", False))
    zp = _zoompan_expr(shot.motion, frames, action=action)

    # Build the filter chain: zoompan → [corner logo] → [title banner] → final.
    inputs = ["-framerate", "1", "-loop", "1", "-t", "1", "-i", str(framed)]
    segs = [f"[0:v]{pre}{zp}[vz]"]
    prev = "vz"
    if corner_logo is not None:
        # logo top-right with a 36px margin; logo PNG already carries its alpha
        inputs += ["-i", str(corner_logo)]
        segs.append(f"[{prev}][1:v]overlay=W-w-36:36[vl]")
        prev = "vl"
    if banner_text:
        from config import TITLE_BANNER_FONTSIZE
        font = Path(__file__).resolve().parent.parent.parent / "fonts" / "Anton-Regular.ttf"
        fontfile = str(font).replace("\\", "/")
        bt = _drawtext_escape(banner_text.upper())
        # small white box, dark text, top-center on EVERY frame (captions sit at the
        # bottom, the logo top-right — top-center stays clear of both).
        segs.append(
            f"[{prev}]drawtext=fontfile='{fontfile}':text='{bt}':"
            f"fontcolor=black:fontsize={int(TITLE_BANNER_FONTSIZE)}:"
            f"box=1:boxcolor=white@0.92:boxborderw=22:"
            f"x=(w-text_w)/2:y=140[vb]"
        )
        prev = "vb"
    filter_complex = ";".join(segs)

    cmd = [
        ff, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{prev}]",
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
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


def _drawtext_escape(text: str) -> str:
    """Escape text for an ffmpeg drawtext filter: backslash + colon, and a straight
    apostrophe → curly (a straight ' breaks the single-quoted text='...' value)."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def _zoompan_expr(motion: str, frames: int, action: bool = False) -> str:
    """ffmpeg zoompan expression. CALM panels keep a subtle eased push (max 1.05,
    smoothstep). ACTION panels (fights/impacts) get a stronger, faster push
    (max ~1.13 with an ease-OUT 'punch' that hits fast then settles) so the moment
    feels dynamic — the motion-comic feel. action=False reproduces the prior subtle
    behavior byte-for-byte."""
    s = f"{OUTPUT_W}x{OUTPUT_H}"
    fps = FPS
    d = max(1, frames)
    if action:
        zamt, hi, pamt = "0.13", "1.13", "0.06"
        ease = f"(1-pow(1-on/{d},2))"            # ease-out: fast hit, then settle (punch)
    else:
        zamt, hi, pamt = "0.05", "1.05", "0.03"
        ease = f"pow(on/{d},2)*(3-2*(on/{d}))"   # smoothstep 0->1, eased ends
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
    if motion == "pan_right":
        return (
            f"zoompan=z='1.03':"
            f"x='iw/2-(iw/zoom/2)+(iw*{pamt})*{ease}':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    # Vertical pans — z=1.08 gives vertical room so the y travel stays inside the image
    # (no black bars). pan_down sweeps TOP→BOTTOM, pan_up sweeps BOTTOM→TOP.
    if motion == "pan_down":
        return (
            f"zoompan=z='1.08':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='(ih-ih/zoom)*{ease}':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "pan_up":
        return (
            f"zoompan=z='1.08':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='(ih-ih/zoom)*(1-{ease})':"
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


def _prepare_panel_frame(panel_png: Path, out_path: Path) -> Path:
    """Fit the panel into 1080×1920.

    Default = cover-scale (fill frame, crop overflow). Reference channels favor
    this when the panel is large enough to fill the frame without much upscale.

    BUG 1 fix (+ extreme-wide fix): when a panel would need >2.5× cover-scale, the
    cover-crop is bad in TWO ways — a small frame-shaped panel becomes a blurry
    giant, and a wide/tall panel gets cropped down to a meaningless center sliver
    (e.g. a 3.8:1 establishing strip at 6× cover shows only ~15% of its width).
    BOTH are fixed by contain+blur: show the WHOLE panel sharp (capped at 2×
    upscale) centered over a blurred copy of itself filling the frame.

    Triggers (either): (1) cover-scale > _BLUR_FALLBACK_SCALE, OR (2) a LANDSCAPE
    panel (iw ≥ ih·1.2). A wide panel forced into the 1080×1920 PORTRAIT frame by
    cover-crop chops its left/right off to a center sliver (e.g. the magik 0:53
    p12 strip, 1.83:1 at 1.77× cover, showed only ~31% of its width = a disembodied
    hand). Showing the whole panel on a blurred bg keeps the context. Portrait/tall
    splashes (ih>iw) stay on cover-scale and fill the frame edge-to-edge."""
    with Image.open(panel_png) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        cover = max(OUTPUT_W / iw, OUTPUT_H / ih)

        is_landscape = iw >= ih * 1.2  # wide panel: cover-crop chops the sides
        use_blur_bg = cover > _BLUR_FALLBACK_SCALE or is_landscape
        if not use_blur_bg:
            # ── Cover-scale (original behavior) ──
            new_w = max(OUTPUT_W, int(round(iw * cover)))
            new_h = max(OUTPUT_H, int(round(ih * cover)))
            scaled = im.resize((new_w, new_h), Image.LANCZOS)
            x0 = (new_w - OUTPUT_W) // 2
            y0 = (new_h - OUTPUT_H) // 2
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


def _crop_panel(source_image: str, bbox: dict[str, int], out_path: Path,
                text_bboxes: list[dict] | None = None,
                skip_mirror: bool = False) -> Path:
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
        ih, iw = img.shape[:2]
        # 1. Crop the panel region (with padding) FIRST — so inpaint works on a
        #    small image, not the whole page.
        x = int(bbox.get("x", 0)); y = int(bbox.get("y", 0))
        w = int(bbox.get("w", 0)); h = int(bbox.get("h", 0))
        if w <= 0 or h <= 0:
            x, y, w, h = 0, 0, iw, ih
        _pp = _pad_pct_for(w, h, iw, ih)
        pad_x = int(w * _pp); pad_y = int(h * _pp)
        left = max(0, x - pad_x); top = max(0, y - pad_y)
        right = min(iw, x + w + pad_x); bottom = min(ih, y + h + pad_y)
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

    # ── PIL fallback (no inpaint, no mirror) ──
    with Image.open(src) as im:
        iw, ih = im.size
        x = int(bbox.get("x", 0)); y = int(bbox.get("y", 0))
        w = int(bbox.get("w", 0)); h = int(bbox.get("h", 0))
        if w <= 0 or h <= 0:
            x, y, w, h = 0, 0, iw, ih
        _pp = _pad_pct_for(w, h, iw, ih)
        pad_x = int(w * _pp); pad_y = int(h * _pp)
        left = max(0, x - pad_x); top = max(0, y - pad_y)
        right = min(iw, x + w + pad_x); bottom = min(ih, y + h + pad_y)
        cropped = im.convert("RGB").crop((left, top, right, bottom))
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
