"""
Hand-add a custom image to ONE beat in the review UI.

Master's design (approved): an image Master adds himself is CERTAIN to appear in the
video — cosine similarity is never a select/reject gate for it, only an ASSIGNMENT
signal deciding which beat it lands on (stages.stage_5.shots.assign_custom_images).
This module is the instant, always-succeeds half of that flow: copy the picked file into
review/custom/ and record it in the sidecar (review/custom/custom_images.json) that
Stage 5 reads. enrich_custom_image() is the slow half (VLM describe + embed + Qdrant
upsert) — meant to be run in a background thread/task so the UI never blocks on it, and
degrades to a "pending"/"*_failed" sidecar status on ANY failure (LM Studio down, no
SDK login, ...) rather than raising, per review_gate.py's own graceful-degradation
convention. Master can still lock the image to a beat immediately even if enrichment
never completes — Stage-5 assignment simply falls back to an empty-desc score of 0.0.

Pure module (no flet) so the review-gate screen can call it and it stays unit-testable.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

CUSTOM_DIR = "review/custom"
SIDECAR_NAME = "custom_images.json"


def _sidecar_path(project_root: Path) -> Path:
    return Path(project_root) / CUSTOM_DIR / SIDECAR_NAME


def _load_sidecar(project_root: Path) -> dict:
    p = _sidecar_path(project_root)
    if not p.exists():
        return {"images": []}
    try:
        doc = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"images": []}
    doc.setdefault("images", [])
    return doc


def _save_sidecar(project_root: Path, doc: dict) -> None:
    p = _sidecar_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False))


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")
    return s or "image"


def list_custom_images(project_root: Path) -> list[dict]:
    """Every custom-image sidecar entry for this project ([] if none yet)."""
    return list(_load_sidecar(project_root).get("images") or [])


def point_id(rel_file: str) -> str:
    """Deterministic Qdrant point id for a custom image (stable across re-enrich runs, so a
    re-upsert REPLACES the same point instead of duplicating it)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(rel_file)))


def add_custom_image(project_root: Path, src_image: Path, beat_key: str, *,
                     data: bytes | None = None) -> dict:
    """Write projects/<slug>/review/custom/custom_<ts>_<origname>.<ext> and append a
    sidecar entry (beat_key is the card Master clicked "Add image" on — display-only;
    Stage 5's custom-assign argmax is free to place the image on a DIFFERENT beat unless
    Master also locks it here). Instant — no VLM/embedding call — so the UI never blocks.

    `data`: raw bytes to write directly, when given — the Flet WEB-mode path.
    FilePicker.pick_files(with_data=True) is the only way to get a picked file's
    CONTENT server-side in web mode: `.path` is always None for a browser-side file (no
    filesystem access), only `.bytes` is populated. `src_image` is then just used for its
    name/extension (need not exist on disk). Desktop mode (real `.path`, `data=None`)
    still copies from `src_image` on disk, unchanged. Raises on a missing/unreadable
    source when `data` is not given."""
    project_root = Path(project_root)
    src_image = Path(src_image)
    if data is None and not src_image.exists():
        raise FileNotFoundError(f"source image not found: {src_image}")

    custom_dir = project_root / CUSTOM_DIR
    custom_dir.mkdir(parents=True, exist_ok=True)
    ext = src_image.suffix.lower() or ".jpg"
    fname = f"custom_{int(time.time())}_{_slug(src_image.stem)}{ext}"
    dest = custom_dir / fname
    dest.write_bytes(data if data is not None else src_image.read_bytes())
    rel = f"{CUSTOM_DIR}/{fname}"

    doc = _load_sidecar(project_root)
    entry = {"file": rel, "beat_key": str(beat_key), "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "desc": "", "enrich_status": "pending"}
    doc["images"].append(entry)
    _save_sidecar(project_root, doc)
    return entry


def _update_entry(project_root: Path, rel_file: str, *, desc: str, enrich_status: str) -> None:
    doc = _load_sidecar(project_root)
    for e in doc.get("images") or []:
        if e.get("file") == rel_file:
            e["desc"] = desc
            e["enrich_status"] = enrich_status
    _save_sidecar(project_root, doc)


def enrich_custom_image(project_root: Path, rel_file: str, *, log=print) -> None:
    """Best-effort ONE-TIME enrichment for a just-added custom image: VLM-describe it (Claude
    SDK vision, same sdk_complete_vision pattern as stages/review_gate.py's vision judge),
    Qwen/text-embed the description, and SigLIP-embed the pixels — then upsert BOTH into the
    project's per-project Qdrant collections with payload {"custom": True, "image_path": rel}
    so Stage 5's custom-assign can score it. Meant to run OFF the UI thread (asyncio.to_thread /
    run_blocking) — never raises; any step that fails (SDK not logged in, LM Studio down, Qdrant
    down) is logged and the sidecar's enrich_status records how far it got. Locking + rendering
    the image do NOT depend on this succeeding — a pending/failed enrich just means Stage 5's
    argmax scores this image 0.0 (no desc, no SigLIP vector) unless Master hand-locks it."""
    project_root = Path(project_root)
    abs_path = project_root / rel_file
    if not abs_path.exists():
        log(f"[custom-image] {rel_file}: source file missing — skip enrich")
        _update_entry(project_root, rel_file, desc="", enrich_status="missing_file")
        return

    slug = project_root.name
    desc = ""
    status_bits: list[str] = []

    try:
        from stages._claude_sdk import sdk_available, sdk_complete_vision
        if sdk_available():
            raw = sdk_complete_vision(
                "Describe this comic-book-style image in ONE plain sentence: the scene, the "
                "characters/subjects present, and the mood. No preamble, no markdown, no quotes.",
                f"IMAGE: {abs_path}",
                log=log,
            )
            desc = (raw or "").strip()
        else:
            status_bits.append("sdk_unavailable")
    except Exception as exc:  # noqa: BLE001 — enrich is background sugar, never raise
        log(f"[custom-image] {rel_file}: VLM describe failed ({type(exc).__name__}: {exc})")
        status_bits.append("desc_failed")

    if desc:
        try:
            from stages import _qdrant
            from stages._embedding import embed_batch, embed_dim
            vec = embed_batch([desc])[0]
            if vec is not None:
                _qdrant.ensure_collection(slug, embed_dim())
                _qdrant.upsert_panels(slug, [{
                    "id": point_id(rel_file),
                    "vector": vec.tolist() if hasattr(vec, "tolist") else list(vec),
                    "payload": {"custom": True, "image_path": rel_file, "desc": desc},
                }])
            else:
                status_bits.append("text_embed_failed")
        except Exception as exc:  # noqa: BLE001
            log(f"[custom-image] {rel_file}: text upsert failed ({type(exc).__name__}: {exc})")
            status_bits.append("text_upsert_failed")
    else:
        status_bits.append("desc_empty")

    try:
        from stages import _img_index, _qdrant
        if _img_index.img_embed_available():
            from PIL import Image
            with Image.open(abs_path) as im:
                vecs = _img_index.embed_images([im.convert("RGB")])
            _img_index.release()
            if vecs is not None:
                from qdrant_client import models
                name = _img_index._img_collection_name(slug)
                c = _qdrant.client()
                if not c.collection_exists(name):
                    c.create_collection(name, vectors_config=models.VectorParams(
                        size=int(vecs.shape[1]), distance=models.Distance.COSINE))
                c.upsert(name, points=[models.PointStruct(
                    id=point_id(rel_file), vector=vecs[0].tolist(),
                    payload={"custom": True, "image_path": rel_file})])
            else:
                status_bits.append("image_embed_failed")
        else:
            status_bits.append("siglip_unavailable")
    except Exception as exc:  # noqa: BLE001
        log(f"[custom-image] {rel_file}: image upsert failed ({type(exc).__name__}: {exc})")
        status_bits.append("image_upsert_failed")

    status = "ok" if desc and "text_upsert_failed" not in status_bits else "pending"
    if status_bits:
        status = ",".join(status_bits) if not desc else f"ok({','.join(status_bits)})"
    _update_entry(project_root, rel_file, desc=desc, enrich_status=status)
    log(f"[custom-image] {rel_file}: enrich done (status={status}, desc={'yes' if desc else 'no'})")
