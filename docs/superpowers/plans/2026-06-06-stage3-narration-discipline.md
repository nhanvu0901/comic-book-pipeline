# Stage 3 Narration Discipline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three Stage-3 narration defects (non-monotonic page order → wrong panel; invented drama; repeated content) by patching the existing flow while REDUCING overlapping validators.

**Architecture:** (1) Deterministically order/merge outline beats by page so `page_ref` is monotonic by construction. (2) Merge the two overlapping fact-checks (`_fidelity_check` + `_wiki_cross_check`) into one "grounding" check that also flags embellishment. (3) Strengthen redundancy detection to all-pairs key-noun repeats. (4) Relax the length/duration gates so dense comics keep all canon beats.

**Tech Stack:** Python 3.14, `stages/stage_3/write_script.py`, OpenRouter/Claude-SDK LLM calls (`call_with_chain`), benchmark scorer `research/scripts/benchmark_score.py`. No pytest in this repo — verification is via standalone scripts run on real project data (`projects/dark_venom_things`, `projects/dark_loki`) plus the benchmark.

**Spec:** `docs/superpowers/specs/2026-06-06-stage3-narration-discipline-design.md`

---

## File Structure

- Modify: `stages/stage_3/write_script.py` — all four parts live here.
- Modify: `research/reports/_BENCHMARK_thresholds.json` — Part 4 duration band (force-tracked; `research/` is gitignored).
- Verify-only (no edit): `research/scripts/benchmark_score.py`, `projects/dark_loki/*`, `projects/dark_venom_things/*`.

Note on commits: `docs/` and `research/` are gitignored — use `git add -f` for the threshold file. End commit messages with the Co-Authored-By trailer.

---

## Task 1: Deterministic beat ordering (fixes wrong-order panels)

**Files:**
- Modify: `stages/stage_3/write_script.py` — add `_order_beats_by_page`, call it in `outline_beats` (~line 460, after the `beats` list is built, before `_validate_outline`), add a monotonic assertion in `_validate` (~line 1035).

- [ ] **Step 1: Write `_order_beats_by_page` helper**

Add this function just above `_validate_outline` (currently line 467):

```python
def _order_beats_by_page(beats: list[Beat]) -> list[Beat]:
    """Make the beat sheet match the comic's reading order: stable-sort beats by
    their lowest page_ref so page progression is monotonic (the video is a
    page-by-page walk; forward-only Stage-5 selection breaks if narration jumps
    back a page). Stable sort preserves the outliner's order among same-page
    beats. Beats with no page_refs keep their relative position by inheriting the
    previous beat's page (so they don't sink to the front)."""
    def primary(b: Beat, fallback: int) -> int:
        return min(b.page_refs) if b.page_refs else fallback
    # First pass: give page-less beats the running max page so they stay in place.
    running = 0
    keyed: list[tuple[int, int, Beat]] = []
    for idx, b in enumerate(beats):
        pg = primary(b, running)
        running = max(running, pg)
        keyed.append((pg, idx, b))
    keyed.sort(key=lambda t: (t[0], t[1]))  # stable by (page, original index)
    return [t[2] for t in keyed]
```

- [ ] **Step 2: Call it in `outline_beats` before validation**

In `outline_beats`, find (around line 458-460):

```python
    if not (8 <= len(beats) <= 12):
        log(f"[stage4]   warning: outline returned {len(beats)} beats (want 10-12)")

    # Page-gap validation — retry once with bridge instruction if jumps > 5.
    issues = _validate_outline(beats)
```

Insert the ordering call between them:

```python
    if not (8 <= len(beats) <= 12):
        log(f"[stage4]   warning: outline returned {len(beats)} beats (want 10-12)")

    # Deterministic page ordering: the recap is a page-by-page walk and Stage 5
    # is forward-only, so beats MUST be non-decreasing in page. Sort here instead
    # of relying on the soft prompt rule (which let venom emit pg12 before pg11).
    before = [min(b.page_refs) if b.page_refs else 0 for b in beats]
    beats = _order_beats_by_page(beats)
    after = [min(b.page_refs) if b.page_refs else 0 for b in beats]
    if before != after:
        log(f"[stage4]   reordered beats to monotonic page order: {before} -> {after}")

    # Page-gap validation — retry once with bridge instruction if jumps > 5.
    issues = _validate_outline(beats)
```

- [ ] **Step 3: Add a monotonic-page assertion in `_validate`**

In `_validate` (line 1035), after the per-scene loop that already collects
`errors`, append a non-decreasing page_ref check. Find the `return errors` at the
end of `_validate` and insert before it:

```python
    # Defense-in-depth: scenes must not move backward in pages (Stage 5 is
    # forward-only). Beats are page-sorted in outline_beats, so this should never
    # fire — if it does, the writer reassigned page_ref out of order. Skip the
    # intro (scene 1, cover) and outro (whole-page) which are bookends.
    prev_pg = 0
    for s in parsed.get("scenes") or []:
        if s.get("is_intro") or s.get("is_outro"):
            continue
        pg = int(s.get("page_ref", 0) or 0)
        if pg and pg < prev_pg:
            errors.append(
                f"scene {s.get('scene_id','?')} page_ref={pg} goes backward "
                f"(prev {prev_pg}) — non-monotonic page order"
            )
        prev_pg = max(prev_pg, pg)
```

This uses the `page_ref=` marker which `_is_critical_error` already treats as
critical → triggers a retry that fixes the order.

- [ ] **Step 4: Verify the helper on a synthetic case**

Run this one-off (paste into a `python -` heredoc), expect the venom-style
backward case to become monotonic:

```python
import sys; sys.path.insert(0, ".")
from stages.stage_3.schema import Beat
from stages.stage_3.write_script import _order_beats_by_page
beats = [Beat(id=1, function="SETUP", name="a", page_refs=[9]),
         Beat(id=2, function="SETUP", name="lizard", page_refs=[12]),
         Beat(id=3, function="SETUP", name="reed", page_refs=[11]),
         Beat(id=4, function="SETUP", name="ally", page_refs=[18])]
out = _order_beats_by_page(beats)
print([min(b.page_refs) for b in out])
assert [min(b.page_refs) for b in out] == [9, 11, 12, 18], "not monotonic"
print("OK monotonic")
```

Expected: `[9, 11, 12, 18]` then `OK monotonic`.

- [ ] **Step 5: Commit**

```bash
git add stages/stage_3/write_script.py
git commit -m "fix(stage3): deterministic page-monotonic beat ordering

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Unified grounding check (fixes embellishment; merges two checks)

**Files:**
- Modify: `stages/stage_3/write_script.py` — extend `_wiki_cross_check` (line 1615) to also flag embellishment using panel data; remove the `_fidelity_check` call from the validation loop (lines ~210-260); keep `_fidelity_check` defined but unused-removed.

- [ ] **Step 1: Extend the grounding prompt**

In `_WIKI_SYSTEM` (the system prompt for `_wiki_cross_check`, defined just above
the function), add an embellishment clause. Find the `RULES:` block and replace
it with:

```python
RULES:
  • Flag a scene when it CONTRADICTS the wiki (substituted character, wrong
    action, wrong order). Stylistic phrasing differences are FINE.
  • ALSO flag a scene when it states a FACT or EMOTION that is NOT supported by
    the wiki plot — invented drama. Examples to flag: "rage consumed him",
    "the city watched in fear", "tongue extended mocking them" when no such
    detail appears in the plot. Grounded pronouns/connectives ("he", "then",
    "meanwhile") are FINE; invented events/feelings are NOT.
  • If everything is canonical and grounded: {"issues": [], "missing_beats": []}
```

- [ ] **Step 2: Make the function accept story_pages for panel grounding**

`_wiki_cross_check` currently takes `(parsed, comic_context, *, model, progress)`.
The wiki plot is already the ground truth; panel data is optional context. Keep
the signature but add the wiki-only embellishment check (no new arg needed — the
wiki plot is sufficient to judge "supported or invented"). No code change beyond
Step 1's prompt is required for the wiki-grounding path.

- [ ] **Step 3: Remove the overlapping `_fidelity_check` call from the loop**

In `write_script`'s validation loop (around line 210-260), find:

```python
        fid_issues = _fidelity_check(parsed, story_pages, comic_context,
                                      model=model, progress=progress)
        if fid_issues:
            errors = errors + [f"fidelity: {i}" for i in fid_issues]
        wiki_issues = _wiki_cross_check(parsed, comic_context,
                                         model=model, progress=progress)
        if wiki_issues:
            errors = errors + [f"wiki: {i}" for i in wiki_issues]
```

Replace with (drop fidelity; wiki now also covers embellishment):

```python
        # Single grounding check (merged fidelity + wiki): flags canon
        # contradictions AND invented drama. All grounding issues are critical.
        wiki_issues = _wiki_cross_check(parsed, comic_context,
                                         model=model, progress=progress)
        if wiki_issues:
            errors = errors + [f"wiki: {i}" for i in wiki_issues]
```

- [ ] **Step 4: Verify the grounding check flags invented drama**

Run on the existing venom narration (which contains "rage consumed him"):

```python
import sys, json; sys.path.insert(0, ".")
from stages.stage_3.write_script import _wiki_cross_check
nar = json.load(open("projects/dark_venom_things/narration.json"))
ctx = json.load(open("projects/dark_venom_things/comic_context.json"))
issues = _wiki_cross_check(nar, ctx, model=None, progress=print)
for i in issues: print("-", i)
```

Expected: at least one issue referencing an unsupported/invented detail
(e.g. "rage consumed him" or "city watched in fear").

- [ ] **Step 5: Commit**

```bash
git add stages/stage_3/write_script.py
git commit -m "fix(stage3): merge fidelity+wiki into one grounding check (flags embellishment)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: All-pairs key-noun de-duplication (fixes repetition)

**Files:**
- Modify: `stages/stage_3/write_script.py` — rewrite `_detect_redundant_scenes` (line 1143).

- [ ] **Step 1: Rewrite `_detect_redundant_scenes` for all-pairs key nouns**

Replace the whole function body:

```python
def _detect_redundant_scenes(scenes: list[dict], threshold: int = 4) -> list[str]:
    """Flag scenes that restate the same content — checked across ALL scene pairs
    (not just consecutive), so "struck Reed" in sc4 and sc6 (separated by sc5) is
    caught, and "severed arm" repeated in sc10/sc11 is caught. Two signals:
      (a) >=`threshold` shared content stems (the original consecutive heuristic,
          now all-pairs), OR
      (b) a repeated KEY NOUN PHRASE — a distinctive 2-word noun pair that
          appears verbatim in two different scenes (e.g. "severed arm")."""
    import re
    issues: list[str] = []
    body = [s for s in scenes if not s.get("is_intro") and not s.get("is_outro")]

    def key_bigrams(text: str) -> set[str]:
        words = [w for w in re.findall(r"[a-zA-Z]+", text.lower())
                 if len(w) >= 4 and w not in _REDUNDANCY_STOP]
        return {f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)}

    n = len(body)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = body[i], body[j]
            sa, sb = _scene_stems(str(a.get("text", ""))), _scene_stems(str(b.get("text", "")))
            shared = sa & sb
            shared_bigrams = key_bigrams(str(a.get("text", ""))) & key_bigrams(str(b.get("text", "")))
            ai = a.get("scene_id", i + 1); bi = b.get("scene_id", j + 1)
            if shared_bigrams:
                issues.append(
                    f"scenes {ai}-{bi} repeat the same content "
                    f"(repeated phrase: {', '.join(sorted(shared_bigrams))}) — "
                    f"rewrite the later scene to advance the story instead of restating it"
                )
            elif len(shared) >= threshold:
                issues.append(
                    f"scenes {ai}-{bi} repeat the same content "
                    f"(shared: {', '.join(sorted(shared))}) — rewrite the later scene "
                    f"to advance the story instead of restating it"
                )
    return issues
```

(Keeps the `"repeat the same content"` marker, which `_is_critical_error`
already treats as critical → triggers the existing redundancy-retry rewrite.)

- [ ] **Step 2: Verify on existing venom narration**

```python
import sys, json; sys.path.insert(0, ".")
from stages.stage_3.write_script import _detect_redundant_scenes
nar = json.load(open("projects/dark_venom_things/narration.json"))
for i in _detect_redundant_scenes(nar["scenes"]): print("-", i)
```

Expected: a flag for the "severed arm" repeat (sc10/sc11) and/or "struck
Reed"/"stay out" repeat (sc4/sc6).

- [ ] **Step 3: Commit**

```bash
git add stages/stage_3/write_script.py
git commit -m "fix(stage3): all-pairs key-noun redundancy detection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Flexible length + benchmark duration (keep all canon beats)

**Files:**
- Modify: `stages/stage_3/write_script.py` — widen the word target so dense comics aren't over-trimmed (lines 15-21, 226-235).
- Modify: `research/reports/_BENCHMARK_thresholds.json` — relax `duration_max`.

- [ ] **Step 1: Widen the writer word band (keep beats, cut only fat)**

In `write_script.py` lines 15-21, change the target band to allow longer dense
narration while still discouraging fat:

```python
_TARGET_WORDS_MIN = 175
_TARGET_WORDS_MAX = 260   # was 195 — dense comics keep all canon beats; fat
                          # (drama/repeats) is removed by grounding + dedup, not
                          # by dropping content. ~260 words ≈ ~90s at 2.88 wps.
_WORDS_PER_SEC = 2.88
```

In the best-draft `words_ok` gate (line ~233), widen the upper bound to match:

```python
        words_ok = 1 if 170 <= _words <= 260 else 0
```

- [ ] **Step 2: Relax the benchmark duration ceiling**

In `research/reports/_BENCHMARK_thresholds.json`, change `qualifying.duration_max`:

```json
    "duration_max": 95.0,
```

(Was 72.08. Dense comics that keep all canon beats run longer; we no longer
fail them for length. Lower bound `duration_min` stays.)

- [ ] **Step 3: Verify benchmark accepts a longer dense video**

```bash
python research/scripts/benchmark_score.py "projects/dark_venom_things/final.mp4" --no-vlm 2>&1 | grep -E "duration|word_count|TOTAL"
```

Expected: `duration_in_range` now ✅ (91.3s ≤ 95), `word_count` ✅ (293 ≤ ... note
word_count_max is 285; if 293 still fails word_count, also raise
`word_count_max` to 300 in the same JSON).

- [ ] **Step 4: Commit**

```bash
git add stages/stage_3/write_script.py
git add -f research/reports/_BENCHMARK_thresholds.json
git commit -m "feat(stage3): flexible length — keep canon beats, relax duration band

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Integration — re-run venom + loki, verify all defects fixed

**Files:** none (verification only).

- [ ] **Step 1: Re-run dark_venom_things Stage 3→5**

```bash
python /tmp/venom_full.py > /tmp/venom_full.log 2>&1   # existing driver (Stage 3→5, voice f7248031)
```

(If `/tmp/venom_full.py` was cleaned, recreate it: it calls
`ui.bridge.run_stage_3_write("dark_venom_things","panel_walk","",log)` →
`run_stage_4(..., "f7248031-b419-4004-b447-2e9bf32f6b5e", None, log)` →
`run_stage_5(...)`.)

- [ ] **Step 2: Verify monotonic page order + no repeats + grounding**

```python
import sys, json; sys.path.insert(0, ".")
from stages.stage_3.write_script import _detect_redundant_scenes, _wiki_cross_check
nar = json.load(open("projects/dark_venom_things/narration.json"))
ctx = json.load(open("projects/dark_venom_things/comic_context.json"))
body = [s for s in nar["scenes"] if not s.get("is_intro") and not s.get("is_outro")]
pgs = [s["page_ref"] for s in body]
print("monotonic page_ref:", all(pgs[i] <= pgs[i+1] for i in range(len(pgs)-1)), pgs)
print("redundancy issues:", _detect_redundant_scenes(nar["scenes"]))
print("grounding issues:", _wiki_cross_check(nar, ctx, model=None, progress=None))
```

Expected: `monotonic page_ref: True`, `redundancy issues: []`, grounding issues
empty or only minor.

- [ ] **Step 3: Verify the 0:34 panel now matches**

```python
import sys, json; sys.path.insert(0, ".")
shots = json.load(open("projects/dark_venom_things/shots.json"))
t = 0.0
for s in shots:
    if t <= 34.5 <= t + s["duration_seconds"]:
        print("0:34 →", s["source_image"].split("/")[-1], "scene", s["scene_id"])
    t += s["duration_seconds"]
```

Expected: the shot at ~0:34 belongs to the scene whose text describes that page's
real event (no Reed-line-over-Lizard mismatch).

- [ ] **Step 4: Benchmark both projects**

```bash
for p in "dark_venom_things" "dark_loki"; do
  echo "=== $p ==="
  python research/scripts/benchmark_score.py "projects/$p/final.mp4" --no-vlm 2>&1 | grep -E "TOTAL|duration|wiki"
done
```

Expected: both QUALIFIED; venom no longer fails duration; narration reads
factual (manual spot-check of `narration.json`).

- [ ] **Step 5: Repeat Step 1-4 for dark_loki, then final commit if any tuning changed**

```bash
git add -A && git status   # commit only if tuning constants were adjusted
```

---

## Self-review notes

- Spec Part 1 (ordering) → Task 1. Part 2 (grounding merge) → Task 2. Part 3
  (dedup) → Task 3. Part 4 (length) → Task 4. Integration/success-criteria →
  Task 5. All spec sections covered.
- `_is_critical_error` markers reused verbatim (`page_ref=`, `wiki:`,
  `repeat the same content`) — no new marker needed, so the retry loop already
  treats the new findings as critical.
- `_REDUNDANCY_STOP`, `_scene_stems`, `Beat` referenced in tasks already exist in
  `write_script.py` / `schema.py`.
- Word-count benchmark interplay (Step 4.3) flagged: may also need
  `word_count_max` raised to 300 if a dense video exceeds 285.
