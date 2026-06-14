# Art long-form — Chapter cards + anti-repetition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the long-form narration from re-describing the same painting across chapters, and add full-screen chapter title cards inside the inter-chapter silence — plus a durable guard so a scene that names a specific artwork never shows a different one.

**Architecture:** All changes are art-side (`art_pipeline/`, `tests/art/`). Comic code (`stages/`, `ui/`, root `config.py`) is READ-ONLY; `stages/_embedding.py` and comic Stage 4/5 bricks are imported unchanged. Three independent units: (1) writer anti-repetition (prompt ledger + `dedupe.py` embedding guard with surgical rewrite), (2) chapter title cards (`config` + `longform_tts` silence widen + `assemble` render/overlay), (3) named-artwork↔image guard in `hunt.py`.

**Tech Stack:** Python, ffmpeg (drawtext/overlay/xfade via `subprocess`), `sentence_transformers` via `stages/_embedding.py`, Cartesia TTS via comic Stage 4, pytest.

**Spec:** `docs/superpowers/specs/2026-06-14-art-longform-chapter-separation-anti-repetition-design.md`

**Branch:** `feat/art-v2` (already checked out).

**Run tests with:** `.venv/bin/python3 -m pytest tests/art/ -q` (the repo `.venv` has dotenv/config + sentence_transformers; bare `python3` does not). For a single test: `.venv/bin/python3 -m pytest tests/art/test_dedupe.py -v`.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `art_pipeline/config.py` | constants for dedup + cards | modify |
| `art_pipeline/dedupe.py` | near-dup detection + surgical rewrite (NEW) | create |
| `art_pipeline/narrate_longform.py` | prior-sentence ledger + role budget in prompt; call dedupe | modify |
| `art_pipeline/longform_tts.py` | widen inter-chapter silence when cards on | modify |
| `art_pipeline/assemble.py` | render + overlay chapter title cards | modify |
| `art_pipeline/hunt.py` | named-artwork ↔ resolved-image guard | modify |
| `tests/art/test_config.py` | new-constant assertions | modify |
| `tests/art/test_dedupe.py` | dedupe unit tests (NEW) | create |
| `tests/art/test_narrate_longform.py` | prompt-helper unit tests | modify |
| `tests/art/test_longform_tts.py` | gap-selection unit test | modify |
| `tests/art/test_assemble.py` | card window / render / overlay tests | modify |
| `tests/art/test_hunt.py` | artwork-match guard tests | modify (create if absent) |

---

## Task 1: Config constants

**Files:**
- Modify: `art_pipeline/config.py` (after the existing long-form block, after line 107)
- Test: `tests/art/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/art/test_config.py`:

```python
def test_dedup_and_card_constants_exist():
    from art_pipeline import config as C
    assert C.ART_LF_SAID_LINES_MAX == 60
    assert C.ART_LF_DEDUP_THRESHOLD == 0.86
    assert C.ART_LF_DEDUP_MAX_PASSES == 2
    assert C.ART_LF_CHAPTER_CARDS is True
    assert C.ART_LF_CHAPTER_CARD_SEC == 2.6
    assert C.ART_CARD_BG == "#0d1b2a"
    assert C.ART_CARD_ACCENT == "#c9a44a"
    assert C.ART_CARD_FONT.endswith("Anton-Regular.ttf")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_config.py::test_dedup_and_card_constants_exist -v`
Expected: FAIL with `AttributeError: module 'art_pipeline.config' has no attribute 'ART_LF_SAID_LINES_MAX'`

- [ ] **Step 3: Add the constants**

In `art_pipeline/config.py`, immediately after line 107 (`ART_LF_REGION_REUSE_WINDOW = 6 ...`), add:

```python


# ── Anti-repetition (long-form, 2026-06-14) ──────────────────────────────────
# Long-form writes one chapter at a time; each chapter re-describes the same
# painting → near-verbatim repeats (Toledo: the brushstroke line appeared 3x).
# Layer 1: feed prior chapters' sentences into the prompt (truncated). Layer 2:
# embedding near-dup guard + surgical rewrite (dedupe.py).
ART_LF_SAID_LINES_MAX = int(os.getenv("ART_LF_SAID_LINES_MAX", "60"))
ART_LF_DEDUP_THRESHOLD = float(os.getenv("ART_LF_DEDUP_THRESHOLD", "0.86"))
ART_LF_DEDUP_MAX_PASSES = int(os.getenv("ART_LF_DEDUP_MAX_PASSES", "2"))

# ── Chapter title cards (long-form, 2026-06-14) ──────────────────────────────
# Full-screen card (fade-to-black) before chapters 2..N so viewers know which
# section they are in. The card sits inside the inter-chapter silence — to make
# room, that silence is widened from ART_LF_CHAPTER_GAP_S to CARD_SEC. Because
# longform_tts folds the silence into every later scene's offset, scene_timings
# stays consistent → zero A/V drift.
ART_LF_CHAPTER_CARDS = os.getenv("ART_LF_CHAPTER_CARDS", "true").lower() in ("true", "1", "yes")
ART_LF_CHAPTER_CARD_SEC = float(os.getenv("ART_LF_CHAPTER_CARD_SEC", "2.6"))
ART_CARD_BG = os.getenv("ART_CARD_BG", "#0d1b2a")        # midnight blue
ART_CARD_ACCENT = os.getenv("ART_CARD_ACCENT", "#c9a44a")  # muted gold
ART_CARD_FONT = os.getenv("ART_CARD_FONT", str(_REPO_ROOT / "fonts" / "Anton-Regular.ttf"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_config.py::test_dedup_and_card_constants_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add art_pipeline/config.py tests/art/test_config.py
git commit -m "feat(art): config for long-form anti-repetition + chapter cards"
```

---

## Task 2: dedupe.py — near-duplicate detector

**Files:**
- Create: `art_pipeline/dedupe.py`
- Test: `tests/art/test_dedupe.py` (NEW)

Detection is a pure function over scene dicts/objects, using `semantic_sim` from
`stages/_embedding.py`. Tests monkeypatch `art_pipeline.dedupe.semantic_sim` so
they never need the real model.

- [ ] **Step 1: Write the failing test**

Create `tests/art/test_dedupe.py`:

```python
"""Tests for art_pipeline.dedupe — near-duplicate detection + surgical rewrite."""
import art_pipeline.dedupe as dedupe


def _fake_sim(a, b):
    """Deterministic stand-in for the embedding model: 1.0 if identical text
    (case/space-insensitive), else a low constant."""
    na = " ".join(a.lower().split())
    nb = " ".join(b.lower().split())
    return 1.0 if na == nb else 0.1


def test_find_near_duplicates_flags_later_scene(monkeypatch):
    monkeypatch.setattr(dedupe, "semantic_sim", _fake_sim)
    scenes = [
        {"scene_id": 1, "text": "The cathedral dominates the skyline."},
        {"scene_id": 2, "text": "A river winds through the foreground."},
        {"scene_id": 3, "text": "The cathedral dominates the skyline."},  # dup of #1
    ]
    dups = dedupe.find_near_duplicates(scenes, threshold=0.86)
    # only the LATER scene is flagged, paired with its strongest earlier match
    assert len(dups) == 1
    later, earlier, sim = dups[0]
    assert (later, earlier) == (2, 0)   # 0-based indices
    assert sim == 1.0


def test_find_near_duplicates_none_when_distinct(monkeypatch):
    monkeypatch.setattr(dedupe, "semantic_sim", _fake_sim)
    scenes = [{"scene_id": 1, "text": "A"}, {"scene_id": 2, "text": "B"}]
    assert dedupe.find_near_duplicates(scenes, threshold=0.86) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_dedupe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'art_pipeline.dedupe'`

- [ ] **Step 3: Create the module with the detector**

Create `art_pipeline/dedupe.py`:

```python
"""A4b safety net: catch near-verbatim cross-scene repeats that the per-chapter
prompt missed, and surgically rewrite the LATER offending scene to say something
new. Long-form writes chapters independently, so the same painting gets
re-described (Toledo: the brushstroke line landed in scenes 15, 26, 49). We
embed every scene and rewrite the second occurrence of any near-duplicate pair —
never the first — so earlier chapters stay stable.

Uses the shared local embedder (stages/_embedding.semantic_sim); if the model is
unavailable every similarity is 0.0 → this pass is a no-op (graceful degrade)."""
import json

from config import CREATIVE_LLM_MODELS
from stages.stage_3._llm import call_with_chain
from stages._embedding import semantic_sim

from ._json import extract_json
from .narrate import _starts_with_connective
from .config import (
    ART_LF_DEDUP_MAX_PASSES, ART_LF_DEDUP_THRESHOLD, ART_LF_SCENE_MAX_WORDS,
    ART_WORDS_PER_SEC,
)


def _text(scene) -> str:
    return scene["text"] if isinstance(scene, dict) else scene.text


def find_near_duplicates(scenes, threshold: float):
    """Return [(later_idx, earlier_idx, sim)] (0-based) — for each scene, its
    single strongest earlier match at or above `threshold`. Only the later scene
    of a pair is reported, so a rewrite never touches the first occurrence."""
    texts = [_text(s) for s in scenes]
    dups = []
    for j in range(len(texts)):
        best = None
        for i in range(j):
            sim = semantic_sim(texts[i], texts[j])
            if sim >= threshold and (best is None or sim > best[2]):
                best = (j, i, sim)
        if best:
            dups.append(best)
    return dups
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_dedupe.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add art_pipeline/dedupe.py tests/art/test_dedupe.py
git commit -m "feat(art): near-duplicate scene detector (dedupe.py)"
```

---

## Task 3: dedupe.py — surgical rewrite + orchestrator

**Files:**
- Modify: `art_pipeline/dedupe.py`
- Test: `tests/art/test_dedupe.py`

`dedupe_scenes` mutates each flagged `Scene` in place (text/word_count/
target_seconds/connective), bounded by `max_passes`, and returns a report. The
LLM call is isolated in `_rewrite_scene` so tests monkeypatch it.

- [ ] **Step 1: Write the failing test**

Add to `tests/art/test_dedupe.py`:

```python
from stages.stage_3.schema import Scene


def _scene(sid, text):
    wc = len(text.split())
    return Scene(scene_id=sid, text=text, page_ref=1, panel_ref=0, word_count=wc,
                 target_seconds=round(wc / 2.88, 2), connective=False, beat_id=sid,
                 is_intro=False, is_outro=False)


def test_dedupe_scenes_rewrites_later_duplicate(monkeypatch):
    monkeypatch.setattr(dedupe, "semantic_sim", _fake_sim)
    # rewrite returns a brand-new, distinct sentence
    monkeypatch.setattr(dedupe, "_rewrite_scene",
                        lambda scene, ban, role, ctx, log: "A wholly different observation here.")
    scenes = [_scene(1, "The cathedral dominates the skyline."),
              _scene(2, "The cathedral dominates the skyline.")]
    roles = {1: "cold_open", 2: "twist"}
    report = dedupe.dedupe_scenes(scenes, {}, roles, log=lambda m: None)
    assert scenes[1].text == "A wholly different observation here."
    assert scenes[1].word_count == 5
    assert len(scenes) == 2          # count preserved
    assert report["rewrites"] == 1
    assert report["max_similarity_after"] < 0.86


def test_dedupe_scenes_keeps_best_when_rewrite_keeps_duplicating(monkeypatch):
    monkeypatch.setattr(dedupe, "semantic_sim", _fake_sim)
    # rewrite stubbornly returns the SAME duplicate text every pass
    monkeypatch.setattr(dedupe, "_rewrite_scene",
                        lambda scene, ban, role, ctx, log: "The cathedral dominates the skyline.")
    scenes = [_scene(1, "The cathedral dominates the skyline."),
              _scene(2, "The cathedral dominates the skyline.")]
    warnings = []
    report = dedupe.dedupe_scenes(scenes, {}, {1: "cold_open", 2: "twist"},
                                  log=lambda m: warnings.append(m))
    assert len(scenes) == 2          # never drops a scene, never raises
    assert report["unresolved"] == 1
    assert any("still duplicated" in w for w in warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_dedupe.py -k dedupe_scenes -v`
Expected: FAIL with `AttributeError: module 'art_pipeline.dedupe' has no attribute 'dedupe_scenes'`

- [ ] **Step 3: Implement rewrite + orchestrator**

Append to `art_pipeline/dedupe.py`:

```python
_REWRITE_SYSTEM = (
    "You rewrite ONE sentence of an art-history narration so it says something "
    "NEW. Neutral, precise, second-person where natural. No hype, no CTA. "
    "Respond with STRICT JSON only: {\"text\": \"...\"}")


def _rewrite_scene(scene, ban: list[str], role: str, ctx: dict, log) -> str:
    """Ask the text LLM for a fresh sentence for this scene's slot. Returns the
    new text, or the original text if the model fails (caller re-checks)."""
    original = _text(scene)
    n_words = len(original.split())
    lo, hi = max(6, int(n_words * 0.8)), min(ART_LF_SCENE_MAX_WORDS, int(n_words * 1.2) + 1)
    role_hint = ("describe a NEW visual detail of the painting"
                 if role in ("cold_open", "evidence")
                 else "make a NEW interpretive or historical point — do not describe appearance")
    user = (
        f"This sentence repeats something already said and must be replaced:\n"
        f"  \"{original}\"\n"
        f"Write a replacement of {lo}-{hi} words that fits this chapter (role: "
        f"{role}); {role_hint}. It MUST NOT restate any of these already-said "
        f"lines:\n" + "\n".join(f"- {b}" for b in ban[:40]) +
        f"\n\nArtwork title: {ctx.get('title', '')}. "
        'Return JSON: {"text": "..."}')
    try:
        raw, _ = call_with_chain(system=_REWRITE_SYSTEM, user=user,
                                 models=CREATIVE_LLM_MODELS, max_tokens=300,
                                 progress=log, label="art-lf-dedup",
                                 validator=lambda c: extract_json(c) is not None)
        data = extract_json(raw) or {}
        new = str(data.get("text") or "").strip()
        return new or original
    except Exception as exc:                       # never let one rewrite kill the run
        log(f"[dedupe] rewrite failed ({exc}) — keeping original")
        return original


def _apply_text(scene, new_text: str) -> None:
    """Mutate a Scene (or dict) in place with new text + derived fields."""
    wc = len(new_text.split())
    secs = round(wc / ART_WORDS_PER_SEC, 2)
    conn = _starts_with_connective(new_text)
    if isinstance(scene, dict):
        scene.update(text=new_text, word_count=wc, target_seconds=secs, connective=conn)
    else:
        scene.text = new_text
        scene.word_count = wc
        scene.target_seconds = secs
        scene.connective = conn


def dedupe_scenes(scenes, ctx: dict, roles_by_sid: dict, *,
                  threshold: float = ART_LF_DEDUP_THRESHOLD,
                  max_passes: int = ART_LF_DEDUP_MAX_PASSES, log=print) -> dict:
    """Detect near-duplicate scenes and surgically rewrite each later occurrence.
    Mutates `scenes` in place. Never drops a scene, never raises. Returns a report
    {rewrites, unresolved, max_similarity_after}."""
    rewrites = 0
    for _pass in range(max_passes):
        dups = find_near_duplicates(scenes, threshold)
        if not dups:
            break
        for later, _earlier, _sim in dups:
            sc = scenes[later]
            sid = sc["scene_id"] if isinstance(sc, dict) else sc.scene_id
            ban = [_text(s) for k, s in enumerate(scenes) if k != later]
            new = _rewrite_scene(sc, ban, roles_by_sid.get(sid, "middle"), ctx, log)
            if " ".join(new.lower().split()) != " ".join(_text(sc).lower().split()):
                _apply_text(sc, new)
                rewrites += 1
                log(f"[dedupe] rewrote scene {sid} (was a near-duplicate)")
    remaining = find_near_duplicates(scenes, threshold)
    for later, earlier, sim in remaining:
        sid = scenes[later]["scene_id"] if isinstance(scenes[later], dict) else scenes[later].scene_id
        log(f"[dedupe] scene {sid} still duplicated after {max_passes} passes "
            f"(sim {sim:.2f}) — keeping best")
    max_after = max((s for _, _, s in remaining), default=0.0)
    return {"rewrites": rewrites, "unresolved": len(remaining),
            "max_similarity_after": round(max_after, 3)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_dedupe.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add art_pipeline/dedupe.py tests/art/test_dedupe.py
git commit -m "feat(art): surgical rewrite + dedupe_scenes orchestrator"
```

---

## Task 4: Wire dedupe into write_longform_narration

**Files:**
- Modify: `art_pipeline/narrate_longform.py:414-425` (after the redraw loop, before `validate_cross_chapter`)
- Test: `tests/art/test_narrate_longform.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/art/test_narrate_longform.py`:

```python
def test_dedupe_called_after_draw_loop(monkeypatch, tmp_path):
    """write_longform_narration runs the dedupe guard on the assembled scenes
    and writes repetition_report.json."""
    import art_pipeline.narrate_longform as nlf
    from stages.stage_3.schema import Scene

    captured = {}

    def fake_dedupe(scenes, ctx, roles, **kw):
        captured["n"] = len(scenes)
        return {"rewrites": 0, "unresolved": 0, "max_similarity_after": 0.0}

    monkeypatch.setattr(nlf, "dedupe_scenes", fake_dedupe)
    # Drive the helper directly with a stub all_scenes via a thin wrapper:
    rep = nlf._run_dedupe(
        [Scene(scene_id=1, text="x y z", page_ref=1, panel_ref=0, word_count=3,
               target_seconds=1.0, connective=False, beat_id=1,
               is_intro=False, is_outro=False)],
        {"title": "T"},
        [{"chapter_id": 1, "role": "cold_open", "scene_ids": [1]}],
        tmp_path, log=lambda m: None)
    assert captured["n"] == 1
    assert (tmp_path / "repetition_report.json").exists()
    assert rep["rewrites"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_narrate_longform.py::test_dedupe_called_after_draw_loop -v`
Expected: FAIL with `AttributeError: module 'art_pipeline.narrate_longform' has no attribute '_run_dedupe'`

- [ ] **Step 3: Add the helper and call it**

In `art_pipeline/narrate_longform.py`, add the import near the top (after line 19's `from .narrate import ...`):

```python
from .dedupe import dedupe_scenes
```

Add this helper just above `write_longform_narration` (before line 309):

```python
def _run_dedupe(all_scenes, ctx, chapters_meta, root, *, log=print) -> dict:
    """Run the cross-scene anti-repetition guard on the full ordered scene list
    and persist a per-project report. Kept as a seam so it is unit-testable
    without driving the whole writer."""
    roles_by_sid = {sid: cm["role"] for cm in chapters_meta for sid in cm["scene_ids"]}
    report = dedupe_scenes(all_scenes, ctx, roles_by_sid, log=log)
    (root / "repetition_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False))
    log(f"[narrate-lf] dedupe: {report['rewrites']} rewrite(s), "
        f"max cross-scene sim now {report['max_similarity_after']}")
    return report
```

Then in `write_longform_narration`, between the redraw `for/else` (ends line 422)
and `validate_cross_chapter` (line 424), insert:

```python
    _run_dedupe(all_scenes, ctx, chapters_meta, root, log=log)
    total_words = sum(sc.word_count for sc in all_scenes)  # rewrites may shift it
```

(The existing `validate_cross_chapter(...)` and everything after stays as-is.
`total_words` is recomputed so `narration.total_word_count` /
`estimated_duration_seconds` reflect the rewritten scenes.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_narrate_longform.py::test_dedupe_called_after_draw_loop -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add art_pipeline/narrate_longform.py tests/art/test_narrate_longform.py
git commit -m "feat(art): run dedupe guard after the long-form draw loop"
```

---

## Task 5: Prompt-level prevention — prior-sentence ledger + role budget

**Files:**
- Modify: `art_pipeline/narrate_longform.py` (new helpers + prompt wiring in `write_longform_narration`)
- Test: `tests/art/test_narrate_longform.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/art/test_narrate_longform.py`:

```python
def test_role_budget_text():
    import art_pipeline.narrate_longform as nlf
    assert "MAY describe" in nlf._role_budget("cold_open")
    assert "MAY describe" in nlf._role_budget("evidence")
    # interpretive chapters must NOT re-catalog appearance
    assert "do NOT" in nlf._role_budget("twist").lower() or \
           "not catalog" in nlf._role_budget("twist").lower()
    assert "do NOT" in nlf._role_budget("resolution").lower() or \
           "not catalog" in nlf._role_budget("resolution").lower()


def test_said_block_truncates_and_lists():
    import art_pipeline.narrate_longform as nlf
    lines = [f"sentence number {i}" for i in range(100)]
    block = nlf._said_block(lines, limit=10)
    assert "sentence number 99" in block          # keeps the most recent
    assert "sentence number 89" in block
    assert "sentence number 50" not in block       # older ones dropped
    assert nlf._said_block([], limit=10) == ""      # empty → empty block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_narrate_longform.py -k "role_budget or said_block" -v`
Expected: FAIL with `AttributeError: ... has no attribute '_role_budget'`

- [ ] **Step 3: Add helpers + wire into the prompt**

In `art_pipeline/narrate_longform.py`, add the config import for the cap — extend
the `from .config import (...)` block (lines 20-25) to also import
`ART_LF_SAID_LINES_MAX`:

```python
from .config import (
    ART_LF_CHAPTER_WORDS_BAND, ART_LF_REGION_REUSE_WINDOW, ART_LF_REHOOK_POSITIONS,
    ART_LF_SAID_LINES_MAX, ART_LF_SCENE_MAX_WORDS, ART_LF_SCENES_PER_CHAPTER_MAX,
    ART_LF_SCENES_PER_CHAPTER_MIN, ART_LF_TOTAL_WORDS_FLOOR, ART_WORDS_PER_SEC,
    get_art_project_path,
)
```

Add the two helpers just below `_has_cta` (after line 48):

```python
def _role_budget(role: str) -> str:
    """Description budget by chapter role: only the cold_open and the evidence
    chapter may catalog the painting's appearance; interpretive chapters must
    reference features to build meaning, not re-describe them."""
    if role in ("cold_open", "evidence"):
        return ("DESCRIPTION BUDGET: You MAY describe the painting's visual "
                "appearance in this chapter.")
    return ("DESCRIPTION BUDGET: Do NOT catalog the painting's appearance — it "
            "has been described already. Reference a feature only to make a new "
            "interpretive or historical point.")


def _said_block(said_lines: list[str], *, limit: int = ART_LF_SAID_LINES_MAX) -> str:
    """The most-recent `limit` already-narrated sentences, as a bullet block for
    the prompt. Empty list → empty string (chapter 1 has nothing prior)."""
    recent = said_lines[-limit:]
    if not recent:
        return ""
    return ("ALREADY NARRATED (do NOT restate any of these — add only NEW "
            "information):\n" + "\n".join(f"- {s}" for s in recent))
```

In `write_longform_narration`, add a `said_lines` accumulator next to
`used_subjects` (line 330) inside the `for draw` loop:

```python
            used_subjects: list[str] = []
            said_lines: list[str] = []
```

In the `user` prompt string (lines 353-372), add the role budget + said block
right after the `PREVIOUS CHAPTER ENDED WITH:` line (line 364). Replace:

```python
                f"PREVIOUS CHAPTER ENDED WITH: {prev_tail or '(video start)'}\n\n"
```

with:

```python
                f"PREVIOUS CHAPTER ENDED WITH: {prev_tail or '(video start)'}\n\n"
                f"{_role_budget(ch['role'])}\n\n"
                + (_said_block(said_lines) + "\n\n" if said_lines else "")
                +
```

Accumulate the sentences after each chapter — next to the `used_subjects +=`
update (after line 403):

```python
            said_lines += [sc.text for sc in scenes]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_narrate_longform.py -k "role_budget or said_block" -v`
Expected: PASS

- [ ] **Step 5: Run the full long-form narration test module (no regressions)**

Run: `.venv/bin/python3 -m pytest tests/art/test_narrate_longform.py -q`
Expected: PASS (all existing + new tests)

- [ ] **Step 6: Commit**

```bash
git add art_pipeline/narrate_longform.py tests/art/test_narrate_longform.py
git commit -m "feat(art): prior-sentence ledger + role budget in chapter prompt"
```

---

## Task 6: longform_tts — widen inter-chapter silence when cards are on

**Files:**
- Modify: `art_pipeline/longform_tts.py` (gap selection at lines 14, 84)
- Test: `tests/art/test_longform_tts.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/art/test_longform_tts.py`:

```python
def test_inter_chapter_gap_follows_card_flag(monkeypatch):
    import art_pipeline.longform_tts as lftts
    from art_pipeline import config as C
    monkeypatch.setattr(C, "ART_LF_CHAPTER_CARDS", True)
    monkeypatch.setattr(C, "ART_LF_CHAPTER_CARD_SEC", 2.6)
    monkeypatch.setattr(C, "ART_LF_CHAPTER_GAP_S", 1.0)
    assert lftts._inter_chapter_gap_s() == 2.6
    monkeypatch.setattr(C, "ART_LF_CHAPTER_CARDS", False)
    assert lftts._inter_chapter_gap_s() == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_longform_tts.py::test_inter_chapter_gap_follows_card_flag -v`
Expected: FAIL with `AttributeError: module 'art_pipeline.longform_tts' has no attribute '_inter_chapter_gap_s'`

- [ ] **Step 3: Add the gap helper and use it**

In `art_pipeline/longform_tts.py`, add the helper near the top (after the
`_chapter_dir` function, around line 25):

```python
def _inter_chapter_gap_s() -> float:
    """Silence between chapters. When chapter cards are on, widen the gap to fit
    the card (it sits entirely inside this silence → no A/V drift); otherwise the
    plain micro-pause."""
    return (C.ART_LF_CHAPTER_CARD_SEC if C.ART_LF_CHAPTER_CARDS
            else C.ART_LF_CHAPTER_GAP_S)
```

Then change line 84 from:

```python
    gap_frames = int(round(ART_LF_CHAPTER_GAP_S * framerate))
```

to:

```python
    gap_frames = int(round(_inter_chapter_gap_s() * framerate))
```

And update the log line (122) to report the real gap:

```python
        f"({total:.1f}s, gap {_inter_chapter_gap_s()}s)")
```

(`C` is already imported in this module; `ART_LF_CHAPTER_GAP_S` stays imported for
the fallback path.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_longform_tts.py::test_inter_chapter_gap_follows_card_flag -v`
Expected: PASS

- [ ] **Step 5: Run the tts test module (no regressions)**

Run: `.venv/bin/python3 -m pytest tests/art/test_longform_tts.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add art_pipeline/longform_tts.py tests/art/test_longform_tts.py
git commit -m "feat(art): widen inter-chapter silence to host the title card"
```

---

## Task 7: assemble — render a chapter title card (ffmpeg drawtext)

**Files:**
- Modify: `art_pipeline/assemble.py` (new `_render_chapter_card`)
- Test: `tests/art/test_assemble.py`

The card is a single PNG: solid `ART_CARD_BG`, a gold `CHAPTER N` kicker above the
title in white Anton. The title is written to a sidecar `.txt` and passed via
`drawtext=textfile=` so apostrophes (e.g. "The City That Isn't There") need no
escaping.

- [ ] **Step 1: Write the failing test**

Add to `tests/art/test_assemble.py`:

```python
import shutil
import subprocess


def test_render_chapter_card(tmp_path):
    import art_pipeline.assemble as A
    out = tmp_path / "card.png"
    A._render_chapter_card(2, "The City That Isn't There", out, w=640, h=360)
    assert out.exists() and out.stat().st_size > 0
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    if shutil.which(ffprobe) or os.path.exists(ffprobe):
        dims = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
            capture_output=True, text=True).stdout.strip()
        assert dims == "640,360"
```

(Add `import os` to the test file imports if not already present.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_assemble.py::test_render_chapter_card -v`
Expected: FAIL with `AttributeError: module 'art_pipeline.assemble' has no attribute '_render_chapter_card'`

- [ ] **Step 3: Implement the renderer**

In `art_pipeline/assemble.py`, add after `_apply_film_look` (after line 388):

```python
def _render_chapter_card(chapter_id: int, title: str, out_png: Path, *,
                         w: int | None = None, h: int | None = None) -> None:
    """Render a full-screen title card PNG: solid ART_CARD_BG with a gold
    'CHAPTER N' kicker above the chapter title in white Anton. The title goes
    through a sidecar textfile so apostrophes/colons need no drawtext escaping."""
    import stages.stage_5.shots as shots
    ff = _resolve_ffmpeg()
    w = w or shots.OUTPUT_W
    h = h or shots.OUTPUT_H
    title_txt = out_png.with_suffix(".title.txt")
    title_txt.write_text(title)
    kicker_fs = max(12, int(h * 0.05))
    title_fs = max(20, int(h * 0.085))
    font = C.ART_CARD_FONT.replace(":", r"\:")
    vf = (
        f"drawtext=fontfile='{font}':text='CHAPTER {chapter_id}':"
        f"fontcolor={C.ART_CARD_ACCENT}:fontsize={kicker_fs}:"
        f"x=(w-text_w)/2:y=h*0.40,"
        f"drawtext=fontfile='{font}':textfile='{title_txt}':"
        f"fontcolor=white:fontsize={title_fs}:x=(w-text_w)/2:y=h*0.48"
    )
    cmd = [ff, "-y", "-f", "lavfi", "-i", f"color=c={C.ART_CARD_BG}:s={w}x{h}",
           "-frames:v", "1", "-vf", vf, str(out_png)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    title_txt.unlink(missing_ok=True)
    if res.returncode != 0:
        raise RuntimeError(f"chapter-card render failed: {res.stderr[-600:]}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_assemble.py::test_render_chapter_card -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add art_pipeline/assemble.py tests/art/test_assemble.py
git commit -m "feat(art): render full-screen chapter title card PNG"
```

---

## Task 8: assemble — card windows + overlay filtergraph (pure)

**Files:**
- Modify: `art_pipeline/assemble.py` (`_card_windows`, `_build_card_filtergraph`)
- Test: `tests/art/test_assemble.py`

These two pure functions hold the logic worth testing: WHERE each card goes
(never before chapter 1) and HOW the ffmpeg filtergraph is shaped. The ffmpeg
invocation itself is wired in Task 9.

- [ ] **Step 1: Write the failing test**

Add to `tests/art/test_assemble.py`:

```python
def test_card_windows_skip_chapter_one():
    import art_pipeline.assemble as A
    chapters = [
        {"chapter_id": 1, "title": "One", "scene_ids": [1, 2, 3]},
        {"chapter_id": 2, "title": "Two", "scene_ids": [4, 5]},
        {"chapter_id": 3, "title": "Three", "scene_ids": [6, 7]},
    ]
    timings = [
        {"scene_id": 1, "start": 0.0, "end": 5.0},
        {"scene_id": 2, "start": 5.0, "end": 9.0},
        {"scene_id": 3, "start": 9.0, "end": 12.0},
        {"scene_id": 4, "start": 14.6, "end": 18.0},   # 2.6s gap after scene 3
        {"scene_id": 5, "start": 18.0, "end": 21.0},
        {"scene_id": 6, "start": 23.6, "end": 27.0},
        {"scene_id": 7, "start": 27.0, "end": 30.0},
    ]
    wins = A._card_windows(chapters, timings)
    assert [w["chapter_id"] for w in wins] == [2, 3]   # no card before ch1
    assert wins[0]["t0"] == 12.0 and wins[0]["t1"] == 14.6
    assert wins[1]["title"] == "Three"


def test_build_card_filtergraph_chains_overlays():
    import art_pipeline.assemble as A
    wins = [{"chapter_id": 2, "title": "Two", "t0": 12.0, "t1": 14.6},
            {"chapter_id": 3, "title": "Three", "t0": 23.6, "t1": 26.2}]
    fg, final_label = A._build_card_filtergraph(wins, fade=0.5)
    assert final_label == "[v2]"             # one label per overlaid card
    assert fg.count("overlay=") == 2
    assert "between(t,12.000,14.600)" in fg
    assert "alpha=1" in fg                    # cards fade via alpha
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_assemble.py -k "card_windows or filtergraph" -v`
Expected: FAIL with `AttributeError: ... has no attribute '_card_windows'`

- [ ] **Step 3: Implement the pure helpers**

In `art_pipeline/assemble.py`, add after `_render_chapter_card`:

```python
def _card_windows(chapters: list[dict], scene_timings: list[dict]) -> list[dict]:
    """The silent window before each chapter 2..N: [prev chapter's last-scene
    end, this chapter's first-scene start]. Chapter 1 gets no card (the hook
    opens immediately). Skips a boundary whose window is non-positive."""
    by_id = {int(t["scene_id"]): t for t in scene_timings or []}
    wins: list[dict] = []
    for prev, cur in zip(chapters, chapters[1:]):
        if not prev.get("scene_ids") or not cur.get("scene_ids"):
            continue
        last, first = prev["scene_ids"][-1], cur["scene_ids"][0]
        if last not in by_id or first not in by_id:
            continue
        t0, t1 = float(by_id[last]["end"]), float(by_id[first]["start"])
        if t1 > t0:
            wins.append({"chapter_id": cur["chapter_id"], "title": cur["title"],
                         "t0": round(t0, 3), "t1": round(t1, 3)})
    return wins


def _build_card_filtergraph(windows: list[dict], *, fade: float) -> tuple[str, str]:
    """Build the filter_complex that overlays each card (inputs [1:v], [2:v], …)
    onto the base video [0:v], each gated to its window with an alpha fade
    0→1→0. Returns (filtergraph, final_video_label). Card input k corresponds to
    windows[k-1]."""
    parts: list[str] = []
    base = "[0:v]"
    for k, win in enumerate(windows, start=1):
        t0, t1 = win["t0"], win["t1"]
        dur = max(0.0, t1 - t0)
        f = min(fade, dur / 2) if dur else 0.0
        # card input k: fade alpha in/out, shift its PTS to start at t0
        parts.append(
            f"[{k}:v]format=yuva420p,"
            f"fade=t=in:st=0:d={f:.3f}:alpha=1,"
            f"fade=t=out:st={max(0.0, dur - f):.3f}:d={f:.3f}:alpha=1,"
            f"setpts=PTS-STARTPTS+{t0:.3f}/TB[c{k}]")
        out_lab = f"[v{k}]"
        parts.append(f"{base}[c{k}]overlay=enable='between(t,{t0:.3f},{t1:.3f})'{out_lab}")
        base = out_lab
    return ";".join(parts), base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_assemble.py -k "card_windows or filtergraph" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add art_pipeline/assemble.py tests/art/test_assemble.py
git commit -m "feat(art): chapter-card window + overlay filtergraph builders"
```

---

## Task 9: assemble — overlay cards into the final video (wired)

**Files:**
- Modify: `art_pipeline/assemble.py` (`_overlay_chapter_cards` + call in `assemble_art_video`)
- Test: `tests/art/test_assemble.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/art/test_assemble.py` (builds a tiny silent video with ffmpeg, then
asserts the overlay preserves duration exactly):

```python
def _make_silent(ff, path, seconds, w=320, h=180):
    subprocess.run([ff, "-y", "-f", "lavfi", "-i",
                    f"color=c=gray:s={w}x{h}:d={seconds}", "-r", "25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   capture_output=True, text=True, check=True)


def test_overlay_chapter_cards_preserves_duration(tmp_path):
    import art_pipeline.assemble as A
    from stages.stage_5.pipeline import _probe_duration
    ff = A._resolve_ffmpeg()
    silent = tmp_path / "video_silent.mp4"
    _make_silent(ff, silent, 12.0)
    before = _probe_duration(silent)
    chapters = [{"chapter_id": 1, "title": "One", "scene_ids": [1, 2]},
                {"chapter_id": 2, "title": "Two", "scene_ids": [3, 4]}]
    timings = [{"scene_id": 1, "start": 0.0, "end": 3.0},
               {"scene_id": 2, "start": 3.0, "end": 5.0},
               {"scene_id": 3, "start": 7.6, "end": 10.0},
               {"scene_id": 4, "start": 10.0, "end": 12.0}]
    A._overlay_chapter_cards(silent, chapters, timings, w=320, h=180, log=lambda m: None)
    after = _probe_duration(silent)
    assert abs(after - before) < 0.15      # zero drift (within one frame)


def test_overlay_no_op_without_boundaries(tmp_path):
    import art_pipeline.assemble as A
    from stages.stage_5.pipeline import _probe_duration
    ff = A._resolve_ffmpeg()
    silent = tmp_path / "v.mp4"
    _make_silent(ff, silent, 4.0)
    before = _probe_duration(silent)
    # single chapter → no boundary → file untouched
    A._overlay_chapter_cards(silent, [{"chapter_id": 1, "title": "Solo",
                                       "scene_ids": [1]}], [{"scene_id": 1,
                                       "start": 0.0, "end": 4.0}], log=lambda m: None)
    assert abs(_probe_duration(silent) - before) < 0.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_assemble.py -k overlay_chapter -v`
Expected: FAIL with `AttributeError: ... has no attribute '_overlay_chapter_cards'`

- [ ] **Step 3: Implement the overlay orchestrator + wire it in**

In `art_pipeline/assemble.py`, add after `_build_card_filtergraph`:

```python
def _overlay_chapter_cards(silent_video: Path, chapters: list[dict],
                           scene_timings: list[dict], *, w: int | None = None,
                           h: int | None = None, log=print) -> None:
    """Render a card per chapter boundary and overlay them onto the silent video
    inside their silent windows (opaque, alpha-faded). Total duration is
    unchanged — the cards live in silence the audio already contains, so A/V
    sync is preserved. No-op when there are no boundaries."""
    windows = _card_windows(chapters, scene_timings)
    if not windows:
        return
    import stages.stage_5.shots as shots
    w = w or shots.OUTPUT_W
    h = h or shots.OUTPUT_H
    ff = _resolve_ffmpeg()
    card_dir = silent_video.parent / "_chapter_cards"
    card_dir.mkdir(exist_ok=True)
    card_pngs: list[Path] = []
    for win in windows:
        png = card_dir / f"card_{win['chapter_id']:02d}.png"
        _render_chapter_card(win["chapter_id"], win["title"], png, w=w, h=h)
        card_pngs.append(png)
    fg, final_label = _build_card_filtergraph(windows, fade=0.5)
    inputs = ["-i", str(silent_video)]
    for win, png in zip(windows, card_pngs):
        dur = win["t1"] - win["t0"]
        inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(png)]
    out = silent_video.with_suffix(".carded.mp4")
    cmd = [ff, "-y", *inputs, "-filter_complex", fg, "-map", final_label,
           "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-preset", "medium", "-an", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"chapter-card overlay failed: {res.stderr[-700:]}")
    out.replace(silent_video)
    log(f"[assemble] overlaid {len(windows)} chapter card(s)")
```

Then in `assemble_art_video`, just after the `_apply_film_look` block (after
line 463) and before the `captions = root / "captions.ass"` line (line 464),
insert:

```python
    if longform and C.ART_LF_CHAPTER_CARDS:
        chapters_meta = json.loads((root / "chapters.json").read_text())
        _overlay_chapter_cards(silent, chapters_meta, scene_timings, log=log)
```

NOTE: `longform` is defined at line 465 (`longform = (root / "chapters.json").exists()`).
Move that one line up so it is computed BEFORE this new block — i.e. relocate
`longform = (root / "chapters.json").exists()` to just before the new `if longform`
block, and delete the later duplicate assignment.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_assemble.py -k overlay_chapter -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the whole assemble module (no geometry regressions)**

Run: `.venv/bin/python3 -m pytest tests/art/test_assemble.py -q`
Expected: PASS (existing geometry tests under the autouse `_no_polish` fixture + new card tests)

- [ ] **Step 6: Commit**

```bash
git add art_pipeline/assemble.py tests/art/test_assemble.py
git commit -m "feat(art): overlay chapter title cards inside inter-chapter silence"
```

---

## Task 10: hunt — named-artwork ↔ resolved-image guard (Part 3)

**Files:**
- Modify: `art_pipeline/hunt.py` (new `_image_matches_named_artwork`, gate at line 319) + writer subject rule in `narrate_longform.py:_LF_SYSTEM`
- Test: `tests/art/test_hunt.py` (create if absent)

A `related` scene whose `subject` names a specific artwork (Title-Case
possessive/`by` pattern, e.g. "Vincent van Gogh's The Starry Night") must resolve
to an image whose title shares the core title tokens; otherwise reject → fallback
to a region of the primary painting (never show a wrongly-named work).

- [ ] **Step 1: Write the failing test**

Create `tests/art/test_hunt.py` (or add to it):

```python
"""Tests for art_pipeline.hunt — named-artwork ↔ resolved-image guard."""
import art_pipeline.hunt as hunt


def test_named_artwork_subject_detected():
    assert hunt._named_artwork("Vincent van Gogh's The Starry Night") == "the starry night"
    assert hunt._named_artwork("The Fighting Temeraire by J. M. W. Turner") == "the fighting temeraire"
    # generic subjects are NOT named artworks → no gating
    assert hunt._named_artwork("a portrait of the artist") is None
    assert hunt._named_artwork("Toledo cathedral interior") is None


def test_image_matches_named_artwork():
    # subject names Starry Night, resolved image is Turner → REJECT
    assert hunt._image_matches_named_artwork(
        "Vincent van Gogh's The Starry Night", "The Fighting Temeraire") is False
    # resolved title contains the work → ACCEPT
    assert hunt._image_matches_named_artwork(
        "Vincent van Gogh's The Starry Night",
        "The Starry Night, Vincent van Gogh, 1889") is True
    # generic subject → always accept (guard does not fire)
    assert hunt._image_matches_named_artwork(
        "a portrait of the artist", "El Greco self-portrait") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/art/test_hunt.py -v`
Expected: FAIL with `AttributeError: module 'art_pipeline.hunt' has no attribute '_named_artwork'`

- [ ] **Step 3: Implement the guard**

In `art_pipeline/hunt.py`, add near the top (after the imports, before `_ua_for`):

```python
import re

# A subject "names a specific artwork" when it carries a possessive/by-attributed
# Title-Case work, e.g. "Van Gogh's The Starry Night" / "The Fighting Temeraire by
# Turner". Best-effort (documented in the spec) — it targets the prominent, quoted
# titles that caused the Toledo mismatch, not arbitrary art-title NER.
_NAMED_ART_RE = re.compile(
    r"(?:'s|by)\s+((?:The\s+)?[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){1,5})")


def _named_artwork(subject: str) -> str | None:
    """Return the lowercased artwork title named in `subject`, or None if the
    subject is generic. 'by <Title>' / "<Artist>'s <Title>" patterns only."""
    m = _NAMED_ART_RE.search(subject or "")
    return " ".join(m.group(1).lower().split()) if m else None


def _image_matches_named_artwork(subject: str, resolved_title: str) -> bool:
    """True = accept the image. If `subject` names a specific artwork, require the
    resolved image title to contain that work's core tokens (≥80% overlap, the
    fact_is_grounded style). Generic subjects always pass (guard does not fire)."""
    work = _named_artwork(subject)
    if not work:
        return True
    title_tokens = set(re.findall(r"[a-z0-9]+", (resolved_title or "").lower()))
    work_tokens = [t for t in re.findall(r"[a-z0-9]+", work)
                   if t not in ("the", "a", "of")]
    if not work_tokens:
        return True
    hit = sum(1 for t in work_tokens if t in title_tokens)
    return hit / len(work_tokens) >= 0.8
```

Then gate the resolved image. In `hunt_visuals`, change the acceptance condition
at line 319 from:

```python
        if dims:
```

to:

```python
        if dims and _image_matches_named_artwork(
                str(d.get("subject") or ""), str((c or {}).get("title") or "")):
```

and, so the rejection reason is accurate, change the fallback `reason` expression
(lines 344-347) to add the mismatch case at the front:

```python
        reason = ("named-artwork mismatch" if (dims and c) else
                  "no SDK candidate" if not c else
                  "duplicate image" if not attempted else
                  "download/size reject (both candidates)" if len(attempted) > 1 else
                  "download/size reject")
```

(When the image downloaded fine but names the wrong artwork, `dims` is truthy and
`c` is set → "named-artwork mismatch", and control falls through to the existing
region/full fallback that shows the primary painting.)

- [ ] **Step 4: Add the writer subject rule**

In `art_pipeline/narrate_longform.py`, extend `_LF_SYSTEM` rule 3's `related`
bullet (line 67-68) so the writer ties the subject to a named work. Replace:

```python
   - {{"kind": "related", "subject": "<concrete searchable image>"}} — artist,
     era, place, technique, x-ray. Aim for roughly 30% related scenes.
```

with:

```python
   - {{"kind": "related", "subject": "<concrete searchable image>"}} — artist,
     era, place, technique, x-ray. Aim for roughly 30% related scenes. If the
     scene's TEXT names a specific artwork (e.g. "Van Gogh's The Starry Night"),
     the subject MUST be that exact artwork title — never a different work.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python3 -m pytest tests/art/test_hunt.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add art_pipeline/hunt.py art_pipeline/narrate_longform.py tests/art/test_hunt.py
git commit -m "feat(art): guard against named-artwork/image mismatch (Starry Night)"
```

---

## Task 11: Full art test suite + final review gate

**Files:** none (verification only)

- [ ] **Step 1: Run the whole art suite**

Run: `.venv/bin/python3 -m pytest tests/art/ -q`
Expected: PASS — all prior tests (≈178) plus the new dedupe/card/hunt tests, zero failures.

- [ ] **Step 2: Confirm comic code untouched**

Run: `git diff --name-only main...HEAD -- stages/ ui/ config.py`
Expected: EMPTY output (no comic files changed on this branch beyond the pre-existing, already-committed unify edit).

- [ ] **Step 3: Dispatch the final whole-implementation code review** (handled by the executing skill).

---

## Re-render Toledo (after all tasks pass)

Per the project rule (run from scratch, keep `raw_art/`), regenerate the Toledo
long-form end-to-end so the fixes land in the actual video:

Confirmed CLI subcommands (`art_pipeline/cli.py`): `narrate`, `hunt`, `tts`,
`video` (the assembler), all taking the project name as a positional arg. Our
changes touch narrate→hunt→tts→video, so re-run from `narrate`:

```bash
cd "/Users/nhan/Documents/Mac home project/comic-book-pipeline"
.venv/bin/python3 -m art_pipeline.cli narrate toledo-longform
.venv/bin/python3 -m art_pipeline.cli hunt    toledo-longform --force
.venv/bin/python3 -m art_pipeline.cli tts     toledo-longform --force
.venv/bin/python3 -m art_pipeline.cli video   toledo-longform --force
```

(For a full from-scratch run keeping only `raw_art/`, also re-run `regions`,
`ground`, `outline` before `narrate`.)

Then verify: `repetition_report.json` shows `max_similarity_after < 0.86` and
`rewrites`/`unresolved` counts; spot-check `final.mp4` at each chapter boundary
(cards at ~02:xx, 04:xx, 06:xx, 08:xx) and confirm any artwork named in narration
shows the correct work.

---

## Self-review notes (author)

- **Spec coverage:** Part 1a → Task 5; Part 1b → Tasks 2-4; Part 2a → Task 6;
  Part 2b → Tasks 7-9; Part 3 → Task 10. Benchmark: the spec named the shared
  `research/reports/_BENCHMARK_thresholds.json`, but that file has uncommitted
  parallel-session changes; to avoid clobbering it the plan writes a per-project
  `repetition_report.json` instead (Task 4). Flag for the reviewer.
- **Signatures consistent:** `find_near_duplicates(scenes, threshold)`,
  `dedupe_scenes(scenes, ctx, roles_by_sid, *, threshold, max_passes, log)`,
  `_run_dedupe(all_scenes, ctx, chapters_meta, root, *, log)`,
  `_render_chapter_card(chapter_id, title, out_png, *, w, h)`,
  `_card_windows(chapters, scene_timings)`,
  `_build_card_filtergraph(windows, *, fade)`,
  `_overlay_chapter_cards(silent_video, chapters, scene_timings, *, w, h, log)`,
  `_named_artwork(subject)`, `_image_matches_named_artwork(subject, title)` — all
  used consistently across tasks/tests.
- **READ-ONLY honored:** every Modify/Create path is under `art_pipeline/` or
  `tests/art/`. No `stages/`, `ui/`, or root `config.py` edits.
