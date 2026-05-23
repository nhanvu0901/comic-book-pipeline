# Pipeline v3 — narrative coherence + smart panel selection

**Date**: 2026-05-23
**Author**: Claude (with user direction)
**Status**: Approved by user, ready for implementation
**Approach**: A2 — prompt + outline validator + smart Stage 5 panel picker

## Context & motivation

After channel-match v2 shipped (commit `78faf80` and earlier), user feedback on
the venom v2 output was:

1. **Duplicate panel images** — visuals jump from one panel to another and back
   to the original within a single scene.
2. **Story drifts from source** — narration adds details not in the comic and/or
   skips entire chapters of the comic.
3. **Confusing narrative** — too many details crammed per sentence, hard to
   follow the throughline.

Deep analysis of the actual venom v2 artifact + 5 sampled reference VTTs found
**four concrete root causes**:

| # | Issue | Evidence |
|---|---|---|
| 1 | Sentence overstuffing | venom scene 1 = 37 words / 5 events. Channel median = 20 words / 1 event. |
| 2 | Narrative page-gap | venom scenes 1-9 cover pages 3-10, scene 10 jumps to page 32. Pages 11-31 (entire mid-act) skipped. |
| 3 | Panel cycle wrap | Stage 5 caption-chunk mode does `page_panel_idx % len(panels)` → 6 chunks on a 4-panel page cycles 0,1,2,3,0,1 → panel 0 shows TWICE within scene. |
| 4 | Missing channel idiom | "That's when X" / "Just then X" used 4+ times in single reference VTT, NOT in our connective whitelist. |

Goal: pipeline v3 closes all four, no LLM upgrade.

## Architecture overview

Two files touched: `stages/stage_3/write_script.py` (prompt + new validator)
and `stages/stage_5/shots.py` (smart panel picker, replaces `page_panel_idx`).

```
Stage 3 (narration writer)                  Stage 5 (video)
──────────────────────────                  ───────────────

 Phase A outline_beats                      _select_panel_for_chunk():
   - NEW _validate_outline()                  - Score-based pick across an
   - retry once if max page-gap > 5             EXPANDED pool of pages
     with explicit bridge instruction          (page_ref ± 1)
                                              - Score: character overlap (+3)
 Phase C write_scenes                          + keyword overlap (+1 per word)
   - NEW: sentence target 14-22 words         - Track used_panel_keys per scene
   - NEW: "ONE event per sentence"            - Fallback: best-scored repeat
   - NEW: "page coverage mandatory"             only if pool exhausted
   - NEW: "That's when" / "Just then"
     connectives added

 _validate():
   - _SCENE_MIN_WORDS: 22 → 14
   - _SCENE_MAX_WORDS: 35 → 25
   - new soft: median scene ≤ 22w
```

## Component changes

### C-1: Sentence-shape mandate (write_script.py)

Replace the current SENTENCE SHAPE section in `_WRITE_SYSTEM`:

```
3) SENTENCE SHAPE — CRITICAL FOR LISTENABILITY
   - Each scene = ONE simple compound sentence, 14-22 words. Channel median 20.
   - **ONE event per sentence.** NOT multiple events with "while...but...and...".
   - Channel examples (count the events in each):
     ✓ "When Frank Castle entered Valhalla, he couldn't find peace." (9w, 1 event)
     ✓ "So, Odin returned his cosmic powers and turned him into Ghost Rider again." (13w, 1 event)
     ✓ "Just then, Frank went back in time determined to fix his past mistakes." (14w, 1 event)
   - ANTI-pattern (do NOT write):
     ✗ "When suit tears during Secret Wars, tendrils ooze while Reed realizes,
        but tube cracks, and Thing is about to discover it." (5 events, confusing)
```

Constants:

```python
# Was 22 / 35 / 25 / 20
_SCENE_MIN_WORDS = 14
_SCENE_MAX_WORDS = 25
_TARGET_SENT_LEN = 20  # used by soft-validator (median check)
```

### C-2: Add "That's when" / "Just then" connectives

```python
_CONNECTIVES = (
    "But", "So", "However", "When", "After", "Then", "Eventually",
    "As", "Instead", "With", "Now", "Suddenly", "Until", "Meanwhile", "Soon",
    "Just then",   # NEW — channel transitional pivot
    "That's when", # NEW — channel narrative pivot
)
```

NOTE: validator's first-word check uses `first_word.split()[0]`. "Just then,"
splits to "Just" which won't match. Need to update validator to accept
multi-word connectives:

```python
# Match longest connective first
def _starts_with_connective(text: str) -> str | None:
    for c in sorted(_CONNECTIVES, key=len, reverse=True):
        if text.lower().startswith(c.lower()):
            return c
    return None
```

Wire `_starts_with_connective` into `_validate()`.

### C-3: Page coverage mandate (write_script.py)

Add to `_WRITE_SYSTEM` after section 7:

```
8) PAGE COVERAGE — MANDATORY LINEAR FLOW
   - Your scenes MUST move through the comic pages monotonically (each scene's
     page_ref ≥ previous scene's page_ref).
   - Gap between consecutive scenes' page_refs MUST be ≤ 5 pages. If a story
     beat requires skipping 6+ pages, INSERT a bridge sentence summarizing the
     skipped action. Example bridge:
       "Eventually, after [brief summary of the skipped pages], [next event]..."
   - DO NOT jump from page 10 to page 32 without bridging — the viewer loses
     the throughline.
```

### C-4: Outline-phase page-gap validator (write_script.py)

NEW function:

```python
def _validate_outline(beats: list[Beat], max_gap: int = 5) -> list[str]:
    """Soft validation of outline. Returns list of issue strings (empty = OK)."""
    issues = []
    if len(beats) < 8:
        issues.append(f"only {len(beats)} beats (target 10-12)")

    # Sort by lowest page_ref; check consecutive gaps
    sorted_beats = sorted(
        [b for b in beats if b.page_refs],
        key=lambda b: min(b.page_refs),
    )
    for prev, nxt in zip(sorted_beats, sorted_beats[1:]):
        prev_end = max(prev.page_refs)
        next_start = min(nxt.page_refs)
        gap = next_start - prev_end
        if gap > max_gap:
            issues.append(
                f"beat {prev.id}→{nxt.id} page-gap {gap} "
                f"(pages {prev_end+1}-{next_start-1} skipped)"
            )
    return issues


def _retry_outline_with_bridge(
    original_beats: list[Beat], issues: list[str], ...,
) -> list[Beat]:
    """Re-ask LLM for a new outline that inserts bridge beats for the skipped
    pages. Single retry; if it still fails, accept and log."""
    ...
```

Wire into `outline_beats()` after JSON parse:

```python
issues = _validate_outline(beats)
if issues:
    log(f"[stage4]   outline validation: {len(issues)} issue(s)")
    for iss in issues[:3]:
        log(f"[stage4]     - {iss}")
    log("[stage4]   retrying outline with bridge instruction…")
    beats = _retry_outline_with_bridge(beats, issues, ...)
    # accept whatever, even if still imperfect — soft warning only
```

### C-5: Sentence-length soft validator

Add to `_validate()`:

```python
# Median check (soft): bulk of scenes should be ≤22w
import statistics
sent_lens = [len(str(s.get("text", "")).split()) for s in scenes[1:]]  # skip hook
if sent_lens and statistics.median(sent_lens) > _TARGET_SENT_LEN + 3:
    errors.append(
        f"median scene length {statistics.median(sent_lens):.0f} > {_TARGET_SENT_LEN+3} "
        f"(target {_TARGET_SENT_LEN})"
    )
```

`_is_critical_error()` treats this as soft (not in critical_markers).

### S-1: Stage 5 smart panel selection

Replace cycling logic in `_build_shots_per_chunk()`:

```python
def _build_shots_per_chunk(narration, caption_chunks, pages_by_number, scene_timings):
    """Pick BEST-MATCH panel per chunk from an expanded pool (scene's page ± 1
    adjacent page), with no repeat within a scene."""
    ...
    used_by_scene: dict[int, set] = {}  # scene_id -> {(page_num, panel_idx)}
    
    for i, chunk in enumerate(caption_chunks):
        scene = find_scene_for_chunk(chunk)
        if scene is None: continue
        sid = int(scene.get("scene_id", 1))
        used = used_by_scene.setdefault(sid, set())
        
        panel, source_img = _select_panel_for_chunk(
            chunk_text=chunk.get("text", ""),
            scene=scene,
            pages_by_number=pages_by_number,
            used_panel_keys=used,
        )
        bbox = panel.get("bbox", {})
        ...
        shots.append(Shot(...))
    return shots


def _select_panel_for_chunk(*, chunk_text, scene, pages_by_number, used_panel_keys):
    page_ref = int(scene.get("page_ref", 0))
    candidate_pages = [page_ref - 1, page_ref, page_ref + 1]
    
    candidates = []  # [(score, panel, source_image, key)]
    for pn in candidate_pages:
        page = pages_by_number.get(pn)
        if not page: continue
        source_img = str(page.get("source_image", ""))
        for idx, panel in enumerate(page.get("panels", [])):
            key = (pn, idx)
            if key in used_panel_keys: continue
            score = _score_panel(panel, chunk_text, scene)
            candidates.append((score, panel, source_img, key))
    
    if not candidates:
        # Pool exhausted (very long scene). Repeat best scoring panel from full pool.
        return _fallback_pick(scene, pages_by_number, chunk_text)
    
    candidates.sort(key=lambda x: -x[0])
    score, panel, img, key = candidates[0]
    used_panel_keys.add(key)
    return panel, img


def _score_panel(panel, chunk_text, scene) -> float:
    """Character overlap (+3 ea), keyword overlap (+1 ea), minus common-word noise."""
    STOPWORDS = {"the","a","an","is","was","and","of","to","in","on","with","that",
                 "as","but","so","when","then","after","while","he","she","they",
                 "his","her","their","this","by","for","at","from"}
    
    score = 0.0
    panel_chars = set(c.lower() for c in panel.get("characters", []))
    chunk_words = {w.lower().strip(",.!?:;\"'") for w in chunk_text.split()}
    chunk_words -= STOPWORDS
    
    for ch in panel_chars:
        first = ch.split()[0].lower() if ch else ""
        if first and first in chunk_words:
            score += 3.0
    
    desc_words = {w.lower().strip(",.!?:;\"'") for w in panel.get("description", "").split()}
    desc_words -= STOPWORDS
    common = desc_words & chunk_words
    score += len(common) * 1.0
    
    return score


def _fallback_pick(scene, pages_by_number, chunk_text) -> tuple[dict, str]:
    """All panels in the ±1 pool used. Score every panel in the wider pool
    (±2 pages) and return the best — repetition allowed at this point."""
    page_ref = int(scene.get("page_ref", 0))
    best = None  # (score, panel, source_image)
    for pn in range(page_ref - 2, page_ref + 3):
        page = pages_by_number.get(pn)
        if not page: continue
        source_img = str(page.get("source_image", ""))
        for panel in page.get("panels", []):
            s = _score_panel(panel, chunk_text, scene)
            if best is None or s > best[0]:
                best = (s, panel, source_img)
    return (best[1], best[2]) if best else (scene.get("panel_bbox", {}), scene.get("source_image", ""))
```

## Data flow

Unchanged input contracts:
- Stage 3 reads `comic_context.json` + `preprocessed/*.json`, writes `narration.json`.
- Stage 5 reads narration + scene_timings + caption_chunks + preprocessed pages.

New internal data: `used_panel_keys` set tracked per-scene inside Stage 5.

## Error handling

- `_validate_outline()` returns soft issues; retry once with bridge instruction;
  accept after retry regardless. Never crashes the pipeline.
- `_select_panel_for_chunk()` falls back to wider pool if pool exhausted.
  Last-resort fallback to scene's own panel_bbox if no candidates anywhere.
- `_starts_with_connective()` is a pure-text classifier; raises nothing.

## Testing approach

Re-run `thing_bond_with_venom` (existing preprocessed cache — free):

```
rm projects/thing_bond_with_venom/{narration,word_timestamps,scene_timings,caption_chunks}.json
rm projects/thing_bond_with_venom/{audio,audio_mixed,video_silent}.{wav,mp4}
rm -rf projects/thing_bond_with_venom/{shots,captions.ass,final.mp4}

python -m stages.stage_3 --project thing_bond_with_venom --mode panel_walk
python -m stages.stage_4 --project thing_bond_with_venom
python -m stages.stage_5 --project thing_bond_with_venom --force

python research/scripts/view_video.py projects/.../final.mp4 -n 12 --label venom_v3
python research/scripts/gap_report.py
```

## Success criteria (target 5/6 to ship)

| # | Criterion | Now (v2) | Target (v3) |
|---|---|---|---|
| 1 | ≥9/10 scenes ≤25 words | ~3/10 | **≥9/10** |
| 2 | Median scene length (excl hook) | ~30w | **≤22w** |
| 3 | Max page-ref gap between consecutive scenes | 22 | **≤5** |
| 4 | VLM frame audit duplicate visuals (12 sampled) | ~3-5 | **≤1** |
| 5 | "That's when" or "Just then" used ≥1 time | 0 | **≥1** |
| 6 | Gap report overall match ≥ 95% | 100% | ≥95% |

If <5 hit → diagnose and iterate. Specifically:
- #1/#2 fail → prompt too weak, try moving examples to system prompt
- #3 fail → outline validator retry not converging, escalate to enforce hard
- #4 fail → smart picker score function needs tuning

## Out of scope

- LLM upgrade (Gemini → Claude/GPT-4) — defer if A2 measures insufficient.
- Background music — separate concern.
- Cartesia voice/emotion change — fixed in earlier commits.
- Schema change for scene-range mapping (would be Approach A3) — defer.

## Implementation notes

- `_starts_with_connective()` must match LONGEST connective first (so "That's when"
  matches before "That" if "That" were ever added).
- `_score_panel()` STOPWORDS list should match the prompt's anti-pattern list —
  don't reward overlap on filler words.
- Smart panel picker maintains state PER-SCENE not per-video; resetting between
  scenes is important so later scenes can re-use panels from earlier pages if
  needed (rare but possible).
- After "That's when" / "Just then" added to whitelist, also update the WRITE_SYSTEM
  prompt's CONNECTIVE GRAMMAR section to note these are channel-signature pivots.
