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

---

# ADDENDUM 2026-07-05 — REVIEW GATE (blocks TTS + render until Master approves)

## Why
Q&A's #1 risk is fact→WRONG panel/issue (above). And the recurring stale-audio bug (edit
narration.json, forget `--force`, ship old audio under new captions). The review gate makes
the pipeline STOP after narration and force a human check of the narration text + the panel
chosen for each beat before any TTS/render spend. General mechanism — it protects narrate-mode
comics too, and is keyed on `comic_context.plot_source == "answer_research"` (no mode-name hacks).

## Flow
1. Run Stage 1→3 (or `answer_pipeline` up through `narrate`). narration.json exists.
2. `python -m stages.review_gate --project X --build-candidates [--k 10]` → writes
   `review/candidates.json` + cropped `review/thumbs/pXXX_Y.jpg`. Reuses the EXISTING Stage-5
   matcher (`shots._match_panels` in candidates-only mode: same content+page-prior scores, no
   VLM rerank, no assignment). **Needs the LM Studio Qwen embed backend up**, same as Stage 5.
3. Master opens the review UI (separate `ui/`), reviews narration text + picks a panel per beat,
   approves. The UI writes `review/locks.json`.
4. Stage 4 (`synthesize_project`) and Stage 5 (`assemble_project`) call
   `review_gate.ensure_reviewed(project, skip_review)` first → `SystemExit` with instructions
   until `approved` is true AND the approval's `narration_sha1` still matches narration.json
   (an edit after approval re-blocks). `--skip-review` bypasses for a normal comic; it is
   IGNORED for answer_research (Q&A) projects.
5. On render, `build_shots` calls `_apply_review_locks`: a lock for a scene overrides its
   `(page_ref, panel_ref)` so the EXISTING `PANEL_ANCHOR_BIND` path binds Master's pick.
   (Caveat: a lock on a DESC_VERIFY-untrusted page won't hard-bind — `ANCHOR_TRUST` still
   routes it through content-match + the page-prior keeps it on the locked page.)

Stage 4 also AUTO-FORCES when narration.json changed since the cached audio was TTS'd
(compares the current scene hash against the `narration.tts.sha256` sidecar) — kills the
stale-audio bug without needing `--force`. `REVIEW_GATE=0` disables the gate entirely.

## Contract A — `review/locks.json` (the UI writes; the gate + Stage 5 read)
```json
{"approved": false, "approved_at": null, "narration_sha1": null,
 "locks": {"<scene_id>": {"page": 12, "panel": 3, "source": "batcave"}}}
```
- `approved` / `approved_at` — set by the UI on approval; `narration_sha1` = sha1 of
  narration.json bytes at approval time (staleness pin).
- `locks` — keyed by string `scene_id`; `page`/`panel` are a pool key (global page number,
  0-based panel index) straight from Contract B; `source` is provenance ("batcave").

## Contract B — `review/candidates.json` (build_candidates writes; the UI reads)
```json
{"generated_at": "<iso>",
 "beats": [{"scene_id": 2, "narration_text": "...", "page_ref": 10, "panel_ref": 0,
            "source": {"title": "...", "issue": "...", "url": "...", "research_urls": ["..."]},
            "candidates": [{"page": 10, "panel": 0, "score": 9.5,
                            "thumb": "review/thumbs/p010_0.jpg",
                            "desc": "...", "dialog": "BOOM"}]}]}
```
- One beat per STORY scene (intro/outro excluded — their panels are deterministic cold-open /
  loop-close, not a matching decision). Candidates are the matcher's own top-`k` ranked panels.
- `source` cites the beat's comic: per answer-item for Q&A (mapped by the `chNN_` page prefix),
  else the single source comic from comic_context.

## FUTURE — web-image fallback (when NO downloaded panel scores above the floor)
Not built. Spec for when a beat's fact finds no batcave panel: fetch candidate web images →
(1) **phash dedupe** to drop near-identical results; (2) reject anything with shorter side <700px
(upscaling smears under render); (3) **VLM desc** each survivor + **SigLIP text↔image** score
against the narration line (same joint space as the panel image-match blend); (4) run **Magi ONLY
when the image is itself a comic page** (panel/bubble detection is meaningless on a photo/poster).
Surface web-sourced candidates in Contract B with a distinct `source` so Master sees the
provenance before locking. Copyright: keep panels-only preference — web images are a last resort.
