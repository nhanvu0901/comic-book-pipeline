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
