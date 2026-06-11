"""A2: download CC0 artwork image(s) + metadata into an art project.

Layout written (mirrors the comic project shape so downstream reuse is free):
  art_projects/<slug>/raw_art/art_NNN_<objectID>.jpg
  art_projects/<slug>/raw_art/manifest.json      [{label, pages:[abs paths]}]
  art_projects/<slug>/met_meta_<objectID>.json
  art_projects/<slug>/selection.json             {mode, object_ids, theme}
"""
import json

from .config import get_art_project_path
from .sources import met


def build_manifest(entries: list[dict]) -> list[dict]:
    """[{label, image_path}] → comic-shaped manifest (one 'page' per artwork)."""
    return [{"label": e["label"], "pages": [e["image_path"]]} for e in entries]


def fetch_artworks(
    project_name: str,
    object_ids: list[int],
    *,
    mode: str = "painting_deep_dive",
    theme: str = "",
    log=print,
) -> dict:
    root = get_art_project_path(project_name)
    raw = root / "raw_art"
    raw.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    for n, oid in enumerate(object_ids, start=1):
        meta = met.fetch_meta(oid)
        ok, why = met.validate_cc0(meta)
        if not ok:
            raise ValueError(why)  # hard CC0 gate — spec §4-A2
        img = raw / f"art_{n:03d}_{oid}.jpg"
        if img.exists():
            log(f"[fetch] reusing {img.name}")
        else:
            log(f"[fetch] downloading {meta.get('title')!r} (objectID {oid})…")
            met.fetch_image(meta, img)
        (root / f"met_meta_{oid}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False))
        entries.append({"label": meta.get("title") or f"object {oid}",
                        "image_path": str(img.resolve())})

    manifest = build_manifest(entries)
    (raw / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    (root / "selection.json").write_text(json.dumps(
        {"mode": mode, "object_ids": list(object_ids), "theme": theme},
        indent=2, ensure_ascii=False))
    log(f"[fetch] {len(entries)} artwork(s) ready in {raw}")
    return {"manifest": manifest, "count": len(entries)}
