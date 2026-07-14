"""
Hand-inject an external image as a Q&A intro panel.

Master can open a Q&A Short with an image picked from disk (avif/jpg/png) instead
of whatever comic panel the free matcher liked. This automates the by-hand inject
already done for the Mjolnir / Batcave intros, as one call:

  1. sips-convert the source to raw_comic/_intro_<slug>_p<N>.jpg (macOS built-in;
     avif/heic/png → jpeg handled here).
  2. write preprocessed/page_<N>_<hash>.json — one full-image "story" panel,
     desc_verified, characters=[subject], content_hash = sha256[:16] of the jpg.
  3. prepend {"page":N,"panel":0,"score":101,"force_intro":true} to
     subject_panels.json (marked manual so a Stage 2 re-run won't clobber it), so
     Stage 5's Q&A intro shows it first and no-reuse can't drop it.

Pure module (no flet) so the review-gate screen can call it and it stays
unit-testable.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


_INTRO_SCORE = 101  # above every real subject-panel score → sorts first in the intro


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "intro"


def _next_page_number(preprocessed: Path) -> int:
    """Lowest free page number at/above 200 — import pages live above real pages so
    they never collide with a scraped issue's numbering."""
    used: set[int] = set()
    if preprocessed.exists():
        for p in preprocessed.glob("page_*.json"):
            m = re.match(r"page_(\d+)_", p.name)
            if m:
                used.add(int(m.group(1)))
    n = 200
    while n in used:
        n += 1
    return n


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return 0, 0


def import_intro_image(
    project_root: Path,
    src_image: Path,
    subject: str,
    *,
    description: str | None = None,
) -> dict:
    """Inject `src_image` as a full-image intro panel for the Q&A project at
    `project_root`. Returns the subject_panels.json entry that was written, plus the
    resolved jpg / page-json paths and the effective subject. Raises on any failure
    (missing source, sips unavailable/failed) — the caller shows the message."""
    project_root = Path(project_root)
    src_image = Path(src_image)
    if not src_image.exists():
        raise FileNotFoundError(f"source image not found: {src_image}")
    subject = (subject or "").strip() or src_image.stem

    raw = project_root / "raw_comic"
    prep = project_root / "preprocessed"
    raw.mkdir(parents=True, exist_ok=True)
    prep.mkdir(parents=True, exist_ok=True)

    page_n = _next_page_number(prep)
    jpg = raw / f"_intro_{_slug(subject)}_p{page_n}.jpg"

    # 1. convert → jpg (macOS sips; handles avif/heic/png → jpeg)
    try:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", str(src_image), "--out", str(jpg)],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("`sips` not found — intro import needs macOS.") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"sips failed to convert {src_image.name}: {(e.stderr or '').strip()}") from e
    if not jpg.exists():
        raise RuntimeError(f"sips produced no output for {src_image.name}")

    content_hash = hashlib.sha256(jpg.read_bytes()).hexdigest()[:16]
    w, h = _image_size(jpg)
    desc = description or f"Imported intro image featuring {subject}."

    # 2. preprocessed page json — mirrors the manual-inject shape (Mjolnir/Batcave):
    #    one full-image story panel, desc_verified so no gate re-checks it.
    page_doc = {
        "page_number": page_n,
        "source_image": str(jpg),
        "image_dimensions": {"width": w, "height": h},
        "is_story_page": True,
        "page_type": "story",
        "panels": [{
            "index": 0,
            "bbox": {"x": 0, "y": 0, "w": w, "h": h},
            "description": desc,
            "characters": [subject],
            "dominant_emotion": "",
            "cluster_ids": [],
            "dialog": [],
        }],
        "text_blocks": [],
        "page_summary": desc,
        "issue_label": "#intro",
        "vlm_model": "manual-inject",
        "vlm_model_used": "manual-inject",
        "content_hash": content_hash,
        "preprocessing_method": "manual-inject",
        "skip_reason": "",
        "desc_verified": True,
    }
    page_json = prep / f"page_{page_n:03d}_{content_hash}.json"
    page_json.write_text(json.dumps(page_doc, indent=2, ensure_ascii=False))

    entry = {"page": page_n, "panel": 0, "score": _INTRO_SCORE, "force_intro": True}

    # 3. prepend to subject_panels.json; mark manual so build_subject_panels (Stage 2
    #    re-run) leaves it alone. Drop any stale entry for the same page first.
    sp_path = project_root / "subject_panels.json"
    doc: dict = {}
    if sp_path.exists():
        try:
            doc = json.loads(sp_path.read_text())
        except Exception:
            doc = {}
    panels = [p for p in (doc.get("panels") or []) if int(p.get("page", -1)) != page_n]
    doc["subject"] = doc.get("subject") or subject
    doc["manual"] = True
    doc["panels"] = [entry] + panels
    sp_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))

    return {**entry, "jpg_path": str(jpg), "page_json": str(page_json),
            "subject": doc["subject"]}


def remove_intro_image(project_root: Path, page_number: int) -> None:
    """Revert import_intro_image for `page_number`: delete the page json + its jpg and
    drop the matching subject_panels.json entry. Idempotent — missing pieces are
    skipped, never raised."""
    project_root = Path(project_root)
    page_number = int(page_number)
    prep = project_root / "preprocessed"

    for page_json in prep.glob(f"page_{page_number:03d}_*.json"):
        try:
            doc = json.loads(page_json.read_text())
            src = doc.get("source_image")
            if src:
                Path(src).unlink(missing_ok=True)
        except Exception:
            pass
        page_json.unlink(missing_ok=True)

    sp_path = project_root / "subject_panels.json"
    if sp_path.exists():
        try:
            doc = json.loads(sp_path.read_text())
        except Exception:
            return
        doc["panels"] = [p for p in (doc.get("panels") or [])
                         if int(p.get("page", -1)) != page_number]
        sp_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
