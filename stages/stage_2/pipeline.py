"""
Stage 2 orchestrator: preprocess downloaded comic pages.

Reads the download manifest written by download.py, then for each page:
  SHA-256 cache check → Magi panel detect → VLM enrich → persist JSON.

Sequential processing keeps things simple and well under OpenRouter's
20 RPM / 50 RPD free-tier limits for a typical 22-page issue. The one
exception: the single-page front-matter/back-matter/issue-edge path (each
extract_page() call is independent, no continuity state) runs a contiguous
group of pages concurrently — see VLM_PAGE_WORKERS.
"""
import difflib
import json
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

from PIL import Image

from config import VLM_BATCH_SIZE, VLM_EXTRACT, VLM_MODEL, VLM_PAGE_WORKERS, get_project_dirs
from .._panel_index import DIALOG_TRUTH
from .cache import image_hash, load_cached, save_cached
from .panel_detect import assign_to_panels, detect_full
from .schema import PanelInfo, PreprocessedPage, TextBlock
from .vlm_extract import extract_page, extract_pages_batch, verify_page_descriptions

# The last N pages of an issue (covers/credits/ads/cliffhanger back-matter) are
# processed single-page instead of batched — batching mislabels them.
_BACKMATTER_TAIL = 4
# The first N pages (cover + recap + TITLE/CREDITS page) are ALSO front-matter that
# batching mislabels: a title/credits page mid-front (common after a cold open, e.g.
# "What If...? Galactus Transformed Hulk" puts its title+credits on p7) gets a mid-
# batch shift that keeps the SAME count, so the count-mismatch guard misses it and a
# neighbour's story description lands on the credits image. Single-page extraction has
# no continuity bias and classifies these correctly (title/credits -> cover/skip), so
# process the head one-by-one too. Covers a cover + a short cold open + the title page.
_FRONTMATTER_HEAD = 8

# Multi-issue (saga) equivalents of the two doc-level windows above: a saga flattens
# N issues into one GLOBAL page list (see the flatten loop below), so issue 2..N's own
# cover/recap and issue 1..N-1's own letters/ads sit MID-document, outside the doc-level
# HEAD/TAIL windows, and get batched with running narrative state instead of processed
# single-page — mislabel risk. Smaller than the doc-level 8/4 (cap VLM cost; only used
# when a page's OWN issue actually needs the no-continuity-bias treatment).
_ISSUE_FRONTMATTER_HEAD = 3
_ISSUE_BACKMATTER_TAIL = 2


def _page_state_issue_bounds(page_states: list[dict]) -> dict[str, tuple[int, int]]:
    """Map issue label -> (first global pn, last global pn) among `page_states` (the
    pre-VLM {"pn","label",...} records built by the flatten loop). Same idea as
    _issue_page_ranges() below but keyed off page_states' field names, since this runs
    BEFORE any final page dict exists."""
    bounds: dict[str, tuple[int, int]] = {}
    for s in page_states:
        lo, hi = bounds.get(s["label"], (s["pn"], s["pn"]))
        bounds[s["label"]] = (min(lo, s["pn"]), max(hi, s["pn"]))
    return bounds


def _is_near_own_issue_edge(pn: int, label: str, bounds: dict[str, tuple[int, int]]) -> bool:
    """True when page `pn` sits within _ISSUE_FRONTMATTER_HEAD of its OWN issue's first page,
    or within _ISSUE_BACKMATTER_TAIL of its OWN issue's last page — Fix 3's saga-aware
    equivalent of the doc-level `i < _FRONTMATTER_HEAD or i >= n - _BACKMATTER_TAIL` gate."""
    lo, hi = bounds.get(label, (pn, pn))
    return pn - lo < _ISSUE_FRONTMATTER_HEAD or hi - pn < _ISSUE_BACKMATTER_TAIL


def _single_page_where(
    i: int, n: int, s: dict, *, multi_issue: bool, issue_bounds: dict[str, tuple[int, int]]
) -> str | None:
    """Classify Phase 2 slot `i` as a single-page (no-continuity) site — front-matter head,
    back-matter tail, or (saga only) own-issue edge — or None when it belongs to the batched
    VLM path instead. Single source of truth for the condition so the main loop and its
    contiguous-group collector (parallel single-page processing) can never disagree."""
    if i < _FRONTMATTER_HEAD:
        return "front-matter head"
    if i >= n - _BACKMATTER_TAIL:
        return "back-matter tail"
    if multi_issue and _is_near_own_issue_edge(s["pn"], s["label"], issue_bounds):
        return "own-issue edge"
    return None


def _prevlm_gate(
    magi: dict, pn: int, label: str, bounds: dict[str, tuple[int, int]]
) -> tuple[str, str, str] | None:
    """Classify obvious cover/ad pages from Magi output ALONE, before any VLM call.

    Rule A — cover: the FIRST page of its own issue (deterministic from the manifest).
    Panel count can't tell a cover from a splash (both are one full-page box), position
    can. Safety: >=2 panels or any speech balloon on that first page reads as a
    cold-open story page (cover missing from the scan) → fall through to the VLM.
    Rule B — non-story: no panels AND no character boxes AND no speech balloons is not
    sequential art (house ad / letters / text page). All three must hold: whole-page
    story renders exist with 0 panels, but Magi still sees characters or speech there.

    Returns (page_type, skip_reason, page_summary), or None to proceed with the VLM.
    """
    texts = magi.get("texts") or []
    has_speech = any(str(t.get("type", "")) == "speech" for t in texts)
    n_panels = len(magi.get("panels") or [])
    lo, _hi = bounds.get(label, (None, None))
    if pn == lo and n_panels <= 1 and not has_speech:
        return "cover", "", "Cover page"
    if n_panels == 0 and not (magi.get("characters") or []) and not has_speech:
        corpus = " ".join(str(t.get("ocr", "") or t.get("text", "")) for t in texts)
        if _looks_like_ad(corpus):
            return "skip", "advertisement", "Advertisement (pre-VLM gate)"
        return "skip", "back_matter", "Non-story page (pre-VLM gate)"
    return None


def _prevlm_page(
    *, page_number: int, issue_label: str, image_path: Path,
    dimensions: tuple[int, int], content_hash: str,
    page_type: str, skip_reason: str, page_summary: str,
) -> dict:
    """Materialise a _prevlm_gate verdict as a PreprocessedPage dict (no VLM fields)."""
    width, height = dimensions
    return PreprocessedPage(
        page_number=page_number,
        source_image=str(image_path.resolve()),
        image_dimensions={"width": width, "height": height},
        is_story_page=False, page_type=page_type, panels=[], text_blocks=[],
        page_summary=page_summary, issue_label=issue_label,
        vlm_model="", vlm_model_used="", content_hash=content_hash,
        preprocessing_method="heuristic_skip", skip_reason=skip_reason,
    ).to_dict()


def _finish_single_page(
    entry: dict, *, project_root: Path, log: Callable[[str], None], story_context: str,
) -> dict:
    """Second half of single-page Phase 2 processing — the part that calls extract_page()
    (a VLM round-trip with no prior_page/running_state) and is therefore safe to run
    concurrently across a contiguous group (see _single_page_where in the main loop):
    materialise the pre-VLM gate verdict, or run the full _build_page_from_single() build.
    `entry` is one item from the group collected by the sequential Magi-detect + gate pass
    (carries s/dims/magi/gate — cheap, no network, so those logs stay in page order).
    A group runs several independent pages CONCURRENTLY, so one page's unexpected crash is
    caught here and downgraded to the same soft-fail shape extract_page() itself already
    returns on total VLM exhaustion (page_type=skip/vlm_failure) — it must not take down its
    already-running siblings, and the existing cache-invalidation guard (Phase 1 above, "if
    cached.get('skip_reason') == 'vlm_failure': cached = None") retries it on the next run.
    Returns {"page_dict", "gated", "image_path"} for the caller to persist in page order."""
    s, dims, magi, gate = entry["s"], entry["dims"], entry["magi"], entry["gate"]
    if gate is not None:
        g_type, g_reason, g_summary = gate
        log(f"[preprocess]   p{s['pn']:03d}: pre-VLM gate → {g_type}"
            f"{('/' + g_reason) if g_reason else ''} (VLM skipped)")
        page_dict = _prevlm_page(
            page_number=s["pn"], issue_label=s["label"], image_path=s["img"],
            dimensions=dims, content_hash=s["hash"],
            page_type=g_type, skip_reason=g_reason, page_summary=g_summary,
        )
        save_cached(project_root, s["pn"], s["hash"], page_dict)
        return {"page_dict": page_dict, "gated": True, "image_path": s["img"]}
    try:
        page_dict = _build_page_from_single(
            page_number=s["pn"], issue_label=s["label"], image_path=s["img"],
            panels_raw=magi["panels"], dimensions=dims, project_root=project_root,
            log=log, story_context=story_context, content_hash=s["hash"],
            magi_data=magi,
        )
    except Exception as exc:
        log(f"[preprocess]   p{s['pn']:03d}: ✗ single-page build crashed: "
            f"{type(exc).__name__}: {exc} — marking vlm_failure")
        page_dict = _prevlm_page(
            page_number=s["pn"], issue_label=s["label"], image_path=s["img"],
            dimensions=dims, content_hash=s["hash"],
            page_type="skip", skip_reason="vlm_failure", page_summary="",
        )
        save_cached(project_root, s["pn"], s["hash"], page_dict)
    return {"page_dict": page_dict, "gated": False, "image_path": s["img"]}


def _collect_single_page_group(
    page_states: list[dict], i: int, n: int, *,
    magi_by_pn: dict[int, dict], issue_bounds: dict[str, tuple[int, int]],
    multi_issue: bool, log: Callable[[str], None],
) -> tuple[list[dict], int]:
    """Collect the CONTIGUOUS run of single-page (no-continuity) slots starting at `i` —
    front-matter head, back-matter tail, or (saga) own-issue edge — stopping at the first
    cache hit or batched-path slot. Magi detect + the pre-VLM gate verdict are decided here,
    SEQUENTIALLY (cheap, no network call), so those per-page logs stay in page order even
    though the VLM-calling second half (_finish_single_page, via _process_single_page_group)
    may run concurrently. Returns (group, next_i) — next_i is where the caller's `i` resumes."""
    group: list[dict] = []
    while i < n:
        s = page_states[i]
        if s["cached"] is not None:
            break
        where = _single_page_where(i, n, s, multi_issue=multi_issue, issue_bounds=issue_bounds)
        if where is None:
            break
        with Image.open(s["img"]) as im:
            dims = im.size
        magi = magi_by_pn.get(s["pn"]) or detect_full(s["img"])
        log(f"[preprocess]   p{s['pn']:03d}: Magi → {len(magi['panels'])} panel(s) "
            f"({where} → single-page, no batch)")
        gate = _prevlm_gate(magi, s["pn"], s["label"], issue_bounds)
        group.append({"s": s, "dims": dims, "magi": magi, "gate": gate})
        i += 1
    return group, i


def _process_single_page_group(
    group: list[dict], *, project_root: Path, log: Callable[[str], None], story_context: str,
) -> list[dict]:
    """Run _finish_single_page over `group`: CONCURRENTLY (ThreadPoolExecutor) when
    VLM_PAGE_WORKERS > 1 and the group has more than one page — extract_page() takes no
    prior_page/running_state, so every entry is independent — otherwise strictly serial,
    one page at a time, same as the original loop (VLM_PAGE_WORKERS=1 always takes this
    branch). Returns per-page results in GROUP (page) ORDER regardless of thread completion
    order, so the caller's results/prev_page_dict bookkeeping is byte-identical to serial."""
    workers = min(VLM_PAGE_WORKERS, len(group))
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(
                lambda e: _finish_single_page(
                    e, project_root=project_root, log=log, story_context=story_context),
                group,
            ))
    return [
        _finish_single_page(e, project_root=project_root, log=log, story_context=story_context)
        for e in group
    ]


# Description↔bbox verify gate (crop + look ground-truth check — see vlm_extract.
# verify_page_descriptions). Master 2026-07-24: DEFAULT OFF — panels are now hand-picked in
# review, so VLM descriptions no longer decide the panel, and the extra VLM round-trip per page
# is dead cost. DESC_VERIFY=1 re-enables the gate (pages then carry desc_verified / the anchor-
# trust path in shots.py reactivates). Off = every page treated as trusted (old-project parity).
DESC_VERIFY = os.getenv("DESC_VERIFY", "0").strip().lower() not in ("0", "false", "no", "")

# Coverage guard: flag story pages where Magi's panel boxes cover suspiciously little of
# the page (likely MISSED panels). Default ON; COVERAGE_GUARD=0/false disables.
COVERAGE_GUARD = os.getenv("COVERAGE_GUARD", "1").strip().lower() not in ("0", "false", "no", "")

# difflib SequenceMatcher ratio below which a panel's VLM `text` is judged NOT to match its
# Magi OCR ground truth. Tuned to ~0.4: a garbled-but-genuine OCR of the same line ("PUNY
# GOD" vs "PUNY G0D") ratios ~0.8, while a fabricated line ("WE'VE REACHED THE BIG BANG" vs
# the real "SO NOW WHAT DO WE DO?") ratios <0.2 — so 0.4 flags fabrication without tripping
# on OCR noise. Best-PAIR (max over line pairs): we only flag when NOTHING the VLM wrote
# matches ANY OCR line, the strong signal of a wholly invented panel transcription.
_DIALOG_MISMATCH_RATIO = 0.4

# Magi typically boxes 80-95% of a Western story page. Below this fraction it likely MISSED
# panels (splash bleed, low-contrast gutters). 0.5 is deliberately loose so a legitimately
# sparse splash (one big panel still covering most of the page) does NOT fire — only a clear
# under-detection does.
_COVERAGE_MIN = 0.5

# LM Studio sequencing knob: LM Studio serves the Qwen3-Embedding-8B model (:1234) as a
# SEPARATE OS PROCESS — release_model() below only frees Magi, it can never touch LM
# Studio. If LM Studio's JIT keep-alive (default 60min TTL) leaves the ~7-8GB embed
# server loaded, it sits co-resident with Magi's ~3-4GB through the whole Phase 1.5
# batch-detect below and pushes a 16GB Mac into swap. LMS_AUTO_UNLOAD=0 disables both
# helpers (e.g. no LM Studio installed, or plenty of RAM to spare).
LMS_AUTO_UNLOAD = os.getenv("LMS_AUTO_UNLOAD", "1").strip().lower() not in ("0", "false", "no", "")


def _lms_relevant() -> bool:
    """True only when the configured embedding backend actually IS LM Studio's
    OpenAI-compatible server — no reason to touch LM Studio for Gemini/Azure/local."""
    import config
    return config.EMBED_BACKEND in ("qwen", "openai")


def _lms_bin() -> str | None:
    """Locate the `lms` CLI. None if LM Studio isn't installed on this machine."""
    binary = shutil.which("lms")
    if binary:
        return binary
    fallback = Path.home() / ".lmstudio" / "bin" / "lms"
    return str(fallback) if fallback.exists() else None


def _lms_unload_all(log: Callable[[str], None] = print) -> None:
    """Unload every LM Studio-served model right before Stage 2's memory-heavy Magi
    batch-detect phase, freeing the ~7-8GB Qwen3-Embedding-8B server process for Magi.
    Best-effort: missing binary / non-zero exit / timeout are all logged and swallowed,
    never raised — a machine without LM Studio (or on a non-qwen backend) runs
    completely unaffected."""
    if not LMS_AUTO_UNLOAD or not _lms_relevant():
        return
    binary = _lms_bin()
    if not binary:
        return
    try:
        subprocess.run([binary, "unload", "--all"], capture_output=True, timeout=20, check=False)
    except Exception as exc:
        log(f"[lms] unload --all skipped ({type(exc).__name__}: {exc})")
        return
    _lms_kill_zombie_nodes(log)


_ZOMBIE_NODE_MARKER = ".lmstudio/.internal/utils/node"
_ZOMBIE_RSS_FLOOR_KB = 3_000_000  # 3GB — LM Studio 0.4.18's known zombie sits at 5-7GB


def _lms_kill_zombie_nodes(log: Callable[[str], None] = print) -> None:
    """LM Studio 0.4.18 bug: after `unload --all`, the `.lmstudio/.internal/utils/node`
    helper process occasionally survives as a zombie holding 5-7GB RAM even though
    `lms ps` reports no loaded model -- enough to push a 16GB Mac into swap. Sweep it,
    but only when `lms ps` confirms nothing is genuinely loaded/computing (a real,
    in-use server must never be killed) and the process is fat enough to be the known
    zombie rather than a fresh legitimate one. Best-effort: any failure is logged and
    swallowed, same contract as _lms_unload_all above."""
    binary = _lms_bin()
    if not binary:
        return
    try:
        time.sleep(2)
        ps = subprocess.run([binary, "ps"], capture_output=True, timeout=15, check=False, text=True)
        if any(tag in (ps.stdout or "") for tag in ("LOADED", "COMPUTING")):
            return
        procs = subprocess.run(
            ["ps", "-axo", "pid,rss,command"], capture_output=True, timeout=15, check=False, text=True
        )
        for line in (procs.stdout or "").splitlines():
            if _ZOMBIE_NODE_MARKER not in line:
                continue
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            try:
                pid, rss_kb = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if rss_kb <= _ZOMBIE_RSS_FLOOR_KB:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                log(f"[lms] killed zombie node pid={pid} rss={rss_kb / 1_000_000:.1f}GB")
            except Exception as exc:
                log(f"[lms] zombie kill pid={pid} failed ({type(exc).__name__}: {exc})")
    except Exception as exc:
        log(f"[lms] zombie sweep skipped ({type(exc).__name__}: {exc})")


def _ensure_embed_model_loaded(log: Callable[[str], None] = print) -> None:
    """JIT-load LM Studio's embedding model back in right before index_project() needs
    it — _lms_unload_all() above may just have evicted it, and JIT auto-load-on-request
    is an LM Studio setting, not a guarantee. Best-effort: any failure here just falls
    through to _embedding.py's own graceful degrade (down server -> None -> skipped)."""
    if not LMS_AUTO_UNLOAD or not _lms_relevant():
        return
    binary = _lms_bin()
    if not binary:
        return
    import config
    model_key = config.EMBED_OPENAI_MODEL
    models_url = config.EMBED_OPENAI_URL.rsplit("/", 1)[0] + "/models"
    try:
        with urllib.request.urlopen(models_url, timeout=5) as r:
            loaded_ids = {m.get("id") for m in json.load(r).get("data", [])}
        if model_key in loaded_ids:
            return
    except Exception as exc:
        log(f"[lms] model-list probe failed ({type(exc).__name__}: {exc}); loading anyway")
    try:
        subprocess.run([binary, "load", model_key], capture_output=True, timeout=120, check=False)
    except Exception as exc:
        log(f"[lms] load {model_key!r} skipped ({type(exc).__name__}: {exc})")


def _write_panel_viz(project_root: Path, results: list[dict], log: Callable[[str], None]) -> None:
    """Draw each page's detected panel bboxes (green box, width 5 + black-bg green index
    number) over the raw page image and save to <project>/panel_viz/page_NNN_panels.jpg —
    same look as scripts/magi_panel_viz.py, but generated from the already-saved
    preprocess results (no Magi re-run). Manual-QA output only."""
    from PIL import ImageDraw, ImageFont
    out_dir = project_root / "panel_viz"
    out_dir.mkdir(exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
    except OSError:
        font = ImageFont.load_default()
    written = 0
    for r in results:
        src = r.get("source_image")
        if not src or not Path(src).exists():
            continue
        img = Image.open(src).convert("RGB")
        draw = ImageDraw.Draw(img)
        for i, p in enumerate(r.get("panels") or [], 1):
            b = p.get("bbox") or {}
            x1, y1 = b.get("x", 0), b.get("y", 0)
            x2, y2 = x1 + b.get("w", 0), y1 + b.get("h", 0)
            draw.rectangle([x1, y1, x2, y2], outline=(0, 255, 0), width=5)
            draw.rectangle([x1, y1, x1 + 44, y1 + 44], fill=(0, 0, 0))
            draw.text((x1 + 10, y1 + 6), str(i), fill=(0, 255, 0), font=font)
        img.save(out_dir / f"page_{int(r.get('page_number', 0)):03d}_panels.jpg", quality=92)
        written += 1
    log(f"[panel-viz] {written} page overlay(s) → {out_dir}")


def preprocess_project(
    project_name: str,
    *,
    progress: Callable[[str], None] | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    """
    Run preprocessing on already-downloaded comic pages.
    Reads raw_comic/manifest.json written by the download stage.
    Returns list of page dicts (also written to disk as individual JSON files).
    """
    log = progress or print

    project_root = get_project_dirs(project_name)["root"]
    manifest_path = project_root / "raw_comic" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No download manifest found for project '{project_name}'. "
            "Run the Download stage first."
        )

    manifest = json.loads(manifest_path.read_text())
    total_chapters = len(manifest)
    log(f"[preprocess] project={project_name} — {total_chapters} chapter(s) from manifest")

    # Identity Hook 0: catch a wrong-comic plot_summary BEFORE it can bias VLM
    # extraction (see stages/stage_2/identity_check.py module docstring). Never
    # raises Stage 2 — a bad ctx read/write here just skips the pre-check.
    try:
        _run_identity_precheck(project_root, log)
    except Exception as exc:
        log(f"[identity]   pre-check crashed unexpectedly: {type(exc).__name__}: {exc}")

    story_context = _load_story_context(project_root, log)

    # Flatten manifest into a single ordered list of (page_number, label, path).
    # Continuity in reading flow > chapter boundaries — we batch across chapters
    # only if they're adjacent in the manifest, which they always are.
    flat: list[tuple[int, str, Path]] = []
    global_page_num = 0
    for chapter in manifest:
        label = chapter["label"]
        for img_path_str in chapter["pages"]:
            img_path = Path(img_path_str)
            if not img_path.exists():
                log(f"[preprocess]   ⚠ missing: {img_path.name} — skipping")
                continue
            global_page_num += 1
            flat.append((global_page_num, label, img_path))

    log(f"[preprocess] {len(flat)} total page(s); batch_size={VLM_BATCH_SIZE}")
    if not VLM_EXTRACT:
        log("[preprocess] VLM_EXTRACT=0 → Magi-only (no OpenRouter desc)")

    # ── Phase 1: hash every page, separate cached vs uncached (preserve order) ──
    page_states: list[dict] = []  # parallel to flat; carries "cached" dict OR None
    for pn, label, img_path in flat:
        h = image_hash(img_path)
        cached = None if force_refresh else load_cached(project_root, pn, h, img_path, log=log)
        if cached is not None and cached.get("skip_reason") == "vlm_failure":
            cached = None  # invalidate prior failures so we retry with batch
        page_states.append({"pn": pn, "label": label, "img": img_path, "hash": h, "cached": cached})

    cached_count = sum(1 for s in page_states if s["cached"] is not None)
    log(f"[preprocess] cache: {cached_count}/{len(page_states)} pages have valid results — "
        f"{len(page_states) - cached_count} need VLM")

    # Per-issue (start_pn, end_pn) bounds, keyed off page_states so Fix 3 below can tell
    # whether page i sits near the start/end of ITS OWN issue. Single-issue projects get
    # exactly one entry here, so `_multi_issue` is False and the per-issue window never
    # fires — the loop below falls through to the original doc-level-only gate untouched.
    _issue_bounds = _page_state_issue_bounds(page_states)
    _multi_issue = len(_issue_bounds) > 1

    # ── Phase 1.5: batch Magi panel-detection for all uncached pages ──
    # Magi detection depends ONLY on the page image (independent of the running_state /
    # prior-page continuity that the VLM-describe loop below threads), so precomputing it
    # in one batched pass is safe and cuts local forward-pass launches — the biggest Stage 2
    # compute block. Cached pages already carry their panels → skipped. Any failure leaves
    # magi_by_pn empty and the per-page call sites fall back to detect_full() individually.
    from config import MAGI_BATCH_SIZE
    from .panel_detect import detect_full_batch
    magi_by_pn: dict[int, dict] = {}
    _uncached = [ps for ps in page_states if ps["cached"] is None]
    if _uncached:
        # Free LM Studio's embed server BEFORE Magi loads — only worth doing when Magi
        # is actually about to run (all-cache-hit projects never touch Magi).
        _lms_unload_all(log)
    if _uncached and MAGI_BATCH_SIZE > 1:
        log(f"[preprocess] ▶ Magi batch-detect {len(_uncached)} uncached page(s) "
            f"(batch={MAGI_BATCH_SIZE})")
        try:
            t_magi = time.time()
            _magi_list = detect_full_batch([ps["img"] for ps in _uncached],
                                           batch_size=MAGI_BATCH_SIZE, log=log)
            for ps, m in zip(_uncached, _magi_list):
                magi_by_pn[ps["pn"]] = m
            log(f"[preprocess]   ✓ Magi batch-detect done in {time.time() - t_magi:.1f}s")
        except Exception as exc:
            log(f"[preprocess]   ✗ Magi batch-detect failed ({type(exc).__name__}: {exc}); "
                f"falling back to per-page detection")
            magi_by_pn = {}

    # ── Phase 2: walk pages in order, batching uncached runs ──
    # Overlap pattern: each batch carries the IMMEDIATELY PRIOR page (its full extracted
    # data + image) as context. The prior page is NOT re-processed (first-wins lock-in)
    # — if VLM ignores instructions and emits an entry for it, vlm_extract drops it.
    results: list[dict] = []
    running_state = ""  # Fallback memory used when no prior page is available (first batch only)
    prev_page_dict: dict | None = None
    prev_image_path: Path | None = None
    i = 0
    n = len(page_states)
    while i < n:
        s = page_states[i]
        if s["cached"] is not None:
            log(f"[preprocess]   ✓ cache hit p{s['pn']:03d} ({s['img'].name})")
            results.append(s["cached"])
            # Cached page becomes the prior-context for whatever batch comes next.
            prev_page_dict = s["cached"]
            prev_image_path = s["img"]
            summary = (s["cached"].get("page_summary") or "").strip()
            if summary and not running_state:
                running_state = summary[:240]
            i += 1
            continue

        # Back-matter (credits/ads/covers/cliffhanger) clusters in the last few
        # pages, where multi-image batching mislabels it: the VLM's continuity
        # bias + a dropped/shifted page index makes a credits page inherit a
        # neighbor's story description and be tagged "story" (then chosen as the
        # outro). Single-page extract_page() has no continuity bias and is
        # verified to classify these correctly (credits -> skip/solicit_credits),
        # so we process the trailing pages one-by-one via the per-page path.
        # Fix 3: a page near the START or END of ITS OWN issue (saga only — see
        # _multi_issue above) gets the same no-continuity-bias single-page treatment as
        # the doc-level head/tail, even though it sits mid-document globally.
        _where = _single_page_where(i, n, s, multi_issue=_multi_issue, issue_bounds=_issue_bounds)
        if _where is not None:
            # extract_page() takes no prior_page/running_state — every single-page slot is
            # independent of every other, so a CONTIGUOUS run of them (e.g. the whole
            # _FRONTMATTER_HEAD block) can run concurrently.
            group, i = _collect_single_page_group(
                page_states, i, n, magi_by_pn=magi_by_pn, issue_bounds=_issue_bounds,
                multi_issue=_multi_issue, log=log,
            )
            finished = _process_single_page_group(
                group, project_root=project_root, log=log, story_context=story_context,
            )
            # Persist results and update prior-context in PAGE ORDER, regardless of which
            # thread finished first — byte-identical to running the old serial loop.
            for f in finished:
                results.append(f["page_dict"])
                if f["gated"]:
                    # Deliberately NOT updating prev_page_dict — a cover/ad carries no
                    # story continuity for the next batch's prior-page context.
                    continue
                prev_page_dict = f["page_dict"]
                prev_image_path = f["image_path"]
            continue

        # Collect a contiguous run of uncached pages up to VLM_BATCH_SIZE (never
        # crossing into the single-page back-matter tail).
        batch_end = i
        tail_start = n - _BACKMATTER_TAIL
        while (batch_end < n and page_states[batch_end]["cached"] is None
               and (batch_end - i) < VLM_BATCH_SIZE and batch_end < tail_start):
            batch_end += 1
        batch = page_states[i:batch_end]
        batch_pns = [b["pn"] for b in batch]
        overlap_note = f" + prior p{prev_page_dict['page_number']:03d}" if prev_page_dict is not None else ""
        log(f"[preprocess] ▶ VLM batch of {len(batch)} fresh page(s): {batch_pns}{overlap_note}")

        # Magi v3 full extraction: panels + characters (with cluster_id) + texts (with OCR + speaker)
        # Each page then passes the pre-VLM gate: obvious covers/ads are materialised
        # immediately from Magi output alone and EXCLUDED from the VLM batch.
        batch_entries: list[dict] = []  # per page, in order: b/dims/magi/panels/gate
        for b in batch:
            t_panel = time.time()
            with Image.open(b["img"]) as im:
                dims = im.size
            magi = magi_by_pn.get(b["pn"]) or detect_full(b["img"])
            panels_raw = magi["panels"]
            log(f"[preprocess]   p{b['pn']:03d}: Magi → {len(panels_raw)} panel(s), "
                f"{len(magi['characters'])} char(s), {len(magi['texts'])} text(s) "
                f"in {time.time() - t_panel:.1f}s")
            gate = _prevlm_gate(magi, b["pn"], b["label"], _issue_bounds)
            if gate is not None:
                log(f"[preprocess]   p{b['pn']:03d}: pre-VLM gate → {gate[0]}"
                    f"{('/' + gate[1]) if gate[1] else ''} (VLM skipped)")
            batch_entries.append(
                {"b": b, "dims": dims, "magi": magi, "panels": panels_raw, "gate": gate})

        vlm_entries = [e for e in batch_entries if e["gate"] is None]

        # Call multi-image VLM with overlap (gated pages excluded). Returns None on
        # total failure → fall back per-page.
        vlm_pages, new_state, model_used = (None, None, "")
        if vlm_entries and not VLM_EXTRACT:
            # Magi-only (VLM_EXTRACT=0): no OpenRouter describe pass. Feed each non-gated
            # panel an EMPTY vlm_data ({}); _assemble_page_dict then builds the page purely
            # from Magi — bboxes + OCR dialog (with bbox, so bubble-inpaint survives) — and
            # defaults page_type to "story". {} is falsy-but-not-None, so the consume loop
            # below takes the assemble branch (not the per-page VLM fallback).
            vlm_pages, model_used = [{} for _ in vlm_entries], "magi-only"
        elif vlm_entries:
            t_vlm = time.time()
            vlm_pages, new_state, model_used = extract_pages_batch(
                [e["b"]["img"] for e in vlm_entries],
                [e["panels"] for e in vlm_entries],
                progress=log,
                story_context=story_context,
                running_state=running_state,
                prior_page=prev_page_dict,
                prior_image_path=prev_image_path,
            )
            vlm_dt = time.time() - t_vlm
            if vlm_pages is not None:
                log(f"[preprocess]   ✓ batch ok in {vlm_dt:.1f}s via {model_used}")
                running_state = new_state or running_state
            else:
                log(f"[preprocess]   ✗ batch failed — falling back to per-page extract_page()")

        vlm_iter = iter(vlm_pages or [])
        # First pass (cheap, no network for the common assemble path): build each page's
        # pre-verify dict and flag which ones need the DESC_VERIFY gate (a VLM round-trip).
        # gated → save, no verify; assembled → save + verify; per-page fallback → neither
        # save nor verify (matches prior behavior exactly).
        pending: list[dict] = []
        for e in batch_entries:
            b, dims, magi, panels_raw = e["b"], e["dims"], e["magi"], e["panels"]
            if e["gate"] is not None:
                g_type, g_reason, g_summary = e["gate"]
                page_dict = _prevlm_page(
                    page_number=b["pn"], issue_label=b["label"], image_path=b["img"],
                    dimensions=dims, content_hash=b["hash"],
                    page_type=g_type, skip_reason=g_reason, page_summary=g_summary,
                )
                pending.append({"b": b, "dims": dims, "magi": magi, "panels": panels_raw,
                                "page_dict": page_dict, "gated": True, "save": True, "verify": False})
                continue
            vlm_page = next(vlm_iter, None) if vlm_pages is not None else None
            if vlm_page is None:
                # Whole-batch VLM failure, or the VLM returned fewer pages than sent
                # (the old zip() silently DROPPED those pages) — per-page fallback.
                page_dict = _build_page_from_single(
                    page_number=b["pn"], issue_label=b["label"], image_path=b["img"],
                    panels_raw=panels_raw, dimensions=dims, project_root=project_root,
                    log=log, story_context=story_context, content_hash=b["hash"],
                    magi_data=magi,
                )
                pending.append({"b": b, "dims": dims, "magi": magi, "panels": panels_raw,
                                "page_dict": page_dict, "gated": False, "save": False, "verify": False})
            else:
                page_dict = _assemble_page_dict(
                    page_number=b["pn"], issue_label=b["label"], image_path=b["img"],
                    panels_raw=panels_raw, dimensions=dims, vlm_data=vlm_page,
                    content_hash=b["hash"], vlm_model_used=vlm_page.get("_vlm_model_used", model_used),
                    magi_data=magi, log=log,
                )
                pending.append({"b": b, "dims": dims, "magi": magi, "panels": panels_raw,
                                "page_dict": page_dict, "gated": False, "save": True, "verify": True})

        # DESC_VERIFY the assembled pages CONCURRENTLY — each is an independent VLM
        # round-trip (no shared state), so serial-in-a-loop was pure added latency.
        _verify_pending_concurrently(pending, story_context=story_context, log=log)

        # Second pass: persist + order-preserving results + prior-context update.
        for p in pending:
            b, page_dict = p["b"], p["page_dict"]
            if p["save"]:
                save_cached(project_root, b["pn"], b["hash"], page_dict)
            results.append(page_dict)
            if not p["gated"]:
                # The LAST non-gated page of this batch becomes prior-context for the next.
                prev_page_dict = page_dict
                prev_image_path = b["img"]

        i = batch_end

    log(f"[preprocess] running_state final: {running_state[:200]}")
    _reclassify_mid_doc_covers(results, project_root, log)
    _demote_credits_pages(results, project_root, log)
    _demote_backmatter_tail(results, project_root, log)

    # v5 Phase 2: resolve Magi cluster_ids → character names via VLM
    _resolve_clusters_after_preprocess(results, project_root, log)

    story_count = sum(1 for r in results if r.get("is_story_page"))
    log(f"[preprocess] done — {len(results)} pages processed, {story_count} story pages")

    # Panel-viz overlays (green box + index per detected panel) for manual QA of Magi
    # detection → <project>/panel_viz/. ADDITIVE output only: nothing downstream reads
    # these images, and any failure never fails Stage 2.
    try:
        _write_panel_viz(project_root, results, log)
    except Exception as exc:
        log(f"[panel-viz] skipped (error): {exc}")

    # Identity Hook 2: authoritative repair, runs regardless of --no-enrich — the
    # safety net that replaces the human hand-fix from the Moon Knight #9 incident
    # (see identity_check module docstring). Never raises Stage 2.
    try:
        _run_identity_repair(project_root, results, log, force_refresh=force_refresh)
    except Exception as exc:
        log(f"[identity]   repair hook crashed unexpectedly: {type(exc).__name__}: {exc}")

    # Free Magi (local vision model, ~3-4GB float32 on Mac) BEFORE embedding — otherwise it
    # stays co-resident with the 8B Qwen embed server (~6GB) and OOMs a 16GB Mac at the embed
    # step. Sequential (Magi → free → embed), NOT parallel. See panel_detect.release_model.
    from .panel_detect import release_model
    release_model()

    # Persist panel embeddings to Qdrant so Stage 5 matches against pre-computed
    # vectors instead of re-embedding every panel each run. Graceful no-op if
    # Qdrant/embeddings are unavailable (matcher falls back to in-memory embed).
    from .._panel_index import index_project
    from config import PANEL_TEXT_EMBED
    if PANEL_TEXT_EMBED:                    # only pay the LM Studio JIT model-load when indexing
        _ensure_embed_model_loaded(log)
    index_project(project_name, {int(r.get("page_number", 0)): r for r in results}, log=log)

    # Feature A: ALSO embed the panel PIXELS into SigLIP's joint image-text space so Stage 5
    # has a desc-FREE second matching signal — a fabricated VLM description can fake a high
    # TEXT cosine but not the image cosine (see _img_index). Runs AFTER Magi's release_model()
    # above so SigLIP loads into freed memory, and index_project_images frees SigLIP itself.
    # Guarded by availability + PANEL_IMG_EMBED; any failure NEVER fails Stage 2 (the matcher
    # simply falls back to today's text-only path).
    try:
        from .._img_index import index_project_images
        index_project_images(project_name, {int(r.get("page_number", 0)): r for r in results}, log=log)
    except Exception as exc:
        (log or print)(f"[img-index] skipped (error): {exc}")

    # Q&A subject-panel ranking: for an answer_research (Q&A) project ONLY, rank every
    # panel by how strongly it features the QUESTION'S subject character, so Stage 5 can
    # bookend the video (multi-panel intro / outro) with that subject instead of a free-
    # matcher spectacle pick. Text-only + fast; recaps skip it; a hand-written
    # subject_panels.json ("manual": true) is never overwritten. Never fails Stage 2.
    try:
        ctx = json.loads((project_root / "comic_context.json").read_text())
        if str(ctx.get("plot_source") or "") == "answer_research":
            from ..subject_panels import build_subject_panels
            answer_ctx = json.loads((project_root / "answer_context.json").read_text())
            build_subject_panels(project_name, answer_ctx, results, log=log)
    except Exception as exc:
        (log or print)(f"[subject-panels] skipped (error): {exc}")

    return results


def _resolve_clusters_after_preprocess(
    pages: list[dict], project_root: Path, log: Callable[[str], None]
) -> None:
    """v5 Phase 2: VLM-name Magi character clusters. Skips if no clusters found
    or cluster_to_name.json already exists."""
    out_path = project_root / "cluster_to_name.json"
    if out_path.exists():
        log(f"[preprocess] cluster_to_name.json already exists — skipping naming")
        return
    # Any cluster_ids in any panel?
    has_clusters = any(
        panel.get("cluster_ids") for page in pages for panel in (page.get("panels") or [])
    )
    if not has_clusters:
        log("[preprocess] no Magi cluster_ids — skipping VLM naming (run with --force after updating Magi)")
        return

    ctx_path = project_root / "comic_context.json"
    if not ctx_path.exists():
        log("[preprocess] no comic_context.json — skipping cluster naming")
        return
    try:
        comic_context = json.loads(ctx_path.read_text())
    except json.JSONDecodeError:
        log("[preprocess] comic_context.json unreadable — skipping cluster naming")
        return

    log("[preprocess] resolving Magi cluster names via VLM…")
    from .cluster_namer import resolve_cluster_names
    try:
        resolve_cluster_names(pages, comic_context, project_root, progress=log)
    except Exception as exc:
        log(f"[preprocess]   cluster naming failed: {type(exc).__name__}: {exc}")


def _issue_page_ranges(pages: list[dict]) -> dict[str, tuple[int, int]]:
    """Map issue_label -> (first global page_number, last global page_number) among `pages`.
    Feeds the multi-issue-aware guards below: each looks at a page's position within its OWN
    issue's range instead of the whole flattened saga doc. A single-issue project has exactly
    one label, so its range == the whole doc — callers gate on `len(ranges) > 1` to fall back
    to the original doc-level-only behaviour unchanged (byte-identical)."""
    ranges: dict[str, tuple[int, int]] = {}
    for p in pages:
        label = str(p.get("issue_label", "") or "")
        pn = int(p.get("page_number", 0) or 0)
        lo, hi = ranges.get(label, (pn, pn))
        ranges[label] = (min(lo, pn), max(hi, pn))
    return ranges


def _reclassify_mid_doc_covers(
    pages: list[dict], project_root: Path, log: Callable[[str], None]
) -> None:
    """A real cover sits at the edges of the issue. A page tagged 'cover' in the middle is
    almost always a misclassified splash — flip it to story so Narration can use it.

    Multi-issue (saga) awareness: 'the edges' means the edges of the WHOLE flattened doc only
    for a single issue. In a saga, issue 2..N each have their own legitimate front cover sitting
    mid-document (global pn > 2) — the doc-level check flipped those real covers to story,
    polluting the panel pool/cold-open. Fix: a cover within the first ~2 pages of ITS OWN
    issue_label range is still legitimate; only a cover mid-ISSUE (the original bug) flips.
    Single issue → one range == the whole doc → identical to the original check."""
    total = max((int(p.get("page_number", 0) or 0) for p in pages), default=0)
    if total < 5:
        return
    ranges = _issue_page_ranges(pages)
    multi_issue = len(ranges) > 1
    for p in pages:
        if p.get("page_type") != "cover":
            continue
        pn = int(p.get("page_number", 0) or 0)
        if multi_issue:
            issue_start, _issue_end = ranges.get(str(p.get("issue_label", "") or ""), (pn, pn))
            if pn - issue_start <= 1 or pn >= total:
                continue
        elif pn <= 2 or pn >= total:
            continue
        log(f"[preprocess] mid-doc cover at p{pn:03d}/{total} → reclassifying to story (Option 1 heuristic)")
        p["page_type"] = "story"
        p["is_story_page"] = True
        h = str(p.get("content_hash", "") or "")
        if h:
            try:
                save_cached(project_root, pn, h, p)
            except Exception as exc:
                log(f"[preprocess]   ⚠ couldn't persist reclassification for p{pn}: {exc}")


def _demote_credits_pages(
    pages: list[dict], project_root: Path, log: Callable[[str], None]
) -> None:
    """Demote any 'story' page whose FINAL merged text reads like a title/credits/
    recap page (issue-title logo + creative-team credits + recap blurb). The inline
    extraction guard can miss these — a stylized title OCRs poorly mid-extraction, or
    the VLM tags the page 'story' anyway — so re-check the assembled text here, where
    it is complete, and flip matches to skip so they are NEVER chosen as a story panel.
    General: runs on every issue (a credits/title page can sit mid-front after a cold
    open, e.g. p7 of 'What If...? Galactus Transformed Hulk').

    Guarded by `_has_strong_story_signal`: a genuine story page's LAST panel can end on
    a "TO BE CONTINUED!" cliffhanger caption — real case: spiderman-venom-double-trouble
    p22, a real 2-panel scene (Peter recoils in the mirror / Eddie bursts out the window)
    whose final caption is the TBC teaser. `_looks_like_backmatter` matches that phrase
    regardless of what else is on the page, so a page with real multi-panel dialogue must
    win over the phrase match — only a page with NO real story content of its own (the
    hero-splash-reused credits page this function targets) gets demoted."""
    for p in pages:
        if not p.get("is_story_page"):
            continue
        from .._panel_index import page_dialog
        corpus = " ".join(str(tb.get("text", "")) for tb in page_dialog(p))
        if not _looks_like_backmatter(corpus):
            continue
        if _has_strong_story_signal(p):
            continue
        pn = int(p.get("page_number", 0) or 0)
        log(f"[preprocess] credits/title text on p{pn:03d} → demoting story→skip (not a story panel)")
        p["page_type"] = "skip"
        p["is_story_page"] = False
        p["skip_reason"] = "credits_title"
        p["panels"] = []
        h = str(p.get("content_hash", "") or "")
        if h:
            try:
                save_cached(project_root, pn, h, p)
            except Exception as exc:
                log(f"[preprocess]   ⚠ couldn't persist demotion for p{pn}: {exc}")


# Terminal back-matter reasons that ONLY appear at the very END of a Western single
# issue (letters page, house ads, "next issue" previews, solicits). Once one shows up
# in the BACK of the book, the main story never resumes — so every later page is
# back-matter too, INCLUDING a preview of ANOTHER comic rendered as real story panels
# (the VLM tags those "story" because they ARE genuine sequential art — it can't know
# they belong to a different book). A positional cutoff catches what per-page content
# inspection cannot.
_TERMINAL_BACKMATTER = {
    "letter_column", "solicit_credits", "advertisement",
    "next_issue_preview", "back_matter",
}


def _demote_backmatter_tail_one(p: dict, project_root: Path, cut: int, log: Callable[[str], None]) -> None:
    """Shared demotion body for `_demote_backmatter_tail`'s single-issue and per-issue paths."""
    pn = int(p.get("page_number", 0) or 0)
    log(f"[preprocess] back-matter tail after p{cut:03d} → demoting p{pn:03d} story→skip")
    p["page_type"] = "skip"
    p["is_story_page"] = False
    p["skip_reason"] = "back_matter_tail"
    p["panels"] = []
    h = str(p.get("content_hash", "") or "")
    if h:
        try:
            save_cached(project_root, pn, h, p)
        except Exception as exc:
            log(f"[preprocess]   ⚠ couldn't persist tail-demotion for p{pn}: {exc}")


def _has_strong_story_signal(p: dict) -> bool:
    """Multiple panels + real dialogue = this page genuinely continues the story, not a
    single ad/filler page the VLM mistagged 'story'. Guards `_find_tail_cut` below: real
    case — spiderman-venom-double-trouble p17 is a Stan's Soapbox house-ad page sitting
    MID-issue; p18 right after it has 3 panels of real dialogue, so p17 must demote only
    ITSELF (already done at per-page classification time), not drag p18-22 down with it."""
    if len(p.get("panels") or []) < 2:
        return False
    from .._panel_index import page_dialog
    return any(str(tb.get("ocr", "") or tb.get("text", "")).strip() for tb in page_dialog(p))


def _find_tail_cut(issue_pages: list[dict], half: float) -> int | None:
    """Earliest terminal-back-matter page (past `half`) that is a genuine tail START —
    i.e. the main story never resumes after it. Candidates are walked in ascending page
    order so the first genuine one wins.

    A terminal reason does NOT start a tail when a real story page (multi-panel + real
    dialogue) appears ANYWHERE later in the issue. That later content is either a lone
    ad with story resuming right after it, or the SECOND story of a two-in-one issue
    whose interior cover/credits sit between the two — real case: AvX: VS #5, where
    story 1 (Hawkeye/Angel) ends on a 'WINNER … TO BE CONTINUED' page tagged
    'back_matter' and story 2 (Black Panther/Storm) begins right after. Scanning only the
    immediate next page missed this, because a non-story transition page (interior cover /
    end-card) sits between the terminal page and the resuming story. Genuine end-of-book
    previews carry no real content of their own (no multi-panel dialogue), so they never
    trip this guard and still demote — the money-story-vs-preview tie always breaks toward
    keeping real content."""
    ordered = sorted(issue_pages, key=lambda p: int(p.get("page_number", 0) or 0))
    for i, p in enumerate(ordered):
        pn = int(p.get("page_number", 0) or 0)
        if pn <= half or str(p.get("skip_reason", "")) not in _TERMINAL_BACKMATTER:
            continue
        if any(nxt.get("is_story_page") and _has_strong_story_signal(nxt)
               for nxt in ordered[i + 1:]):
            continue  # real story resumes later — lone ad or a two-in-one 2nd story
        return pn
    return None


def _demote_backmatter_tail(
    pages: list[dict], project_root: Path, log: Callable[[str], None]
) -> None:
    """Demote every story page that follows the first GENUINE terminal back-matter page
    in the BACK HALF of the issue. General: keyed on page position + an already-detected
    terminal reason, no per-issue constants. The front story is never touched — a recap
    or credits page near the FRONT sits below the half-way cutoff and is ignored, so this
    only ever trims a genuine end-of-book tail (e.g. a G.I. Joe preview printed after the
    letters page that the VLM mislabelled 'story'). A lone ad/filler page MID-issue (already
    self-demoted at per-page classification time) does not drag the tail down with it when
    real story with real dialogue resumes right after — see `_find_tail_cut`.

    Multi-issue (saga) awareness: a GLOBAL half-cutoff mis-fires on sagas. Simulated case
    that shipped broken: a 5-issue saga at 18pp/issue (90 pages total), each issue ending
    with its own "to be continued" terminal back-matter page — global half=45, so the FIRST
    terminal page found anywhere past p45 (issue #3's own tail, ~p50) demoted every story
    page after it, wiping issues #4 and #5 wholesale (51/85 story pages survived instead of
    ~85). Fix: compute the half-cutoff and tail-demotion WITHIN each issue_label's own page
    range, so issue #4/#5's own tails are found (and only THEIR own tails trimmed) instead
    of inheriting issue #3's cutoff. Single issue → one range == the whole doc → runs the
    untouched original algorithm (byte-identical)."""
    numbers = [int(p.get("page_number", 0) or 0) for p in pages]
    if not numbers:
        return
    ranges = _issue_page_ranges(pages)
    if len(ranges) <= 1:
        half = max(numbers) * 0.5
        cut = _find_tail_cut(pages, half)
        if cut is None:
            return
        for p in pages:
            pn = int(p.get("page_number", 0) or 0)
            if pn <= cut or not p.get("is_story_page"):
                continue
            _demote_backmatter_tail_one(p, project_root, cut, log)
        return

    for label, (lo, hi) in ranges.items():
        issue_pages = [p for p in pages if str(p.get("issue_label", "") or "") == label]
        half = lo + (hi - lo) * 0.5
        cut = _find_tail_cut(issue_pages, half)
        if cut is None:
            continue
        for p in issue_pages:
            pn = int(p.get("page_number", 0) or 0)
            if pn <= cut or not p.get("is_story_page"):
                continue
            _demote_backmatter_tail_one(p, project_root, cut, log)


def _run_identity_precheck(project_root: Path, log: Callable[[str], None]) -> None:
    """Hook 0 (wiring only — decision logic lives in identity_check.py): flag a
    plot_summary that shares no names with the user's own identification prompt.
    Sets ctx['identity_suspect'] and persists it; the actual rebuild happens in
    _run_identity_repair() once real page_summaries exist to rebuild FROM."""
    ctx_path = project_root / "comic_context.json"
    if not ctx_path.exists():
        return
    try:
        ctx = json.loads(ctx_path.read_text())
    except json.JSONDecodeError:
        return
    from .identity_check import prompt_disagrees_with_plot
    if not prompt_disagrees_with_plot(ctx):
        return
    ctx["identity_suspect"] = True
    log("[identity] ⚠ plot_summary shares no names with the user prompt — "
        "suspect wrong comic; will rebuild from panels after preprocess")
    ctx_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False))


def _run_identity_repair(
    project_root: Path, results: list[dict], log: Callable[[str], None],
    *, force_refresh: bool = False,
) -> None:
    """Hook 2 (wiring only): trigger identity_check.rebuild_plot_from_panels when the
    plot looks suspect, missing, weak, or disagrees with the pages. Idempotent — a
    plot already rebuilt this way (plot_source == "panels") is skipped unless this is
    a --force run, so a normal cached re-run doesn't re-spend an SDK call every time."""
    ctx_path = project_root / "comic_context.json"
    if not ctx_path.exists():
        return
    try:
        ctx = json.loads(ctx_path.read_text())
    except json.JSONDecodeError:
        return
    if ctx.get("plot_source") == "answer_research":
        # explore_answer (Q&A) mode: comic_context.json's "plot" is a countdown
        # digest across N different comics, built by Stage-1 web research, not
        # this project's own pages. plot_agrees_with_pages() below compares
        # proper-noun overlap against ONE issue's panels — a legitimate Q&A
        # digest will share few nouns with any single cited issue, so the gate
        # would false-trigger and clobber the researched answer with that one
        # issue's plot. Skip identity repair entirely for this plot_source.
        return
    if ctx.get("plot_source") == "panels" and not force_refresh:
        return

    from .identity_check import plot_agrees_with_pages, rebuild_plot_from_panels
    story_pages = [p for p in results if p.get("is_story_page")]
    weak_plot = (
        not ctx.get("wiki_url") and not ctx.get("plot_source")
        and len(str(ctx.get("plot_summary", "") or "")) < 300
    )
    agrees = plot_agrees_with_pages(ctx, story_pages)
    if not (ctx.get("identity_suspect") or ctx.get("plot_status") == "MISSING"
            or weak_plot or agrees is False):
        return

    log(f"[identity] triggering panel-sourced plot rebuild "
        f"(suspect={ctx.get('identity_suspect', False)}, "
        f"plot_status={ctx.get('plot_status')!r}, weak_plot={weak_plot}, agrees={agrees})")
    if not rebuild_plot_from_panels(ctx, story_pages, log=log):
        return
    ctx.pop("identity_suspect", None)
    ctx_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False))


def _load_story_context(project_root: Path, log: Callable[[str], None]) -> str:
    ctx_path = project_root / "comic_context.json"
    if not ctx_path.exists():
        log("[preprocess] no comic_context.json — VLM runs without story context")
        return ""
    try:
        ctx = json.loads(ctx_path.read_text())
    except json.JSONDecodeError:
        log("[preprocess] comic_context.json unreadable — VLM runs without story context")
        return ""
    if ctx.get("identity_suspect"):
        # A flagged plot_summary means the roster below it is likely from the WRONG
        # comic (see identity_check module docstring) — feeding it to the VLM would
        # bias every page_summary toward the wrong cast. Cold-read: extract with NO
        # story context; Hook 2 rebuilds plot_summary from the honest results after.
        log("[preprocess] identity_suspect set — VLM runs COLD (no story context) "
            "until plot_summary is rebuilt from panels")
        return ""
    summary = ctx.get("summary") or {}
    if not summary:
        log("[preprocess] comic_context.summary missing — VLM runs without story context")
        return ""
    from stages.stage_1.tools.summarize_context import format_for_vlm
    block = format_for_vlm(summary)
    log(f"[preprocess] story context loaded: {len(block)} chars, {len(summary.get('characters') or [])} characters")
    return block


# Ad-page markers, checked against the page's OWN OCR text (word-boundary
# regexes — "ign" must not match "design"/"lightning"). ≥2 DISTINCT markers on
# one page = advertisement; a single hit can occur in real dialog.
_AD_MARKER_PATTERNS = [
    r"\bon[- ]sale\b", r"\bin stores\b", r"\bavailable now\b",
    r"\bdiscover yours\b", r"\bvolumes?\s+[\dI]", r"\bsubscribe\b",
    r"\bentertainment weekly\b", r"\bign\b", r"\.com\b", r"\bisbn\b",
    r"\bnext issue\b", r"\bfree preview\b", r"\bgraphic novel\b",
    r"\bdon.t miss\b", r"\bnew volume\b", r"\bcovers? by\b",
    r"\bexclusive to\b", r"\bfirst issue\b", r"\bnew series\b",
]


def _looks_like_ad(corpus: str) -> bool:
    """True when a page's OCR text reads like a house ad / promo page."""
    low = " ".join(corpus.lower().split())
    if not low:
        return False
    hits = sum(1 for p in _AD_MARKER_PATTERNS if re.search(p, low))
    return hits >= 2


# End-of-issue BACK-MATTER markers (creator credits + "to be continued" / next-issue
# teaser). The VLM mis-labels these "story" when they reuse a big hero splash — real
# case: Thor Annual 2023 p29, "WOULD YOU KNOW MORE? ... TO BE CONTINUED IN THE
# IMMORTAL THOR #1" over a Thor figure + the creative-team credits. Not story.
_BACKMATTER_STRONG = [
    r"\bto be continued\b", r"\bcontinued in\b", r"\bcreated by\b",
    r"\bwould you know more\b", r"\bcoming soon\b", r"\bnext month\b",
]
_BACKMATTER_ROLE_PATTERNS = [
    r"\bwriter\b", r"\bpencill?er\b", r"\bartist\b", r"\binker\b",
    r"\bcolou?rist\b", r"\bletterer\b", r"\beditor\b", r"\bcover art\b",
]


def _looks_like_backmatter(corpus: str) -> bool:
    """True when a page's text reads like end-of-issue back-matter: a 'to be
    continued'/next-issue teaser or a dense creative-team credits block (3+ role
    labels). These pages reuse hero art so the VLM calls them 'story' — they are not."""
    low = " ".join(corpus.lower().split())
    if not low:
        return False
    if any(re.search(p, low) for p in _BACKMATTER_STRONG):
        return True
    return sum(1 for p in _BACKMATTER_ROLE_PATTERNS if re.search(p, low)) >= 3


def _vlm_text_blocks_with_magi_bboxes(vlm_text_blocks, panel_texts, magi_texts_list):
    """VLM gives clean TEXT but NO pixel bbox; Magi gives the text-region BBOXES + its own
    pixel OCR plus a geometric panel assignment (panel_texts = box-inside-panel). Pair them
    PER PANEL in reading order so each VLM bubble regains a bbox for the inpaint mask —
    without it a mirrored panel shows the comic's own dialogue BACKWARDS and the text-
    coverage penalty reads 0. We ALSO carry each Magi region's OCR onto the paired block as
    a `.ocr` attribute: that OCR is deterministic ground truth read from pixels, so
    panel_embed_text can prefer it over the VLM `text` (which the batch VLM fabricates — see
    _panel_index.DIALOG_TRUTH) and _apply_dialog_truth_gate can flag divergences. No VLM-
    string↔Magi-box CONTENT pairing is needed; positional reading-order pairing is enough
    (the gate compares SETS). Magi boxes with no VLM partner are appended as text-empty
    blocks (still carrying their OCR + bbox) so the mask erases ALL detected text and their
    OCR still counts as ground truth.
    # ponytail: ocr rides as a dynamic attr on TextBlock — schema.py is not owned here, and
    # the final stored dialog is a plain dict anyway. Upgrade path: add TextBlock.ocr field."""
    magi_by_panel: dict[int, list[dict]] = {}
    for pi, t_idxs in (panel_texts or {}).items():
        regs = [magi_texts_list[ti] for ti in t_idxs if 0 <= ti < len(magi_texts_list)]
        regs = [r for r in regs if (r.get("bbox") or {}).get("w") and (r.get("bbox") or {}).get("h")]
        regs.sort(key=lambda r: (int((r.get("bbox") or {}).get("y", 0)),
                                 int((r.get("bbox") or {}).get("x", 0))))  # reading order
        magi_by_panel[int(pi)] = regs

    text_blocks: list[TextBlock] = []
    seen_per_panel: dict[int, int] = {}
    for tb in vlm_text_blocks:
        if not str(tb.get("text", "")).strip():
            continue
        pidx = int(tb.get("panel_index", -1))
        regs = magi_by_panel.get(pidx, [])
        k = seen_per_panel.get(pidx, 0)
        seen_per_panel[pidx] = k + 1
        reg = regs[k] if k < len(regs) else {}
        block = TextBlock(
            panel_index=pidx,
            text=str(tb.get("text", "")),
            type=str(tb.get("type", "speech")),
            speaker=tb.get("speaker") or None,
            bbox=(reg.get("bbox") or {}),
        )
        block.ocr = str(reg.get("ocr", "") or "")
        text_blocks.append(block)
    # Leftover Magi regions (more detected than VLM bubbles) → text-empty mask entries that
    # still carry their OCR (ground truth) + bbox (mask).
    for pi, regs in magi_by_panel.items():
        for r in regs[seen_per_panel.get(pi, 0):]:
            block = TextBlock(panel_index=pi, text="", type="magi_box", bbox=(r.get("bbox") or {}))
            block.ocr = str(r.get("ocr", "") or "")
            text_blocks.append(block)
    return text_blocks


def _assemble_page_dict(
    *,
    page_number: int,
    issue_label: str,
    image_path: Path,
    panels_raw: list[dict],
    dimensions: tuple[int, int],
    vlm_data: dict,
    content_hash: str,
    vlm_model_used: str,
    magi_data: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Combine VLM output + Magi (v3 full) outputs into a PreprocessedPage dict.

    VLM provides: page_type, page_summary, per-panel description/characters/emotion.
    Magi provides: panel bboxes, character bboxes + cluster_ids, text bboxes + OCR
                   + speaker associations.

    We merge: each panel gets Magi's cluster_ids of characters inside it, and the
    text_blocks list is built from Magi's OCR (more accurate than VLM)."""
    width, height = dimensions

    # Cover shortcut: first page, no panels detected, no VLM data → mark as cover.
    if not panels_raw and page_number == 1 and not vlm_data:
        return PreprocessedPage(
            page_number=page_number,
            source_image=str(image_path.resolve()),
            image_dimensions={"width": width, "height": height},
            is_story_page=False, page_type="cover", panels=[], text_blocks=[],
            page_summary="Cover page", issue_label=issue_label,
            vlm_model="", vlm_model_used="", content_hash=content_hash,
            preprocessing_method="magi+vlm", skip_reason="",
        ).to_dict()

    page_type = str(vlm_data.get("page_type", "story")).lower()
    if page_type not in ("cover", "story", "skip"):
        page_type = "story"
    skip_reason = str(vlm_data.get("skip_reason", ""))
    vlm_text_blocks = vlm_data.get("text_blocks") or []

    # Deterministic ad guard: the VLM can hallucinate a story summary for
    # back-matter house ads (real case: a trailing BOOM! ad classified "story"
    # with a summary copied from the previous page's finale). Magi's OCR is
    # honest — if the page's own text reads like an ad, force skip regardless
    # of what the VLM said.
    if page_type == "story":
        _ocr_corpus = " ".join(
            [str(t.get("text", "")) for t in (magi_data or {}).get("texts", [])]
            + [str(tb.get("text", "")) for tb in vlm_text_blocks]
        )
        if _looks_like_ad(_ocr_corpus):
            page_type, skip_reason = "skip", "advertisement"
        elif _looks_like_backmatter(_ocr_corpus):
            page_type, skip_reason = "skip", "back_matter"

    # Build Magi assignments: which panel each char/text bbox belongs to.
    panel_chars: dict[int, list[int]] = {}
    panel_texts: dict[int, list[int]] = {}
    if magi_data:
        try:
            panel_chars, panel_texts = assign_to_panels(
                panels_raw, magi_data.get("characters", []), magi_data.get("texts", []),
            )
        except Exception:
            panel_chars, panel_texts = {}, {}

    if page_type == "skip":
        panel_infos: list[PanelInfo] = []
        page_summary = ""
    else:
        # Per-panel cluster IDs from Magi characters inside that panel.
        magi_chars_list = (magi_data or {}).get("characters", [])
        magi_texts_list = (magi_data or {}).get("texts", [])

        vlm_panel_map = _map_vlm_entries(
            vlm_data.get("panels") or [], len(panels_raw),
            page_number=page_number, log=log,
        )
        panel_infos = []
        for i, p in enumerate(panels_raw):
            cluster_ids = []
            char_boxes = []
            for char_idx in panel_chars.get(i, []):
                if 0 <= char_idx < len(magi_chars_list):
                    cid = magi_chars_list[char_idx].get("cluster_id", -1)
                    if cid >= 0:
                        cluster_ids.append(cid)
                    # Keep the BOX too, not just the cluster id: Stage 5's 9:16 crop
                    # needs to know WHERE the figure is. Unclustered chars (cid -1) still
                    # get a box — for framing, "a person is here" is the whole point;
                    # knowing WHICH person is not (Magi's re-id collapses on Western
                    # comics anyway: 15% vs 57% on manga, CoMix NeurIPS 2024).
                    cb = magi_chars_list[char_idx].get("bbox") or {}
                    if cb.get("w") and cb.get("h"):
                        char_boxes.append({
                            "x": int(cb["x"]), "y": int(cb["y"]),
                            "w": int(cb["w"]), "h": int(cb["h"]),
                            "cluster_id": int(cid),
                        })
            entry = vlm_panel_map.get(i, {})
            panel_infos.append(PanelInfo(
                index=i, bbox=p["bbox"],
                description=str(entry.get("description") or ""),
                characters=entry.get("characters") or [],
                dominant_emotion=str(entry.get("dominant_emotion") or ""),
                cluster_ids=cluster_ids,  # NEW v5 Phase 2
                char_boxes=char_boxes,
            ))

        # Dialog/text, PREFERRING the VLM transcription over Magi's OCR (Magi garbles
        # stylized bubbles: "I had an epiphany at a cave! I could suck at a periper").
        # Built as a flat list (panel_index + Magi bbox), then NESTED into each panel's
        # `dialog` so ALL of a panel's data lives in one object — no separate page-level
        # text_blocks. Magi is the fallback when the VLM returned no text.
        if vlm_text_blocks:
            flat = _vlm_text_blocks_with_magi_bboxes(
                vlm_text_blocks, panel_texts, magi_texts_list)
        elif magi_texts_list:
            # No VLM transcription → Magi OCR IS the dialog. Store it under both `text`
            # (rendered/captioned) and `.ocr` (ground truth) so the dialog-truth gate finds
            # them identical (ratio 1.0 → never falsely flagged) and embedding uses the OCR.
            flat = []
            text_to_panel: dict[int, int] = {}
            for pi, t_idxs in panel_texts.items():
                for ti in t_idxs:
                    text_to_panel[ti] = pi
            for ti, tx in enumerate(magi_texts_list):
                if not str(tx.get("ocr", "")).strip():
                    continue
                block = TextBlock(
                    panel_index=text_to_panel.get(ti, -1),
                    text=str(tx.get("ocr", "")),
                    type=str(tx.get("type", "narration")),
                    speaker=None,  # Magi gives cluster, not name; resolved later if available
                    speaker_cluster_id=tx.get("speaker_cluster_id"),
                    bbox=tx.get("bbox", {}),
                )
                block.ocr = str(tx.get("ocr", ""))
                flat.append(block)
        else:
            flat = []

        # Nest dialog into its panel (drop panel_index — implied by nesting). Blocks the
        # detector could not place on any panel (panel_index < 0) are dropped (rare).
        dialog_by_panel: dict[int, list[dict]] = {}
        for tb in flat:
            if tb.panel_index < 0:
                continue
            dialog_by_panel.setdefault(tb.panel_index, []).append({
                "text": tb.text, "type": tb.type, "speaker": tb.speaker,
                "speaker_cluster_id": tb.speaker_cluster_id, "bbox": tb.bbox,
                "ocr": getattr(tb, "ocr", ""),
            })
        for pinfo in panel_infos:
            pinfo.dialog = dialog_by_panel.get(pinfo.index, [])

        # Last-resort fill: any panel with a BLANK description is invisible to the
        # embedding matcher. Synthesize one from its dialog → characters → a generic marker.
        for pinfo in panel_infos:
            if str(pinfo.description or "").strip():
                continue
            dlg = " ".join(str(d.get("text", "")).strip() for d in pinfo.dialog
                           if str(d.get("text", "")).strip())
            chars = ", ".join(pinfo.characters or [])
            if dlg:
                pinfo.description = (f"{chars}: " if chars else "") + dlg[:160]
            elif chars:
                pinfo.description = f"{chars} ({pinfo.dominant_emotion or 'present'})"
            else:
                pinfo.description = "Wordless transition/SFX panel"
        page_summary = str(vlm_data.get("page_summary", ""))

    result = PreprocessedPage(
        page_number=page_number,
        source_image=str(image_path.resolve()),
        image_dimensions={"width": width, "height": height},
        is_story_page=(page_type == "story"),
        page_type=page_type, panels=panel_infos,
        page_summary=page_summary, issue_label=issue_label,
        vlm_model=VLM_MODEL, vlm_model_used=vlm_model_used,
        content_hash=content_hash, preprocessing_method="magi+vlm",
        skip_reason=skip_reason,
    ).to_dict()
    # Deterministic, network-free flag gates on the finalized page (both no-op on skip
    # pages / when their env kill-switch is off). Feature B: flag panels whose VLM dialog
    # contradicts Magi OCR. Feature G: flag pages where Magi under-covered the panels.
    _apply_dialog_truth_gate(result, log=log or print)
    _apply_coverage_guard(result, log=log or print)
    return result


def _verify_pending_concurrently(pending: list[dict], *, story_context: str,
                                 log: Callable[[str], None]) -> None:
    """Run _apply_desc_verify_gate on the entries flagged verify=True, in parallel
    (VLM_VERIFY_WORKERS threads). Each verify is an independent VLM round-trip with no
    shared state, so a serial loop was pure added latency. Writes the (possibly
    re-described) result back into p["page_dict"] in place. Serial when workers<=1 or
    fewer than 2 pages need verifying."""
    from config import VLM_VERIFY_WORKERS
    todo = [p for p in pending if p.get("verify")]
    if not todo:
        return

    def _run(p: dict) -> None:
        b = p["b"]
        p["page_dict"] = _apply_desc_verify_gate(
            p["page_dict"], image_path=b["img"], panels_raw=p["panels"],
            dimensions=p["dims"], magi_data=p["magi"], content_hash=b["hash"],
            story_context=story_context, log=log)

    if VLM_VERIFY_WORKERS > 1 and len(todo) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(VLM_VERIFY_WORKERS, len(todo))) as ex:
            list(ex.map(_run, todo))
    else:
        for p in todo:
            _run(p)


def _apply_desc_verify_gate(
    page_dict: dict,
    *,
    image_path: Path,
    panels_raw: list[dict],
    dimensions: tuple[int, int],
    magi_data: dict | None,
    content_hash: str,
    story_context: str,
    log: Callable[[str], None],
) -> dict:
    """DESC_VERIFY gate: crop + look ground-truth check that each panel's description
    matches its own bbox pixels (see vlm_extract.verify_page_descriptions) — catches
    batch-shift/hallucinated descriptions before they poison Stage 3/5. Soft gate: on
    mismatch, re-describe ONCE via the single-page path (no continuity bias) and verify
    again; still failing → keep the result, flagged desc_verified=False. Never raises,
    never loops (max 2 verify calls + 1 redo describe per page)."""
    if not DESC_VERIFY or not VLM_EXTRACT or page_dict.get("page_type") not in ("cover", "story"):
        return page_dict  # Magi-only mode never makes the OpenRouter verify round-trip
    pn = page_dict.get("page_number")
    if verify_page_descriptions(page_dict, image_path, log=log):
        page_dict["desc_verified"] = True
        return page_dict
    log(f"[desc-verify] p{pn:03d}: mismatch found — re-describing via single-page path")
    vlm_data = extract_page(image_path, panels_raw, progress=log, story_context=story_context)
    redone = _assemble_page_dict(
        page_number=pn, issue_label=page_dict.get("issue_label", ""),
        image_path=image_path, panels_raw=panels_raw, dimensions=dimensions,
        vlm_data=vlm_data, content_hash=content_hash,
        vlm_model_used=str(vlm_data.get("_vlm_model_used", "")),
        magi_data=magi_data, log=log,
    )
    redone["desc_verified"] = verify_page_descriptions(redone, image_path, log=log)
    if not redone["desc_verified"]:
        log(f"[desc-verify] p{pn:03d}: still mismatched after redo — keeping best-effort result")
    return redone


def _norm_dialog_text(s: str) -> str:
    """Normalize a dialog line for fuzzy comparison: uppercase, keep only alphanumeric
    WORDS. Drops OCR/VLM punctuation + spacing noise ('WE WAIT.' vs 'WE WAIT') so only the
    actual words drive the SequenceMatcher ratio."""
    return " ".join(re.findall(r"[A-Z0-9]+", str(s).upper()))


def _apply_dialog_truth_gate(page_dict: dict, *, log: Callable[[str], None] = print) -> dict:
    """Feature B: flag panels whose VLM `text` does NOT match Magi's pixel OCR ground truth.
    The batch VLM fabricates dialog from story flow (real case: doom-rocket-raccoon p28
    panel 1 — pixels read 'SO NOW WHAT DO WE DO?' but the VLM wrote 'WE'VE REACHED THE BIG
    BANG'), which poisons the panel embedding and mis-grounds Stage 3/5. Deterministic
    (stdlib difflib, no network): for each panel that has BOTH a VLM transcription and Magi
    OCR, take the best-pair SequenceMatcher ratio; below _DIALOG_MISMATCH_RATIO sets the
    panel-level `dialog_mismatch = True` (contract consumed by Stage 5 _panel_untrusted;
    absent = trusted). Flag-only — never rewrites/removes the VLM dialog content."""
    if not DIALOG_TRUTH or page_dict.get("page_type") not in ("cover", "story"):
        return page_dict
    pn = page_dict.get("page_number", "?")
    for panel in page_dict.get("panels") or []:
        dialog = panel.get("dialog") or []
        vlm_norm = [n for n in (_norm_dialog_text(d.get("text", "")) for d in dialog) if n]
        ocr_norm = [n for n in (_norm_dialog_text(d.get("ocr", "")) for d in dialog) if n]
        # Only judge when BOTH sides carry real words — a Magi-only or VLM-only (or pure
        # SFX/punctuation) panel has nothing to cross-check.
        if not vlm_norm or not ocr_norm:
            continue
        sim = max(difflib.SequenceMatcher(None, v, o).ratio()
                  for v in vlm_norm for o in ocr_norm)
        if sim < _DIALOG_MISMATCH_RATIO:
            panel["dialog_mismatch"] = True
            log(f"[dialog-truth] p{pn} panel {panel.get('index')}: "
                f"VLM dialog does not match OCR — flagged")
    return page_dict


def _apply_coverage_guard(page_dict: dict, *, log: Callable[[str], None] = print) -> dict:
    """Feature G: flag a story page where Magi's panel boxes cover suspiciously little of
    the page — a sign it MISSED panels (which would silently drop story beats). Sum of panel
    bbox areas / page area; below _COVERAGE_MIN (and >=1 panel) sets page-level
    `panel_coverage_low = True`. Flag-only, no behaviour change. Overlapping panels only
    inflate the fraction, so this can false-negative but never false-positive."""
    if not COVERAGE_GUARD or page_dict.get("page_type") != "story":
        return page_dict
    panels = page_dict.get("panels") or []
    dims = page_dict.get("image_dimensions") or {}
    parea = int(dims.get("width", 0) or 0) * int(dims.get("height", 0) or 0)
    if not panels or parea <= 0:
        return page_dict
    covered = sum(
        int((p.get("bbox") or {}).get("w", 0) or 0) * int((p.get("bbox") or {}).get("h", 0) or 0)
        for p in panels
    )
    frac = covered / parea
    if frac < _COVERAGE_MIN:
        page_dict["panel_coverage_low"] = True
        log(f"[coverage-guard] p{page_dict.get('page_number', '?')}: panels cover only "
            f"{frac * 100:.0f}% of page — possible missed panels")
    return page_dict


def _build_page_from_single(
    *,
    page_number: int,
    issue_label: str,
    image_path: Path,
    panels_raw: list[dict],
    dimensions: tuple[int, int],
    project_root: Path,
    log: Callable[[str], None],
    story_context: str,
    content_hash: str,
    magi_data: dict | None = None,
) -> dict:
    """Single-image fallback: called when a multi-image batch fails."""
    # First-page-no-panels cover shortcut.
    if not panels_raw and page_number == 1:
        log(f"[stage2]     p{page_number:03d} no panels + first page → COVER shortcut")
        out = _assemble_page_dict(
            page_number=page_number, issue_label=issue_label, image_path=image_path,
            panels_raw=[], dimensions=dimensions, vlm_data={},
            content_hash=content_hash, vlm_model_used="",
            magi_data=magi_data,
        )
        save_cached(project_root, page_number, content_hash, out)
        return out

    if not VLM_EXTRACT:
        # Magi-only (VLM_EXTRACT=0): skip the OpenRouter describe call; empty vlm_data →
        # _assemble_page_dict builds from Magi alone (page_type default "story", dialog
        # from OCR + bbox). Covers the single-page front/back-matter path too.
        vlm_data: dict = {}
    else:
        log(f"[stage2]     p{page_number:03d} fallback single-image VLM ({len(panels_raw)} panels)…")
        t_vlm = time.time()
        vlm_data = extract_page(image_path, panels_raw, progress=log, story_context=story_context)
        log(f"[stage2]     p{page_number:03d} fallback done in {time.time() - t_vlm:.1f}s")

    out = _assemble_page_dict(
        page_number=page_number, issue_label=issue_label, image_path=image_path,
        panels_raw=panels_raw, dimensions=dimensions, vlm_data=vlm_data,
        content_hash=content_hash,
        vlm_model_used=str(vlm_data.get("_vlm_model_used", "")),
        magi_data=magi_data, log=log,
    )
    out = _apply_desc_verify_gate(
        out, image_path=image_path, panels_raw=panels_raw, dimensions=dimensions,
        magi_data=magi_data, content_hash=content_hash, story_context=story_context, log=log,
    )
    save_cached(project_root, page_number, content_hash, out)
    return out


def _map_vlm_entries(
    entries: list[dict], n: int, *, id_key: str = "index",
    page_number: int | None = None, log: Callable[[str], None] | None = None,
) -> dict[int, dict]:
    """Map a VLM-returned list of per-panel dicts back to panel slots by the echoed
    `id_key` field — IDENTITY-based, never by list position. A dropped, reordered,
    or duplicated entry from the model must not shift every later panel onto its
    neighbor's data (the desc↔bbox misalignment this guards against): entries with
    a missing/non-integer, out-of-range, or duplicate id are DROPPED (and logged)
    instead of silently reassigned. Returns {panel_index: entry} — an index absent
    from the result has no matching entry and the caller falls back to a default."""
    log = log or (lambda _msg: None)
    label = f"page {page_number} " if page_number is not None else ""
    by_index: dict[int, dict] = {}
    for entry in entries:
        raw_idx = entry.get(id_key)
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            log(f"[vlm] {label}panel id={raw_idx!r}: missing/invalid {id_key} — dropped")
            continue
        if not (0 <= idx < n):
            log(f"[vlm] {label}panel {idx}: {id_key} out of range [0,{n}) — dropped")
            continue
        if idx in by_index:
            log(f"[vlm] {label}panel {idx}: duplicate {id_key} — keeping first, dropping duplicate")
            continue
        by_index[idx] = entry
    return by_index
