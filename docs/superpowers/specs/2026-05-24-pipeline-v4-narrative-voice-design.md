# Pipeline v4 — narrative voice + Claude Sonnet writer

**Date**: 2026-05-24
**Author**: Claude (with user direction)
**Status**: Approved by user, ready for implementation
**Approach**: P2 — prompt overhaul + LLM upgrade for Stage 3 phase C only

## Context & motivation

After pipeline v3 shipped (commit history), user feedback on the venom v3
output: still doesn't match @TheComicCivilian feel despite metric parity. Deep
comparison of v3 narration vs 3 reference VTTs revealed 10 narrative-quality
gaps that prompt-engineering on Gemini Flash Lite (small model) cannot close:

| # | Aspect | Reference | Ours v3 |
|---|---|---|---|
| 1 | Sentence length variance | Mixed 5-30w; punchy short ones | Uniform 19-24w |
| 2 | Narrator voice | Storyteller (he/him, feeling-anchored) | Descriptor (names repeated, abstract) |
| 3 | Outro pattern | ALWAYS "The comic is X" | None |
| 4 | Specificity | "threatened to kill him if he escaped" | "voices deep distrust" |
| 5 | Pronoun usage | He/him after 1st intro | Names repeated each sentence |
| 6 | Emotional anchor | "Frank couldn't find peace" (state) | "deep distrust and fear" (named) |
| 7 | Temporal markers | "There he had..." / "Then later..." | "Meanwhile / Eventually" (bullet-y) |
| 8 | Dialog attribution | "stating X..." participle phrases | None |
| 9 | Final image | Concrete visual | Abstract concept |
| 10 | Short punch sentences | 5-12w landing moments | Zero — all medium-uniform |

Root cause: Gemini Flash Lite produces uniformly-structured creative text — it
matches structural rules (word counts, hooks) but doesn't naturally vary voice.
Strong creative LLMs (Sonnet, GPT-4o) do this natively.

Goal: pipeline v4 closes all 10 gaps. Approach P2 = prompt overhaul + LLM
upgrade for ONLY the creative phase (write_scenes + retry).

## Architecture overview

Split LLM routing by phase nature:

```
Stage 3 (narration writer)
─────────────────────────────────
 Phase A outline_beats   ← LLM_MODELS chain (structured JSON extraction)
                            (Gemini Flash Lite + free models)
 Phase B build_glossary  ← LLM_MODELS chain (structured JSON extraction)
 Phase C write_scenes    ← NEW CREATIVE_LLM_MODELS chain:
                            primary: anthropic/claude-sonnet-4-6
                            fallback: google/gemini-2.5-flash-lite
 Phase C retry_fix       ← Same CREATIVE_LLM_MODELS chain
```

**Why split**: outline/glossary are bounded-shape tasks small models handle
well. Write is creative-voice — needs the storytelling capacity Sonnet has
natively.

**Cost impact**: write_scenes prompt ~7.5K input + 3K output tokens × Sonnet
($3/M input + $15/M output) ≈ **$0.07/video**. Outline/glossary phases unchanged.
Total v4 cost ~$0.08-0.10/video (was ~$0.02 v3).

## Component changes

### C-1: `CREATIVE_LLM_MODELS` chain (config.py)

```python
# Creative writing chain — separate from LLM_MODELS for Stage 3 phase C.
# Phase C produces voice/rhythm sensitive narration; Gemini Flash Lite is too
# small for natural sentence-length variance and storyteller pronoun discipline.
_DEFAULT_CREATIVE_CHAIN = (
    "anthropic/claude-sonnet-4-6,"
    "google/gemini-2.5-flash-lite"
)
CREATIVE_LLM_MODELS: list[str] = [
    m.strip() for m in os.getenv("CREATIVE_LLM_MODELS", _DEFAULT_CREATIVE_CHAIN).split(",")
    if m.strip()
]
```

### C-2: write_scenes + retry use CREATIVE_LLM_MODELS

In `stages/stage_3/write_script.py`:

```python
# Import alongside existing
from config import CREATIVE_LLM_MODELS, OPENROUTER_MODEL

# In write_scenes() and _retry_fix(), replace:
chain = [model] if model else None
# WITH:
chain = [model] if model else list(CREATIVE_LLM_MODELS)
```

Other phases (outline_beats, build_glossary, propose_modes) keep using
`LLM_MODELS` default chain via `call_with_chain` no-models arg.

### C-3: WRITE_SYSTEM section 11 — VOICE & RHYTHM (the 10 rules)

Append to current `_WRITE_SYSTEM`:

```
11) VOICE & RHYTHM — channel-calibrated from 219 reference Shorts

   11a. SENTENCE LENGTH VARIANCE — mix short + long
        Distribute scene lengths like a real script:
          - 2-3 PUNCH sentences (5-12 words) for landing moments
          - 6-8 medium sentences (14-22 words) for main flow
          - 1-2 long sentences (23-30 words) for setup/exposition
        Channel example: "But, even as an infant, Thanos was a unit." (9w)
        Channel example: "Stating they would die anyway." (5w)
        AVOID uniform 19-24w throughout — that's the AI-tell.

   11b. STORYTELLER VOICE — not panel-reader
        After INTRODUCING a character by canonical name, switch to pronouns
        (he/him/she/her) in the next 2-3 sentences. Re-introduce by name
        ONLY when scene shifts to a different character.
        Channel: "When Frank Castle entered Valhalla, he couldn't find peace.
        So, Odin returned his cosmic powers and turned him into Ghost Rider again."
        AVOID: "Reed Richards X... Reed Y... Reed Z..." every sentence.

   11c. SHOW DON'T TELL — concrete actions over named emotions
        Channel: "Peter unmasked Hobgoblin and threatened to kill him."
        AVOID: "voices deep distrust and fear" (you NAMED the emotion).
        Anchor feelings in CONCRETE physical state:
          ✓ "He was haunted by nightmares."
          ✓ "Frank couldn't find peace."
          ✗ "Reed expresses anxiety about the symbiote situation."

   11d. NATURAL TEMPORAL MARKERS
        Prefer narrative-prose phrases over bullet-y connectives:
          ✓ "There he had a nightmare where..."
          ✓ "Then later that night, while visiting Aunt May..."
          ✓ "Frank had traveled to this specific planet to get advice."
          ✗ "Meanwhile, [event]." / "Eventually, [event]." (too bullet-y)

   11e. "STATING X" FOR DIALOG ATTRIBUTION
        Channel uses participle phrases for what characters say. Use 1-3x:
          ✓ "...stating the suit was alive and was messing with his head."
          ✓ "Stating a timeline where the Punisher raised Thanos would be even worse."

   11f. CONCRETE FINAL IMAGE
        End the LAST narrative scene with a physical, visual moment — not abstract:
          ✓ "...left Hobgoblin's dead body hanging in a spider web."
          ✓ "...he decides to protect this new universe and the life inside."
          ✗ "...reinforcing man's inherent monstrosity and the darkness within."
        (Abstract endings give the viewer's eye nowhere to land.)

   11g. OUTRO CREDIT — MANDATORY closing line
        After all narrative scenes, ADD a final very-short closing scene crediting
        the source. Format: "The comic is [comic title]." (5-8 words, no connective).
        Channel uses this in 100% of videos. Examples:
          "The comic is Spider-Man the Spider Shadow issue"
          "The comic is Cosmic Ghost"
        Include as the FINAL scene with connective=null and page_ref equal to the
        LAST narrative scene's page_ref (so Stage 5 picks a final-page panel).
```

Also update **section 3** (SENTENCE SHAPE) to ALIGN with 11a:

```
3) SENTENCE SHAPE — MIX OF SHORT + MEDIUM + LONG
   - DO NOT write uniformly-sized sentences. Real scripts vary.
   - Target distribution across 12-14 scenes (including outro credit):
     • 2-3 short PUNCH sentences (5-12w)
     • 6-8 medium sentences (14-22w)
     • 1-2 long setup sentences (23-30w)
     • 1 outro credit (~5-8w)
   - Variance is the channel signature; uniformity is the AI-tell.
```

### C-4: Full-script few-shot loader (write_script.py)

Replace existing `_load_few_shot_examples()` (hook-only) with new
`_load_full_script_examples()`:

```python
def _load_full_script_examples(n: int = 2, cap_words: int = 400) -> str:
    """Pick n .vtt files from research/reference/, extract FULL transcript
    (capped at cap_words for token budget), format as end-to-end channel-style
    demonstration. Cached after first call. Deterministic Random(42)."""
    global _FEW_SHOT_CACHE
    if _FEW_SHOT_CACHE is not None:
        return _FEW_SHOT_CACHE

    ref_dir = Path(__file__).resolve().parent.parent.parent / "research" / "reference"
    if not ref_dir.exists():
        _FEW_SHOT_CACHE = ""
        return ""
    vtts = sorted(ref_dir.glob("*.en.vtt"))
    if not vtts:
        _FEW_SHOT_CACHE = ""
        return ""

    sample = random.Random(42).sample(vtts, min(n, len(vtts)))
    blocks: list[str] = []
    for vtt in sample:
        cues = _parse_vtt_cues(vtt)
        if not cues:
            continue
        full = " ".join(cues)
        snippet = " ".join(full.split()[:cap_words])
        if snippet:
            blocks.append(f"=== FULL CHANNEL SCRIPT {len(blocks)+1} ===\n{snippet}")
    if not blocks:
        _FEW_SHOT_CACHE = ""
        return ""

    _FEW_SHOT_CACHE = (
        "FULL CHANNEL SCRIPTS BELOW — study the COMPLETE arc: hook → setup → "
        "complications → climax → resolution → outro credit. Mirror the voice, "
        "sentence-length variance, pronoun-after-intro pattern, "
        "\"stating X\" attribution, and the \"The comic is X\" outro.\n\n"
        + "\n\n".join(blocks)
        + "\n\nApply this voice to OUR comic — don't reuse any of THEIR "
          "specific story content."
    )
    return _FEW_SHOT_CACHE
```

`_load_few_shot_examples` name retained as alias for backward compat:

```python
def _load_few_shot_examples(n: int = 2) -> str:
    return _load_full_script_examples(n=n)
```

Cache key remains `_FEW_SHOT_CACHE`.

### C-5: Outro scene support in validator

The outro "The comic is X" scene is SHORT (5-8w), has **connective=null**, and
has no beat_id mapping. Current validator would reject:
- Length floor (8-14w) — outro is 5-8w
- "scene 1 must have connective=null" but outros aren't scene 1
- beat_id validation already soft per v2

Update `_validate()`:

```python
# Detect outro: very short scene with no/null connective, last in list.
is_outro = (
    i == len(scenes)
    and (s.get("connective") in (None, "", "null"))
    and wc <= 12
    and "comic is" in text.lower()
)
if is_outro:
    continue  # skip all per-scene validation for the outro line
```

Place this BEFORE the connective + length checks in the per-scene loop.

## Data flow

Unchanged input contracts. Internal change only in which LLM chain phase C
uses. Output `narration.json` schema unchanged — outro is just another scene
entry with `connective=null` and `text="The comic is X"`.

## Error handling

- `CREATIVE_LLM_MODELS` chain failure: falls back to Gemini Flash Lite. If that
  also fails (rate limit, etc.), `call_with_chain` raises RuntimeError per
  existing behavior. No new exception paths.
- Outro scene missing: existing `_validate()` won't enforce — it's a prompt-only
  expectation. If LLM omits, soft warning logged.
- Sentence-variance criteria: soft post-check only, never blocks ship.

## Testing approach

Re-run `thing_bond_with_venom` (preprocessed cache reused — free):

```
rm projects/thing_bond_with_venom/{narration,word_timestamps,scene_timings,caption_chunks}.json
rm projects/thing_bond_with_venom/{audio,audio_mixed,video_silent}.{wav,mp4}
rm -rf projects/thing_bond_with_venom/{shots,captions.ass,final.mp4}

python -m stages.stage_3 --project thing_bond_with_venom --mode panel_walk
python -m stages.stage_4 --project thing_bond_with_venom
python -m stages.stage_5 --project thing_bond_with_venom --force
```

Run analyzers:
- `view_video.py` → confirm no font glitches, no panel duplicates
- `gap_report.py` → channel metric match
- Inspect narration.json manually for the 10 voice criteria

## Success criteria — target 8/10 to ship

| # | Criterion | Now v3 | Target v4 |
|---|---|---|---|
| 1 | ≥2 punch sentences (5-12 words) | 0 | **≥2** |
| 2 | ≥2 long sentences (23-30 words) | 0 | **≥2** |
| 3 | Sentence-length stdev ≥ 4 words | ~2 | **≥4** |
| 4 | ≥3 pronoun usages (he/him/she/her) after intro | low | **≥3** |
| 5 | "stating X" attribution used ≥1 time | 0 | **≥1** |
| 6 | Outro "The comic is X" sentence present | ✗ | **✓** |
| 7 | Median scene length 14-22w (carry-over from v3) | 20 ✓ | maintain |
| 8 | No abstract closing phrases (no "monstrosity", "darkness within", "inherent X") | ✗ | concrete only |
| 9 | Max page-gap ≤ 5 (outline bridge from v3) | 9 ✗ | **≤5** |
| 10 | VLM frame audit: 0 panel duplicates (v3 fix held) | 12/12 ✓ | maintain |

If <8 hit: diagnose. If model itself can't hit voice variance even with Sonnet,
the issue is the prompt template not the model — investigate prompt clarity.

## Out of scope

- Background music (separate concern)
- Cartesia voice/emotion change (locked in earlier commits)
- VLM upgrade for Stage 2 panel description (separate)
- Schema refactor (scene-range mapping) — defer

## Implementation notes

- `_DEFAULT_CREATIVE_CHAIN` env var lets users swap to GPT-4o or Haiku 4.5 without
  code change. Keep Sonnet as default.
- The outro scene's `page_ref` should equal the highest story page so Stage 5
  picks a final-page panel (the climax/resolution image). Set in WRITE_SYSTEM:
  "outro scene's page_ref MUST match the LAST narrative scene's page_ref."
- Outro scene's `panel_ref` should still be a valid panel index from that page.
- Caption chunk for the outro will be 1-2 chunks naturally given its short
  length — no special handling needed in Stage 5.
- Token cost of full-script few-shot: ~1000 input tokens × 2 examples = 2000.
  At Sonnet $3/M input that's $0.006/call — negligible.
