"""Shot list construction and per-shot ffmpeg Ken Burns rendering."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter

from .schema import Shot


OUTPUT_W = 1080
OUTPUT_H = 1920
TARGET_ASPECT = OUTPUT_W / OUTPUT_H
FPS = 30

# ── Semantic text↔panel alignment (TODO #124) ───────────────────────────────
# A small local sentence-embedding model scores how well a narration chunk
# matches a panel's VLM description — far more reliable than lexical word
# overlap ("mistletoe" vs "spear", "reverted to mortal form" vs "FIZAPPT").
# Lazy-loaded singleton; if unavailable the scorer degrades gracefully to 0.
_SENT_MODEL = None
_SENT_MODEL_TRIED = False
_EMBED_CACHE: dict[str, "object"] = {}


def _sent_model():
    global _SENT_MODEL, _SENT_MODEL_TRIED
    if _SENT_MODEL_TRIED:
        return _SENT_MODEL
    _SENT_MODEL_TRIED = True
    try:
        from sentence_transformers import SentenceTransformer
        _SENT_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        _SENT_MODEL = None
    return _SENT_MODEL


def _embed(text: str):
    """Cached unit-normalized embedding for a piece of text (or None)."""
    text = (text or "").strip()
    if not text:
        return None
    if text in _EMBED_CACHE:
        return _EMBED_CACHE[text]
    m = _sent_model()
    if m is None:
        return None
    vec = m.encode(text, convert_to_numpy=True, normalize_embeddings=True)
    _EMBED_CACHE[text] = vec
    return vec


def _semantic_sim(a: str, b: str) -> float:
    """Cosine similarity in [0,1] between two texts (0 if model unavailable)."""
    va, vb = _embed(a), _embed(b)
    if va is None or vb is None:
        return 0.0
    import numpy as np
    return max(0.0, min(1.0, float(np.dot(va, vb))))
PADDING_PCT = 0.05
# Erase the comic's own speech-bubble text from panels before render (the video
# already carries narration + burned captions, so on-art dialogue is clutter).
INPAINT_BUBBLE_TEXT = True
# Horizontally flip each panel before render (the cleaned, mirrored frame is no
# longer a pixel-identical copy of the source page).
MIRROR_PANELS = True
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
SHOT_TARGET_SECONDS = 2.0   # ~1 panel / 2s → snappier cut rate (ref ~1.5-2s)
SHOT_MIN_SECONDS = 0.6         # caption-chunk mode: ~0.5-2s per shot
STATIC_MOTION_BELOW_SECONDS = 1.5
SILENCE_GAP_THRESHOLD = 0.2
SNAP_WINDOW_SECONDS = 0.5

# ── Fix A+C (scene panel coherence) ──────────────────────────────────────────
# Distinct panels per scene are now driven by _llm_assign_panels, which returns an
# ORDERED LIST of panels (preferring the scene's own page) so one narration line
# plays across 2-4 panels of its moment unfolding. n_panels is capped by the
# caption-chunk count and the ~1-panel-per-SHOT_TARGET_SECONDS cut rate.
# C — LLM tie-breaker for near-tie panel scores. Disabled: it fired ~28% of
# chunks, ran on the default LLM chain (no page_ref preference, often the
# unavailable free models) and injected nondeterministic neighbor-page picks.
# The heuristic + page_ref-first gathering (A) is reliable without it.
LLM_PANEL_JUDGE = True


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
            if dur < STATIC_MOTION_BELOW_SECONDS:
                motion = "static"
            elif SHOTS_PER_SCENE <= 1:
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


def _llm_assign_panels(narration: dict, pages_by_number: dict[int, dict],
                       cluster_to_name: dict[int, str] | None = None) -> dict[int, list[tuple[int, int]]]:
    """ONE global LLM call: assign each narration scene → an ORDERED LIST of the
    (page, panel_index) panels that depict its moment UNFOLDING in reading order,
    by reading every story panel's description.

    Embedding cosine can't disambiguate panels in an entity-homogeneous comic
    (every panel says Ben/Reed/symbiote → similarity is dominated by shared
    nouns, not the distinguishing ACTION). An LLM reading the now-detailed
    descriptions reasons about the action ("raises the sonic gun" → the sonic-gun
    panel). Returns {scene_id: [(page, idx), ...]}; empty on any failure → caller
    falls back to the heuristic _select_panel_for_chunk. Multiple panels per scene
    let one narration line play across its page (the slam → the count → the
    triumphant face) instead of holding a single frame."""
    scenes = [s for s in (narration.get("scenes") or [])
              if not s.get("is_intro") and not s.get("is_outro")]
    if not scenes or not pages_by_number:
        return {}
    beats = {int(b.get("id", 0) or 0): b for b in (narration.get("beats") or [])}
    cat: list[str] = []
    valid: set[tuple[int, int]] = set()
    for pn in sorted(pages_by_number):
        page = pages_by_number[pn]
        if _is_skip_page(page):
            continue
        for idx, panel in enumerate(page.get("panels") or []):
            desc = str(panel.get("description", "") or "").strip()
            if not desc:
                continue
            chars = ", ".join(panel.get("characters", []) or [])
            cat.append(f"p{pn}#{idx} [{chars}]: {desc[:160]}")
            valid.add((pn, idx))
    if not cat:
        return {}
    sl = []
    for s in scenes:
        beat = beats.get(int(s.get("beat_id", 0) or 0), {})
        sl.append(f"S{s.get('scene_id')} (page {s.get('page_ref')}): {s.get('text','')}"
                  f"   (beat: {(beat.get('summary','') or '')[:120]})")
    prompt = (
        "Match each NARRATION scene to the comic PANELS that depict it.\n\n"
        "NARRATION SCENES (story order — each tagged with its page):\n" + "\n".join(sl) + "\n\n"
        "PANELS (reading order — label [characters]: description):\n" + "\n".join(cat) + "\n\n"
        "For EACH scene, list the 2-4 PANELS (in reading order) that show its moment "
        "UNFOLDING — e.g. the slam, then standing over the body, then the triumphant face. "
        "Rules:\n"
        "- STRONGLY PREFER panels on the scene's OWN page (the 'page N' tag). Only borrow "
        "from a neighbouring page if it genuinely continues the SAME moment.\n"
        "- Panel #1 MUST be the single best one that depicts the line (match verb + object: "
        "'raises the sonic gun' → the sonic-gun-raised panel); the rest continue the action.\n"
        "- Use FEWER panels (even just 1) when the page has no extra panels that fit — NEVER "
        "pad with an unrelated panel.\n"
        "- Don't reuse a panel across scenes.\n"
        "Return ONLY JSON mapping every scene to a LIST: "
        "{\"S2\":[\"p10#2\",\"p10#0\",\"p10#9\"], \"S13\":[\"p50#0\",\"p50#1\",\"p50#2\"], ...}."
    )
    try:
        from stages.stage_3._llm import call_with_chain
        raw, _ = call_with_chain(
            system="You match narration lines to the comic panels that depict them. Return only JSON.",
            user=prompt, max_tokens=2000, label="panel-assign",
        )
    except Exception:
        return {}
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return {}
    try:
        mp = json.loads(m.group(0))
    except Exception:
        return {}
    out: dict[int, list[tuple[int, int]]] = {}
    for k, v in mp.items():
        sid = int(re.sub(r"[^0-9]", "", str(k)) or 0)
        if not sid:
            continue
        # v is a LIST of "pN#i" (new schema); tolerate a bare string (old schema).
        items = v if isinstance(v, list) else [v]
        seq: list[tuple[int, int]] = []
        for item in items:
            mm = re.match(r"\s*p(\d+)#(\d+)", str(item))
            if not mm:
                continue
            pg, ix = int(mm.group(1)), int(mm.group(2))
            if (pg, ix) in valid and (pg, ix) not in seq:  # dedup within scene, keep order
                seq.append((pg, ix))
        if seq:
            out[sid] = seq
    return out


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

    # GLOBAL no-repeat across the whole video (BUG #122 Fix B — stops the same
    # splash panel reappearing in two scenes). With ≤2 panels/scene (E) and
    # page_ref-first gathering (A), a page is rarely exhausted, so scenes now
    # stay on their own page instead of bleeding into neighbors.
    used_global: set = set()
    shots: list[Shot] = []
    prev_panel: dict | None = None
    shot_id = 0

    # PRIMARY panel selection: one global LLM call assigns each scene → the panel
    # that depicts it (reads detailed descriptions, reasons about the action — beats
    # embedding cosine, which collapses to "magnet" panels in an entity-homogeneous
    # comic). Heuristic _select_panel_for_chunk is the per-scene fallback.
    llm_panels = _llm_assign_panels(narration, pages_by_number or {}, cluster_to_name or {})

    def _resolve(pg_ix):
        pg, ix = pg_ix
        page = (pages_by_number or {}).get(pg) or {}
        panels = page.get("panels") or []
        if 0 <= ix < len(panels):
            pnl = dict(panels[ix]); pnl["_page_number"] = pg; pnl["index"] = ix
            return pnl, str(page.get("source_image") or "")
        return None, ""

    for scene, members in groups:
        total_dur = sum(m[2] for m in members)
        # MULTI-PANEL per scene: the LLM returns an ORDERED LIST of panels that show
        # this one narration line unfolding (the slam → the count → the triumphant
        # face). slice[i] shows panel_list[i], so a scene now plays across 2-4
        # distinct panels of its OWN page instead of holding a single frame.
        _sid = int(scene.get("scene_id") or 0)
        panel_list = llm_panels.get(_sid) or []
        # Distinct panels this scene gets: the LLM's list length, capped by the
        # caption-chunk count (can't show more panels than time slices) and the
        # ~1-panel-per-SHOT_TARGET_SECONDS cut rate. No list → 1 (heuristic fills).
        cap = max(1, min(len(members), 4, round(total_dur / SHOT_TARGET_SECONDS)))
        n_panels = max(1, min(cap, len(panel_list))) if panel_list else cap
        for i, slice_members in enumerate(_split_members(members, n_panels)):
            slice_text = " ".join(m[0] for m in slice_members).strip()
            slice_dur = sum(m[2] for m in slice_members)
            panel = source_image = None
            if i < len(panel_list):   # the LLM's i-th panel for this scene's i-th slice
                panel, source_image = _resolve(panel_list[i])
            if panel is None:   # LLM had no usable pick for this slice → heuristic fallback
                panel, source_image = _select_panel_for_chunk(
                    chunk_text=slice_text,
                    scene=scene,
                    pages_by_number=pages_by_number or {},
                    used_panel_keys=used_global,
                    prev_panel=prev_panel,
                    cluster_to_name=cluster_to_name or {},
                )
            if panel is not None:   # register so no panel repeats across the video
                used_global.add((panel.get("_page_number"), panel.get("index")))
            prev_panel = panel  # track for next slice's coherence score
            text_bboxes: list[dict] = []
            if panel is None:
                # Last resort — no panel data anywhere. Use scene's own bbox.
                bbox = scene.get("panel_bbox") or {}
                source_image = str(scene.get("source_image") or "")
            else:
                bbox = panel.get("bbox") or {}
                # Comic's own text-block bboxes inside this panel → inpaint mask.
                text_bboxes = _panel_text_bboxes(panel, pages_by_number or {})

            motion = _choose_motion(panel, slice_dur)
            shots.append(Shot(
                shot_id=shot_id,
                scene_id=int(scene.get("scene_id") or 1),
                duration_seconds=max(0.4, slice_dur),
                panel_bbox={"x": int(bbox.get("x", 0)), "y": int(bbox.get("y", 0)),
                            "w": int(bbox.get("w", 0)), "h": int(bbox.get("h", 0))},
                source_image=source_image,
                motion=motion,
                text_bboxes=text_bboxes,
            ))
            shot_id += 1
    return shots


def _split_members(members: list, n: int) -> list[list]:
    """Split a scene's chunk members into n contiguous slices of roughly EQUAL
    DURATION (not equal count) — so panels are held for even lengths instead of
    one long 4s hold next to a 0.9s flash. members = (text, start, dur)."""
    if n <= 1 or len(members) <= 1:
        return [members]
    n = min(n, len(members))
    total = sum(m[2] for m in members) or 1.0
    # Bucket each member into one of n equal-DURATION time slots by its midpoint.
    # Members are time-ordered, so midpoints rise → buckets stay contiguous.
    buckets: list[list] = [[] for _ in range(n)]
    cum = 0.0
    for m in members:
        mid = cum + m[2] / 2.0
        idx = min(n - 1, int(mid / total * n))
        buckets[idx].append(m)
        cum += m[2]
    out = [b for b in buckets if b]
    if len(out) < n:
        # A lumpy chunk left a bucket empty → fall back to even-count split so we
        # still get n contiguous, non-empty slices.
        base, rem = divmod(len(members), n)
        out, idx = [], 0
        for j in range(n):
            take = base + (1 if j < rem else 0)
            out.append(members[idx:idx + take])
            idx += take
        out = [s for s in out if s]
    return out


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


def _choose_motion(panel: dict | None, dur: float) -> str:
    """Content-aware motion picker. Default static; subtle zoom_in only for
    splash-sized panels with enough duration. No random pans on close-ups."""
    if dur < MOTION_MIN_DURATION:
        return "static"
    if panel is None:
        return "static"
    bbox = panel.get("bbox") or {}
    w = int(bbox.get("w", 0) or 0)
    h = int(bbox.get("h", 0) or 0)
    if w <= 0 or h <= 0:
        return "static"
    # Page dimensions inferred via source image dimensions if present, else
    # use a typical comic page area threshold (~1200x1800 = 2.16M px).
    panel_area = w * h
    full_page_typical = 1200 * 1800
    # Splash (big panel) → slow zoom_in to make the moment feel epic.
    if panel_area / full_page_typical >= PANEL_BIG_AREA_RATIO:
        return "zoom_in"
    # Otherwise static — most narration chunks are 1-2s and a small panel
    # doesn't benefit from motion (random pan/zoom feels arbitrary).
    return "static"


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

    # ── I: Scene's canonical (page_ref, panel_ref) bonus — SIZE-SCALED ───
    # Stage 3's narration includes panel_ref — the canonical pick. Normally
    # honor it with a big +15. BUT if that pick is a tiny panel (blown up >2×),
    # shrink the bonus so a bigger, clearer panel on the same page can win.
    # A 4% page panel (scale ~4.8×) only gets ~+3; a normal panel keeps +15.
    scene_panel_ref = scene.get("panel_ref")
    panel_index = int(panel.get("index", -1))
    panel_page = int(panel.get("_page_number", 0) or 0)
    scene_page_ref_int = int(scene.get("page_ref", 0) or 0)
    if (scene_panel_ref is not None and scene_page_ref_int
            and panel_page == scene_page_ref_int
            and panel_index == int(scene_panel_ref)):
        honor_factor = max(0.2, min(1.0, 1.0 - 0.8 * (panel_scale - 2.0) / 2.0))
        score += 15.0 * honor_factor

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

        # Monotonic page progression (narrative order): reward a panel whose page
        # is equal-or-forward vs the previous shot, and penalize a backward jump
        # by distance — so a panel from an early page never resurfaces late.
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

    # ── C: Visual salience (TODO #124 signal 3) — bigger panel = more important
    # moment (splash/reveal). Replaces the old near-negligible +0.5·log term with
    # a normalized [0,1] bonus weighted +3. The small-panel upscale penalty above
    # stays as a separate image-QUALITY guard (distinct axis: blur vs importance).
    bbox = panel.get("bbox", {}) or {}
    area = int(bbox.get("w", 0) or 0) * int(bbox.get("h", 0) or 0)
    if area > 50000:
        area_saliency = min(1.0, math.log(area / 50000) / 3.0)
        score += 3.0 * area_saliency

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
    if pt in ("cover", "credits", "ads", "promo", "title"):
        return True
    summary = (page.get("page_summary") or "").lower()
    summary_markers = (
        "promotional or advertisement",
        "promotional page",
        "advertisement page",
        "creator credits",
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


def _select_panel_for_chunk(
    *,
    chunk_text: str,
    scene: dict,
    pages_by_number: dict[int, dict],
    used_panel_keys: set,
    prev_panel: dict | None = None,
    cluster_to_name: dict[int, str] | None = None,
) -> tuple[dict | None, str]:
    """Pick BEST-MATCH panel from pool (scene.page_ref ± 1 adjacent pages).

    Never picks an already-used (page, panel_idx) within the same scene's chunks.
    Falls back to ±2 pool with repetition allowed if the ±1 pool is exhausted.
    Returns (panel_dict_or_None, source_image_path)."""
    page_ref = int(scene.get("page_ref", 0) or 0)

    # Intro teaser scene: ALWAYS show the cover full-frame. The cover is a
    # page_type=="cover" page, which _is_skip_page() deliberately filters out for
    # story shots — so we resolve it directly here and bypass the skip filter.
    if scene.get("is_intro"):
        cover = pages_by_number.get(page_ref)
        if cover:
            src = str(cover.get("source_image") or "")
            dims = cover.get("image_dimensions") or {}
            iw = int(dims.get("width", 0) or 0)
            ih = int(dims.get("height", 0) or 0)
            panels = cover.get("panels") or []
            if iw and ih:
                full = {"bbox": {"x": 0, "y": 0, "w": iw, "h": ih}, "_page_number": page_ref}
                return full, src
            if panels:
                p = dict(panels[0]); p["_page_number"] = page_ref
                return p, src

    def gather(pages_range):
        out = []  # (score, panel, source_image, key)
        for pn in pages_range:
            page = pages_by_number.get(pn)
            if not page:
                continue
            if _is_skip_page(page):
                continue
            src = str(page.get("source_image") or "")
            page_tb = page.get("text_blocks") or []
            for idx, panel in enumerate(page.get("panels") or []):
                key = (pn, idx)
                if key in used_panel_keys:
                    continue
                # Inject page_number so _score_panel can apply page_ref bonus
                panel_with_pg = dict(panel)
                panel_with_pg["_page_number"] = pn
                s = _score_panel(
                    panel_with_pg, chunk_text, scene,
                    page_text_blocks=page_tb,
                    prev_panel=prev_panel,
                    cluster_to_name=cluster_to_name,
                )
                out.append((s, panel_with_pg, src, key))
        return out

    # FORWARD-ONLY widening (TODO #125): always include the scene's page AND the
    # next 1-2 pages, NEVER an earlier page. This mirrors reading order — scenes
    # march forward through the comic and an early panel can't resurface late
    # (kills the "random early panel appears at the end" duplication). When the
    # scene's own page has no good free panel (e.g. only the abstract FIZAPPT
    # left), a semantically matching panel on the NEXT page can now win, because
    # the page_ref bonus was loosened to +4. Backward fallback is gone on purpose.
    candidates = gather([page_ref, page_ref + 1, page_ref + 2])
    if not candidates:
        # Last resort only: the scene's forward window is entirely exhausted.
        candidates = gather(range(page_ref, page_ref + 5))

    if candidates:
        # Sort by score desc
        candidates.sort(key=lambda t: -t[0])

        # G: LLM-as-judge tie-breaker — only when top 2 scores are close.
        # Gated off by default (C): it injected nondeterministic neighbor-page
        # picks via the default LLM chain. Heuristic top is used directly.
        if (LLM_PANEL_JUDGE and len(candidates) >= 2
                and (candidates[0][0] - candidates[1][0]) < 1.0):
            winner = _llm_judge_tiebreak(chunk_text, candidates[:5])
            if winner is not None:
                _, panel, src, key = winner
                used_panel_keys.add(key)
                return panel, src

        score, panel, src, key = candidates[0]
        used_panel_keys.add(key)
        return panel, src

    # Last resort — wider pool, repetition allowed
    best: tuple[float, dict, str] | None = None
    for pn in range(max(1, page_ref - 3), page_ref + 4):
        page = pages_by_number.get(pn)
        if not page or _is_skip_page(page):
            continue
        src = str(page.get("source_image") or "")
        page_tb = page.get("text_blocks") or []
        for panel in (page.get("panels") or []):
            s = _score_panel(panel, chunk_text, scene,
                             page_text_blocks=page_tb, prev_panel=prev_panel,
                             cluster_to_name=cluster_to_name)
            if best is None or s > best[0]:
                best = (s, panel, src)
    if best:
        return best[1], best[2]

    # No panel data anywhere — caller will fall back to scene's own bbox
    return None, str(scene.get("source_image") or "")


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
) -> Path:
    """Render one Ken Burns shot to MP4."""
    ff = _require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = work_dir or out_path.parent / "_panels"
    work_dir.mkdir(parents=True, exist_ok=True)

    panel_png = work_dir / f"panel_{shot.shot_id:03d}.png"
    _crop_panel(shot.source_image, shot.panel_bbox, panel_png,
                text_bboxes=getattr(shot, "text_bboxes", None))

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
    filter_complex = f"[0:v]{pre}{_zoompan_expr(shot.motion, frames)}[v]"

    cmd = [
        ff, "-y",
        "-framerate", "1",
        "-loop", "1",
        "-t", "1",
        "-i", str(framed),
        "-filter_complex", filter_complex,
        "-map", "[v]",
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


def _zoompan_expr(motion: str, frames: int) -> str:
    """ffmpeg zoompan expression. Subtle motion (max 1.05) — reference channels
    favor static cuts with tiny push-in on splash moments. Big zoom on every
    shot feels random/distracting."""
    s = f"{OUTPUT_W}x{OUTPUT_H}"
    fps = FPS
    if motion == "zoom_in":
        return (
            f"zoompan=z='min(1.05,zoom+{0.05 / max(1, frames):.6f})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "zoom_out":
        return (
            f"zoompan=z='if(eq(on,0),1.05,max(1.0,zoom-{0.05 / max(1, frames):.6f}))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={s}:fps={fps}"
        )
    if motion == "pan_right":
        return (
            f"zoompan=z='1.03':"
            f"x='iw/2-(iw/zoom/2)+(iw*0.03)*(on/{max(1, frames)})':"
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
    upscale) centered over a blurred copy of itself filling the frame. We no longer
    gate on aspect ratio — high cover-scale alone is the trigger, because that is
    exactly when cover-crop loses too much regardless of shape."""
    with Image.open(panel_png) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        cover = max(OUTPUT_W / iw, OUTPUT_H / ih)

        use_blur_bg = cover > _BLUR_FALLBACK_SCALE
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
        out = _LAMA(rgb, Image.fromarray(mask))      # PIL RGB result
        if out.size != (iw, ih):
            out = out.resize((iw, ih))
        return np.ascontiguousarray(np.array(out)[:, :, ::-1])  # RGB → BGR
    except Exception:
        _LAMA_FAILED = True
        return None


def _crop_panel(source_image: str, bbox: dict[str, int], out_path: Path,
                text_bboxes: list[dict] | None = None) -> Path:
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
        int(bbox.get("w", 0)), int(bbox.get("h", 0)), bool(MIRROR_PANELS),
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
        pad_x = int(w * PADDING_PCT); pad_y = int(h * PADDING_PCT)
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
        # 3. Mirror horizontally.
        if MIRROR_PANELS:
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
        pad_x = int(w * PADDING_PCT); pad_y = int(h * PADDING_PCT)
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
