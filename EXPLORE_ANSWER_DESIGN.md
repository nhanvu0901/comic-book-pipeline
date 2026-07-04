# "Explore Answer" (Q&A) Mode — Design Doc

**Status:** DESIGNED, approved-for-review 2026-07-04. Not yet built. Read this before implementing (roadmap item #3).
**Owner decisions locked in:** panels-only (NO video clips), reuse saga machinery, dark/obscure question lane.

## What it is
Input = a QUESTION ("Who has survived Ghost Rider's Penance Stare?") → a ~45-55s Short that ANSWERS it
as a countdown listicle across multiple comics, rendered by the existing pipeline.

## Core insights
1. **Grounding INVERTS vs narrate mode.** Narrate: panels exist → write text grounded in them.
   Q&A: FACTS exist (web research) → must FIND the panel for each fact. Visual sourcing is the hard problem.
2. **A Q&A project = a "saga" of N excerpts.** The multi-issue fix (commit d55c756) is the foundation:
   global page numbering + `issue_label` per source + per-issue post-processing heuristics mean N different
   comics can live in ONE project and Stages 2→5 run nearly untouched. `--saga` already accepts N reader URLs.

## Format spec (2026-07-04 research, qa-format-scout)
- **4-5 items per Short, countdown #5→#1** — best answer LAST (retention bait). Count-up underperforms.
- **7-10s per item**, total ~45-55s → needs its OWN word band ≈150-180 words at 3.4 wps (narrate band 195-245 is too long).
- **Question on screen + spoken in first 2s** (banner = the question).
- Ending: loop back to the question OR tease the next question ("Next: who beat Superman?").
- **Untapped lane = Grimframe's brand**: dark/obscure questions (D-list villain feats, dark origin variants,
  one-shot-sourced answers). Mainstream questions (lift Mjolnir / beat Superman / survive the snap) are saturated.
- Title: format-scout claims question-form + emoji wins IN THE Q&A LANE (contradicts story-recap finding where
  statements won; that data was stronger). → A/B question-title vs meme-flip register; don't assume.

## Copyright decision (2026-07-04 research, clip-rights-scout) — PANELS ONLY
- Shorts >1min with ANY active ContentID claim are AUTO-BLOCKED from distribution (July 2025 enforcement).
  Our 60-90s videos sit exactly in that regime → a single claimed clip kills reach.
- Movie/TV/anime clips ≈70% claim rate; official trailers same; game footage still ~55%.
- Static comic panels ≈95% clean, zero documented strikes on major panel channels (Comicstorian 5M+ subs) —
  ContentID fingerprints audio/video, not static art.
- **Video-clip phase is CUT.** If ever revisited: game footage only, <10s, 2-3 snippets max.

## Build plan — only 3 new pieces (everything else reused)
| Piece | What | Size |
|---|---|---|
| 1. Answer Research (Stage 1 mode `explore_answer`) | SDK web research answers the question → `answer_context.json`: ≤5 items, each {character/entity, source comic (series+issue), how/why (1-2 sentences), the drawable MOMENT, batcave reader URL} + per-item verification (reuse the verify-twist-endings cross-check pattern). Reuse `_claude_sdk` web machinery + OpenRouter fallback. | M |
| 2. Writer mode `explore_answer` (plug into existing `MODES_BY_KEY` in stages/stage_3/write_script.py) | Hook = the question (on-screen + spoken); countdown 5→1 with one scene per item; **beat = answer item** (deterministic beat anchoring reused as-is — beat carries page_ref/panel_ref into the source comic); outro = loop/tease. Own word band ~150-180 (constant, not a prompt rewrite). | M |
| 3. Stage-2 orchestration glue | Take the reader-URL list from answer_context → download as saga (`--saga` with N reader URLs already works post-d55c756); `issue_label` = source comic name. | S |
- Stage 4 (TTS): unchanged. Stage 5 (render): unchanged — the pure-vector + SigLIP matcher finds the panel
  matching each fact's text; PANEL_COS_FLOOR holds when nothing matches.

## #1 risk + mitigation
Fact → WRONG issue = wrong download = wrong video. Mitigations: (a) per-item verification mandatory in
Answer Research (multiple sources per item); (b) fail-loud when an item's fact text finds no panel above the
cosine floor (floor machinery exists); (c) Stage-1 self-ID hardening (roadmap #5) helps here too.
Note the Moon Knight precedent: SDK pulled "Strange #9" for "Moon Knight #9 Stranger" — ambiguous titles WILL
mis-resolve; verification is not optional.

## Competition gate (adapted)
The no-narration gate becomes: "has this exact QUESTION already been done well on YouTube?" — search the
question phrasing, not the comic titles.

## First test case
Master's own example: "Who has survived Ghost Rider's Penance Stare?" — run end-to-end as the pilot.

## Implementation notes for the future builder
- Delegation per CLAUDE.md: Fable orchestrates; Opus (deep-reasoner) designs the Stage-1 answer-research
  prompt + verification; Sonnet (fast-worker) does the mode plumbing/tests.
- ADDITIVE only: new mode keys, new constants; do not touch narrate-mode prompts/flow/validators.
- All the 2026-07-03/04 upgrades apply automatically (DESC_VERIFY, DIALOG_TRUTH, ANCHOR_TRUST, SigLIP
  image-match, cold-open scorer, hook rules for the intro line, meme-flip title register).
