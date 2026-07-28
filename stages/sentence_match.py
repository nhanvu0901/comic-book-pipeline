"""Q&A (explore_answer) SUB-SHOT step — split each beat's narration into SENTENCES and match
each sentence to the BEST panel among that beat's REVIEW-LOCKED panels, so the render can show
one panel per sentence.

Only meaningful for answer_research projects (keyed by the caller on
comic_context.plot_source == "answer_research"): Master locks 2-5 panels per beat in the review
UI, and this step distributes the beat's sentences across them. Recap comics never call it.

"If sparse, leave it" — a sentence with no confident match, or a beat with no resolvable
candidate panel, gets null page/panel/score. The render then reuses the previous panel.

Scoring reuses the EXACT Stage-5 content matcher pieces over just the beat's candidate panels:
_panel_content_score (text cosine + render tie-break), the Feature-A SigLIP image blend, and the
PANEL_COS_FLOOR floor. It deliberately does NOT run the page prior / anchor bind / uniqueness /
VLM rerank — those decide WHICH panel a scene gets; here the panels are already chosen by hand
and we only split them across sentences. Needs the embed backend up (LM Studio Qwen), same as
review_gate.build_candidates; if it is down, every sentence falls below the floor → all null →
render falls back to its own per-scene pick (no hard failure — this is a refinement layer).

Contract — review/sentence_panels.json:
  {"generated_at": iso, "scenes": [{"scene_id": int, "sentences": [
      {"text": str, "start": float, "end": float,
       "page": int|null, "panel": int|null, "score": float|null}]}]}

CLI: python -m stages.sentence_match --project X
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from stages.review_gate import (QA_PANEL_IMG_WEIGHT, _beat_source, _load_json, _now_iso,
                                 _project_root, load_state, lock_panels)
# Panel TEXT-embed master switch (see config.PANEL_TEXT_EMBED). OFF (default) → distribute a
# scene's LOCKED panels across its sentences deterministically (round-robin) instead of by cosine,
# so the render never touches the embed backend. Bound to the module so tests can flip it.
from config import PANEL_TEXT_EMBED

# When ON (default), sentences WITHIN one scene take DISTINCT panels from the scene's
# chosen set (optimal 1:1 via Hungarian) so an item shows visual variety instead of
# repeating its single best-matching panel. Reuse is only forced when a scene has more
# sentences than chosen panels. SENTENCE_PANEL_NO_REUSE=0 → plain per-sentence argmax.
SENTENCE_PANEL_NO_REUSE = os.getenv("SENTENCE_PANEL_NO_REUSE", "1").strip().lower() not in (
    "0", "false", "no", "")


# ─── sentence splitting ─────────────────────────────────────────────────────────

def _split_sentences(text: str, *, min_words: int = 3) -> list[str]:
    """Split on . ! ? keeping the delimiter, then merge any fragment shorter than min_words
    FORWARD into the next sentence. That fold keeps a countdown label like "The Punisher."
    riding with "In Thunderbolts #29, ..." instead of matching a panel on its own two words.
    A short TRAILING fragment folds backward into the previous sentence."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text) if p.strip()]
    merged: list[str] = []
    buf = ""
    for p in parts:
        buf = f"{buf} {p}".strip() if buf else p
        if len(buf.split()) >= min_words:
            merged.append(buf)
            buf = ""
    if buf:                                     # leftover short tail
        if merged:
            merged[-1] = f"{merged[-1]} {buf}".strip()
        else:
            merged.append(buf)
    return merged


# ─── timing alignment ───────────────────────────────────────────────────────────

def _norm(word: str) -> str:
    """Lowercase, strip everything but a-z0-9 — so "Stare?"→"stare", "#29,"→"29", and a
    punctuation-only token ("—", ",") normalises to "" and is dropped as a timing anchor."""
    return re.sub(r"[^a-z0-9]", "", str(word or "").lower())


def _align(sentences: list[str], wt_toks: list[tuple], cursor: int,
           *, lookahead: int = 12) -> tuple[list[tuple], int]:
    """Assign each sentence a (start, end) by a FORWARD subsequence match of its words against
    wt_toks (list of (norm, start, end)) starting at `cursor`. Robust to TTS noise: a missing
    expected word is skipped (cursor doesn't advance past a non-match), and inserted words
    (e.g. an expanded number) are jumped over within `lookahead`. Falls back to positional
    consumption when nothing matches so the cursor always makes monotonic progress. Returns
    (spans, new_cursor); a span is (None, None) only when there is nothing left to align to."""
    spans: list[tuple] = []
    pos = cursor
    ntoks = len(wt_toks)
    for sent in sentences:
        exp = [t for t in (_norm(w) for w in sent.split()) if t]
        first = last = None
        local = pos
        matched = 0
        for tok in exp:
            hit = None
            for k in range(local, min(local + lookahead, ntoks)):
                if wt_toks[k][0] == tok:
                    hit = k
                    break
            if hit is not None:
                if first is None:
                    first = wt_toks[hit][1]
                last = wt_toks[hit][2]
                local = hit + 1
                matched += 1
        if matched == 0 and pos < ntoks:        # positional fallback
            end_i = min(pos + max(1, len(exp)), ntoks)
            first = wt_toks[pos][1]
            last = wt_toks[end_i - 1][2]
            local = end_i
        spans.append((first, last))
        pos = local
    return spans, pos


# ─── candidate resolution + scoring ──────────────────────────────────────────────

def _candidate_keys(scene: dict, locks: dict) -> list[tuple[int, int]]:
    """Panels this scene's sentences may match: the review-locked panels (v2 or v1 shape via
    lock_panels), else the scene's OWN (page_ref, panel_ref) anchor as a single candidate —
    only when panel_ref resolves to a real panel (>= 0). No lock + no panel anchor → [] →
    every sentence goes null (sparse), and the render reuses the previous panel."""
    panels = lock_panels(locks.get(str(scene.get("scene_id"))))
    if panels:
        return [(int(p["page"]), int(p["panel"])) for p in panels]
    page_ref = int(scene.get("page_ref", 0) or 0)
    panel_ref = int(scene.get("panel_ref", -1) if scene.get("panel_ref") is not None else -1)
    if page_ref > 0 and panel_ref >= 0:
        return [(page_ref, panel_ref)]
    return []


def _f(x) -> float | None:
    return None if x is None else round(float(x), 3)


def _match_sentences(sentences: list[str], spans: list[tuple], scene: dict,
                     cands: list[tuple], panel_vecs: dict, project: str, *, log,
                     drawable_moment: str = "", always_assign: bool = False) -> list[dict]:
    """Score every sentence against every candidate panel and pick the best. Falls back to
    all-null when there are no candidates. Timing (start/end) is filled regardless of match so
    the render always has sentence boundaries even for a sparse (null-panel) sentence.

    always_assign=True forces a panel for EVERY item even below PANEL_COS_FLOOR (used by the Q&A
    chunk-level render over K time-groups — each group is a real segment of the beat and should
    show one of Master's locked panels, never go blank). Default False keeps the sentence path's
    "if sparse, leave it" behaviour so the render can reuse the previous panel.

    Q&A: every sentence of a beat targets the SAME visual, so the item's `drawable_moment` (a
    precise VISUAL of the exact panel to draw) is blended into each sentence's query — for the
    text cosine AND the SigLIP text→ART image blend, both of which read this query text. The
    sentence-specific part still varies the row enough for the no-reuse Hungarian to spread
    DISTINCT panels across the sentences. To stop the dm blend from washing out the one pair that
    matters, the single strongest dm-FREE sentence↔panel match (over the floor) is PINNED before
    the Hungarian — see the pin block below. No-op when dm=="" (legacy stays byte-identical)."""
    out = [{"text": s, "start": _f(spans[i][0]), "end": _f(spans[i][1]),
            "page": None, "panel": None, "score": None}
           for i, s in enumerate(sentences)]
    if not cands or not sentences:
        return out

    # NO-EMBED: cosine is dead (Master picked the panels by hand). Spread the scene's locked panels
    # across its sentences ROUND-ROBIN — distinct across the first len(cands) sentences, then cycle;
    # the caller's own no-reuse guard resolves any within-beat repeat. Never touches the embed backend.
    if not PANEL_TEXT_EMBED:
        m = len(cands)
        for i in range(len(sentences)):
            key = cands[i % m][0]
            out[i]["page"], out[i]["panel"], out[i]["score"] = int(key[0]), int(key[1]), None
        return out

    import numpy as np
    from stages._embedding import embed_batch
    from stages.stage_5.shots import (PANEL_COS_FLOOR, _blend_image_content,
                                       _panel_content_score)

    scene_text = str(scene.get("text", "") or "")
    dm = str(drawable_moment or "").strip()
    queries = [f"{s} {dm}".strip() if dm else s for s in sentences]
    chunk_vecs = embed_batch(queries)
    scene_vec = embed_batch([scene_text])[0]

    n, m = len(sentences), len(cands)
    content = np.full((n, m), -1.0e9, dtype="float64")
    sim = np.zeros((n, m), dtype="float64")
    for i in range(n):
        for j, (key, panel, _src, page_tb) in enumerate(cands):
            sc, sc_sim = _panel_content_score(
                panel, panel_vecs.get(key), chunk_vecs[i], scene_vec, page_tb,
                chunk_text=queries[i], scene_text=scene_text)
            content[i][j] = sc
            sim[i][j] = sc_sim

    # Feature A: blend the desc-free SigLIP image signal into `content` (parity with the render
    # matcher). No-op when the image channel is unavailable or < 2 candidate panels carry a
    # stored image vector. `sim` (raw TEXT cosine) stays untouched so the floor keeps its
    # text semantics — same split the matcher relies on.
    _blend_image_content(content, cands, [(scene, q) for q in queries], project)

    # PIN the strongest PURE-TEXT pair. `dm` is blended into EVERY query, so when dm describes one
    # panel (its own splash) that column of `content` goes near-uniform across rows — Hungarian then
    # splits it by margin noise and the sentence whose OWN words name the panel (the payoff) can lose
    # it to an opening group (batcave-breach "I AM BANE" splash, 2026-07-09). Recompute a dm-FREE
    # (sentence-only) sim matrix, and if its global-best cell clears the text floor, pin that (row,col)
    # so the assignment keeps it. Guarded on dm != "": when dm=="" queries==sentences → this matrix
    # equals `content`, so pinning would perturb the legacy result — skip it and stay byte-identical
    # (recap path + old sentence path). Single pin only, so the rest of the distribution is untouched.
    if dm:
        text_vecs = embed_batch(sentences)
        text_sim = np.zeros((n, m), dtype="float64")
        for i in range(n):
            for j, (key, panel, _src, page_tb) in enumerate(cands):
                _sc, tsim = _panel_content_score(
                    panel, panel_vecs.get(key), text_vecs[i], scene_vec, page_tb,
                    chunk_text=sentences[i], scene_text=scene_text)
                text_sim[i][j] = tsim
        ri, cj = (int(v) for v in np.unravel_index(int(np.argmax(text_sim)), text_sim.shape))
        if float(text_sim[ri][cj]) >= PANEL_COS_FLOOR:
            content[ri][cj] += 1.0e9      # dominates argmax + Hungarian cost → (ri,cj) stays paired

    # Choose which panel each sentence shows. Default: DISTINCT panels within the scene
    # (Hungarian max-content 1:1) so the item doesn't repeat one panel; fall back to plain
    # per-sentence argmax (reuse allowed) when the rule is off, or per-sentence for the
    # overflow when there are more sentences than panels.
    chosen_j = [int(np.argmax(content[i])) for i in range(n)]
    if SENTENCE_PANEL_NO_REUSE and m >= 1:
        try:
            from scipy.optimize import linear_sum_assignment
            cost = -content.copy()
            if n > m:  # pad with dummy panels so every sentence gets a column (overflow → argmax)
                cost = np.concatenate([cost, np.full((n, n - m), 1e18)], axis=1)
            rows, cols = linear_sum_assignment(cost)
            picked = {int(r): int(c) for r, c in zip(rows, cols)}
            chosen_j = [picked[i] if picked.get(i, m) < m else int(np.argmax(content[i]))
                        for i in range(n)]
        except Exception as exc:  # scipy missing / degenerate → keep argmax
            log(f"[sentence-match] no-reuse assignment fell back to argmax ({exc})")

    for i in range(n):
        j = chosen_j[i]
        if not always_assign and float(sim[i][j]) < PANEL_COS_FLOOR:
            continue                            # sparse → leave null, render reuses previous
        key = cands[j][0]
        out[i]["page"] = int(key[0])
        out[i]["panel"] = int(key[1])
        out[i]["score"] = round(float(sim[i][j]), 4)
    return out


# ─── entrypoint ──────────────────────────────────────────────────────────────────

def build_sentence_panels(project, *, log=print) -> Path:
    """Split each STORY scene's narration into sentences, time-align them to word_timestamps,
    match each to the best of the scene's chosen panels, and write review/sentence_panels.json.
    Returns the output path. Intro/outro scenes are skipped (no sub-shot) but still consume
    their words so later scenes stay time-aligned."""
    root = _project_root(project)
    slug = root.name
    scenes = (_load_json(root / "narration.json").get("scenes")) or []
    if not scenes:
        raise FileNotFoundError(f"narration.json missing/empty: {root / 'narration.json'}")

    wt_raw = _load_json(root / "word_timestamps.json")
    words = wt_raw if isinstance(wt_raw, list) else (wt_raw.get("words") or [])
    wt_toks = [(_norm(w.get("word", "")), float(w.get("start", 0) or 0), float(w.get("end", 0) or 0))
               for w in words if _norm(w.get("word", ""))]

    locks = load_state(project).get("locks") or {}

    from stages.stage_5.pipeline import _load_preprocessed_pages
    from stages.stage_5.shots import _panel_pool
    from stages._panel_index import load_vectors
    from stages import _img_index
    pages_by_number = _load_preprocessed_pages(root)
    pool_by_key = {key: (key, panel, src, tb) for (key, panel, src, tb) in _panel_pool(pages_by_number)}
    panel_vecs = load_vectors(slug)

    # drawable_moment per scene resolved via the same page_ref→issue→item map review_gate uses.
    comic_ctx = _load_json(root / "comic_context.json")
    answer_ctx = _load_json(root / "answer_context.json")
    page_to_issue = {int(p.get("page_number", 0) or 0): str(p.get("issue_label", "") or "")
                     for p in pages_by_number.values()}
    multi_issue = len({v for v in page_to_issue.values() if v}) > 1
    qa_mode = str(comic_ctx.get("plot_source", "") or "") == "answer_research"

    def _drawable_moment(scene) -> str:
        issue_label = page_to_issue.get(int(scene.get("page_ref", 0) or 0), "") if multi_issue else ""
        return str(_beat_source(scene, comic_ctx, answer_ctx, issue_label=issue_label)
                   .get("drawable_moment", "") or "")

    # FIX B: trust the SigLIP image signal more for a drawable_moment (visual) query.
    _orig_img_w = _img_index.PANEL_IMG_WEIGHT
    if qa_mode:
        _img_index.PANEL_IMG_WEIGHT = QA_PANEL_IMG_WEIGHT

    out_scenes = []
    cursor = 0
    try:
        for scene in scenes:
            sentences = _split_sentences(str(scene.get("text", "") or ""))
            spans, cursor = _align(sentences, wt_toks, cursor)   # advance cursor for EVERY scene
            if scene.get("is_intro") or scene.get("is_outro"):
                continue
            cand_keys = _candidate_keys(scene, locks)
            cands = [pool_by_key[k] for k in cand_keys if k in pool_by_key]
            missing = [k for k in cand_keys if k not in pool_by_key]
            if missing:
                log(f"[sentence-match] scene {scene.get('scene_id')}: {len(missing)} locked panel(s) "
                    f"not in preprocessed pages {missing} — ignored")
            sents = _match_sentences(sentences, spans, scene, cands, panel_vecs, slug, log=log,
                                     drawable_moment=_drawable_moment(scene))
            matched = sum(1 for s in sents if s["page"] is not None)
            log(f"[sentence-match] scene {scene.get('scene_id')}: {len(sents)} sentence(s), "
                f"{len(cands)} candidate panel(s), {matched} matched, {len(sents) - matched} sparse")
            out_scenes.append({"scene_id": int(scene.get("scene_id") or 0), "sentences": sents})
    finally:
        _img_index.PANEL_IMG_WEIGHT = _orig_img_w

    out_path = root / "review" / "sentence_panels.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"generated_at": _now_iso(), "scenes": out_scenes},
                                   indent=2, ensure_ascii=False))
    log(f"[sentence-match] wrote {out_path} ({len(out_scenes)} story scene(s))")
    return out_path


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m stages.sentence_match",
        description="Split Q&A beats into sentences and match each to a locked panel.")
    ap.add_argument("--project", required=True, help="Project slug under projects/.")
    args = ap.parse_args(argv)
    path = build_sentence_panels(args.project)
    print(f"[sentence-match] sentence_panels -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
