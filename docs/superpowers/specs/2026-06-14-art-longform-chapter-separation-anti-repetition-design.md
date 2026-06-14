# Art pipeline long-form — Chapter separation + anti-repetition

**Date:** 2026-06-14 · **Branch:** `feat/art-v2` · **Status:** approved by user

## Problem

User watched the full Toledo long-form video (`art_projects/toledo-longform/final.mp4`)
and reported two distinct, compounding defects:

1. **Repetition** — "some part is repeated". Confirmed from `narration.json` (98
   scenes): the same *physical description of the painting* is rewritten across
   chapters, several times near-verbatim:

   | Repeated idea | Scenes (verbatim / near-verbatim) |
   |---|---|
   | "visible brushstrokes, giving texture to both landscape and city" | **15, 26, 49** (≈identical, 3×) |
   | cathedral dominates skyline / spires reach the turbulent sky | **4, 34** (+ 46, 63, 76, 93) |
   | small bridge & buildings cluster along the riverbank | **8, 35, 53** (3×) |
   | city buildings organically grown from rocky terrain | **9, 37, 69** (3×) |
   | lush greenery + winding river adds depth | **7, 36, 73** |
   | vibrant green "almost unnatural luminosity" | **14, 43, 51** |
   | sky grows exceptionally dark near the city | **41, 57** |

2. **No section orientation** — "it is hard to know what this part is talk
   about; we need to separate it by section of chapter". Five chapters already
   exist in `chapters.json` and ship as YouTube chapter markers
   (`youtube_chapters.txt`), **but the rendered video has no in-video signal of a
   chapter boundary** — 98 scenes crossfade into one continuous flow. With no
   boundary cue, a chapter that re-describes the cathedral reads as "saying it
   again" instead of "a new section".

### Root cause (mechanism)

Long-form narration is written **one chapter at a time** (`build_chapter_scenes`
called per chapter in `write_longform_narration`). Each chapter independently
"looks at the same painting" and pads to its scene-count floor
(`ceil(target_words/17)` ≈ 18–20 scenes) with generic painting description. The
writer is given prior **subjects** to avoid (`used_subjects`, for related
images) but is **not** given the prior **sentence texts**, so it re-describes the
same physical features. There is no cross-scene similarity guard. Separately,
chapter boundaries exist only in metadata, never on screen.

## User decisions (verbatim intent)

- **Scope:** "Pipeline + re-render Toledo" — durable pipeline fix, then re-render
  Toledo from scratch (per the project rule: wipe generated artifacts, keep
  `raw_art/`, run all stages end-to-end).
- **Chapter separation style:** "Title card toàn màn hình" — full-screen title
  card, fade-from-black ~2 s, dark background in the channel look, Anton font,
  showing `CHAPTER N` + the chapter title.
- **Audio cue:** "Chỉ khoảng lặng" — no added sound; the card sits inside the
  inter-chapter silence.
- **Bundle the Starry Night durable fix** (Part 3): user said "yes" to including
  it, because re-rendering from scratch regenerates narration and would otherwise
  reintroduce the "scene names artwork X but shows artwork Y" defect.

## Constraints

- **Comic code is READ-ONLY**: `stages/`, `ui/`, root `config.py` must not change.
  `stages/_embedding.py` is *imported* read-only (already shared by the art track).
- **Do not change the logic/flow of comic Stage 4/5.** Art reuses comic bricks via
  the existing runtime-override pattern only.
- **A/V sync is sacred**: never alter audio length after `scene_timings` is
  computed; any video addition must occupy an existing silent window so total
  video length tracks audio exactly (the long-form drift discipline already in place).

---

## Architecture overview

Three independent units, all art-side:

```
narrate_longform.py ──(prompt rule: prior sentences + role)──┐
                                                             ├─► narration.json (no repeats)
        dedupe.py  ──(embedding near-dup → surgical rewrite)─┘
hunt.py        ──(named-artwork ↔ image grounding)──────────► visual_plan / related images (Part 3)
longform_tts.py ──(widen silence at carded boundaries)──┐
                                                        ├─► audio.wav + scene_timings with card-sized gaps
assemble.py    ──(_overlay_chapter_cards over silent)───┘──► final.mp4 (visible chapter cards)
```

---

## Part 1 — Anti-repetition (writer)

Two layers: prompt-level prevention + a deterministic safety net.

### 1a. Prompt-level prevention (`narrate_longform.py`)

`write_longform_narration` already accumulates `used_subjects` across chapters.
Add a parallel accumulator of **prior scene texts** and feed it into each
chapter's prompt, plus a role-based description budget.

- New accumulator `said_lines: list[str]` (every prior chapter's scene `text`).
- `build_chapter_scenes(...)` gains a keyword arg `said_lines: list[str] | None`
  threaded the same way `used_subjects` is.
- The chapter prompt (built in `write_longform_narration`) gains a block:

  > "Sentences already narrated in earlier chapters are listed below. Every
  > sentence you write must add NEW information. Do NOT restate any physical
  > description of the painting that has already been said (e.g. the cathedral
  > spires, the riverbank bridge, the green hills, the visible brushstrokes). You
  > may *reference* a feature only to make a new analytical point — never to
  > describe it again.
  > ALREADY SAID:\n{said_lines}"

  `said_lines` is truncated to the most recent N (config `ART_LF_SAID_LINES_MAX`,
  default 60) to bound the prompt; the deterministic guard (1b) catches anything
  the truncation misses.

- **Role-based description budget.** Pure visual description is allowed only in
  `cold_open` and `evidence` roles. For `backfill`, `twist`, `resolution` the
  prompt instructs: "this chapter is interpretation/history — reference the
  painting's features to build meaning, do not catalog its appearance." Keyed on
  `chapter["role"]`, which already exists in `chapters.json` / outline.

This changes **prompt text and threading only** — no change to scene-count
floors, word budgets, validation, or the redraw loop.

### 1b. Deterministic dedup guard (`art_pipeline/dedupe.py`, new)

After all chapters are assembled and the total-words floor passes (i.e. right
before/around the existing `validate_cross_chapter` call at
`narrate_longform.py:424`), run a near-duplicate pass.

**Detection.** For the assembled scene list, compute pairwise cosine with
`stages/_embedding.py`:

```python
from stages._embedding import embed  # 384-d sentence vectors, cached model

def find_near_duplicates(scenes, threshold):
    """Return [(later_idx, earlier_idx, sim)] for cross-scene pairs >= threshold,
    keeping for each later scene only its single strongest earlier match."""
    vecs = [embed(s["text"]) for s in scenes]
    dups = []
    for j in range(len(scenes)):
        best = None
        for i in range(j):
            sim = _cos(vecs[i], vecs[j])
            if sim >= threshold and (best is None or sim > best[2]):
                best = (j, i, sim)
        if best:
            dups.append(best)
    return dups
```

`threshold = ART_LF_DEDUP_THRESHOLD` (default **0.86** — chosen so brushstroke
≈0.98, cathedral ≈0.90, bridge ≈0.86 are all caught; tune against the benchmark).
Use a local `_cos` (numpy dot / norms) — do not add new deps.

**Resolution — surgical rewrite (preserves scene count + timing).** For each
flagged later scene, ask the text LLM (via `call_with_chain`, the single
SDK/OpenRouter switch) to rewrite **only that one scene's text** so it:
- conveys a NEW point appropriate to its chapter `role` and neighbours,
- stays within ±20% of the original word count (so `scene_timings` barely moves;
  long-form recomputes timings from audio anyway, so small drift is absorbed),
- avoids every sentence in a ban-list (the matched earlier sentence + recent
  `said_lines`).

Re-embed the rewritten text; accept if `max_sim < threshold`. Bounded retries
`ART_LF_DEDUP_MAX_PASSES` (default 2). If still duplicated after the budget, keep
the best candidate and `log` a warning (never silently ship a 0.98 repeat without
a recorded warning) — does not raise, to avoid blocking the run on one stubborn
line.

**Why rewrite, not regenerate-chapter or drop:** dropping a scene changes scene
count and breaks region/timing assumptions; regenerating a whole chapter risks
new duplicates and re-runs validation. A targeted rewrite is the minimal,
sync-safe edit.

**Benchmark.** Add an `art_longform_repetition` entry to
`research/reports/_BENCHMARK_thresholds.json` recording, per produced project,
`max_cross_scene_similarity` and the count of pairs ≥ threshold. This matches the
project's benchmark culture (mismatch → tune + record).

---

## Part 2 — Chapter title cards (assembler)

Insert a full-screen title card at each chapter boundary **except before
chapter 1** (the cold-open hook must open immediately). For 5 chapters that is 4
cards (before chapters 2, 3, 4, 5).

### 2a. Make room in the audio (`longform_tts.py`)

Today the stitcher writes a single `ART_LF_CHAPTER_GAP_S = 1.0 s` of silence
between every chapter (`longform_tts.py:110-112`), and **writes
`scene_timings.json` itself** (line 115) from each chapter's per-scene timings
shifted by a running `offset = frames_written / framerate` — where
`frames_written` already includes the inter-chapter silence. A ~2 s card needs a
slightly larger silent window.

Since cards sit before chapters 2…N, **every** inter-chapter boundary carries a
card. So: introduce `ART_LF_CHAPTER_CARD_SEC` (default **2.6 s**) and, when
`ART_LF_CHAPTER_CARDS` is true, use it as the inter-chapter silence length for all
boundaries (otherwise `ART_LF_CHAPTER_GAP_S`). Because the running `offset`
already folds the silence into every later scene's `start`/`end`, the widened gap
propagates into `scene_timings.json` automatically — the next chapter's first
scene simply starts ~2.6 s after the previous chapter's last scene ends. No manual
timing math; only the `gap_frames` value changes.

The stitch logic, frame math, atomic write, and calm-filter pass are otherwise
unchanged.

### 2b. Render + overlay the card (`assemble.py`)

Two new helpers, invoked **after** `_apply_film_look(silent)` (line 463) and
**before** captions/`_final_encode` so card text is crisp (un-vignetted) and the
final mux is untouched:

- `_render_chapter_card(chapter_id, title, out_png)` — draw on a solid dark
  background (`ART_CARD_BG`, default `#0d1b2a` midnight-blue) a small gold
  (`ART_CARD_ACCENT`, default `#c9a44a`) `CHAPTER {N}` kicker above the chapter
  `title` in white, Anton (`./fonts/Anton-Regular.ttf`), centered, at
  `ART_LF_OUTPUT_W × ART_LF_OUTPUT_H` (1920×1080). Pure ffmpeg `drawtext` on
  `color=` source → PNG.

- `_overlay_chapter_cards(silent_video, chapters, scene_timings, log)` — for each
  carded boundary compute the window `[prev_chapter_last_scene_end,
  this_chapter_first_scene_start]` from `scene_timings.json` (keyed by `scene_id`)
  + `chapters.json` (scene_ids per chapter). Composite the card as an **opaque**
  overlay enabled only in that window, with an alpha ramp `0→1→0` (≈0.5 s
  fade-in, hold, ≈0.5 s fade-out) so the painting dips to the card and back:

  ```
  ffmpeg -i silent.mp4 -i card_2.png -i card_3.png ... \
    -filter_complex "
      [1:v]format=rgba,fade=t=in:st=T0:d=0.5:alpha=1,fade=t=out:st=T1-0.5:d=0.5:alpha=1,
           setpts=PTS-STARTPTS+T0/TB[c2];
      [0:v][c2]overlay=enable='between(t,T0,T1)'[v2]; ... " \
    -map "[vN]" silent_carded.mp4
  ```

  (Implementation may loop boundaries programmatically rather than a single huge
  filtergraph; the contract is: opaque card, alpha-faded, enabled only inside the
  silent window.) Total duration is unchanged → **zero A/V drift**.

Gating: `ART_LF_CHAPTER_CARDS` (default true) and only when `longform`
(`chapters.json` exists). Short-form is untouched.

---

## Part 3 — Starry Night durable fix (bundled)

**Defect:** a scene whose narration names a specific famous external artwork
(e.g. "Vincent van Gogh's The Starry Night") was paired with an image of a
*different* artwork (Turner's Fighting Temeraire). The manual Toledo patch
(swapping scene image pages) will be lost on a from-scratch re-render.

**Durable guard (deterministic, best-effort NER):**

1. **Writer contract (`narrate_longform.py` / visual-plan build):** when a
   `related` scene's `text` names a specific artwork, its `subject` must be set to
   that exact artwork title. Detect named artworks by matching the scene text
   against a curated set assembled from `art_context` facts + a capitalized-title
   regex (e.g. `«Artist('s)? "Title"»`, `«Title by Artist»`). When matched, the
   writer/validator forces `subject = "<Artist> — <Title>"`.

2. **Hunt verification (`hunt.py`):** after an image resolves for such a scene,
   compare the resolved image `title` (already captured as `c["title"]`) against
   the named artwork using token overlap (reuse the `fact_is_grounded` ≥80% style
   already in `outline.py`). On mismatch, **reject the image** and fall back to a
   region of the primary artwork (`pick_fallback_region`) — i.e. never display a
   wrong-named painting; showing the painting itself is always safe.

**Scope/limitation (explicit):** this is a deterministic best-effort guard, not a
full art-title NER. It targets the concrete failure class the user hit
(prominently named, quoted/capitalized artwork titles). Documented here so the
spec reviewer can accept the limitation or cut Part 3.

---

## Config additions (`art_pipeline/config.py`, env-overridable)

```python
# Anti-repetition (long-form)
ART_LF_SAID_LINES_MAX   = int(os.getenv("ART_LF_SAID_LINES_MAX", "60"))
ART_LF_DEDUP_THRESHOLD  = float(os.getenv("ART_LF_DEDUP_THRESHOLD", "0.86"))
ART_LF_DEDUP_MAX_PASSES = int(os.getenv("ART_LF_DEDUP_MAX_PASSES", "2"))

# Chapter title cards (long-form)
ART_LF_CHAPTER_CARDS = os.getenv("ART_LF_CHAPTER_CARDS", "true").lower() in ("true","1","yes")
ART_LF_CHAPTER_CARD_SEC = float(os.getenv("ART_LF_CHAPTER_CARD_SEC", "2.6"))
ART_CARD_BG     = os.getenv("ART_CARD_BG", "#0d1b2a")
ART_CARD_ACCENT = os.getenv("ART_CARD_ACCENT", "#c9a44a")
ART_CARD_FONT   = os.getenv("ART_CARD_FONT", str(_REPO_ROOT / "fonts" / "Anton-Regular.ttf"))
```

`ART_LF_CHAPTER_GAP_S` (existing, 1.0) stays as the non-carded / fallback gap.

---

## Data flow & A/V sync (the critical invariant)

1. `narrate` writes `narration.json` (deduped) + `chapters.json`.
2. `tts` stitches per-chapter WAVs with `ART_LF_CHAPTER_CARD_SEC` silence at every
   inter-chapter boundary (when cards on), and in the same pass writes
   `scene_timings.json` + `word_timestamps.json` by shifting each chapter's
   per-scene timings by the running `offset` (which includes that silence). →
   `audio.wav`, `scene_timings.json` with card-sized inter-chapter gaps. No manual
   math — the offset folds the gap in.
3. `assemble` builds `video_silent.mp4` (unchanged shot/xfade/film-look path),
   then `_overlay_chapter_cards` paints opaque faded cards **inside the existing
   silent windows** — video length unchanged.
4. `_final_encode(silent_carded, mixed, captions, final)` — unchanged mux.

Because every card lives entirely inside a silent window that already exists in
the audio, audio↔video alignment is preserved by construction.

## Error handling

- Embedding model load failure in `dedupe.py` → log + skip the dedup pass (do not
  block narration; Part 1a prompt prevention still applies). The model is already
  used by the comic track, so this is the same failure surface.
- Stubborn duplicate after `ART_LF_DEDUP_MAX_PASSES` → keep best, log warning,
  record in benchmark; never raise.
- Missing font / ffmpeg drawtext failure for a card → raise (a card is a declared
  deliverable; silent omission would mislead). `_resolve_ffmpeg` already centralizes
  the binary.
- Card window shorter than the fade envelope (≈1.0 s) → clamp fades; if a boundary
  window is unexpectedly < min, log and skip that one card (never overrun into
  narrated audio).

## Testing strategy (`tests/art/`)

- `test_dedupe.py`: synthetic scenes with one near-verbatim pair → detected;
  rewrite path mocked LLM returns a distinct line → final `max_sim < threshold`;
  scene count and (±20%) word count preserved; stubborn-dup path logs + keeps best,
  does not raise.
- `test_narrate_longform.py` (extend): `said_lines` threaded into the prompt;
  role-based rule present for `twist`/`resolution`; existing floor/redraw tests
  still pass.
- `test_assemble.py` (extend): `_render_chapter_card` produces a 1920×1080 PNG;
  `_overlay_chapter_cards` computes windows from a fixture `scene_timings` +
  `chapters.json` and produces a video whose duration equals the input silent
  duration (drift == 0); no card before chapter 1; cards gated off when
  `ART_LF_CHAPTER_CARDS=false`. Reuse the autouse `_no_polish` fixture pattern.
- `test_longform_tts.py` (extend): carded boundary writes `ART_LF_CHAPTER_CARD_SEC`
  silence; non-carded/short-form uses `ART_LF_CHAPTER_GAP_S`; stitched frame count
  matches the widened gaps.
- `test_hunt.py` (Part 3): scene naming artwork X + resolved image titled Y →
  rejected → fallback region; matching title → kept.
- `test_config.py` (extend): new constants parse with documented defaults.

Per the project rule, **delete throwaway probe code after the run**; keep the
permanent unit tests.

## Files touched (all art-side — comic stays read-only)

| File | Change |
|---|---|
| `art_pipeline/config.py` | new constants (above) |
| `art_pipeline/narrate_longform.py` | `said_lines` thread + role rule; call dedup guard |
| `art_pipeline/dedupe.py` (new) | near-dup detector + surgical rewrite |
| `art_pipeline/longform_tts.py` | widen silence at carded boundaries |
| `art_pipeline/assemble.py` | `_render_chapter_card` + `_overlay_chapter_cards` |
| `art_pipeline/hunt.py` | Part 3 named-artwork ↔ image verification |
| `research/reports/_BENCHMARK_thresholds.json` | `art_longform_repetition` entry |
| `tests/art/*` | new + extended tests |

## Non-goals

- No background music / spoken-transition rewrites beyond the existing re-hooks.
- No chapter card before chapter 1 (hook leads).
- No change to short-form, to comic stages, or to the calm-voice / zoom / film-look
  work already shipped.
- Part 3 is a targeted guard, not general artwork NER.
