# Channel-Match v2 — pipeline updates to copy @TheComicCivilian style

**Date**: 2026-05-23
**Author**: Claude (with user direction)
**Status**: Approved by user, ready for implementation plan
**Approach**: A (prompt + few-shot only — no LLM upgrade)

## Context & motivation

We have analyzed 219 of @TheComicCivilian's shorts (VTT transcripts) and 29 with VLM
frame audits. Despite our pipeline hitting "100% metric match" earlier, the user
reports the output still doesn't feel like the channel. Five concrete gaps were
identified from the data:

1. **Hook formula**: 66% of channel videos start with `"When [event happened], [twist]..."`.
   Ours opens with character names ("The Green Goblin unleashes...") or generic
   "What if...?" — neither matches the channel-dominant pattern.

2. **Word count**: Channel mean 242 words (range 140-373). Ours: 193.

3. **Sentence count**: Channel mean 12 sentences. Ours: 8.

4. **Connective "So"**: Used 238× by the channel (9.0% of sentences — the 2nd most
   common connective). NOT in our `_CONNECTIVES` whitelist.

5. **Visual**: 65 of 66 channel frames audited use single-panel-filling-frame.
   We use blur-fill background when panel aspect < 0.7. Channel doesn't.

Goal: close all 5 gaps with prompt-only changes (no LLM upgrade — keep cost flat).
Defer LLM upgrade (approach B) until A is measured insufficient.

## Architecture overview

Two files touched: `stages/stage_3/write_script.py` (4 changes) and
`stages/stage_5/shots.py` (1 change). No new modules.

```
Stage 3 (narration writer)                  Stage 5 (video)
──────────────────────────                  ───────────────
                                            
 Phase A outline_beats:                     _prepare_panel_frame():
   - 8-10 beats → 10-12 beats                 - Always cover-scale
                                              - Drop blur-fill branch
 Phase B build_glossary: unchanged            - Drop ASPECT_THRESHOLD
                                            
 Phase C write_scenes:                      
   - NEW: hook formula "When [event],..."  
   - NEW: connective whitelist + "So"      
   - NEW: target 240w / 12 sentences       
   - NEW: 3 few-shot channel transcripts   
                                            
 _validate():                               
   - Word window 230-290 (was 210-270)     
```

## Component changes

### C-1: Hook formula rewrite (Stage 3 WRITE_SYSTEM)

Replace the current "PREFER archetype A/B" section with a stricter "When [event]..."
mandate. Real channel examples from `research/reports/vtt_patterns_full.json`:

```
1) HOOK FORMULA — MANDATORY "When [event], [twist]..." structure

Channel benchmark (66% of 219 videos open with "When..."):
  ✓ "When Miles Morales investigated a rooftop while being invisible, he came across..."
  ✓ "When members of the Titans and Justice League were kidnapped, Wonder Woman..."
  ✓ "When Frank Castle entered Valhalla, he couldn't find peace, so Odin..."

Acceptable but less common alternates:
  ◐ "After [event happened], [character action]..." (~10% of channel)
  ◐ "What if [question]?" (rare on this channel — used by other channel only)

HARD BAN:
  ✗ Starting with a character name as the first word
  ✗ "In an alternate universe..." (different channel's signature)
  ✗ "Today we're looking at..." / framing meta-talk

The hook MUST be 18-28 words and end with an open thread that pulls the viewer
into scene 2.
```

### C-2: Connective whitelist + "So"

`stages/stage_3/write_script.py`:

```python
# Was: ("But", "However", "As", "When", "After", "Eventually", "Instead",
#       "With", "Now", "Suddenly", "Then", "Until", "Meanwhile", "Soon")
# Add "So" (channel's 2nd most-used connective at 9.0% of sentences).

_CONNECTIVES = (
    "But", "So", "However", "When", "After", "Then", "Eventually",
    "As", "Instead", "With", "Now", "Suddenly", "Until", "Meanwhile", "Soon",
)
```

Validation already checks first-word membership in this tuple, so adding "So"
just expands the legal set. No code path change.

### C-3: Word / sentence / beat targets

| Constant | Was | New | Reason |
|---|---|---|---|
| `_TARGET_WORDS_MIN` | 220 | **240** | Channel mean 242 |
| `_TARGET_WORDS_MAX` | 260 | **280** | Channel max 373 — give upper room |
| `_SCENE_MIN_WORDS` | 25 | **22** | Lower per-scene min so writer can fit 12 sentences in budget |
| Validation window | 210-270 | **230-290** | Wider to reduce retry pressure |
| Outline beats target | 8-10 | **10-12** | Channel avg 12 sentences ≈ 12 beats |

Update outline prompt: "Extract **10-12 beats** that build a {mode} arc..."
Update WRITE prompt LENGTH BUDGET section to say "240-280 words total. 10-12 scenes."

### C-4: Few-shot channel examples

New helper in `write_script.py`:

```python
def _load_few_shot_examples(n: int = 3) -> str:
    """Pick n .vtt files from research/reference/, extract the first 80 words of each
    (hook + 1-2 scenes), format as channel-style demonstration block.

    Returns "" if research/reference/ is missing (gracefully degrade to no few-shot)."""
    ref_dir = Path(__file__).parent.parent.parent / "research" / "reference"
    if not ref_dir.exists():
        return ""
    vtts = sorted(ref_dir.glob("*.en.vtt"))
    if not vtts:
        return ""
    sample = random.Random(42).sample(vtts, min(n, len(vtts)))  # deterministic
    blocks = []
    for vtt in sample:
        # Reuse parse_vtt logic from research/scripts/vtt_pattern_analyzer.py
        # (extracted to a shared helper as part of this work — see implementation plan).
        cues = _parse_vtt_cues(vtt)
        full = " ".join(c["text"] for c in cues)
        # Take first 80 words = hook + 1-2 scenes worth.
        snippet = " ".join(full.split()[:80])
        if snippet:
            blocks.append(f"Example {len(blocks)+1} hook + opening:\n{snippet}")
    if not blocks:
        return ""
    return (
        "CHANNEL STYLE EXAMPLES (real top-performing Shorts from the channel we are
mimicking — study their hook rhythm and connective density):\n\n"
        + "\n\n".join(blocks)
        + "\n\nMatch this rhythm and density. Don't copy any specific story — apply the
shape to OUR comic.\n\n"
    )
```

Injected into `write_scenes()` user prompt **after** GLOSSARY block, **before**
PAGE DETAIL block. Adds ~500-700 chars to prompt (negligible vs 30K total).

Random seed `42` for determinism — same 3 examples every run, easy to debug.

### S-1: Always cover-scale (Stage 5)

`stages/stage_5/shots.py:_prepare_panel_frame()` — delete the entire wide-panel
blur-fill branch:

```python
def _prepare_panel_frame(panel_png: Path, out_path: Path) -> Path:
    """Always cover-scale to fill 1080×1920. Wide panels lose edge content;
    channel data shows this trade-off is correct."""
    with Image.open(panel_png) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        scale = max(OUTPUT_W / iw, OUTPUT_H / ih)
        new_w = max(OUTPUT_W, int(round(iw * scale)))
        new_h = max(OUTPUT_H, int(round(ih * scale)))
        scaled = im.resize((new_w, new_h), Image.LANCZOS)
        x0 = (new_w - OUTPUT_W) // 2
        y0 = (new_h - OUTPUT_H) // 2
        frame = scaled.crop((x0, y0, x0 + OUTPUT_W, y0 + OUTPUT_H))
    frame.save(out_path, "PNG")
    return out_path
```

Delete `ASPECT_THRESHOLD` constant. Remove `from PIL import ImageFilter`
import if no other use.

## Data flow

Unchanged. Same I/O contracts:
- Stage 3 reads `comic_context.json` + `preprocessed/*.json`, writes `narration.json`.
- Stage 5 reads narration + `scene_timings.json` + `caption_chunks.json` + preprocessed pages, writes `final.mp4`.
- Few-shot examples load lazily at first `write_scenes()` call.

## Error handling

- `_load_few_shot_examples()` returns "" if `research/reference/` missing or empty
  → graceful degradation, writer still runs with new prompt structure but no examples.
- All Stage 3 changes are prompt-only: no new exception types.
- Stage 5 cover-scale change is a code deletion: no new branches to handle.

## Testing approach

Re-run the existing `thing_bond_with_venom` project end-to-end:

1. **Clean Stage 3+ outputs** (keep preprocessed — free):
   ```
   rm projects/thing_bond_with_venom/{narration,word_timestamps,scene_timings,caption_chunks}.json
   rm projects/thing_bond_with_venom/{audio,audio_mixed,video_silent}.{wav,mp4}
   rm -rf projects/thing_bond_with_venom/{shots,captions.ass,final.mp4}
   ```

2. **Run pipeline**:
   ```
   python -m stages.stage_3 --project thing_bond_with_venom --mode panel_walk
   python -m stages.stage_4 --project thing_bond_with_venom
   python -m stages.stage_5 --project thing_bond_with_venom --force
   ```

3. **Run analyzers**:
   ```
   python research/scripts/analyze_video.py projects/.../final.mp4 \
       --words-json projects/.../word_timestamps.json --label venom_v2
   python research/scripts/view_video.py projects/.../final.mp4 -n 8 --label venom_v2
   python research/scripts/gap_report.py
   ```

4. **Success criteria** (target 5/6 to ship; <5 → escalate to Approach B):
   - [ ] Hook archetype classified `interrogative` or `temporal` (currently `other`)
   - [ ] Word count ≥ 230 (was 193)
   - [ ] Sentence count ≥ 10 (was 8)
   - [ ] At least 1 use of "So" connective in narration (was 0)
   - [ ] Stage 5: 0 frames with `panel_layout: "letterboxed with blur bg"` per VLM audit
   - [ ] Gap report overall match ≥ 95%

## Out of scope

- LLM upgrade (Gemini → Claude) — defer to Approach B if A is insufficient.
- Background music — separate concern, not in benchmark gap.
- Cartesia voice change — pacing already addressed in earlier commits.
- Caption animation (per-word reveal) — channel uses static, ours already static.

## Implementation notes

- Few-shot example loading uses `random.Random(42).sample(...)` for deterministic
  reproducibility. Don't switch to global `random` or os-default seed.
- Don't bump `_HOOK_MIN_WORDS` / `_HOOK_MAX_WORDS` — channel hook lengths
  (15-30 words) are already within current 18-30 range.
- Connective ordering in `_CONNECTIVES` doesn't affect validator (just `in` check),
  but put "But", "So", "However" first for readability.
- After "So" is added, also update `_retry_fix` prompt text — currently lists
  connective whitelist explicitly; keep in sync.
