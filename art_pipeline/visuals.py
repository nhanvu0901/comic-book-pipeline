# art_pipeline/visuals.py
"""A4.5: swap weakly-grounded context scenes onto license-safe web images.

Best-effort contract (spec §2.4): provider errors, zero candidates, weak
matches, failed downloads — all leave the scene on the painting and never
raise. Narration TEXT is never modified; only page_ref/panel_ref re-pointing.
visuals_manifest.json records original refs so --force restores then redoes."""
import hashlib
import json
import urllib.request
from pathlib import Path

from PIL import Image

from stages._embedding import semantic_sim
from stages.stage_2.cache import image_hash, save_cached
from stages.stage_2.schema import PanelInfo, PreprocessedPage

from .config import (
    MET_USER_AGENT, VISUAL_KEEP_THRESHOLD, VISUAL_MATCH_MIN,
    VISUAL_MAX_PER_VIDEO, VISUAL_MIN_SHORT_SIDE,
    get_art_project_path,
)
from .sources.web_images import met_artist_works, search_commons, search_openverse


def region_similarity(scene: dict, pages_by_number: dict) -> float:
    page = pages_by_number.get(scene.get("page_ref"))
    if not page:
        return 0.0
    desc = ""
    for pn in page.get("panels") or []:
        if pn.get("index") == scene.get("panel_ref", -1):
            desc = pn.get("description") or ""
            break
    if not desc:
        desc = page.get("page_summary") or ""
    return semantic_sim(str(scene.get("text", "")), desc)


def rank_candidates(scene_text: str, candidates: list[dict], used_urls: set) -> list[tuple[float, dict]]:
    scored = [(semantic_sim(scene_text, c.get("title") or ""), c)
              for c in candidates if c.get("image_url") not in used_urls]
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored


def passes_swap(score: float) -> bool:
    return score >= VISUAL_MATCH_MIN


_STOPWORDS = frozenset("""a an and are as at be been being between but by called could did
do does for from had has have he her his in into is it its of on or she so than that the
their them then there these they this those to was were where which while who with would
your you i we our us not no nor very much many more most other others own same just also
one two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty
sixty seventy eighty ninety hundred thousand eighteen nineteen first second third
created working likely appears appear painted painting paintings made make makes
""".split())


def build_queries(text: str, artist: str) -> list[str]:
    """Two keyword queries per scene: leading content words, then rarest (longest)
    words — prose sentences return nothing on Commons/Openverse file search."""
    import re as _re
    words = [w for w in _re.findall(r"[a-zA-Z]+", (text or "").lower())
             if len(w) >= 4 and w not in _STOPWORDS]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    if not seen:
        return [artist] if artist else []
    q1 = " ".join(seen[:4])
    q2 = " ".join(sorted(seen, key=len, reverse=True)[:2])
    out = []
    for q in (q1, q2):
        full = f"{q} {artist}".strip()
        if full and full not in out:
            out.append(full)
    return out


def _download(url: str, dest: Path) -> tuple[int, int] | None:
    """Download + size-gate. Returns (w, h) or None (file removed on reject)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MET_USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
        with Image.open(dest) as im:
            w, h = im.size
        if min(w, h) < VISUAL_MIN_SHORT_SIDE:
            dest.unlink(missing_ok=True)
            return None
        return (w, h)
    except Exception:
        dest.unlink(missing_ok=True)
        return None


def build_related_page(*, page_number: int, image_path: str, width: int,
                       height: int, title: str) -> dict:
    return PreprocessedPage(
        page_number=page_number,
        source_image=image_path,
        image_dimensions={"width": width, "height": height},
        is_story_page=True,
        page_type="story",
        panels=[PanelInfo(index=0,
                          bbox={"x": 0, "y": 0, "w": width, "h": height},
                          description=title, characters=[],
                          dominant_emotion="", cluster_ids=[])],
        text_blocks=[],
        page_summary=title,
        issue_label=f"related: {title}",
        vlm_model="", vlm_model_used="web",
        content_hash="", preprocessing_method="web-related", skip_reason="",
    ).to_dict()


def enrich_visuals(project_name: str, *, force: bool = False, log=print) -> dict:
    root = get_art_project_path(project_name)
    manifest_path = root / "visuals_manifest.json"
    narration_path = root / "narration.json"
    narration = json.loads(narration_path.read_text())
    ctx = json.loads((root / "art_context.json").read_text())

    pages: dict[int, dict] = {}
    for p in sorted((root / "preprocessed").glob("page_*.json")):
        d = json.loads(p.read_text())
        pages[int(d["page_number"])] = d

    if manifest_path.exists():
        if not force:
            log("[visuals] visuals_manifest.json exists — skipping (use force to redo)")
            return {"swapped": 0, "skipped": True}
        prev = {e["scene_id"]: e for e in json.loads(manifest_path.read_text())}
        for s in narration.get("scenes") or []:
            e = prev.get(s.get("scene_id"))
            if e:
                s["page_ref"], s["panel_ref"] = e["original_page_ref"], e["original_panel_ref"]
        # drop stale related pages so numbering and disk stay clean
        for n, pd_ in list(pages.items()):
            if pd_.get("preprocessing_method") == "web-related":
                for f in (root / "preprocessed").glob(f"page_{n:03d}_*.json"):
                    f.unlink()
                pages.pop(n)
        log("[visuals] force: restored original refs + removed stale related pages")

    artist = (((ctx.get("summary") or {}).get("characters") or [{}])[0]).get("name", "")
    own_ids = {a.get("object_id") for a in ctx.get("artworks") or []}
    try:
        shared_pool = met_artist_works(artist, exclude_ids=own_ids) if artist else []
    except Exception as exc:
        log(f"[visuals] met_artist_works failed ({exc}) — continuing without it")
        shared_pool = []

    rel_dir = root / "related_images"
    rel_dir.mkdir(exist_ok=True)
    used_urls: set = set()
    manifest: list[dict] = []
    credits: list[dict] = []
    next_page = max(pages) + 1 if pages else 1
    swapped = 0
    art_page_numbers = {n for n, d in pages.items()
                        if d.get("preprocessing_method") != "web-related"}

    for s in narration.get("scenes") or []:
        if s.get("is_intro") or s.get("is_outro"):
            continue
        if swapped >= VISUAL_MAX_PER_VIDEO:
            log(f"[visuals] cap {VISUAL_MAX_PER_VIDEO} reached — stopping")
            break
        if s.get("page_ref") not in art_page_numbers:
            continue
        sim_region = region_similarity(s, pages)
        if sim_region >= VISUAL_KEEP_THRESHOLD:
            log(f"[visuals] scene {s['scene_id']}: keeps painting (region sim {sim_region:.2f})")
            continue
        queries = build_queries(str(s.get("text", "")), artist)
        try:
            candidates = list(shared_pool)
            for q in queries:
                candidates += search_commons(q) + search_openverse(q)
        except Exception as exc:
            log(f"[visuals] scene {s['scene_id']}: provider error ({exc}) — keeps painting")
            continue
        chosen = None
        for score, c in rank_candidates(str(s.get("text", "")), candidates, used_urls)[:3]:
            if not passes_swap(score):
                break  # ranked desc — nothing below will pass either
            dest = rel_dir / f"rel_{s['scene_id']:02d}_{hashlib.sha256(c['image_url'].encode()).hexdigest()[:8]}.jpg"
            dims = _download(c["image_url"], dest)
            if dims:
                chosen = (score, c, dest, dims)
                break
            log(f"[visuals]   scene {s['scene_id']}: download/size reject — next candidate")
        if not chosen:
            log(f"[visuals] scene {s['scene_id']}: no qualifying image (region sim {sim_region:.2f}) — keeps painting")
            continue
        score, c, dest, (w, h) = chosen
        page = build_related_page(page_number=next_page, image_path=str(dest.resolve()),
                                  width=w, height=h, title=c["title"])
        save_cached(root, next_page, image_hash(dest), page)
        pages[next_page] = page
        manifest.append({
            "scene_id": s["scene_id"],
            "original_page_ref": s["page_ref"], "original_panel_ref": s["panel_ref"],
            "page_number": next_page, "image": str(dest),
            "title": c["title"], "author": c["author"], "license": c["license"],
            "source_url": c["source_url"], "score": round(score, 3),
        })
        credits.append({k: c.get(k, "") for k in ("title", "author", "license", "source_url")})
        used_urls.add(c["image_url"])
        s["page_ref"], s["panel_ref"] = next_page, 0
        log(f"[visuals] ✓ scene {s['scene_id']} → {c['title']!r} "
            f"({c['license']}, sim {score:.2f} > region {sim_region:.2f})")
        next_page += 1
        swapped += 1

    narration_path.write_text(json.dumps(narration, indent=2, ensure_ascii=False))
    # NOTE: written even when swapped==0 — a later run will SKIP unless force=True
    # (the UI button and CLI --force both force; this is the idempotency contract).
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    ctx["extra_image_credits"] = credits
    ctx["sources"] = list(dict.fromkeys(
        (ctx.get("sources") or []) + [c["source_url"] for c in credits if c["source_url"]]))
    (root / "art_context.json").write_text(json.dumps(ctx, indent=2, ensure_ascii=False))
    log(f"[visuals] done — {swapped} scene(s) enriched")
    return {"swapped": swapped, "credits": credits}
