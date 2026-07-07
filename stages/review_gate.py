"""Review gate — BLOCK Stage 4 (TTS) and Stage 5 (render) until Master approves the
narration text + panel choices in the review UI.

State lives in projects/<slug>/review/:
  • locks.json      — approval flag + per-scene panel LOCKS (the review UI writes this).
  • candidates.json — the matcher's ranked panel shortlist per beat (this module writes it).
  • thumbs/         — cropped panel previews referenced by candidates.json.

Two JSON contracts the UI is built against (documented in EXPLORE_ANSWER_DESIGN.md):

locks.json (v2 — the review UI now locks 1-5 panels per scene)
  {"approved": bool, "approved_at": iso|null, "narration_sha1": str|null,
   "locks": {"<scene_id>": {"panels": [{"page": int, "panel": int}, ...1-5...],
                            "source": "batcave"}}}
  BACKWARD-COMPAT: a lock written before the multi-select upgrade is the OLD single shape
  {"page": int, "panel": int, "source": ...}. Use lock_panels() to read EITHER shape as a
  normalised [{"page","panel"}, ...] list.

candidates.json
  {"generated_at": iso,
   "beats": [{"scene_id": int, "narration_text": str, "page_ref": int|null,
              "panel_ref": int|null,
              "source": {"title","issue","url","research_urls":[...]},
              "candidates": [{"page": int, "panel": int, "score": float,
                              "thumb": "review/thumbs/pXXX_Y.jpg",
                              "desc": str, "dialog": str}]}]}

CLI:
  python -m stages.review_gate --project X --build-candidates [--k 10]
  python -m stages.review_gate --project X --build-candidates --all   # ALL panels/beat, ranked
  python -m stages.review_gate --project X                            # print gate status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import PROJECTS_ROOT

# Default-ON boolean env, same idiom as shots.PANEL_ANCHOR_BIND.
REVIEW_GATE = os.getenv("REVIEW_GATE", "1").strip().lower() not in ("0", "false", "no", "")

# Provenance label stamped on every lock — panels come from batcave-downloaded pages.
_LOCK_SOURCE = "batcave"

# Q&A image-blend weight (Feature-A SigLIP). Master 2026-07-07: the old 0.55 over-rewarded
# VISUAL SPECTACLE (big splashes, lightning) — the matcher kept surfacing "big highlight"
# panels instead of the one that depicts what the NARRATION BEAT says (what the audience
# pictures from the spoken line). Lowered toward the recap default so the text-semantic
# (narration) signal leads and the image signal only tie-breaks. Only on answer_research.
QA_PANEL_IMG_WEIGHT = float(os.getenv("QA_PANEL_IMG_WEIGHT", "0.35"))

# How a Q&A beat's match query is formed from the beat narration + item's drawable_moment:
#   "blend"     — NARRATION leads, drawable_moment trails (default). Master 2026-07-07: the
#                 pick must be the panel closest to what the audience HEARS (the narration
#                 line), with drawable_moment only sharpening it — not the reverse.
#   "drawable"  — drawable_moment ONLY (pure visual target)
#   "narration" — narration ONLY (recap parity)
# Recap beats have no drawable_moment so every mode collapses to narration for them.
QA_QUERY_MODE = os.getenv("QA_QUERY_MODE", "blend").strip().lower()
# In "blend", how many times the narration is repeated ahead of the drawable_moment — a
# cheap way to up-weight the spoken line in the embedded query without a dual-cosine rewrite.
QA_NARR_WEIGHT = int(os.getenv("QA_NARR_WEIGHT", "2"))

# ─── Candidate-ranking layer (this file ONLY — the shared matcher is untouched) ──
# The blended cosine is good RECALL but weak ORDERING: VLM panel descriptions share
# one register ("The panel shows…"), so within an issue dozens of panels cluster and
# rank order inside the cluster is noise (Master, 2026-07-07: suggestions "still not
# good"). Two extra signals fix the ordering, both applied AFTER _match_panels here:
#   1. DIALOG CHANNEL — each panel's OCR dialog embedded ALONE (short, distinctive,
#      no boilerplate) and cosined against the beat query. Semantic, so a paraphrase
#      matches ("offers to resurrect her" ↔ "I can restore the child to life").
#      Plus a hard bonus when the query QUOTES dialog verbatim ("Live, Scott" in
#      quotation marks = the writer's explicit verbatim intent → fuzzy string match
#      is valid there and only there).
#   2. VLM VISION RANK — a vision judge LOOKS at the top-K crops and scores "does
#      this panel actually SHOW this moment?" — the only signal that crosses the
#      text↔image modality gap. Cosine+dialog decide WHICH K get judged.
QA_DIALOG_WEIGHT = float(os.getenv("QA_DIALOG_WEIGHT", "0.35"))
QA_QUOTE_BONUS = float(os.getenv("QA_QUOTE_BONUS", "0.6"))
QA_CAND_VLM_K = int(os.getenv("QA_CAND_VLM_K", "14"))   # 0 → skip the vision judge
# Moment-present floor: if the BEST vision score (0-10) across a beat's whole issue is
# below this, no panel in the cited issue actually depicts the beat's moment → the
# research almost certainly named the WRONG issue (the moment lives in a neighbour, e.g.
# Children's Crusade: strip-power is in #8, cited #9 is aftermath). We can't fix the
# number automatically (which neighbour?), but we FLAG it loudly for Master at review —
# the one error class that resolve_reader_url / verify_issue structurally cannot catch.
QA_MOMENT_FLOOR = float(os.getenv("QA_MOMENT_FLOOR", "4.0"))


# ─── paths / helpers ──────────────────────────────────────────────────────────

def _project_root(project) -> Path:
    """Accept a project slug OR a path to the project dir."""
    p = Path(project)
    return p if p.is_dir() else PROJECTS_ROOT / str(project)


def _locks_path(project) -> Path:
    return _project_root(project) / "review" / "locks.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# ─── state ────────────────────────────────────────────────────────────────────

def load_state(project) -> dict:
    """Read review/locks.json, always returning a fully-shaped state dict."""
    st = _load_json(_locks_path(project))
    st.setdefault("approved", False)
    st.setdefault("approved_at", None)
    st.setdefault("narration_sha1", None)
    st.setdefault("locks", {})
    return st


def lock_panels(lock: dict | None) -> list[dict]:
    """A scene's locks.json entry as a normalised list of {"page","panel"} dicts, handling
    BOTH the v2 multi-panel shape ({"panels": [{"page","panel"}, ...]}) and the old v1 single
    shape ({"page","panel", ...}) so a project locked before the multi-select upgrade still
    loads as a 1-item selection. [] when the lock is empty / malformed. Mirrors the review
    UI's own normaliser (ui/screens/s_review_gate._normalize_lock_panels) so the writer and
    every reader agree on the contract."""
    if not lock:
        return []
    if "panels" in lock:
        return [{"page": int(p["page"]), "panel": int(p["panel"])}
                for p in (lock.get("panels") or []) if p.get("page") is not None]
    if lock.get("page") is not None:
        return [{"page": int(lock["page"]), "panel": int(lock["panel"])}]
    return []


def save_state(project, state: dict) -> Path:
    p = _locks_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    return p


def narration_sha1(project) -> str | None:
    """Stable sha1 of narration.json BYTES — the approval is pinned to this so an edit
    after approval invalidates it. None when narration.json is absent."""
    np_ = _project_root(project) / "narration.json"
    return hashlib.sha1(np_.read_bytes()).hexdigest() if np_.exists() else None


def _plot_source(project) -> str:
    return str(_load_json(_project_root(project) / "comic_context.json").get("plot_source", "") or "")


# ─── gate ─────────────────────────────────────────────────────────────────────

def ensure_reviewed(project, skip_flag: bool = False, *, log=print) -> None:
    """Raise SystemExit unless the project is approved in the review UI. Called at the top
    of Stage 4 (TTS) and Stage 5 (render). `skip_flag` (--skip-review) bypasses the gate
    for a normal narrate-mode comic, but is IGNORED for an answer_research (Q&A) project —
    those carry the wrong-issue / wrong-panel risk (EXPLORE_ANSWER_DESIGN #1 risk) and must
    be reviewed. Keyed on comic_context.plot_source, not a mode name."""
    if not REVIEW_GATE:
        return
    answer_mode = _plot_source(project) == "answer_research"
    if skip_flag:
        if answer_mode:
            log("[review-gate] --skip-review IGNORED: answer_research (Q&A) panel choices "
                "carry the wrong-issue/wrong-panel risk and must be reviewed")
        else:
            log("[review-gate] gate skipped (--skip-review)")
            return
    state = load_state(project)
    if not state.get("approved"):
        raise SystemExit(
            f"[review-gate] BLOCKED: '{project}' is not approved.\n"
            f"  Open the review UI, check the narration text + panel choices, and approve.\n"
            f"  Build the panel shortlist first if needed:\n"
            f"    python -m stages.review_gate --project {project} --build-candidates\n"
            f"  (bypass for a normal comic with --skip-review; not allowed for Q&A projects.)"
        )
    approved_sha, current_sha = state.get("narration_sha1"), narration_sha1(project)
    if approved_sha and current_sha and approved_sha != current_sha:
        raise SystemExit(
            f"[review-gate] BLOCKED: narration.json changed since '{project}' was approved "
            f"(approval is stale). Re-review and re-approve in the UI."
        )
    log(f"[review-gate] '{project}' approved — proceeding")


# ─── candidates exporter ────────────────────────────────────────────────────────

def _extract_urls(text: str) -> list[str]:
    """Pull citation URLs from a verification note. The research often writes BARE
    domains (cbr.com/…, marvel.fandom.com/…) with no scheme, so match those too and
    normalise to https:// — otherwise Master's cross-check links come out empty."""
    raw = re.findall(
        r'(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s\)\]"\'<>]*)?',
        str(text or ""), flags=re.IGNORECASE)
    seen, out = set(), []
    for u in raw:
        # Require a path or www./scheme so bare sentence words with a dot aren't caught.
        if "/" not in u and not u.lower().startswith(("www.", "http")):
            continue
        full = u if u.lower().startswith("http") else "https://" + u
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def _chapter_index(source_image) -> int | None:
    """1-based chapter number from a saga page filename (chNN_page_MM.jpg) — maps a scene
    to its source issue / answer item."""
    if not source_image:
        return None
    m = re.search(r"ch(\d+)_", Path(str(source_image)).name)
    return int(m.group(1)) if m else None


def _beat_source(scene: dict, comic_ctx: dict, answer_ctx: dict, *, issue_label: str = "") -> dict:
    """Citation shown next to a beat so Master can sanity-check the panel choice. For a Q&A
    project each beat cites its OWN answer item (different comic per beat); for a normal
    comic every beat cites the single source comic.

    Item lookup order: (1) the beat's issue_label "#N" → item N-1 (robust — survives a
    narration hand-edit that drops per-scene source_image), then (2) the saga page
    filename's chapter index, then (3) fall back to the single comic_context source."""
    items = answer_ctx.get("items") or []
    if items:
        idx = None
        m = re.match(r"#\s*(\d+)", str(issue_label or ""))
        if m:
            idx = int(m.group(1))
        if not (idx and 1 <= idx <= len(items)):
            idx = _chapter_index(scene.get("source_image"))
        if idx and 1 <= idx <= len(items):
            it = items[idx - 1]
            return {"title": it.get("source_comic", ""), "issue": str(it.get("source_year", "")),
                    "url": it.get("reader_url", ""),
                    "drawable_moment": it.get("drawable_moment", ""),
                    # Comic Vine cross-check result — surfaced so the review UI can WARN
                    # on a flagged item (a wrong-issue download is exactly what Master is
                    # here to catch; hiding the flag made the gate review blind).
                    "verified": bool(it.get("verified", True)),
                    "verify_note": str(it.get("verify_note", "") or ""),
                    "research_urls": _extract_urls(it.get("verification_note", ""))}
    urls = comic_ctx.get("reader_urls") or []
    research = [u for u in (comic_ctx.get("wiki_url"), comic_ctx.get("batcave_url")) if u]
    return {"title": comic_ctx.get("title", ""),
            "issue": str(comic_ctx.get("issue", "") or comic_ctx.get("issues", "") or ""),
            "url": (urls[0] if urls else comic_ctx.get("batcave_url", "")),
            "drawable_moment": "",
            "verified": True, "verify_note": "",
            "research_urls": research}


_QUOTE_SPAN_RE = re.compile(r'[“"]([^”"]{3,80})[”"]')


def _dialog_rescore(cands: list, query_text: str, pages_by_number: dict) -> list:
    """Dialog-channel boost (see knob block). Adds QA_DIALOG_WEIGHT·cos(query, panel's
    OCR dialog) to each candidate that HAS dialog (silent panels are not penalised),
    plus QA_QUOTE_BONUS when a quoted span from the query matches the dialog. Returns
    the list re-sorted by adjusted score; on any embed failure returns it unchanged."""
    if not cands or QA_DIALOG_WEIGHT <= 0:
        return cands
    dialogs = []
    for c in cands:
        page_tb = (pages_by_number.get(c["page"]) or {}).get("text_blocks")
        dialogs.append(_panel_dialog_str(c["panel"], page_tb))
    try:
        from stages._embedding import embed_batch
        vecs = embed_batch([query_text] + dialogs)
    except Exception:
        return cands
    qv = vecs[0]
    if qv is None:
        return cands
    from stages.stage_2.pipeline import _norm_dialog_text
    spans = [_norm_dialog_text(sp) for sp in _QUOTE_SPAN_RE.findall(query_text)]
    spans = [sp for sp in spans if len(sp.split()) >= 2]
    import difflib
    for c, dlg, dv in zip(cands, dialogs, vecs[1:]):
        boost = 0.0
        if dv is not None:
            boost += QA_DIALOG_WEIGHT * max(0.0, float(sum(a * b for a, b in zip(qv, dv))))
        if spans and dlg:
            dn = _norm_dialog_text(dlg)
            if any(sp in dn or difflib.SequenceMatcher(None, sp, dn).ratio() >= 0.8
                   for sp in spans):
                boost += QA_QUOTE_BONUS
        c["score"] = float(c["score"]) + boost
    return sorted(cands, key=lambda c: c["score"], reverse=True)


def _vlm_rank_top(cands: list, query_text: str, root: Path, *, log=print) -> list:
    """Vision-judge the TOP QA_CAND_VLM_K candidates: one sdk_complete_vision call per
    beat scores every crop 0-10 on "does this panel SHOW this moment?", and the top
    slice is re-ordered by (vlm score, adjusted cosine). The tail keeps its order.
    Never raises; any failure (SDK down, unparseable, missing crops) → unchanged."""
    if QA_CAND_VLM_K <= 0 or len(cands) < 2:
        return cands
    try:
        from stages._claude_sdk import sdk_complete_vision, sdk_available
        if not sdk_available():
            return cands
        top = cands[:QA_CAND_VLM_K]
        paths = []
        for c in top:
            rel = f"review/thumbs/p{c['page']:03d}_{c['panel_idx']}.jpg"
            ap = root / rel
            if not ap.exists():
                if not _write_thumb(c["src"], (c["panel"].get("bbox") or {}), ap):
                    return cands          # can't show the judge every crop → don't half-judge
            paths.append(ap)
        listing = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(paths))
        raw = sdk_complete_vision(
            "You are a comic panel judge. Read each numbered image (a comic panel crop) and "
            "score 0-10 how well the PICTURE ITSELF shows the described moment — the drawn "
            "action/character, not caption text. Return STRICT JSON only: "
            '{"scores": [<one number per image, in order>]}',
            f"MOMENT to depict: {query_text}\n\nPANEL CROPS:\n{listing}",
            log=log,
        )
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        scores = (json.loads(m.group(0)) if m else {}).get("scores")
        if not isinstance(scores, list) or len(scores) != len(top):
            return cands
        # Stash each judged panel's vision score (0-10) so build_candidates can run the
        # MOMENT-PRESENT check: if the BEST score across the whole issue is low, the
        # cited issue probably doesn't depict this moment (wrong issue number in the
        # research — the CC #9-vs-#8 class of error that resolve/verify can't catch).
        for i, c in enumerate(top):
            try:
                c["_vlm"] = float(scores[i])
            except (TypeError, ValueError):
                pass
        order = sorted(range(len(top)),
                       key=lambda i: (float(scores[i]), float(top[i]["score"])), reverse=True)
        return [top[i] for i in order] + cands[len(top):]
    except Exception as exc:  # noqa: BLE001 — ranking sugar must never block the gate
        log(f"[review-gate] vlm rank skipped ({type(exc).__name__}: {exc})")
        return cands


def _write_thumb(src, bbox: dict, out_path: Path, *, max_side: int = 520) -> bool:
    """Crop one candidate panel from its page and save a small JPEG preview. Returns False
    (leaving the thumb path empty) when the source image is missing / unreadable."""
    from PIL import Image
    if not src or not Path(src).exists():
        return False
    try:
        img = Image.open(src).convert("RGB")
        x, y, w, h = (int((bbox or {}).get(k, 0) or 0) for k in ("x", "y", "w", "h"))
        if w > 0 and h > 0:
            img = img.crop((x, y, x + w, y + h))
        img.thumbnail((max_side, max_side))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "JPEG", quality=82)
        return True
    except Exception:
        return False


def _panel_dialog_str(panel: dict, page_tb) -> str:
    """Panel dialog as one string — Magi OCR ground truth preferred over VLM text."""
    from stages._panel_index import panel_dialog
    blocks = panel_dialog(panel, page_tb)
    ocr = " ".join(str(b.get("ocr", "")).strip() for b in blocks if str(b.get("ocr", "")).strip())
    if ocr:
        return ocr
    return " ".join(str(b.get("text", "")).strip() for b in blocks if str(b.get("text", "")).strip())


def build_candidates(project_name: str, k: int = 10, *, log=print) -> Path:
    """Score every STORY scene against every panel with the EXISTING Stage-5 matcher and
    write review/candidates.json + thumbs. Reuses _match_panels' own ranked scores (no
    duplicate scoring). Intro/outro scenes are excluded — their panels are deterministic
    (cold-open / loop-close), not a matching decision. Needs the embed backend up (LM Studio
    Qwen), same as Stage 5's matcher.

    `k` is a CAP on candidates per beat. k<=0 → emit ALL panels of the beat's OWN issue,
    ranked best-first (the correct panel sometimes ranks 11th+)."""
    root = _project_root(project_name)
    slug = root.name
    # k<=0 → ALL: no output cap (cap=None slices the whole list), and the matcher gets a
    # sentinel candidates_k so its argsort returns the full ranked pool (not a top-k).
    cap = None if k <= 0 else k
    match_k = 10**9 if cap is None else k
    narration_path = root / "narration.json"
    if not narration_path.exists():
        raise FileNotFoundError(f"narration.json missing: {narration_path}. Run Stage 3 first.")
    scenes = (_load_json(narration_path).get("scenes")) or []
    story = [s for s in scenes if not (s.get("is_intro") or s.get("is_outro"))]
    if not story:
        raise ValueError("no story scenes in narration.json — nothing to review")

    comic_ctx = _load_json(root / "comic_context.json")
    answer_ctx = _load_json(root / "answer_context.json")

    # Mirror stage_5.assemble_project's matcher inputs, then run the matcher in
    # candidates-only mode (fills the ranked shortlist, skips assignment + VLM rerank).
    from stages.stage_5.pipeline import _load_preprocessed_pages
    from stages.stage_5.shots import _match_panels
    pages_by_number = _load_preprocessed_pages(root)
    cluster_to_name = {int(kk): str(vv) for kk, vv in _load_json(root / "cluster_to_name.json").items()}

    # Per-issue candidate scoping (general, keyed on data). In a multi-issue project
    # (saga / Q&A countdown) each beat is anchored — via its page_ref — to ONE issue,
    # and its panel MUST come from that issue. The matcher scores whatever pool it is
    # given, so restricting pages_by_number to the beat's own issue keeps a "Punisher /
    # Thunderbolts #29" beat from grabbing a high-cosine Thanos page. Single-issue
    # projects have one group → identical to the old whole-pool call (no-op).
    page_to_issue = {
        int(p.get("page_number", 0) or 0): str(p.get("issue_label", "") or "")
        for p in pages_by_number.values()
    }
    multi_issue = len({v for v in page_to_issue.values() if v}) > 1

    def _issue_of(scene) -> str:
        return page_to_issue.get(int(scene.get("page_ref", 0) or 0), "") if multi_issue else ""

    # Citation + drawable_moment per scene, resolved ONCE (reused for the match query below and
    # each beat's "source" field). For a Q&A beat this carries the item's drawable_moment.
    src_by_id = {id(s): _beat_source(s, comic_ctx, answer_ctx, issue_label=_issue_of(s))
                 for s in story}

    def _query_text(scene) -> str:
        """Match query. For a Q&A beat, the item's drawable_moment (a PRECISE VISUAL of the exact
        panel to find) drives the query — per QA_QUERY_MODE it either leads with narration trailing
        ("blend") or stands alone ("drawable") — so the matcher (text cosine AND the SigLIP
        text→ART image blend, which both read this query) aims at the depicted moment, not the
        story text. A recap beat has no drawable_moment → the query is the narration, UNCHANGED."""
        narr = str(scene.get("text", "") or "")
        dm = str(src_by_id[id(scene)].get("drawable_moment", "") or "").strip()
        if not dm or QA_QUERY_MODE == "narration":
            return narr
        if QA_QUERY_MODE == "drawable":
            return dm
        # "blend": NARRATION leads (repeated QA_NARR_WEIGHT× to weigh the spoken line
        # more), drawable_moment trails to sharpen — so the pick matches what the
        # audience hears, not the flashiest panel on the page.
        return (" ".join([narr] * max(1, QA_NARR_WEIGHT)) + " " + dm).strip()

    # Group story scenes by their beat's issue, preserving order; score each group
    # against only that issue's pages. Beats with an unknown/blank issue fall back to
    # the full pool ("" group).
    groups: dict[str, list] = {}
    for s in story:
        groups.setdefault(_issue_of(s), []).append(s)

    # FIX B: a Q&A drawable_moment query is a visual description, so trust the SigLIP image
    # signal more than the recap default. Bump _img_index.PANEL_IMG_WEIGHT (read late-bound
    # by shots._blend_image_content) for the Q&A matcher calls only, then restore.
    from stages import _img_index
    qa_mode = _plot_source(root) == "answer_research"
    _orig_img_w = _img_index.PANEL_IMG_WEIGHT
    if qa_mode:
        _img_index.PANEL_IMG_WEIGHT = QA_PANEL_IMG_WEIGHT

    cands_by_id: dict[int, list] = {}
    try:
        for issue_label, group_scenes in groups.items():
            sub_pages = (
                {pn: p for pn, p in pages_by_number.items()
                 if page_to_issue.get(int(pn)) == issue_label}
                if issue_label else pages_by_number
            )
            group_out: list = []
            _match_panels([(s, _query_text(s)) for s in group_scenes],
                          sub_pages, cluster_to_name, project=slug,
                          candidates_out=group_out, candidates_k=match_k)
            for s, cl in zip(group_scenes, group_out):
                # Candidates-only ranking layer (dialog channel → vision judge);
                # the shared matcher above stays byte-identical for recap renders.
                q = _query_text(s)
                cl = _dialog_rescore(cl, q, pages_by_number)
                cl = _vlm_rank_top(cl, q, root, log=log)
                cands_by_id[id(s)] = cl
    finally:
        _img_index.PANEL_IMG_WEIGHT = _orig_img_w

    cands_out = [cands_by_id.get(id(s), []) for s in story]

    thumbs_dir = root / "review" / "thumbs"
    beats = []
    for scene, cand_list in zip(story, cands_out):
        # MOMENT-PRESENT check (Q&A only): if the vision judge ran and NO panel in the
        # cited issue scored above the floor, the moment isn't drawn here → the research
        # named the wrong issue. Flag it on the beat's source for the review UI + log.
        src = src_by_id[id(scene)]
        vlm_scores = [c["_vlm"] for c in cand_list if "_vlm" in c]
        if qa_mode and vlm_scores:
            best = max(vlm_scores)
            if best < QA_MOMENT_FLOOR:
                src["moment_warn"] = (
                    f"No panel in {src.get('title', 'this issue')} clearly depicts this moment "
                    f"(best {best:.0f}/10) — the cited issue number may be wrong.")
                log(f"[review-gate] ⚠ scene {scene.get('scene_id')}: {src['moment_warn']}")
        out_cands = []
        for c in cand_list[:cap]:
            page, pidx, panel, src = c["page"], c["panel_idx"], c["panel"], c["src"]
            rel = f"review/thumbs/p{page:03d}_{pidx}.jpg"
            thumb_abs = root / rel
            thumb = rel if (thumb_abs.exists() or _write_thumb(src, panel.get("bbox") or {}, thumb_abs)) else ""
            page_tb = (pages_by_number.get(page) or {}).get("text_blocks")
            out_cands.append({
                "page": int(page), "panel": int(pidx), "score": round(float(c["score"]), 4),
                "thumb": thumb, "desc": str(panel.get("description", "") or ""),
                "dialog": _panel_dialog_str(panel, page_tb),
            })
        pref = int(scene.get("page_ref", 0) or 0)
        pnref = int(scene.get("panel_ref", -1) if scene.get("panel_ref") is not None else -1)
        beats.append({
            "scene_id": int(scene.get("scene_id") or 0),
            "narration_text": str(scene.get("text", "") or ""),
            "page_ref": pref or None,
            "panel_ref": pnref if pnref >= 0 else None,
            "source": src_by_id[id(scene)],
            "candidates": out_cands,
        })

    out_path = root / "review" / "candidates.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"generated_at": _now_iso(), "beats": beats},
                                   indent=2, ensure_ascii=False))
    log(f"[review-gate] wrote {out_path} ({len(beats)} beats, "
        f"cap={'ALL' if cap is None else cap} candidates/beat)")
    return out_path


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m stages.review_gate",
        description="Review gate: build the panel-candidate shortlist / report gate status.")
    ap.add_argument("--project", required=True, help="Project slug under projects/.")
    ap.add_argument("--build-candidates", action="store_true",
                    help="Score panels and write review/candidates.json + thumbs.")
    ap.add_argument("--k", type=int, default=10,
                    help="Candidate CAP per beat. Default 10. Use 0 (or --all) for ALL panels "
                         "of the beat's own issue, ranked best-first.")
    ap.add_argument("--all", action="store_true",
                    help="Emit ALL panels of each beat's issue, ranked (same as --k 0).")
    args = ap.parse_args(argv)

    if args.build_candidates:
        path = build_candidates(args.project, k=0 if args.all else args.k)
        print(f"[review-gate] candidates -> {path}")
        return 0

    st = load_state(args.project)
    print(f"[review-gate] project={args.project} approved={st.get('approved')} "
          f"locks={len(st.get('locks') or {})} approved_at={st.get('approved_at')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
