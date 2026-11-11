# Art pipeline v3 — Long-form mode (8-12 min, 16:9, chaptered)

**Date:** 2026-06-12 · **Branch:** `feat/art-v2` · **Status:** design approved by user (spec pending user review)

## Problem

The art track produces 60-75s 9:16 Shorts. The user is pivoting: **no more
Shorts — high-quality 8+ minute videos first.** Research (2026-06-12, two
reports in `research/reports/`) shows the winning genre formula is a
story-led deep dive opened by a mystery/scandal, told in chapters, at
~140 WPM, 16:9, with ~70% time on the painting and ~30% on related imagery.
A single LLM call cannot write 1,600-1,900 grounded words at quality (proven
by current 2-attempt failures at far smaller sizes), and a single Cartesia
request / single SDK hunt session cannot serve a 10-minute video (proven by
the max_turns incident).

## User decisions (this brainstorm)

- **Both modes from day one:** `painting_story` (1 artwork deep dive) and
  `artist_journey` (5-8 artworks, one artist) — shared chapter engine.
- **16:9 landscape 1920x1080** for long-form output.
- **Target length 8-12 minutes** (~1,600-1,700 words, 4-5 chapters,
  ~60-110 scenes; raised from 1,200-1,700 after e2e round 4 measured
  0.36 s/word — 1,285 words rendered only 7:43, so the floor must put the
  85% worst case above 1,360 words ≈ 8:10).
- **BGM: user drops a music file into the project folder per video**
  (no auto music library). Missing file → loud warning, render continues.
- **Architecture A:** chaptered writer on the existing scene engine
  (no separate long-form package; no outline-less looping).
- Karaoke burn-in captions are DROPPED for long-form; export
  `subtitles.srt` instead (user approved explicitly).
- Comic pipeline code (`stages/`, `ui/`, root `config.py`) stays
  **READ-ONLY** — import + runtime attribute overrides in try/finally only.
- Shorts path keeps working unchanged (`--length short` default stays).

## Research playbook → enforced rules

From `research/reports/2026-06-12-longform-narration-channels.md` (16 rules)
and `...-longform-topic-patterns.md` (topic archetypes). Rules the pipeline
ENFORCES (validator) vs PROMPTS (soft guidance):

| # | Rule | Enforced how |
|---|---|---|
| 1 | Hook 0-15s names a concrete mystery/contradiction of THIS work | validator: existing `_hook_is_concrete` + hook ≤ 30 words, chapter 1 role `cold_open` |
| 2 | Causal order: open with the mystery, backfill later — never birth-to-death chronology | outline template roles (cold_open → backfill → evidence → twist → resolution) |
| 3 | Chapters every 2-3 min, YouTube chapter markers | outline 4-5 chapters; `youtube_chapters.txt` from real TTS timestamps |
| 4 | Mid-video re-hook ("but here's where it gets stranger…") | validator: chapters 2 and 3 must END with a forward-reference scene flagged `is_rehook` |
| 5 | Visual change every 3-5s; pattern interrupt every 30-45s | existing scene engine (8-22 words/scene ≈ 3-7s) + split rule + variety rules |
| 6 | ~140 WPM, intimate second-person, no jargon | prompt; benchmark word/scene counts |
| 7 | ~70% painting / ~30% related | prompt soft guidance (per user's earlier decision: never a hard ratio) |
| 8 | "Therefore & but" causal chaining between beats | prompt + chapter writer receives last 2 lines of previous chapter |
| 9 | Ending = thematic observation / open question, no "subscribe" CTA | existing outro logic, thematic variant forced for long-form resolution chapter |
| 10 | Micro-pauses between blocks | 0.8-1.2s silence inserted between chapter WAVs |
| NOT mimicked | personal humor, fake anecdotes, human VO delivery | excluded by design (research "KHÔNG MIMIC ĐƯỢC" list) |

Topic guidance (scout-level, not validator): mystery/scandal (9.5/10) >
hidden details/x-ray (8.8) > tragic biography (8.2); avoid oversaturated
icons (Mona Lisa tier). Niche gap: forgery/authentication/heist drama.

## Architecture

```
A1 select → A2 fetch (Met, 1-N ids) → A3 regions (per artwork)   [unchanged]
→ A4a grounding (Met + Wikipedia + SDK-web, per artwork)          [unchanged]
→ A4o OUTLINE  (new: art_pipeline/outline.py)                     [NEW]
→ A4b chapter writer (narrate.py extended)                        [extended]
→ A4.5 hunt — one SDK session PER CHAPTER (hunt.py reused)        [extended]
→ A5 TTS per chapter + stitch (new: art_pipeline/longform_tts.py) [NEW]
→ A6 assemble 16:9, srt, chapters file (assemble.py extended)     [extended]
```

`--length short` skips A4o and the stitcher and behaves exactly as today.

## A4o — Outline (`art_pipeline/outline.py`, NEW)

One LLM call (`call_with_chain`, CREATIVE_LLM_MODELS) takes the full grounded
context (all artworks for journey mode) → `outline.json`:

```json
{
  "mode": "painting_story | artist_journey",
  "through_line": "one-sentence driving question",
  "chapters": [
    {"chapter_id": 1, "title": "…", "role": "cold_open",
     "facts": ["verbatim grounded snippets…"], "target_words": 280,
     "artwork_ids": [437654]}
  ]
}
```

Roles, in order: `cold_open` (the mystery/contradiction), `backfill`
(only context that serves the question), `evidence` (close reading,
technique, x-ray/hidden details), `twist` (reversal), `resolution`
(answer + thematic close). 4 chapters = merge backfill+evidence.

`artist_journey` differences only: `through_line` is mandatory and must be a
question about the career (NOT "the life of X"); each chapter's
`artwork_ids` picks which painting(s) anchor it; every fetched artwork is
used by ≥1 chapter.

**Validator (retry 3, errors fed back):** 4-5 chapters; roles follow the
template order; every `facts` entry is a (≥80%-overlap) substring of the
grounded context — anti-hallucination at the outline level; no fact assigned
twice; total target_words within 1,600-1,900; journey: through_line is a
question + artwork coverage complete.

## A4b — Chapter writer (extend `art_pipeline/narrate.py`)

One LLM call **per chapter**: long-form system prompt + that chapter's facts
+ role + target_words + the final 2 scene texts of the previous chapter
(for therefore/but continuity) + the artwork's region list.

- 14-22 scenes/chapter; MOST scenes 14-22 words (short ones are rare
  accents) and the prompt spells out the per-chapter word-budget arithmetic
  — chapter ceiling `ART_LF_CHAPTER_WORDS_MAX` = 420 (22 × ~19, a ceiling
  the writer can actually hit; e2e round 3: "short scenes" framing yielded
  ~190-word chapters vs 350 targets). Chapter actual-vs-target band
  `ART_LF_CHAPTER_WORDS_BAND` = 75-150% (sanity; the 8-min guarantee is the 1,360-word total floor) (e2e round 4: the 60% floor let
  middle chapters land at 0.62-0.78 of target → 7:43 video). Same visual
  declaration schema (`painting_region`/`painting_full`/`related`) and the
  same `parse_visual` / `assign_motions` machinery from `visual_plan.py`.
  Long scenes don't slow the cut rate: assemble already splits scenes ≥ 5s
  into two shots.
- Scene dict gains `"chapter_id"`; chapters 2 and 3 last scene gains
  `"is_rehook": true` and must read as a forward reference (validator:
  flag present + scene matches a forward-hook heuristic — future-tense /
  "but…" opener lexicon, mirroring `_classify_hook` style).
- Variety rules, **per-chapter scope**: no two consecutive scenes share a
  target; `painting_full` ≤1 mid-chapter; the same region/full target may
  not recur within `ART_LF_REGION_REUSE_WINDOW` (6) consecutive scenes.
  **Global scope:** related subjects pairwise distinct across the whole
  video; the reuse window is re-checked over the FULL ordered scene list
  (catches repeats straddling chapter boundaries); intro `painting_full`
  only in chapter 1 scene 1, outro full only in final chapter.
  (Window replaced the original once-per-chapter + adjacent-chapter region
  bans: e2e pilot 2026-06-12 showed a 6-region artwork mathematically cannot
  satisfy them at 14-22 scenes/chapter — chapter 1 failed 3/3 attempts.)
  Region spacing is then ENFORCED by a deterministic LRU repair pass
  (`_repair_region_spacing`) that re-aims near-miss `painting_region` repeats
  before validation — the writer owns kind/subject/text, exact region
  spacing is mechanical (same lesson as comic deterministic beat anchoring;
  e2e round 2 same day: window-6 on exactly 6 regions has no combinatorial
  slack, LLM retries kept failing). Effective window per page =
  min(window, n_panels); validators take `panels_by_page` to match.
- Hook gate (chapter 1 scene 1) reuses `_hook_is_concrete`; resolution
  chapter ends thematic (no CTA wording — validator lexicon check).
- Retry 3 per chapter with specific validator messages.
- Outputs: `narration.json` (comic Stage 3 schema + `chapter_id`/`is_rehook`
  extra keys — Stage 4 ignores unknown keys), `visual_plan.json` (existing
  format), `chapters.json` ({chapter_id, title, scene_ids, start: null}).

## A4.5 — Hunt per chapter (reuse `art_pipeline/hunt.py`)

- Group `related` declarations by `chapter_id`; **one `sdk_complete_web`
  session per chapter** (existing `max_turns = max(12, 4*n+4)` per session).
- Global subject dedup BEFORE hunting (same normalized subject in two
  chapters → second occurrence is re-aimed by the writer validator, so this
  is a safety net: reuse the already-downloaded image, don't re-search).
- Expected volume: 15-25 web images/video. Everything else (download
  hygiene, 429 retry, wikimedia UA, 600px gate, alt_image_url, fallback
  region → painting_full, hunt_manifest force-restore, credits) unchanged.

## A5 — TTS stitcher (`art_pipeline/longform_tts.py`, NEW — comic Stage 4 untouched)

- Call comic Stage 4 once per chapter on a temp per-chapter narration file
  (its scenes only), collecting per-chapter `audio.wav`, `scene_timings`,
  word timestamps, caption chunks.
- Stitch: concat WAVs with 0.8-1.2s silence between chapters (ffmpeg);
  add cumulative offsets to every timing/word; write unified `audio.wav`,
  `scene_timings.json`, `word_timestamps`; fill real `start` into
  `chapters.json`.
- Unit-tested invariant: for every chapter boundary,
  `offset(next) == offset(prev) + wav_duration(prev) + gap` exactly; total
  scene_timings count == total scenes.

## A6 — Assemble long-form (extend `art_pipeline/assemble.py` + `video.py`)

- **16:9 runtime override** (try/finally, pattern as MIRROR_PANELS):
  `shots.OUTPUT_W=1920; shots.OUTPUT_H=1080; shots.TARGET_ASPECT=1920/1080`
  (TARGET_ASPECT is computed at import time — must be set explicitly).
- **No burned-in karaoke captions** for long-form (genre-wrong + ASS header
  hardcodes 1080x1920). Instead write `subtitles.srt` from stitched word
  timestamps (chunking ~7 words, plain text).
- **BGM:** look for `<project>/bgm.*` (mp3/m4a/wav/ogg, first match) and pass
  as `bg_music_path` to the existing `_resolve_bgm`; if absent log
  `[assemble] WARNING: long-form without BGM — drop bgm.mp3 into the project`
  and continue. Existing duck/loudnorm mix unchanged.
- `youtube_chapters.txt` (`MM:SS Title` lines, chapter 1 at 00:00) and
  append the same block + "Subtitles available (CC)" into
  `youtube_description.txt`.
- Motion/variety mechanics unchanged: zoom only on `painting_region`,
  related = pan drift, ≥5s scenes split, no static >4s, anti-repeat pass,
  `_expand_extreme_bbox` aspect guard retuned for landscape frame
  (bounds become [1/2.5, 2.5] relative to 16:9 — implementation computes
  from OUTPUT aspect rather than hardcoding portrait numbers).
- `_variety_log.csv` records `length=longform`, mode, chapter count.

## Scout, benchmark, CLI/UI

- **art-scout** (`.claude/agents/art-scout.md`): add story-strength rubric —
  candidate must name its mystery/scandal/x-ray angle; new
  `longform_angle` column in `art_candidates.csv` (existing rows stay valid;
  column appended).
- **Benchmark:** new long-form thresholds file (words 1,360-1,900, scenes
  60-110 — consistent with 14-22 scenes × 4-5 chapters, chapters 4-5,
  re-hook present, hook gate, WPM 130-150 measured on final audio). Shorts
  benchmark untouched.
- **CLI:** `python3 -m art_pipeline all <proj> --ids <id…> --length longform
  --mode painting_story|artist_journey` (+ per-stage commands `outline`,
  `tts` long-form aware). Default `--length short` — zero behavior change
  for existing flows.
- **UI (tab Art, A4):** dropdown Shorts/Long-form + mode; chapter-level
  progress lines in the log pane; warning surfaced when `bgm.*` missing.

## Error handling

- Outline/chapter LLM: 3-attempt retry with validator messages; chapter
  failure fails the stage with the chapter id (resume = rerun narrate; outline
  is cached on disk and not regenerated unless `--force`).
- Hunt: per-chapter best effort, never fatal (unchanged).
- Stitcher: missing chapter WAV → fail loud (no silent gap guessing).
- Assemble: `chapters.json` missing (old projects) → derive single-chapter,
  still renders; `visual_plan.json` missing → existing trivial-plan fallback.

## Testing

- Unit: outline validator (each rule), fact-substring check, chapter writer
  scope rules (per-chapter vs global), re-hook flag validation, stitcher
  offset invariant, srt formatting, chapters.txt, 16:9 override+restore,
  landscape aspect guard, BGM discovery.
- E2E pilot 1: `painting_story` 8-12 min from scratch — checklist: drift
  ≤0.3s at video end AND at every chapter boundary, zoom-on-web = 0,
  static>4s = 0, consecutive-identical = 0, chapters.txt timestamps match
  audio, srt plays in a player.
- E2E pilot 2: `artist_journey` (Seurat) after pilot 1 sign-off.

## Out of scope

- Auto BGM library / mood-matching music (user picks the file).
- Burned-in captions for long-form; any edits to `stages/stage_5/captions.py`.
- 9:16 teaser cut-down of the long-form video (possible later).
- Human voice-over, humor injection, personal anecdotes (research
  "don't mimic" list).
- Multi-language subtitles.
