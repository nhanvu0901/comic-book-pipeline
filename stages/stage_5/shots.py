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
# `no_mirror` is set for whole-page / no-panel renders (see render_shots) and for
# panels whose art carries readable text (_panel_has_critical_text).
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


MOTION_CYCLE = ("zoom_in", "pan_right", "zoom_out")
# Threshold for "big enough to deserve motion": panel area > 25% of full page.
# Below this we keep static to avoid distracting zoom on small panels.
PANEL_BIG_AREA_RATIO = 0.25
# Maximum total motion duration — static when shot is short (cuts feel snappier).
MOTION_MIN_DURATION = 2.0

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
STATIC_MOTION_BELOW_SECONDS = 1.5
SILENCE_GAP_THRESHOLD = 0.2
SNAP_WINDOW_SECONDS = 0.5

# ── Scene panel selection ────────────────────────────────────────────────────
# Distinct panels per scene are driven by _assign_scene_panels (the single panel
# authority): it page-locks to the scene's own page, scores every panel via
# _score_panel, and returns an ORDERED LIST (reading order) so one narration line
# plays across 2-4 panels of its moment unfolding. n_panels is capped by the
# caption-chunk count and the ~1-panel-per-SHOT_TARGET_SECONDS cut rate.
# LLM_PANEL_JUDGE — gate for the R19 tiebreak: when the top two panel scores are
# within PANEL_AMBIGUITY_MARGIN, one small LLM call (page-scoped top-5) breaks the
# tie. Tests set this False for determinism.
LLM_PANEL_JUDGE = True

# ── Unified panel matching (2026-06-17) ─────────────────────────────────────
# A2 whole-page floor — the ONLY new tunable knob. Selection is PAGE-LOCKED, so
# even the weakest pick is already on the scene's correct page; the whole-page
# fallback only needs to catch a page with NO depicting panel (all tiny / text-
# wall / off-topic). Baseline: R12 gives +4 just for being on page_ref; one
# character match (R5) adds +3 → a legit "right page + right character" panel
# lands ≈7. Tuned on thor: page-6 panel 4 ("Thor stands defiantly with Mjolnir")
# scored 7.38 and SHOULD win, so 8.0 was too aggressive. 6.0 accepts page+char
# panels while still rejecting page-only/salience-only noise. Tune via benchmark.
PANEL_MATCH_FLOOR = 6.0
# Narration-driven matcher (final update of beat): below this content score a unit's
# aligned panel doesn't really depict the line → HOLD the previous panel instead of
# showing a wrong one (see _match_panels_forward).
# R19 ambiguity gate (unchanged threshold): top1−top2 below this → LLM tiebreak.
PANEL_AMBIGUITY_MARGIN = 1.0


def build_shots(
    narration: dict,
    *,
    scene_timings: list[dict] | None = None,
    word_timestamps: list[dict] | None = None,
    caption_chunks: list[dict] | None = None,
    pages_by_number: dict[int, dict] | None = None,
    cluster_to_name: dict[int, str] | None = None,
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
            cluster_to_name=cluster_to_name or {},
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

    # ── Narration-driven panel matching (final update of beat) ──────────────
    # Replaces the page-locked, beat-anchored picker. Flatten the scenes into
    # ordered narration UNITS (still split by clause + capped at MAX_PANEL_SECONDS
    # so a long sentence becomes several shots), then walk the panel pool ONCE,
    # FORWARD-ONLY + NO-REUSE: hold the current panel while narration stays on its
    # subject, advance to a clearly-better forward panel when the subject changes.
    units: list[tuple[dict, list, str]] = []   # (scene, slice_members, match_text)
    for scene, members in groups:
        scene_text = str(scene.get("text", "") or "")
        if scene.get("is_intro") or scene.get("is_outro"):
            clause_texts, slices = [scene_text], [members]
        else:
            # visual_beats may be absent (slim Stage 3) → one slice per sentence;
            # _cap_panel_holds then splits a long sentence into ≤MAX_PANEL_SECONDS
            # sub-slices so the matcher can hold OR advance within it.
            clauses = [str(c).strip() for c in (scene.get("visual_beats") or []) if str(c).strip()] \
                or [scene_text]
            buckets = _split_members_by_clause(members, clauses)
            pairs = [(c, b) for c, b in zip(clauses, buckets) if b] or [(scene_text, members)]
            clause_texts = [c for c, _ in pairs]
            slices = [b for _, b in pairs]
            clause_texts, slices = _cap_panel_holds(clause_texts, slices)
        for ct, sl in zip(clause_texts, slices):
            spoken = " ".join(str(m[0]) for m in sl).strip() or ct
            units.append((scene, sl, spoken))

    assigned = _match_panels_forward(
        [(sc, txt) for sc, _sl, txt in units],
        pages_by_number or {}, cluster_to_name or {},
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
            no_mirror=(panel is None or bool(panel.get("_whole_page"))
                       or _panel_has_critical_text(panel)),
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


# One panel holds at most this long. A visual beat can span several caption chunks
# (a 27-word sentence = 4 chunks ≈ 9s); without a cap the panel FREEZES on one image
# for the whole beat (the s8 "Murdock" case held 7.5s while the caption advanced 3×).
MAX_PANEL_SECONDS = 3.5


def _cap_panel_holds(clause_texts: list[str], slices: list[list]) -> tuple[list[str], list[list]]:
    """Break any beat-slice that would hold one panel too long into sub-slices at
    caption-chunk boundaries (each ≤ MAX_PANEL_SECONDS), so the picker assigns a
    DISTINCT panel per sub-slice (no-repeat) and the image changes every ~3.5s instead
    of freezing for the whole beat. Returns expanded (clause_texts, slices) aligned 1:1.
    members = (text, start, dur); a sub-slice keeps the beat's clause text so its panels
    stay on-topic. Never grows a slice — only splits."""
    out_t: list[str] = []
    out_s: list[list] = []
    for ct, members in zip(clause_texts, slices):
        cur: list = []
        cur_dur = 0.0
        for m in members:
            md = float(m[2]) if len(m) > 2 else 0.0
            if cur and cur_dur + md > MAX_PANEL_SECONDS:
                out_t.append(ct); out_s.append(cur)
                cur, cur_dur = [], 0.0
            cur.append(m); cur_dur += md
        if cur:
            out_t.append(ct); out_s.append(cur)
    return out_t, out_s


def _panel_text_bboxes(panel: dict, pages_by_number: dict[int, dict]) -> list[dict]:
    """Return the page-coordinate bboxes of every text block (speech/narration)
    belonging to this panel — matched by panel_index on the panel's page. Used to
    build the inpaint mask that erases the comic's own dialogue."""
    pn = int(panel.get("_page_number", 0) or 0)
    pidx = int(panel.get("index", -1))
    page = pages_by_number.get(pn) or {}
    out: list[dict] = []
    for tb in page.get("text_blocks", []) or []:
        if int(tb.get("panel_index", -1)) != pidx:
            continue
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
    spans the FULL duration so there is no static tail). A splash gets the epic
    slow zoom_in; other panels alternate zoom_in / zoom_out by `seq` for variety."""
    bbox = (panel or {}).get("bbox") or {}
    w = int(bbox.get("w", 0) or 0)
    h = int(bbox.get("h", 0) or 0)
    panel_area = w * h
    full_page_typical = 1200 * 1800  # typical comic page area (~2.16M px)
    # Splash (big panel) → slow zoom_in to make the moment feel epic.
    if panel_area and panel_area / full_page_typical >= PANEL_BIG_AREA_RATIO:
        return "zoom_in"
    # Smaller panel (or no bbox) → still MOVE, never freeze; alternate in/out so
    # consecutive shots don't all push the same way.
    return "zoom_in" if (seq % 2 == 0) else "zoom_out"


_PANEL_STOPWORDS = {
    "the", "a", "an", "is", "was", "are", "were", "and", "or", "of", "to", "in",
    "on", "with", "that", "this", "as", "but", "so", "when", "then", "after",
    "while", "he", "she", "they", "his", "her", "their", "him", "them", "it",
    "its", "by", "for", "at", "from", "into", "over", "before", "during",
}


def _score_panel(
    panel: dict,
    chunk_text: str,
    scene: dict,
    *,
    page_text_blocks: list[dict] | None = None,
    prev_panel: dict | None = None,
    cluster_to_name: dict[int, str] | None = None,
    page_locked: bool = False,
) -> float:
    """Hybrid panel relevance scoring (pipeline v5 Phase 1).

    Component weights — see docs/superpowers/specs/2026-05-24-pipeline-v5-...:
      +5.0 × dialog word overlap   (B — strongest: channel paraphrases dialog)
      +3.0 × character first-name overlap
      +2.0 if emotion matches
      +1.5 × char overlap with prev shot panel  (E — sequence coherence)
      +1.0 × description word overlap  (current keyword scoring)
      +0.5 × log(panel area / 100000)  (C — visual salience)
    """
    import math
    from .emotion_lexicon import detect_chunk_emotion

    score = 0.0
    # Two word sets with DIFFERENT weights (BUG 1-new fix):
    #   chunk_own = this caption fragment's OWN words → weighted ×3 in text match
    #   scene_bg  = the rest of the scene sentence    → weighted ×1 (context only)
    # Before, scene_text + chunk_text were merged into one flat set, so every
    # chunk of a scene scored panels almost identically (the scene's "tendrils"
    # word voted in the "enjoyed human form" chunk too). The first chunk then
    # grabbed the scene-best panel, leaving later chunks mismatched. Weighting
    # the chunk's own words higher makes each fragment pick its own matching
    # panel ("emerged from hand" → tendrils panel, "enjoyed" → reflection).
    scene_text = str(scene.get("text", "") or "")
    chunk_own = {w.lower().strip(",.!?:;\"'") for w in chunk_text.split()} - _PANEL_STOPWORDS
    scene_words = {w.lower().strip(",.!?:;\"'") for w in scene_text.split()} - _PANEL_STOPWORDS
    chunk_words = chunk_own | scene_words           # combined — for name presence checks
    scene_bg = scene_words - chunk_own              # scene context not in this fragment

    # ── Panel upscale factor — how much we'd blow this panel up to fill the
    # 1080×1920 frame. >3× means a tiny source panel → blurry giant where you
    # can't tell what the scene is about. Used to scale the honor bonus and to
    # penalize tiny panels (BUG 1 fix).
    _bb = panel.get("bbox", {}) or {}
    _pw = int(_bb.get("w", 0) or 0)
    _ph = int(_bb.get("h", 0) or 0)
    panel_scale = max(OUTPUT_W / _pw, OUTPUT_H / _ph) if (_pw > 0 and _ph > 0) else 99.0

    # ── R3 RETIRED (unified panel matching, 2026-06-17, choice B) ────────
    # The "honor scene.panel_ref +15" bonus is gone: Stage 3 no longer commits a
    # panel (panel_ref is always -1), so there is nothing to honor. Stage 5
    # (`_assign_scene_panels`) is the sole panel authority. These two values are
    # still read by R10 (page progression) and R12 (page_ref bonus) below.
    panel_page = int(panel.get("_page_number", 0) or 0)
    scene_page_ref_int = int(scene.get("page_ref", 0) or 0)

    # ── Small-panel penalty (BUG 1) — discourage panels that must be heavily
    # upscaled. Applies to ALL candidates so a big clear panel beats a tiny one
    # even when the tiny one was the LLM's pick.
    if panel_scale > 2.5:
        score -= 3.0 * (panel_scale - 2.5)

    # ── Character overlap (existing, +3 per first-name match) ────────────
    panel_chars = panel.get("characters", []) or []
    for ch in panel_chars:
        first = (ch.split()[0].lower() if ch else "").strip(",.!?:;\"'")
        if first and first in chunk_words:
            score += 3.0

    # ── D: Magi cluster-id match via cluster_to_name (v5 Phase 2) ────────
    # For each cluster_id in this panel, look up its character name via
    # cluster_to_name, then check if that name's first-word appears in chunk.
    # +3 per match. Independent of VLM-extracted characters list.
    panel_cluster_ids = panel.get("cluster_ids", []) or []
    if cluster_to_name and panel_cluster_ids:
        for cid in panel_cluster_ids:
            name = cluster_to_name.get(int(cid), "")
            if not name or name.lower() == "unknown":
                continue
            first = name.split()[0].lower().strip(",.!?:;\"'")
            if first and first in chunk_words:
                score += 3.0

    # ── B: Dialog word overlap (strong channel signal — CAPPED) ─────────
    # Capped at 10 to prevent dialog match from dominating over description
    # match. Reason: a 3-word dialog match was scoring +15, beating
    # description match for the cited panel (+2). Now max +10 from dialog,
    # giving description + bbox match a fair shot.
    panel_idx = int(panel.get("index", -1))
    if page_text_blocks:
        dialog_words: set[str] = set()
        for tb in page_text_blocks:
            if int(tb.get("panel_index", -1)) != panel_idx:
                continue
            if tb.get("type") not in ("speech", "narration", "caption"):
                continue
            for w in str(tb.get("text", "")).lower().split():
                dialog_words.add(w.strip(",.!?:;\"'"))
        dialog_words -= _PANEL_STOPWORDS
        # chunk's own words ×5 (strong), scene background ×1.5 (weak context)
        score += min(12.0, 5.0 * len(dialog_words & chunk_own)
                          + 1.5 * len(dialog_words & scene_bg))

    # ── F: Emotion match (+2 if chunk emotion matches panel) ─────────────
    chunk_emotion = detect_chunk_emotion(chunk_text)
    if chunk_emotion and chunk_emotion == panel.get("dominant_emotion", ""):
        score += 2.0

    # ── E: Sequence coherence (+1.5 × shared chars with prev shot) ──────
    if prev_panel is not None:
        prev_chars = {
            (c.split()[0].lower() if c else "").strip(",.!?:;\"'")
            for c in (prev_panel.get("characters", []) or [])
        }
        this_chars = {
            (c.split()[0].lower() if c else "").strip(",.!?:;\"'")
            for c in panel_chars
        }
        score += 1.5 * len(prev_chars & this_chars)

        # ── R10: Monotonic page progression — RETIRED under page-lock (2026-06-17,
        # decision A). This cross-page forward-only signal was for the OLD multi-page
        # pool; now selection is page-locked to the scene's page_ref, so every
        # candidate shares the same page → R10 adds a CONSTANT (no ranking effect)
        # yet deflates the absolute score below PANEL_MATCH_FLOOR whenever narration
        # revisits an earlier page (prev scene on a later page), forcing a spurious
        # whole-page. page-lock takes over R10's job (cf. retired R3). Code kept for
        # any non-page-locked caller; skipped when page_locked=True.
        if not page_locked:
            prev_pg = int(prev_panel.get("_page_number", 0) or 0)
            if prev_pg and panel_page:
                if panel_page > prev_pg:
                    score += 2.0
                elif panel_page == prev_pg:
                    score += 1.0
                else:
                    score -= 5.0 * (prev_pg - panel_page)

    # ── Semantic text↔panel match (TODO #124) — REPLACES lexical desc overlap ──
    # Cosine(narration chunk, panel description) via a local sentence-embedding
    # model. Captures meaning, not shared tokens: "reverted to his mortal form,
    # Donald Blake" scores ~0 against the abstract "FIZAPPT sound effect" panel
    # but high against a panel actually depicting Blake. The old word-overlap
    # term is removed — it measured the same (chunk, desc) pair lexically and
    # would double-count.
    desc = panel.get("description", "") or ""
    sim_chunk = _semantic_sim(chunk_text, desc)
    sim_scene = _semantic_sim(scene_text, desc)
    score += 6.0 * sim_chunk + 1.0 * sim_scene

    # ── H: page_ref bonus — LOOSENED +10 → +4 (TODO #125) ───────────────
    # Narration's page_ref is the scene's canonical page, but a FULL +10 trapped
    # selection on that page even when its only free panel was irrelevant (the
    # FIZAPPT case). +4 keeps it a preference, not a trap, so a semantically
    # matching panel on the NEXT page (forward-only widening) can win.
    if scene_page_ref_int and panel_page and scene_page_ref_int == panel_page:
        score += 4.0

    # ── C/B: Visual salience — PAGE-RELATIVE (2026-06-20). A big/splash panel is a
    # highlight (reveal/money shot); reward it so the epic shot beats a small panel
    # ON THE SAME PAGE. The OLD absolute-area term maxed out on EVERY panel of a
    # large page (e.g. 1961×3050), giving no tie-break — so the Hulkbuster barrage
    # tied with tiny panels. Now scale by fraction-of-page: +4 at a >=50% splash,
    # proportionally less below. Big enough to win close calls, NOT to override a
    # strong content match (dialog +12, character +3). Falls back to absolute area
    # when page dims are unavailable.
    bbox = panel.get("bbox", {}) or {}
    area = int(bbox.get("w", 0) or 0) * int(bbox.get("h", 0) or 0)
    page_area = int(panel.get("_page_area", 0) or 0)
    if page_area > 0:
        score += 4.0 * min(1.0, (area / page_area) / 0.5)
    elif area > 50000:
        score += 3.0 * min(1.0, math.log(area / 50000) / 3.0)

    # ── Text-coverage penalty (Fix 1) — avoid text-WALL panels ───────────
    # A panel drowning in caption/dialogue (e.g. a full-page epilogue splash with
    # a paragraph of text, ~26% covered) is a poor visual: cluttered, and LaMa
    # must inpaint a huge text area → smears. Measured: clean panels 2-7% covered,
    # text-walls 25%+. Penalize above 15% so a cleaner panel wins when one exists.
    if area > 0 and page_text_blocks:
        px, py, pw, ph = (int(bbox.get(k, 0) or 0) for k in ("x", "y", "w", "h"))
        text_area = 0
        for tb in page_text_blocks:
            tbb = tb.get("bbox") or {}
            tx, ty = int(tbb.get("x", 0) or 0), int(tbb.get("y", 0) or 0)
            tw, th = int(tbb.get("w", 0) or 0), int(tbb.get("h", 0) or 0)
            ix0, iy0 = max(px, tx), max(py, ty)
            ix1, iy1 = min(px + pw, tx + tw), min(py + ph, ty + th)
            if ix1 > ix0 and iy1 > iy0:
                text_area += (ix1 - ix0) * (iy1 - iy0)
        coverage = text_area / area
        if coverage > 0.15:
            score -= min(8.0, 30.0 * (coverage - 0.15))

    return score


def _llm_judge_tiebreak(
    chunk_text: str,
    top_candidates: list[tuple[float, dict, str, tuple[int, int]]],
) -> tuple[float, dict, str, tuple[int, int]] | None:
    """G: when top heuristic candidates score within 1.0 of each other, ask an
    LLM which panel best visualizes the caption. Returns winning tuple or
    None on failure (caller falls back to heuristic top)."""
    if len(top_candidates) <= 1:
        return top_candidates[0] if top_candidates else None

    # Build prompt: list candidates with their characters / emotion / dialog / desc
    panel_blocks = []
    for i, (_, panel, _src, (pn, idx)) in enumerate(top_candidates[:5]):
        chars = ", ".join(panel.get("characters", []) or []) or "?"
        emotion = panel.get("dominant_emotion", "?")
        desc = (panel.get("description", "") or "")[:120]
        panel_blocks.append(
            f"  {chr(65 + i)}. page {pn} panel {idx}\n"
            f"     characters: {chars}\n"
            f"     emotion: {emotion}\n"
            f"     visual: {desc}"
        )

    prompt = (
        f"Caption to visualize: {chunk_text!r}\n\n"
        f"Candidate comic panels:\n"
        + "\n".join(panel_blocks)
        + "\n\nWhich panel best visualizes the caption? Reply with ONE LETTER only "
          "(A, B, C, D, or E). No explanation, no punctuation, just the letter."
    )

    try:
        # Import lazily so Stage 5 doesn't pull in Stage 3 LLM machinery at import time
        from stages.stage_3._llm import call_with_chain
        raw, _ = call_with_chain(
            system="You pick comic panels. Reply with one letter only.",
            user=prompt,
            max_tokens=10,
            label="panel-judge",
        )
    except Exception:
        return None  # caller falls back to heuristic top

    if not raw:
        return None
    # Find first letter in {A,B,C,D,E}
    letter = next((c for c in raw.upper() if c in "ABCDE"), None)
    if letter is None:
        return None
    idx = ord(letter) - ord("A")
    if 0 <= idx < min(5, len(top_candidates)):
        return top_candidates[idx]
    return None


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
                           for tb in (page.get("text_blocks") or []))
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


def _gather_scored(pages_range, *, chunk_text, scene, pages_by_number,
                   used_panel_keys, prev_panel, cluster_to_name):
    """Score every free, non-skip panel on the given pages (R16/R18 engine,
    R17 no-repeat, R27-R31 skip-page). Returns (score, panel_with_pg, src, key)."""
    out = []
    for pn in pages_range:
        page = pages_by_number.get(pn)
        if not page or _is_skip_page(page):
            continue
        src = str(page.get("source_image") or "")
        page_tb = page.get("text_blocks") or []
        _pdims = page.get("image_dimensions") or {}
        _parea = int(_pdims.get("width", 0) or 0) * int(_pdims.get("height", 0) or 0)
        for idx, panel in enumerate(page.get("panels") or []):
            key = (pn, idx)
            if key in used_panel_keys:
                continue
            pw = dict(panel)
            pw["_page_number"] = pn
            pw["_page_area"] = _parea
            pw["index"] = idx
            s = _score_panel(pw, chunk_text, scene, page_text_blocks=page_tb,
                             prev_panel=prev_panel, cluster_to_name=cluster_to_name,
                             page_locked=True)
            out.append((s, pw, src, key))
    return out


def _cold_open_panel(pages_by_number):
    """COLD-OPEN: pick a striking STORY panel to open the video on instead of the
    cover — the LARGEST-area panel in the OPENING pages (a splash/big dramatic shot
    grabs in <1s, and an opening panel establishes the premise instead of dropping the
    viewer into a mid-story fight that mismatches the hook). Skips cover/credits/ad
    pages and the final 2 story pages (no ending spoiler). Returns (panel, src) or (None,'')."""
    story_pns = sorted(pn for pn, pg in (pages_by_number or {}).items()
                       if pg and not _is_skip_page(pg))
    if not story_pns:
        return None, ""
    ending = set(story_pns[-2:])   # exclude the last 2 story pages (ending/outro splash)
    # Only the OPENING third (≥3 pages): the hook is about the SETUP, so frame 1 should
    # come from where the premise is established — not the globally-largest panel, which
    # was landing on a mid-story action splash unrelated to the opening line.
    opening = story_pns[:max(3, len(story_pns) // 3)]
    best = None  # (area, panel_dict, src)
    for pn in opening:
        if pn in ending:
            continue
        page = pages_by_number.get(pn) or {}
        src = str(page.get("source_image") or "")
        for idx, panel in enumerate(page.get("panels") or []):
            bb = panel.get("bbox") or {}
            area = int(bb.get("w", 0) or 0) * int(bb.get("h", 0) or 0)
            if area <= 0:
                continue
            if best is None or area > best[0]:
                pw = dict(panel)
                pw["_page_number"] = pn
                pw["index"] = idx
                best = (area, pw, src)
    if best is None:
        return None, ""
    return best[1], best[2]


def _whole_page_panel(scene, pages_by_number):
    """A2/R15 whole-page 'panel' so the renderer shows the entire page_ref page.
    Returns (panel_dict_or_None, source_image)."""
    p = int(scene.get("page_ref", 0) or 0)
    page = pages_by_number.get(p) or {}
    src = str(page.get("source_image") or "")
    dims = page.get("image_dimensions") or {}
    iw = int(dims.get("width", 0) or 0)
    ih = int(dims.get("height", 0) or 0)
    if iw and ih:
        return ({"bbox": {"x": 0, "y": 0, "w": iw, "h": ih},
                 "_page_number": p, "index": -1, "_whole_page": True}, src)
    panels = page.get("panels") or []
    if panels:
        pp = dict(panels[0])
        pp["_page_number"] = p
        pp["index"] = 0
        return pp, src
    return None, src


def _pick_best_panel(text, *, page_ref, scene, pages_by_number, used, prev_panel,
                     cluster_to_name):
    """Page-locked best panel for ONE text (a clause). Scores the scene's own page
    first (A1), widens forward (R16) then last-resort (R18) only if empty; R19 LLM
    tiebreak on a close lead. Returns (score, panel, src, key) or None when nothing
    clears PANEL_MATCH_FLOOR (caller falls back to hold-previous / whole-page)."""
    cands = []
    for rng in ([page_ref], [page_ref + 1, page_ref + 2], range(page_ref, page_ref + 5)):
        cands = _gather_scored(rng, chunk_text=text, scene=scene,
                               pages_by_number=pages_by_number, used_panel_keys=used,
                               prev_panel=prev_panel, cluster_to_name=cluster_to_name)
        if cands:
            break
    if not cands:
        return None
    cands.sort(key=lambda t: -t[0])
    lead = cands[0]
    if (LLM_PANEL_JUDGE and len(cands) >= 2
            and (cands[0][0] - cands[1][0]) < PANEL_AMBIGUITY_MARGIN):
        w = _llm_judge_tiebreak(text, cands[:5])
        if w is not None:
            lead = w
    if cands[0][0] < PANEL_MATCH_FLOOR:
        return None
    return lead


def _panel_by_key(key, pages_by_number):
    """Materialise a scored-style panel dict (with _page_number/index) + its src for a
    (page, idx) key, or (None, '') if it doesn't exist."""
    pn, idx = key
    page = pages_by_number.get(pn) or {}
    panels = page.get("panels") or []
    if not (0 <= idx < len(panels)):
        return None, ""
    pw = dict(panels[idx])
    pw["_page_number"] = pn
    pw["index"] = idx
    return pw, str(page.get("source_image") or "")


def _bbox_tuple(panel: dict) -> tuple[int, int, int, int]:
    b = panel.get("bbox") or {}
    return (int(b.get("x", 0) or 0), int(b.get("y", 0) or 0),
            int(b.get("w", 0) or 0), int(b.get("h", 0) or 0))


def _containment_ratio(a: tuple, b: tuple) -> float:
    """Intersection over the SMALLER panel's area. Unlike plain IoU this catches a
    panel that sits (almost) entirely INSIDE another — exactly the overlapping
    Magi detections that make two scenes on one page render near-identical crops
    (e.g. moonknight pg14: p0 (0,1,1533,1947) nested in p1 (0,1,1981,1956))."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    small = min(aw * ah, bw * bh)
    return inter / small if (inter > 0 and small > 0) else 0.0


# A panel overlapping an already-claimed one by ≥ this (containment) renders a
# near-identical crop — block it too so no two scenes show the same art.
_PANEL_OVERLAP_BLOCK = 0.7


def _overlapping_panel_keys(key, pages_by_number) -> set:
    """Keys of OTHER panels on the same page that overlap `key`'s panel ≥
    _PANEL_OVERLAP_BLOCK (containment). Used to extend the no-repeat set when a
    panel is claimed, so an overlapping duplicate detection can't be picked next."""
    pn, idx = key
    panels = (pages_by_number.get(pn) or {}).get("panels") or []
    if not (0 <= idx < len(panels)):
        return set()
    base = _bbox_tuple(panels[idx])
    out = set()
    for j, p in enumerate(panels):
        if j != idx and _containment_ratio(base, _bbox_tuple(p)) >= _PANEL_OVERLAP_BLOCK:
            out.add((pn, j))
    return out


def _panel_pool(pages_by_number: dict) -> list:
    """All non-skip panels in READING ORDER (page asc, panel idx asc) across every
    page/issue: [(key, panel_dict, source_image, page_text_blocks)]. Each panel dict
    carries _page_number/_page_area/index so _score_panel's rules work unchanged."""
    pool = []
    for pn in sorted(pages_by_number or {}):
        page = pages_by_number.get(pn)
        if not page or _is_skip_page(page):
            continue
        src = str(page.get("source_image") or "")
        page_tb = page.get("text_blocks") or []
        dims = page.get("image_dimensions") or {}
        parea = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
        for idx, panel in enumerate(page.get("panels") or []):
            pw = dict(panel)
            pw["_page_number"] = pn
            pw["_page_area"] = parea
            pw["index"] = idx
            pool.append(((pn, idx), pw, src, page_tb))
    return pool


def _match_panels_forward(units: list, pages_by_number: dict, cluster_to_name: dict) -> list:
    """Align the narration sequence to the panel sequence by MONOTONIC DTW: pick a
    non-decreasing panel index per unit that maximizes the TOTAL content match
    (_score_panel: dialog/char/emotion/salience/text-penalty + semantic sim). This is
    forward-only (panel index never decreases), holds a panel across consecutive units
    that share its subject (same index repeated), and — being a GLOBAL optimum — never
    strands the runway the way a greedy walk does (jumping to a late panel early would
    lower the total, so it won't). A unit whose best aligned panel still doesn't depict
    it (score < PANEL_MATCH_FLOOR) HOLDS the previous panel instead of showing a wrong
    one. `units` = [(scene, match_text)] in audio order → [(panel_or_None, src)]."""
    pool = _panel_pool(pages_by_number)
    n, m = len(units), len(pool)
    if n == 0:
        return []
    if m == 0:
        return [(None, "")] * n

    # cost matrix: static content score of unit i on panel j (no prev-coherence term —
    # the monotonic alignment supplies sequence coherence on its own).
    score = [[0.0] * m for _ in range(n)]
    for i, (scene, text) in enumerate(units):
        for j, (_key, panel, _src, page_tb) in enumerate(pool):
            score[i][j] = _score_panel(panel, text, scene, page_text_blocks=page_tb,
                                       prev_panel=None, cluster_to_name=cluster_to_name,
                                       page_locked=False)

    # DP: dp[i][j] = best total aligning units 0..i with unit i → panel j, j
    # non-decreasing. Either ADVANCE from a strictly-earlier panel (+ADVANCE_BONUS, so
    # the alignment is rewarded for moving to a NEW panel instead of parking on one
    # broadly-matching panel — e.g. a recap/cover collage) or STAY on panel j (a hold,
    # no bonus). The strict-prefix-max is carried in O(1) → O(n·m) overall.
    ADVANCE_BONUS = 2.0
    NEG = float("-inf")
    dp = [[NEG] * m for _ in range(n)]
    bk = [[-1] * m for _ in range(n)]
    for j in range(m):
        dp[0][j] = score[0][j]
    for i in range(1, n):
        spm, sparg = NEG, 0      # strict prefix max of dp[i-1][0..j-1]
        for j in range(m):
            adv = spm + ADVANCE_BONUS if spm > NEG else NEG   # advance from an earlier panel
            stay = dp[i - 1][j]                               # hold on panel j
            if adv >= stay:
                dp[i][j], bk[i][j] = score[i][j] + adv, sparg
            else:
                dp[i][j], bk[i][j] = score[i][j] + stay, j
            if dp[i - 1][j] > spm:
                spm, sparg = dp[i - 1][j], j
    # backtrack from the best end panel
    jbest = max(range(m), key=lambda j: dp[n - 1][j])
    idxs = [0] * n
    j = jbest
    for i in range(n - 1, -1, -1):
        idxs[i] = j
        if i > 0:
            j = bk[i][j]

    out = []
    prev = None        # (panel, src) currently on screen
    for i, (scene, text) in enumerate(units):
        j = idxs[i]
        key, panel, src, _tb = pool[j]
        # Cold-open: the teaser opens on a striking OPENING panel, not a content match.
        if i == 0 and scene.get("is_intro"):
            cp, csrc = _cold_open_panel(pages_by_number)
            if cp is not None:
                out.append((cp, csrc))
                prev = (cp, csrc)
                print(f"[stage5] match u{i}: COLD-OPEN | {text[:42]!r}")
                continue
        if score[i][j] < PANEL_MATCH_FLOOR and prev is not None:
            out.append(prev)                         # weak match → hold (no wrong panel)
            print(f"[stage5] match u{i}: HOLD(weak) {key} base={score[i][j]:.1f} | {text[:42]!r}")
        else:
            out.append((panel, src))
            prev = (panel, src)
            print(f"[stage5] match u{i}: ALIGN {key} base={score[i][j]:.1f} | {text[:42]!r}")
    return out


def _assign_scene_panels(*, scene, pages_by_number, used_panel_keys, prev_panel,
                         cluster_to_name, clause_texts, blocked_keys=frozenset(),
                         pinned_key=None):
    """SINGLE panel authority. Returns one (panel, src) PER CLAUSE, each matched to
    that CLAUSE's OWN text (page-locked A1, no-repeat R17/R24, FLOOR A2, R19 tiebreak,
    every _score_panel rule). A clause that can't clear FLOOR holds the previous
    clause's panel (coherent) or, if it's the first, the whole page. Intro → cover.

    Fix A (2026-06-18) — page contention. When several scenes share a page_ref:
    - blocked_keys: panels RESERVED for OTHER scenes — temporarily marked used so this
      scene can't eat a neighbour's panel, then restored so the rightful owner claims it.
    - pinned_key: the panel THIS scene owns. It is force-assigned to the clause that
      depicts it best, so content-ownership beats R9 scene-entry coherence (the
      'reduced to ash' panel must win for that line even though the previous shot was a
      Ghost Rider panel). Both default off → non-contended scenes are unchanged."""
    if scene.get("is_intro"):
        # Cold-open on a striking story panel (not the cover) so frame 1 grabs in <1s.
        from config import COLD_OPEN
        if COLD_OPEN:
            pnl, src = _cold_open_panel(pages_by_number)
            if pnl is not None:
                return [(pnl, src)]
        pnl, src = _whole_page_panel(scene, pages_by_number)
        return [(pnl, src)] if pnl else []

    page_ref = int(scene.get("page_ref", 0) or 0)
    clause_texts = clause_texts or [str(scene.get("text", "") or "")]
    result: list = []
    prev = prev_panel

    # Resolve the pinned (owned) panel and which clause depicts it best.
    pin_panel = pin_src = None
    pin_clause = -1
    if pinned_key is not None and pinned_key not in used_panel_keys:
        pin_panel, pin_src = _panel_by_key(pinned_key, pages_by_number)
        if pin_panel is not None:
            page_tb = (pages_by_number.get(pinned_key[0]) or {}).get("text_blocks") or []
            best_s = -1e9
            for i, ct in enumerate(clause_texts):
                s = _score_panel(pin_panel, ct, scene, page_text_blocks=page_tb,
                                 prev_panel=None, cluster_to_name=cluster_to_name,
                                 page_locked=True)
                if s > best_s:
                    best_s, pin_clause = s, i

    # Temporarily reserve neighbours' panels + the pinned panel (only those not already
    # claimed), so non-pinned clauses skip them; the finally restores all but real claims.
    newly_blocked = set(blocked_keys)
    if pin_panel is not None:
        newly_blocked.add(pinned_key)
    newly_blocked -= used_panel_keys
    used_panel_keys |= newly_blocked
    try:
        for i, ct in enumerate(clause_texts):
            if i == pin_clause and pin_panel is not None:
                result.append((pin_panel, pin_src))   # force-claim the owned panel
                prev = pin_panel
                newly_blocked.discard(pinned_key)      # permanent claim, survive finally
                # block overlapping detections of the owned panel too (dup guard)
                used_panel_keys |= _overlapping_panel_keys(pinned_key, pages_by_number)
                continue
            lead = _pick_best_panel(ct, page_ref=page_ref, scene=scene,
                                    pages_by_number=pages_by_number, used=used_panel_keys,
                                    prev_panel=prev, cluster_to_name=cluster_to_name)
            if lead is not None:
                _, panel, src, key = lead
                used_panel_keys.add(key)   # R17/R24 no-repeat across the whole video
                # …and block overlapping detections so the next scene on this page
                # can't render a near-identical crop (duplicate-panel guard).
                used_panel_keys |= _overlapping_panel_keys(key, pages_by_number)
                result.append((panel, src))
                prev = panel
            elif result:
                result.append(result[-1])  # clause too thin to match → hold previous
            else:
                pnl, src = _whole_page_panel(scene, pages_by_number)
                result.append((pnl, src))
                prev = pnl
    finally:
        used_panel_keys -= newly_blocked   # free neighbours' reservations again
    return result


def _resolve_page_contention(groups, pages_by_number, cluster_to_name):
    """Fix A (2026-06-18): when several scenes lock to the SAME page_ref, give each
    contended panel to the scene that matches it BEST — not to whichever scene the
    narration reaches first. (BUG: 'The killer was reduced to ash' got the weak
    'Ghost Rider turns away' panel because the previous line had already eaten the
    'dissipates into ash' panel on the same page.)

    Peek-only scoring (no used/prev state, scene's full text as the ownership signal);
    greedy: the highest (scene, panel) score claims first, one panel per scene, one
    scene per panel. Returns {group_index: reserved_panel_key}."""
    by_page: dict[int, list[int]] = defaultdict(list)
    for gi, (scene, _members) in enumerate(groups):
        if scene.get("is_intro") or scene.get("is_outro"):
            continue
        pr = int(scene.get("page_ref", 0) or 0)
        if pr:
            by_page[pr].append(gi)
    reserved: dict[int, tuple] = {}
    for pr, gis in by_page.items():
        if len(gis) < 2:
            continue                       # no contention on this page
        pairs = []                         # (score, group_index, panel_key)
        for gi in gis:
            scene = groups[gi][0]
            text = str(scene.get("text", "") or "")
            for score, _pw, _src, key in _gather_scored(
                    [pr], chunk_text=text, scene=scene,
                    pages_by_number=pages_by_number, used_panel_keys=set(),
                    prev_panel=None, cluster_to_name=cluster_to_name):
                pairs.append((score, gi, key))
        pairs.sort(key=lambda t: -t[0])
        taken_g: set = set()
        taken_p: set = set()
        for _score, gi, key in pairs:
            if gi in taken_g or key in taken_p:
                continue
            reserved[gi] = key
            taken_g.add(gi)
            taken_p.add(key)
            # Block OVERLAPPING detections too, so a second scene on this page can't
            # reserve a near-identical crop (e.g. moonknight pg14: p0 nested in p1).
            # Without this the greedy "one scene per panel" rule treats p0/p1 as
            # distinct (different keys) and hands each to a scene → duplicate panel.
            taken_p |= _overlapping_panel_keys(key, pages_by_number)
    return reserved


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
    if shot.motion in ("zoom_in", "zoom_out", "pan_right"):
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
