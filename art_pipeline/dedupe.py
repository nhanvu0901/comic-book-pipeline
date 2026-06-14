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


_REWRITE_SYSTEM = (
    "You rewrite ONE sentence of an art-history narration so it says something "
    "NEW. Neutral, precise, second-person where natural. No hype, no CTA. "
    "Respond with STRICT JSON only: {\"text\": \"...\"}")

_DEDUP_BAN_MAX = 40   # cap the ban-list fed to the rewrite prompt (avoid an overlong prompt)


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
        f"lines:\n" + "\n".join(f"- {b}" for b in ban[:_DEDUP_BAN_MAX]) +
        f"\n\nArtwork title: {ctx.get('title', '')}. "
        'Return JSON: {"text": "..."}')
    try:
        raw, _ = call_with_chain(system=_REWRITE_SYSTEM, user=user,
                                 models=CREATIVE_LLM_MODELS, max_tokens=300,
                                 progress=log, label="art-lf-dedup",
                                 validator=lambda c: extract_json(c) is not None)
        data = extract_json(raw) or {}
        new = str(data.get("text") or "").strip()
        if not new or len(new.split()) > ART_LF_SCENE_MAX_WORDS:
            return original
        return new
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
    {rewrites, unresolved, max_similarity_after}. Each rewrite is re-checked at the
    start of the next pass (bounded by max_passes); a stubborn duplicate is kept,
    never dropped."""
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
            norm_new = " ".join(new.lower().split())
            if norm_new and norm_new != " ".join(_text(sc).lower().split()):
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
