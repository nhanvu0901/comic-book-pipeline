# Pipeline v5 — VLM-enhanced Magi clusters + hybrid panel scoring

**Date**: 2026-05-24
**Author**: Claude (with user direction)
**Status**: Approved by user, ready for implementation
**Approach**: Hybrid B + D + G — dialog priority + Magi character clustering + LLM tie-breaker, with VLM enhancing Magi by resolving cluster names

## Context & motivation

After v4 shipped (Hermes 405B writer with 10 voice rules), user reports:
- Narrative voice IS better (storyteller mode, pronouns, concrete actions)
- Still **not 100% follow comic** — some panel choices don't match what scene describes
- Need **better panel selection** specifically

Research deep-dive (MaRU paper, ComicsPAP benchmark, Magi V3, CLIP4Clip) showed:
- Raw CLIP scores 6% top-1 on manga, 32% with panel crop — promising but not great
- ComicsPAP best model (fine-tuned Qwen 2.5-VL-7B) reached 62% — research-level
- Channel narration heavily paraphrases **dialog** → dialog match is strongest pragmatic signal
- Magi V3 outputs **visual character clusters** that persist across panels — key for multi-character disambiguation

User chose hybrid combining 3 options:
- **B** (Dialog priority) — match chunk text against panel's actual speech bubbles
- **D** (Magi character clustering) — use visual identity for character persistence
- **G** (LLM-as-judge) — break ties with context-aware decision

User insight: **VLM enhances Magi** by resolving cluster IDs (cluster_0, cluster_1) → actual character names (Peter Parker, Miles Morales). This solves the "Magi doesn't know who is Batman" limitation.

## Architecture overview

```
STAGE 2 — Enhanced preprocessing
─────────────────────────────────

  Magi V3 FULL pipeline (not just panel-detect):
    ├─ panels bbox
    ├─ character bboxes
    ├─ TEXT bboxes (speech bubbles, narration, sfx)
    ├─ character CLUSTERS (visual identity across panels)
    ├─ text-character associations (who said what)
    ├─ OCR (text content of bubbles)
    └─ reading order

  VLM cluster naming (1 call per cluster, ~5 calls per comic):
    Input: 3 sample crops of cluster_N + comic_context.characters list
    Output: {"name": "Peter Parker", "confidence": "high"}
    Result: cluster_to_name dict saved to project metadata

  Enriched panel JSON includes:
    bbox                    (Magi)
    cluster_ids: [0, 2]     (Magi)
    character_names: [...]  (resolved via cluster_to_name)
    text_blocks: [...]      (Magi OCR + speaker cluster_id)
    description             (VLM, optional for keyword scoring)
    dominant_emotion        (VLM)
    bbox_area               (computed from bbox)


STAGE 5 — Hybrid scoring
─────────────────────────

  For each caption_chunk:

    Step 1: HEURISTIC SCORING (vectorized, fast)
      score = 
        +5.0 × dialog_word_overlap         ← B (highest weight)
        +3.0 × cluster_id_overlap          ← D (replaces name-string match)
        +2.0 × emotion_match               ← F
        +1.5 × prev_shot_char_overlap      ← E
        +1.0 × keyword_overlap             ← current
        +0.5 × log(panel_area)             ← C
    
    Step 2: SORT candidates by score
    
    Step 3: TIE-BREAK CHECK
      if top 2 candidates' score gap < 1.0:
        → call LLM-as-judge with chunk_text + top 5 candidates
        → use LLM's letter pick
      else:
        → pick heuristic top
    
    Step 4: Track used (page, panel_idx) in used_panel_keys
            so subsequent chunks don't repeat same panel
```

## Component changes

### P-1: Magi V3 full pipeline output

**File**: `stages/stage_2/panel_detect.py` (extend existing)

Magi V3 has a unified head producing panels + characters + text + associations. Currently we only extract panel bboxes. Need to extract everything.

```python
def detect_full(image_path: Path) -> dict:
    """Extended Magi V3 call returning panels + characters + clusters + text."""
    model, processor = _load_model()
    image = Image.open(image_path).convert("RGB")
    
    # Magi V3 unified call (per Magi project API)
    with torch.no_grad():
        per_page_predictions = model.predict_detections_and_associations([image])
    
    page_result = per_page_predictions[0]
    return {
        "panels": page_result["panels"],          # list of bboxes
        "characters": page_result["characters"],  # list with cluster_id + bbox
        "texts": page_result["texts"],            # list with bbox + ocr + speaker_cluster
        "associations": page_result["associations"],  # text-character links
        "reading_order": page_result["reading_order"],
    }
```

Magi clustering done at PROJECT level (across all pages):

```python
def cluster_characters_across_pages(per_page_chars: list[list[dict]]) -> dict:
    """Group character appearances by visual similarity across all pages.
    Returns cluster_id → list of {page, char_idx} mapping."""
    # Magi exposes character_clusterer for this; aggregate per-character embeddings
    # then run clustering. Output: each character appearance gets cluster_id.
    ...
```

### P-2: VLM cluster naming step

**File**: `stages/stage_2/cluster_namer.py` (NEW)

After Magi clusters detected, run VLM 1x per cluster to resolve name.

```python
def resolve_cluster_names(
    clusters: dict[int, list[dict]],   # cluster_id → appearances
    pages_by_number: dict[int, dict],
    comic_context: dict,
    progress: Callable | None = None,
) -> dict[int, str]:
    """For each cluster, show VLM 3 sample crops + comic context → get character name."""
    cluster_to_name = {}
    known_chars = comic_context.get("characters", [])
    
    for cluster_id, appearances in clusters.items():
        # Pick 3 representative appearances
        sample_appearances = _pick_diverse_samples(appearances, n=3)
        # Crop character bbox from each page
        crops_b64 = [_crop_character(app, pages_by_number) for app in sample_appearances]
        
        prompt = f"""Identify this character.
        
3 sample appearances shown. Comic: "{comic_context.get('title', '')}".
Known characters in story: {known_chars}

Respond JSON: {{"name": "<from list, or 'Unknown'>", "confidence": "high|medium|low"}}
"""
        response = _vlm_call(prompt, crops_b64)
        cluster_to_name[cluster_id] = response.get("name", "Unknown")
    
    return cluster_to_name
```

Wired into `stages/stage_2/pipeline.py` after Magi clustering finishes. Saved to `projects/<name>/cluster_to_name.json`.

### P-3: Stage 5 hybrid scoring

**File**: `stages/stage_5/shots.py` (replace `_score_panel`)

```python
STOPWORDS = {...}  # existing

# Simple emotion lexicon (P-5)
EMOTION_LEXICON = {
    "rage": "angry", "fury": "angry", "outraged": "angry",
    "trapped": "scared", "haunted": "scared", "terrified": "scared",
    "triumphant": "triumph", "victorious": "triumph",
    "grief": "sad", "mourning": "sad", "wept": "sad",
    # ... ~50 entries
}


def _detect_chunk_emotion(chunk_text: str) -> str | None:
    """Map chunk text keywords to one of the dominant_emotion values."""
    words = set(w.lower().strip(",.!?:;") for w in chunk_text.split())
    for word, emotion in EMOTION_LEXICON.items():
        if word in words:
            return emotion
    return None


def _score_panel_hybrid(
    panel: dict,
    chunk_text: str,
    scene: dict,
    chunk_words: set[str],
    cluster_to_name: dict[int, str],
    prev_panel: dict | None,
) -> float:
    score = 0.0
    
    # ─── B: DIALOG OVERLAP (highest signal) ──────────────────────
    panel_dialog_words = set()
    for tb in panel.get("text_blocks", []):
        if tb.get("type") in ("speech", "narration", "caption"):
            for w in tb.get("text", "").lower().split():
                panel_dialog_words.add(w.strip(",.!?:;\"'"))
    panel_dialog_words -= STOPWORDS
    score += 5.0 * len(panel_dialog_words & chunk_words)
    
    # ─── D: CHARACTER CLUSTER MATCH ─────────────────────────────
    # Extract names mentioned in chunk text
    chunk_name_mentions = set()
    for cluster_id, name in cluster_to_name.items():
        if name == "Unknown":
            continue
        # Match by first name OR full name
        first_name = name.split()[0].lower()
        if first_name in chunk_words or name.lower() in chunk_text.lower():
            chunk_name_mentions.add(cluster_id)
    
    panel_clusters = set(panel.get("cluster_ids", []))
    score += 3.0 * len(chunk_name_mentions & panel_clusters)
    
    # ─── F: EMOTION MATCH ───────────────────────────────────────
    chunk_emotion = _detect_chunk_emotion(chunk_text)
    if chunk_emotion and chunk_emotion == panel.get("dominant_emotion", ""):
        score += 2.0
    
    # ─── E: SEQUENCE COHERENCE (with prev shot) ─────────────────
    if prev_panel is not None:
        prev_clusters = set(prev_panel.get("cluster_ids", []))
        score += 1.5 * len(panel_clusters & prev_clusters)
    
    # ─── Current: KEYWORD OVERLAP ────────────────────────────────
    desc_words = {
        w.lower().strip(",.!?:;\"'")
        for w in panel.get("description", "").split()
    } - STOPWORDS
    score += 1.0 * len(desc_words & chunk_words)
    
    # ─── C: VISUAL SALIENCE ─────────────────────────────────────
    bbox = panel.get("bbox", {})
    area = bbox.get("w", 0) * bbox.get("h", 0)
    if area > 100000:
        import math
        score += 0.5 * math.log(area / 100000)
    
    return score
```

### P-4: LLM-as-judge tie-breaker

**File**: `stages/stage_5/shots.py` (new function)

```python
def _llm_judge_tiebreak(
    chunk_text: str,
    top_candidates: list[dict],  # [{panel, source_image, score, page, idx}]
) -> dict:
    """Call LLM to pick best panel among ambiguous top candidates."""
    if len(top_candidates) <= 1:
        return top_candidates[0]
    
    panel_blocks = []
    for i, c in enumerate(top_candidates[:5]):
        panel = c["panel"]
        chars = ", ".join(panel.get("character_names", [])) or "?"
        emotion = panel.get("dominant_emotion", "?")
        desc = panel.get("description", "")[:80]
        dialog = "; ".join(
            f"{tb.get('speaker_name','?')}: {tb.get('text','')[:50]}"
            for tb in panel.get("text_blocks", [])[:3]
        )
        panel_blocks.append(
            f"  {chr(65+i)}. Page {c['page']} panel {c['idx']}\n"
            f"     Characters: {chars}  Emotion: {emotion}\n"
            f"     Visual: {desc}\n"
            f"     Dialog: {dialog}"
        )
    
    prompt = (
        f"Caption text: \"{chunk_text}\"\n\n"
        f"Candidate panels:\n" + "\n".join(panel_blocks) + "\n\n"
        f"Which panel BEST visualizes the caption? Reply ONLY with the letter."
    )
    
    from stages.stage_3._llm import call_with_chain
    raw, _ = call_with_chain(
        system="Pick the panel that best matches the caption. Reply with one letter only.",
        user=prompt,
        max_tokens=10,
        label="panel-judge",
    )
    
    # Parse first letter A-E
    letter = next((c for c in raw.upper() if "A" <= c <= "E"), "A")
    idx = ord(letter) - ord("A")
    return top_candidates[min(idx, len(top_candidates) - 1)]
```

Wire into `_select_panel_for_chunk`:

```python
def _select_panel_for_chunk(..., cluster_to_name: dict[int, str]):
    candidates = gather(range(page_ref - 1, page_ref + 2))
    # ... existing logic ...
    
    # Score with hybrid
    scored = [(
        _score_panel_hybrid(panel, chunk_text, scene, chunk_words, cluster_to_name, prev_panel),
        panel, src, key
    ) for panel, src, key in candidates]
    scored.sort(key=lambda x: -x[0])
    
    # Tie-break check
    if len(scored) >= 2 and (scored[0][0] - scored[1][0]) < 1.0:
        top5 = [{"panel": p, "src": s, "key": k, "score": sc, ...} for sc, p, s, k in scored[:5]]
        winner = _llm_judge_tiebreak(chunk_text, top5)
        used_panel_keys.add(winner["key"])
        return winner["panel"], winner["src"]
    
    score, panel, src, key = scored[0]
    used_panel_keys.add(key)
    return panel, src
```

### P-5: Emotion lexicon

**File**: `stages/stage_5/emotion_lexicon.py` (NEW small module)

~50 keyword → emotion mappings. Lexicon kept tiny + focused on common comic story emotions. Used only for chunk emotion detection in F-scoring.

### P-6: Schema update for preprocessed page JSON

**File**: `stages/stage_2/schema.py` (update `PreprocessedPage` dataclass)

```python
@dataclass
class PanelInfo:
    index: int
    bbox: dict[str, int]
    description: str            # from VLM (existing)
    characters: list[str]       # from VLM legacy (kept for backwards compat)
    dominant_emotion: str       # from VLM (existing)
    # NEW v5 fields:
    cluster_ids: list[int]      # from Magi
    character_names: list[str]  # resolved via cluster_to_name


@dataclass
class TextBlock:
    panel_index: int
    text: str
    type: str
    speaker: str | None         # from VLM legacy
    # NEW v5 fields:
    speaker_cluster_id: int | None
    speaker_name: str | None
    bbox: dict[str, int]        # from Magi
```

## Data flow

```
Stage 2 preprocess:
  raw image → Magi V3 (full pipeline) → panels + chars + texts + clusters
                                ↓
                       VLM cluster_naming (1 call per cluster)
                                ↓
                  cluster_to_name dict saved per project
                                ↓
                    Enriched panel JSON in preprocessed/

Stage 5 build_shots:
  caption_chunks + cluster_to_name + preprocessed pages
                                ↓
              _select_panel_for_chunk (per chunk)
                                ↓
                heuristic score → tie? → LLM-judge
                                ↓
              best panel + source_image → Shot dataclass
                                ↓
                       render Ken-Burns shot
```

## Error handling

- **Magi clustering fails to cluster** (e.g., character only appears once): treat as singleton cluster, name still resolvable
- **VLM uncertain naming**: returns "Unknown" + low confidence → still useful via cluster_id continuity, just no name string
- **Magi V3 model unavailable**: fall back to v4 behavior (panel-only detection, no cluster IDs, scoring via name strings as before)
- **LLM-judge fails / rate-limited**: fall back to heuristic top
- **No panels in pool**: existing _fallback_pick still applies

## Testing approach

Re-run `thing_bond_with_venom` end-to-end:

```bash
# Force full re-preprocess to get Magi clusters
rm -rf projects/thing_bond_with_venom/preprocessed
rm -f projects/thing_bond_with_venom/cluster_to_name.json
python -m stages.stage_2 --project thing_bond_with_venom --force

# Then re-run Stage 3+4+5
rm projects/thing_bond_with_venom/{narration,word_timestamps,scene_timings,caption_chunks}.json
rm projects/thing_bond_with_venom/{audio,audio_mixed,video_silent}.{wav,mp4}
rm -rf projects/thing_bond_with_venom/{shots,captions.ass,final.mp4}

python -m stages.stage_3 --project thing_bond_with_venom --mode panel_walk
python -m stages.stage_4 --project thing_bond_with_venom
python -m stages.stage_5 --project thing_bond_with_venom --force

# Verify
python research/scripts/view_video.py projects/.../final.mp4 -n 12 --label venom_v5
python research/scripts/gap_report.py
cat projects/thing_bond_with_venom/cluster_to_name.json
```

## Success criteria — target 6/8 to ship

| # | Criterion | Now v4 | Target v5 |
|---|---|---|---|
| 1 | cluster_to_name.json contains ≥3 resolved character names | n/a | **≥3** |
| 2 | At least 1 caption chunk picked panel via dialog-overlap >0 | n/a | **≥1** |
| 3 | LLM-judge invoked ≤30% of chunks (most decided by heuristic) | n/a | **≤30%** |
| 4 | Panel duplicates within scene = 0 (v3 invariant holds) | 0 ✓ | maintain |
| 5 | No font glitches in VLM frame audit | ✓ | maintain |
| 6 | Outro "The comic is X." present (v4 carry-over) | ✓ | maintain |
| 7 | Manual inspection: visual panel matches caption topic in ≥80% of chunks | unclear | **≥80%** |
| 8 | Total pipeline cost < $0.10/video (Magi local; VLM ~$0.001; LLM-judge ~$0.005) | $0.05 | **< $0.10** |

## Out of scope (defer to future)

- CLIP semantic embedding (Option A) — defer; hybrid already strong
- Fine-tuned ComicsPAP model (Option H) — research-level, not for production
- Background music (orthogonal to panel selection)

## Implementation notes

- Magi V3 full pipeline output structure exact field names depend on Magi's actual API. Check current Magi project README before implementing; the structure shown above is the spec target, not committed API.
- `cluster_to_name.json` lives at `projects/<name>/cluster_to_name.json` next to comic_context.json. Schema: `{"<cluster_id>": "<name>"}` plain JSON.
- VLM cluster naming uses VLM_MODELS_BATCH chain (Gemini Flash Lite primary) — same chain as Stage 2's batch VLM.
- LLM-judge uses LLM_MODELS chain (free models first) — keep cost low.
- Backwards compat: old preprocessed pages without `cluster_ids` field still work — score component D contributes 0, others still work.
- The emotion lexicon (~50 entries) lives in `stages/stage_5/emotion_lexicon.py` — keep it small and focused, don't over-engineer.
- Pre-existing tests / smoke scripts may need updates for the schema additions; run them after implementation to catch regressions.
