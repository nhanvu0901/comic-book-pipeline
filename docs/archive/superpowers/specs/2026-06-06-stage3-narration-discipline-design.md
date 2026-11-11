# Stage 3 Narration Discipline — Design

Date: 2026-06-06
Status: Approved (brainstorming) — pending spec review → writing-plans

## Problem

Stage 3 narration (`stages/stage_3/write_script.py`) produces text that, on
real runs (dark_venom_things, dark_loki), shows three accuracy/quality defects
the existing validators do **not** catch:

1. **Wrong-order panels (visual mismatch).** The writer emits scenes whose
   `page_ref` is **non-monotonic** (real case, venom: `sc5 page_ref=12` then
   `sc6 page_ref=11`). Stage 5 selection is forward-only, so when narration
   jumps back a page, the line is paired with a later (wrong) panel. Concretely
   at 0:34 the narration says "Ben struck Reed" while the screen shows the
   Lizard, because the Reed beat (pg11) was ordered *after* the Lizard beat
   (pg12) and forward-only could not return to pg11.

2. **Embellishment / untrue drama.** The writer adds dramatic claims not present
   in the panel descriptions or the wiki plot — e.g. "rage consumed him", "the
   city watched in fear", "tongue extended mocking them". These are factually
   unsupported and also inflate length.

3. **Repetition.** The same event/noun is narrated twice — "severed arm" in two
   consecutive scenes (sc10 + sc11); "struck Reed / stay out of his way" in two
   non-consecutive scenes (sc4 + sc6).

Root structural cause: Stage 3 generates free prose first and then runs **many
overlapping validators** (`_validate`, `_detect_hallucinations`,
`_fidelity_check`, `_wiki_cross_check`, `_detect_redundant_scenes`, length
checks, best-draft key, retry loop). They overlap (fidelity vs wiki both
fact-check; redundancy vs hallucination) yet still let the three defects
through.

## Decisions (from brainstorming)

- **Length policy: flexible.** Keep ALL canon beats; only cut drama/repetition.
  A denser comic produces a longer video (e.g. ~90s). The benchmark `duration`
  band is relaxed to scale with story length rather than dropping content.
- **Architecture: patch the current flow** (no full WHAT/HOW rewrite) — BUT the
  patch must **reduce** overlap: each defect is fixed in exactly one place, and
  overlapping validators are merged/replaced rather than added to. Net rule
  count goes down, not up to "8-9 fighting rules".

## Design

### Part 1 — Deterministic beat ordering (fixes defect 1)

Fix the order **once**, deterministically, right after `outline_beats`
(`stages/stage_3/write_script.py`).

- After beats are produced, **stable-sort beats by their primary `page_ref`**
  (lowest page in `page_refs`), preserving wiki/dramatic order within the same
  page. The comic is read page-by-page, so page order == the correct on-screen
  reading order for a recap.
- **Merge** adjacent beats that resolve to the same page / same event so two
  Reed-confrontation beats (pg9, pg11) stay together *before* the Lizard beat
  (pg12), instead of interleaving.
- Result: scene `page_ref` is **monotonic non-decreasing by construction**.
- **Replaces** the soft prompt rule A7 ("page_ref must be monotonic") and the
  soft beat-gap hint with a deterministic guarantee. Add a cheap assertion in
  `_validate` that page_ref is non-decreasing (defense-in-depth; should never
  fire after the sort).

This is the single largest fix: with monotonic page_ref, Stage 5's forward-only
selection always has the correct panel reachable (no more Reed-line-over-Lizard).

### Part 2 — Unified grounding check (fixes defect 2; removes overlap)

Merge `_fidelity_check` (soft, panel-grounded) and `_wiki_cross_check`
(critical, plot-grounded) into **one** "grounding check" LLM pass:

- One LLM call per validation pass receives the narration + the cited panel
  data + the wiki plot.
- It flags a scene when EITHER:
  (a) the scene **contradicts** the wiki plot (wrong character/action/order), OR
  (b) the scene contains a **fact or emotion not supported** by the panel
      description or the wiki — i.e. **embellishment** ("rage consumed him" when
      no panel/wiki states it).
- **All findings are critical** → drive the retry loop.
- The retry prompt instruction: rewrite the flagged scene to state ONLY what the
  panel/wiki supports; remove invented drama, kept the beat's real event.

Net effect: two overlapping LLM checks become one; embellishment (previously
uncaught by the soft fidelity check) is now a critical finding.

### Part 3 — All-pairs noun/event de-duplication (fixes defect 3)

Strengthen `_detect_redundant_scenes`:

- Check **all scene pairs** (not just consecutive) — so sc4/sc6 ("struck Reed",
  separated by sc5) are caught, not only sc10/sc11.
- Detect repeated **key nouns/events** (e.g. "severed arm", proper-noun + verb
  pairs), not just shared 5-char stems.
- A confirmed repeat is **critical** → retry rewrites the LATER scene to advance
  with fresh content (the existing redundancy-retry rule already does this).
- Part 1's beat-merge prevents most repeats at the source; this check is the net.

### Part 4 — Flexible length / benchmark duration

- Remove the hard word ceiling behavior that forced over-trimming. Keep a soft
  target but allow longer narration when all content is canon (drama/repeats
  already removed by Parts 2-3).
- Relax the benchmark `duration` qualifying band so a legitimately dense comic
  (more canon beats) is not failed for being longer. Exact band: scale with
  scene/beat count rather than a flat 54-72s. (Lives in the gitignored
  `research/reports/_BENCHMARK_thresholds.json`; force-tracked.)

## Overlap ledger (the "no-overlap" guarantee)

| Change | Effect on rule set |
| --- | --- |
| Part 1 beat-sort (deterministic) | REPLACES soft rule A7 + beat-gap hint |
| Part 2 grounding check | MERGES `_fidelity_check` + `_wiki_cross_check` → 1 |
| Part 3 dedup all-pairs | STRENGTHENS `_detect_redundant_scenes` (no new pass) |
| Part 4 length | RELAXES existing length/duration gates |

Kept unchanged: `_validate` (schema/page-bounds), `_detect_hallucinations`
(proper-noun whitelist — distinct purpose), best-draft key, retry loop. Net
LLM-validator count goes from 2 (fidelity+wiki) to 1; one deterministic
ordering step is added; no rule is layered on top of an overlapping one.

## Out of scope

- Full WHAT/HOW (beat-sheet → render) rewrite — explicitly deferred.
- Stage 5 selection (already redesigned: DTW + mxbai + bookend + scene-dominant).
- Wiki/canon source accuracy (Stage 1) and VLM description errors (Stage 2).

## Success criteria

- Re-run dark_venom_things + dark_loki Stage 3→5:
  - No non-monotonic `page_ref` (assertion never fires); 0:34-type
    narration↔panel mismatches gone.
  - No repeated key noun/event across scenes (severed-arm-twice gone).
  - Grounding check flags + removes invented drama; spot-check narration reads
    factual, not embellished.
  - All canon beats retained; duration may exceed 72s for dense comics and still
    qualify under the relaxed band.
