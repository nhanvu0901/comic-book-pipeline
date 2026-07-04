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

---

# ADDENDUM 2026-07-04 — verbatim mining (qa-miner) + Master's build constraints. THIS SECTION SUPERSEDES the format spec above where they conflict.

## Research revision (verbatim yt-dlp mining, ~490 videos → ~80 comic listicles probed)
**Assumption-flipping finding:** every ≥100k comic-listicle/Q&A Short found is TEXT-ON-SCREEN + music, NO narration
(Eva Isabel 1.14M, Nerte 237k, SUPER FACTS 94k — all no auto-subs = no voice). Narrated versions (even ScreenRant /
Comicbook.com) sit at 1.8k-66k. Implications: narrated Q&A = LOW-COMPETITION lane, but the video must be consumable
MUTED and must out-depth the silent mass format.

**Format spec v2 (from the only narrated gold samples, esp. Comicbook.com Infinity Gauntlet listicle):**
- HOOK ~5-10s: restate the question as a STATEMENT + promise a shocking entry ("...including some characters you
  might be surprised by"). Do NOT start item #1 at second zero.
- **Order by SURPRISE ascending, not power ranking** — expected names first, cross-universe twist second-to-last,
  the most shocking entry LAST (Thanos → ... → Darkseid → Santa Claus). NO spoken "number five/four..." markers.
- 4-6 items × ~10-14s for our 60-76s band; per item: "[Name]. [1 sentence how/why]. [1 dry/dark remark]".
  Measured narrated pace 2.9-3.9 wps → body ~200-240 words fits the existing 3.4 wps calibration.
- **Cite the source comic ALOUD per item** ("in the 2003 crossover JLA/Avengers...") — genre convention, credibility.
- ENDING: land on the shock entry, then ONE thematic button line that can loop back into the question (replay=view).
  No website CTAs, no "so there you have it", no verbatim question repeat.
- Muted-viewing: reuse the EXISTING persistent top banner for the QUESTION (always visible) + existing burned
  captions carry the text experience. A dedicated per-item name-card overlay is OPTIONAL FUTURE work — do not
  build it in v1 (Master: don't modify current code much).
- Titles in this lane: direct-question titles are proven ("Could Galactus lift Mjolnir?" 32k on a tiny channel);
  meme-flip statements also viable. A/B later.
- Bonus title formula from a 2.5M skit: "Only ONE being can defeat X" — the "only one/only N" scarcity shape.

## Master's build constraints (2026-07-04, binding)
1. **NO UI.** The mode is driven entirely by agents: single non-interactive CLI entry (no mode-picker, no input()
   prompts, sane defaults, --flags only). Machine-parseable stdout status lines welcome.
2. **Clear, correctly-named outputs in the same projects/<slug>/ folder**, readable by Master:
   `answer_context.json` (the question, researched items with sources/whys/URLs, verification notes) alongside the
   standard artifacts (comic_context.json, narration.json, final.mp4 ...). Human-readable field names, no cryptic keys.
3. **Reuse maximum, modify existing code minimally** (small touches allowed where unavoidable, e.g. a mode key).
4. Panels-only (unchanged). Pilot: "Who has survived Ghost Rider's Penance Stare?".
