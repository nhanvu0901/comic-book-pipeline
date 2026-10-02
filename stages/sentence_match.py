"""Q&A (explore_answer) SUB-SHOT step — split each beat's narration into SENTENCES and spread
them over that beat's REVIEW-LOCKED panels, so the render can show one panel per sentence.

Only meaningful for answer_research projects (keyed by the caller on
comic_context.plot_source == "answer_research"): Master locks 2-5 panels per beat in the review
UI, and this step distributes the beat's sentences across them. Recap comics never call it.

Master already chose the panels by eye, so nothing here scores anything: the beat's
locked panels are handed to its sentences ROUND-ROBIN — distinct panels across the first
len(panels) sentences, then cycling. A beat with no resolvable candidate panel gets null
page/panel for every sentence, and the render then reuses the previous panel.

Contract — review/sentence_panels.json:
  {"generated_at": iso, "scenes": [{"scene_id": int, "sentences": [
      {"text": str, "start": float, "end": float,
       "page": int|null, "panel": int|null}]}]}

CLI: python -m stages.sentence_match --project X
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from stages.review_gate import _load_json, _now_iso, _project_root, load_state, lock_panels


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


# ─── candidate resolution + distribution ─────────────────────────────────────────

def _candidate_keys(scene: dict, locks: dict) -> list[tuple[int, int]]:
    """Panels this scene's sentences may take: the review-locked panels (v2 or v1 shape via
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


def _match_sentences(sentences: list[str], spans: list[tuple], cands: list[tuple]) -> list[dict]:
    """Hand the scene's candidate panels to its sentences ROUND-ROBIN: distinct panels across the
    first len(cands) sentences, then cycling (a panel only repeats once the sentences outnumber
    the panels). Master picked the panels by hand, so the sentences only need spreading over
    them. No candidates → page/panel stay None. Timing (start/end) is filled regardless, so the
    render always has sentence boundaries even for a sparse (null-panel) sentence."""
    out = [{"text": s, "start": _f(spans[i][0]), "end": _f(spans[i][1]),
            "page": None, "panel": None}
           for i, s in enumerate(sentences)]
    if not cands:
        return out
    for i, row in enumerate(out):
        key = cands[i % len(cands)][0]
        row["page"], row["panel"] = int(key[0]), int(key[1])
    return out


# ─── entrypoint ──────────────────────────────────────────────────────────────────

def build_sentence_panels(project, *, log=print) -> Path:
    """Split each STORY scene's narration into sentences, time-align them to word_timestamps,
    spread them over the scene's chosen panels, and write review/sentence_panels.json.
    Returns the output path. Intro/outro scenes are skipped (no sub-shot) but still consume
    their words so later scenes stay time-aligned."""
    root = _project_root(project)
    scenes =(_load_json(root / "narration.json").get("scenes")) or []
    if not scenes:
        raise FileNotFoundError(f"narration.json missing/empty: {root / 'narration.json'}")

    wt_raw = _load_json(root / "word_timestamps.json")
    words = wt_raw if isinstance(wt_raw, list) else (wt_raw.get("words") or [])
    wt_toks = [(_norm(w.get("word", "")), float(w.get("start", 0) or 0), float(w.get("end", 0) or 0))
               for w in words if _norm(w.get("word", ""))]

    locks = load_state(project).get("locks") or {}

    from stages.stage_5.pipeline import _load_preprocessed_pages
    from stages.stage_5.shots import _panel_pool
    pages_by_number = _load_preprocessed_pages(root)
    pool_by_key = {key: (key, panel, src, tb) for (key, panel, src, tb) in _panel_pool(pages_by_number)}

    out_scenes = []
    cursor = 0
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
        sents = _match_sentences(sentences, spans, cands)
        matched = sum(1 for s in sents if s["page"] is not None)
        log(f"[sentence-match] scene {scene.get('scene_id')}: {len(sents)} sentence(s), "
            f"{len(cands)} candidate panel(s), {matched} matched, {len(sents) - matched} sparse")
        out_scenes.append({"scene_id": int(scene.get("scene_id") or 0), "sentences": sents})

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
        description="Split Q&A beats into sentences and spread them over the beat's locked panels.")
    ap.add_argument("--project", required=True, help="Project slug under projects/.")
    args = ap.parse_args(argv)
    path = build_sentence_panels(args.project)
    print(f"[sentence-match] sentence_panels -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
