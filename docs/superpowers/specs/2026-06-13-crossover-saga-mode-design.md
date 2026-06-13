# Crossover-Saga mode — narrate a multi-issue storyline (≤5 issues) as one Short

**Date:** 2026-06-13 · **Branch:** `feat/art-v2` · **Status:** design approved by user (spec pending review)

## Problem

The pipeline ingests exactly ONE comic at a time (`narrate_1_comic`): Stage 1
downloads one issue + fetches one wiki context, Stages 2–5 turn it into a ~60–75s
Short. Many of the best stories are SAGAS that span several issues of one series.
We want to feed a whole saga (up to 5 sequential issues of the same series),
download each issue, and weave them into ONE continuous ~60–90s crossover Short —
without disturbing the existing single-comic flow.

## User decisions (this brainstorm)

- **Product = Crossover / unified narrative.** Weave the issues into ONE connected
  story, not a Top-5 anthology / ranking / theme essay.
- **Relationship = N sequential issues of the SAME series/saga.** Already one
  continuous canon story; stitch in issue order.
- **Length = Short ~60–90s** that compresses the whole saga (key turning points,
  ~1–2 beats per issue).
- **Count = up to 5, and MAY be fewer** (2 ≤ N ≤ 5). N is `min(chapters_available,
  max_issues)`.
- **Ingest = ONE batcave series URL → auto-list chapters → download the first N.**
- **Per-issue context is REQUIRED:** Stage 1 fetches a SEPARATE wiki/synopsis
  context for EACH issue, then merges them in arc order (not one storyline article).
- **N = 1 ⇒ behave EXACTLY like today** (`narrate_1_comic`). The multi-issue/arc
  logic only activates when N ≥ 2.
- **Architecture = "Merge ingest + arc-aware Stage 3" (Approach 3).** Download all
  issues into ONE project, reuse Stages 2/4/5 unchanged, add arc awareness only in
  Stage 3 (even beat allocation across issues + per-issue wiki cross-check).
- **Isolation:** a new `PipelineMode`; `narrate_1_comic` behaviour is byte-for-byte
  unchanged.

## Goal

A new opt-in mode that takes one series URL, downloads ≤5 sequential issues with a
per-issue canonical context each, and produces one canon-accurate ~60–90s crossover
Short — while N=1 and the existing single-comic mode are untouched.

## Non-goals

- Anthology / ranking / theme modes (rejected this brainstorm).
- Cross-series or unrelated-comic crossovers (only same-series sagas).
- Longform (3–8 min) output — Short only for now.
- Changing Stage 2/4/5 logic, the `narrate_1_comic` path, or VLM routing.

## Architecture (Approach 3)

New code lives in TWO places only:
1. **Stage 1** — multi-issue ingest + per-issue context fetch + merged arc context.
2. **Stage 3** — an arc-aware branch (beat allocation across issues + per-issue wiki
   cross-check).

Stages 2, 4, 5 are reused unchanged — they already iterate over every page /
scene / beat in a project regardless of how many issues produced them.

### Mode & isolation

- Add a new value to `config.py:PipelineMode` (reuse the empty `STORY_ARC` slot or
  add `CROSSOVER_SAGA = "crossover_saga"` — decide at implementation; prefer a clear
  new name and leave `STORY_ARC` alone).
- Stage 1 CLI gains: `--mode crossover_saga`, `--series-url <batcave series page>`,
  `--max-issues 5` (default 5).
- `state.json.pipeline_mode` records the mode (already threaded through Stage 1).
- **N=1 short-circuit:** if the listed series has only one chapter, or `max_issues=1`,
  Stage 1 runs the EXISTING single-comic path and writes today's `comic_context.json`
  shape (no `issues[]`, no `is_arc`). Downstream is identical to `narrate_1_comic`.

### Stage 1 — ingest + per-issue context

1. Fetch the series page (batcave) and enumerate its chapters (each chapter exposes a
   reader URL — same structure already stored in `raw_comic/manifest.json`, which is a
   LIST of `{chapter_index, label, reader_url, pages[]}`).
2. Take the first `N = min(len(chapters), max_issues)` chapters, in order.
3. For EACH issue k (1..N):
   - **Context:** fetch its own canonical synopsis (reuse `fetch_fandom` →
     `plot_text`; fall back to the SDK web grounding tool when Fandom misses, exactly
     as the single-comic path does today). Produces per-issue
     `{label, plot_summary, story_arc, characters}`.
   - **Pages:** download that issue's pages into the shared `raw_comic/` with an
     issue prefix, e.g. `i{k}_page_01.jpg`, so page provenance is preserved and Stage 2
     keys stay unique across issues.
4. Write ONE merged `comic_context.json`:

```json
{
  "title": "<saga / series title>",
  "is_arc": true,
  "issue_count": N,
  "issues": [
    {"label": "#1", "plot_summary": "...", "story_arc": "...",
     "characters": ["..."], "page_prefix": "i1"},
    "... one per issue, in arc order ..."
  ],
  "plot_summary": "<arc plot = the N per-issue synopses concatenated in order>",
  "batcave_url": "<series url>",
  "summary": { "story_arc": "<combined>", "characters": ["<union>"] }
}
```

- The top-level `plot_summary` / `summary` keep the SAME keys the rest of the pipeline
  already reads, so non-arc-aware code needs no changes.
- **N=1:** omit `is_arc` / `issues[]` / `issue_count`; emit today's exact single-comic
  schema.
- `raw_comic/manifest.json` stays the existing multi-chapter LIST (one entry per issue,
  pages prefixed) — no schema change.

### Stage 2 — reused unchanged

- VLM panel description + Magi clustering + cluster naming run over EVERY page in
  `raw_comic/` (all issues). No code change. Cluster names unify a character that
  recurs across issues (e.g. the protagonist) — desirable for a saga.
- Cost note: N issues × ~22 pages ≈ up to ~110 VLM pages → slower / more tokens than a
  single comic. `--max-issues` is the throttle. A per-issue page cap is a possible
  future optimization (out of scope now; flagged).

### Stage 3 — arc-aware branch (the main new logic)

When `comic_context.is_arc` is true (N ≥ 2):

- **Beat allocation across issues.** `outline_beats` covers the WHOLE arc but
  distributes the global beat budget (~18–22 beats, current tuning) across the N
  issues so each issue gets a fair share (≈ `round(total/N)`, with a floor of 1–2
  beats per issue). Each beat's `page_refs` must point into THAT issue's page range
  (the `i{k}_` prefix), preventing all beats collapsing onto the first issue and
  guaranteeing every issue is represented (the "compress the whole saga" goal).
- **Per-issue wiki cross-check (Phase E).** Each beat is validated against the
  CORRESPONDING issue's per-issue context (`issues[k]`), not one merged blob — so canon
  accuracy is checked at issue granularity (honours the per-issue-context requirement).
- **Reused as-is:** intro archetype rotation + anti-repeat guard, 50/50 thematic/factual
  outro, emotion mapping (Stage 4), deterministic beat→scene anchoring, best-draft
  selection. The arc just feeds more beats spanning more pages.
- The total still targets a 60–90s Short (word/duration bands unchanged).

When `is_arc` is false (N=1 or single-comic mode): Stage 3 runs the existing path
verbatim.

### Stage 4 & 5 — reused unchanged

- Stage 4 (Cartesia TTS + emotion SSML + caption chunks): reads `narration.json`,
  agnostic to issue count. No change.
- Stage 5 (panel selection + Ken Burns + lower-third captions): beats already carry
  `page_ref` into the merged page set (`i{k}_page_XX`); the panel picker resolves
  panels across all issues' `preprocessed/` pages with no change.

## Data flow

```
series URL
  └─ Stage 1 (crossover_saga): list chapters → first N
        ├─ per issue: fetch_fandom/SDK context  → issues[k]
        └─ per issue: download pages            → raw_comic/i{k}_page_*.jpg
     → comic_context.json (is_arc, issues[], merged plot_summary) + manifest (N chapters)
  └─ Stage 2: VLM all pages → preprocessed/*  + cluster_to_name.json     [unchanged]
  └─ Stage 3 (is_arc branch): arc outline (beats spread across issues)
        → per-issue wiki cross-check → narration.json                    [new branch]
  └─ Stage 4: TTS + emotion + captions                                   [unchanged]
  └─ Stage 5: panels across all issues + render → final.mp4              [unchanged]
```

## Edge cases

- **Series page lists fewer than `max_issues` chapters** → N = available; works.
- **Exactly 1 chapter** → N=1 short-circuit → today's behaviour.
- **One issue's Fandom context missing** → that issue uses the SDK web fallback (same
  as single-comic today); if still empty, its per-issue cross-check is skipped (degrade,
  don't block) — flagged in logs.
- **Cloudflare 403 on batcave series listing** → same known pitfall as single-comic;
  surfaced, not silently mis-handled.
- **Uneven issue page counts** → beat allocation uses each issue's own page range, so a
  short issue still gets its floor of beats.
- **Total pages very large (5 long issues)** → slower Stage 2; `--max-issues` is the
  guard. No correctness impact.

## Testing

- Unit: Stage 1 chapter enumeration (mock series page → N reader URLs); per-issue
  context merge (N=3 → `is_arc`, 3 `issues[]`, concatenated `plot_summary`); **N=1 path
  emits today's exact single-comic schema** (regression guard).
- Unit: Stage 3 beat allocation distributes beats across issues (each issue ≥ floor);
  per-issue cross-check selects the right `issues[k]`.
- E2E (manual, from scratch): pick a real ≤5-issue saga on batcave, run Stages 1→5,
  verify each issue appears in the narration, wiki cross-check 0 mismatch, benchmark
  qualifies, and the Short reads as one continuous story.
- Regression: run an existing single-comic project through `narrate_1_comic` AND
  through the new mode with N=1 → identical narration/output.

## Open implementation choices (decide in plan)

- New enum name `CROSSOVER_SAGA` vs reusing `STORY_ARC`.
- Exact beat-allocation formula (even split vs weighted by issue page count).
- Whether to add a per-issue page cap now or defer (currently deferred).
