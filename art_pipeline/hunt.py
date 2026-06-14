"""A4.5: resolve `related` visual-plan scenes onto web images found by ONE
Claude SDK web-research session (spec 2026-06-11 §A4.5).

The SDK does the smart part (read narration, decide what to search, pick the
image); code does the mechanical part (download, size gate, register page,
re-point refs, credits). User decision: free-range web — no license gate;
source_url + license-if-found are RECORDED for traceability.

Best-effort contract: SDK failure / no result / bad download → the scene falls
back to an UNUSED painting region (keeps the variety rules), else painting_full.
The pipeline never dies here."""
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

from stages._claude_sdk import sdk_complete_web
from stages.stage_2.cache import image_hash, save_cached
from stages.stage_2.schema import PanelInfo, PreprocessedPage

from ._json import extract_json
from .config import VISUAL_MIN_SHORT_SIDE, get_art_project_path
from .visual_plan import assign_motions, visual_target

# A subject "names a specific artwork" when it carries a possessive/by-attributed
# Title-Case work, e.g. "Van Gogh's The Starry Night" / "The Fighting Temeraire by
# Turner". Best-effort (documented in the spec) — it targets the prominent, quoted
# titles that caused the Toledo mismatch, not arbitrary art-title NER.
#
# Two patterns:
#   (A) "<Artist>'s <Title>" — title follows the possessive
#   (B) "<Title> by <Artist>" — title precedes "by"; must start at sentence
#       start or after a determiner so we don't trigger on "inspired by".
_POSSESSIVE_RE = re.compile(
    r"'s\s+((?:The\s+)?[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){1,5})")
_TITLE_BY_RE = re.compile(
    r"(?:^|(?<=\s))((?:The\s+)?[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){1,5})\s+by\s+[A-Z]")


def _named_artwork(subject: str) -> str | None:
    """Return the lowercased artwork title named in `subject`, or None if the
    subject is generic. 'by <Title>' / "<Artist>'s <Title>" patterns only."""
    s = subject or ""
    m = _POSSESSIVE_RE.search(s)
    if m:
        return " ".join(m.group(1).lower().split())
    m = _TITLE_BY_RE.search(s)
    if m:
        return " ".join(m.group(1).lower().split())
    return None


def _image_matches_named_artwork(subject: str, resolved_title: str) -> bool:
    """True = accept the image. If `subject` names a specific artwork, require the
    resolved image title to contain that work's core tokens (>=80% overlap, the
    fact_is_grounded style). Generic subjects always pass (guard does not fire)."""
    work = _named_artwork(subject)
    if not work:
        return True
    title_tokens = set(re.findall(r"[a-z0-9]+", (resolved_title or "").lower()))
    work_tokens = [t for t in re.findall(r"[a-z0-9]+", work)
                   if t not in ("the", "a", "of")]
    if not work_tokens:
        return True
    hit = sum(1 for t in work_tokens if t in title_tokens)
    return hit / len(work_tokens) >= 0.8


# Arbitrary websites 403 obvious bot UAs; a browser-ish UA is standard practice
# for one-off fetches of publicly served images.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Wikimedia's UA policy is the OPPOSITE: they 429 anonymous browser-ish UAs and
# ask for tool/version + contact (their 429 body literally says "please contact
# noc@wikimedia", measured 2026-06-12). Identify honestly on their hosts.
_UA_WIKIMEDIA = ("art-pipeline/0.1 (https://github.com/personal-project; "
                 "contact: nhan.vutrong@8seneca.com) urllib")


def _ua_for(url: str) -> str:
    """Wikimedia/Wikipedia hosts get the policy-compliant tool UA; everything
    else keeps the browser UA (arbitrary sites 403 obvious bots instead)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if host.endswith("wikimedia.org") or host.endswith("wikipedia.org"):
        return _UA_WIKIMEDIA
    return _UA

# Wikimedia rate-limits burst downloads (HTTP 429 measured 2026-06-12 on
# circus-sideshow: 3/4 URLs rejected). Space out EVERY download and give one
# rate-limited fetch a second chance after a polite wait.
_DOWNLOAD_GAP_S = 1.0     # pause between any two downloads
_RETRY_429_WAIT_S = 5.0   # wait before the single 429 retry
_sleep = time.sleep       # module-level indirection so tests run at zero cost

# The SDK default max_turns=12 starves multi-subject hunts: aristotle-homer
# (6 subjects) died at "Reached maximum number of turns (12)" → 0/6 resolved
# (measured 2026-06-12). Each subject needs ~2-3 search/fetch turns plus the
# final JSON write, so the turn budget scales with subject count (floor 12).
_HUNT_TURNS_PER_SUBJECT = 4

_HUNT_SYSTEM = """You are an image researcher for short educational art videos.
You receive narration scenes that each need ONE related image. Use WebSearch and
WebFetch to find the best DIRECT image URL (ends in .jpg/.jpeg/.png/.webp or is
a direct image CDN link) for each subject.

Priority order when relevant: (1) x-ray / infrared / underdrawing images of the
painting itself, (2) portraits or photographs of the artist, (3) historical
photographs, maps or documents of the era/place, (4) comparison artworks.
Prefer larger images (at least 600px on the short side) from stable sources
(Wikimedia, museum sites, archives). Record the page you found it on as
source_url and the license if stated, else "unknown".

Respond with ONLY valid JSON:
{"images": {"<scene_id>": {"image_url": "...", "title": "...",
            "source_url": "...", "license": "...",
            "alt_image_url": "..."}}}
"alt_image_url" is optional: a second, DIFFERENT direct image URL for the same
subject from another source, used if the first fails; omit it if you only found
one. Both URLs should be at least 600px on the short side when you can tell.
Omit a scene's key entirely if you cannot find a good image for it.
Budget your searches: at most 2 web searches per scene, then write the JSON.
A partial result with some scenes resolved is better than running out of turns."""


def build_hunt_prompt(ctx: dict, scenes: list[dict], decls: list[dict]) -> str:
    by_id = {s["scene_id"]: s for s in scenes}
    lines = [f"Video: the story behind \"{ctx.get('title', '')}\".",
             "Find one image per scene below. Try painting x-ray/infrared queries "
             "first where the subject suggests technical analysis.", ""]
    for d in decls:
        s = by_id.get(d["scene_id"], {})
        lines.append(f'scene "{d["scene_id"]}": subject: {d["subject"]}')
        lines.append(f'  narration: {s.get("text", "")}')
    return "\n".join(lines)


def parse_hunt_response(raw: str | None) -> dict[int, dict]:
    """SDK text → {scene_id: candidate}. Drops entries without an image_url.
    Returns {} on any parse problem (caller falls back per scene)."""
    data = extract_json(raw or "")
    if not data or not isinstance(data.get("images"), dict):
        return {}
    out: dict[int, dict] = {}
    for k, v in data["images"].items():
        try:
            sid = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict) or not str(v.get("image_url") or "").strip():
            continue
        out[sid] = {"image_url": str(v["image_url"]).strip(),
                    "alt_image_url": str(v.get("alt_image_url") or "").strip(),
                    "title": str(v.get("title") or "").strip(),
                    "source_url": str(v.get("source_url") or "").strip(),
                    "license": str(v.get("license") or "unknown").strip() or "unknown"}
    return out


def _fetch(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _ua_for(url)})
    with urllib.request.urlopen(req, timeout=60) as r:
        dest.write_bytes(r.read())


def _download(url: str, dest: Path) -> tuple[int, int] | str:
    """Download + size-gate. Success → (w, h); reject → short REASON string
    (file removed on reject). Caller distinguishes via isinstance(result, tuple);
    the reason feeds the per-URL diagnostics log and manifest `attempted` list.
    A 429 (rate limit) gets exactly ONE retry after _RETRY_429_WAIT_S."""
    try:
        try:
            _fetch(url, dest)
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            _sleep(_RETRY_429_WAIT_S)
            _fetch(url, dest)   # second 429/any failure falls through to reason
    except Exception as exc:
        dest.unlink(missing_ok=True)
        return f"http: {type(exc).__name__}: {exc}"[:80]
    try:
        with Image.open(dest) as im:
            w, h = im.size
    except Exception as exc:
        dest.unlink(missing_ok=True)
        return f"not an image: {type(exc).__name__}"
    if min(w, h) < VISUAL_MIN_SHORT_SIDE:
        dest.unlink(missing_ok=True)
        return f"too small: {w}x{h}"
    return (w, h)


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
        vlm_model="", vlm_model_used="sdk-web",
        content_hash="", preprocessing_method="web-related", skip_reason="",
    ).to_dict()


def pick_fallback_region(scene: dict, pages_by_number: dict, used: set,
                         neighbor_targets: set) -> tuple[int, int] | None:
    """First region on the scene's page (else any page) that is neither used
    already (rule 2) nor equal to a neighboring scene's target (rule 1)."""
    page_order = [scene.get("page_ref")] + [n for n in sorted(pages_by_number)
                                            if n != scene.get("page_ref")]
    for pn in page_order:
        page = pages_by_number.get(pn)
        if not page or page.get("preprocessing_method") == "web-related":
            continue
        for panel in page.get("panels") or []:
            t = ("r", pn, int(panel["index"]))
            if t in used or t in neighbor_targets:
                continue
            return (pn, int(panel["index"]))
    return None


def hunt_visuals(project_name: str, *, force: bool = False, log=print) -> dict:
    root = get_art_project_path(project_name)
    manifest_path = root / "hunt_manifest.json"
    narration = json.loads((root / "narration.json").read_text())
    plan = json.loads((root / "visual_plan.json").read_text())
    ctx = json.loads((root / "art_context.json").read_text())

    pages: dict[int, dict] = {}
    for p in sorted((root / "preprocessed").glob("page_*.json")):
        d = json.loads(p.read_text())
        pages[int(d["page_number"])] = d

    scenes = narration.get("scenes") or []
    scenes_by_id = {s["scene_id"]: s for s in scenes}
    plan_by_id = {d["scene_id"]: d for d in plan}
    rel_dir = root / "related_images"

    if manifest_path.exists():
        if not force:
            log("[hunt] hunt_manifest.json exists — skipping (use force to redo)")
            return {"requested": 0, "resolved": 0, "skipped": True}
        prev = {e["scene_id"]: e for e in json.loads(manifest_path.read_text())}
        for s in scenes:
            e = prev.get(s["scene_id"])
            if e:
                # .get with current-value defaults: an odd/legacy manifest entry
                # must never kill a force-restore (best-effort contract).
                s["page_ref"] = e.get("original_page_ref", s["page_ref"])
                s["panel_ref"] = e.get("original_panel_ref", s["panel_ref"])
                d = plan_by_id.get(s["scene_id"])
                if d:
                    d.pop("page_ref", None)
                    d["kind"] = e.get("original_kind", d["kind"])
                    d["fallback"] = ""
                    d["panel_ref"] = e.get("original_panel_ref", d["panel_ref"])
        for n, pd_ in list(pages.items()):
            if pd_.get("preprocessing_method") == "web-related":
                for f in (root / "preprocessed").glob(f"page_{n:03d}_*.json"):
                    f.unlink()
                pages.pop(n)
        # stale downloads too — a re-hunt re-downloads what it needs, so old
        # rel_* files are pure disk creep once their page JSONs are gone.
        if rel_dir.exists():
            for f in rel_dir.glob("rel_*"):
                f.unlink(missing_ok=True)
        log("[hunt] force: restored original refs + removed stale related pages")

    decls = [d for d in plan if d["kind"] == "related"]
    requested = len(decls)
    by_chapter: dict[int, list[dict]] = {}
    for d in decls:
        by_chapter.setdefault(int(d.get("chapter_id") or 0), []).append(d)
    results: dict[int, dict] = {}
    for ch_id in sorted(by_chapter):
        group = by_chapter[ch_id]
        raw = sdk_complete_web(_HUNT_SYSTEM,
                               build_hunt_prompt(ctx, scenes, group),
                               max_turns=max(12, _HUNT_TURNS_PER_SUBJECT * len(group) + 4),
                               log=log)
        got = parse_hunt_response(raw)
        results.update(got)
        tag = f"chapter {ch_id}: " if (len(by_chapter) > 1 and ch_id) else ""
        log(f"[hunt] {tag}SDK returned {len(got)}/{len(group)} candidate image(s)")

    rel_dir.mkdir(exist_ok=True)
    used_urls: set = set()
    resolved_by_subject: dict[str, int] = {}
    manifest: list[dict] = []
    credits: list[dict] = []
    next_page = max(pages) + 1 if pages else 1
    resolved = 0

    # targets already on screen (rule 1/2 bookkeeping for fallbacks);
    # decls whose scene vanished from narration are ignored (drift tolerance)
    used_targets = {visual_target(scenes_by_id[d["scene_id"]], d)
                    for d in plan if d["kind"] == "painting_region"
                    and d["scene_id"] in scenes_by_id}

    ordered_ids = [s["scene_id"] for s in scenes]
    for d in decls:
        s = scenes_by_id.get(d["scene_id"])
        if s is None:
            log(f"[hunt] scene {d['scene_id']}: not in narration — skipped")
            continue
        original = {"scene_id": s["scene_id"], "original_page_ref": s["page_ref"],
                    "original_panel_ref": s["panel_ref"], "original_kind": "related"}
        # Same subject already resolved (writer validator forbids dups, this is
        # the safety net for legacy plans): reuse the downloaded page.
        subj_key = " ".join(str(d.get("subject") or "").lower().split())
        prev_page = resolved_by_subject.get(subj_key) if subj_key else None
        if prev_page is not None:
            s["page_ref"], s["panel_ref"] = prev_page, 0
            d["page_ref"] = prev_page
            d["fallback"] = ""
            manifest.append({**original, "page_number": prev_page,
                             "reused_subject": subj_key})
            log(f"[hunt] ✓ scene {d['scene_id']} reuses page {prev_page} "
                f"({subj_key!r})")
            resolved += 1
            continue
        c = results.get(d["scene_id"])
        dims = None
        dest = None
        chosen_url = ""
        # per-URL failure diagnostics (used_urls dups don't count as attempts)
        attempted: list[dict] = []
        if c:
            urls = [c["image_url"]]
            alt = str(c.get("alt_image_url") or "").strip()
            if alt and alt != c["image_url"]:
                urls.append(alt)   # SDK backup: tried only if the primary fails
            for url in urls:
                if url in used_urls:
                    continue
                dest = rel_dir / (f"rel_{d['scene_id']:02d}_"
                                  f"{hashlib.sha256(url.encode()).hexdigest()[:8]}.jpg")
                got = _download(url, dest)
                # Wikimedia rate-limits burst fetches (HTTP 429 measured
                # 2026-06-12) — space out every download, success or fail.
                _sleep(_DOWNLOAD_GAP_S)
                if isinstance(got, tuple):
                    dims = got
                    chosen_url = url
                    break
                attempted.append({"url": url, "reason": got})
                log(f"[hunt]   scene {d['scene_id']}: {url[:90]} → {got}")
        if dims and _image_matches_named_artwork(
                str(d.get("subject") or ""), str((c or {}).get("title") or "")):
            w, h = dims
            page = build_related_page(page_number=next_page,
                                      image_path=str(dest.resolve()),
                                      width=w, height=h,
                                      title=c["title"] or d["subject"])
            save_cached(root, next_page, image_hash(dest), page)
            pages[next_page] = page
            s["page_ref"], s["panel_ref"] = next_page, 0
            d["page_ref"] = next_page
            used_urls.add(chosen_url)
            credits.append({k: c.get(k, "") for k in ("title", "license", "source_url")})
            # image_url in the manifest is the URL ACTUALLY downloaded — when the
            # alt rescued a rejected primary, that's the alt, not the primary;
            # `attempted` then records WHY the primary was rejected.
            manifest.append({**original, "page_number": next_page,
                             "image": str(dest), **c, "image_url": chosen_url,
                             "attempted": attempted})
            log(f"[hunt] ✓ scene {d['scene_id']} → {c['title']!r} ({c['license']})")
            next_page += 1
            resolved += 1
            if subj_key:
                resolved_by_subject[subj_key] = s["page_ref"]
            continue
        # ── fallback: unused painting region, else painting_full ────────────
        reason = ("named-artwork mismatch" if (dims and c) else
                  "no SDK candidate" if not c else
                  "duplicate image" if not attempted else
                  "download/size reject (both candidates)" if len(attempted) > 1 else
                  "download/size reject")
        idx = ordered_ids.index(d["scene_id"])
        # NOTE: a neighboring `related` decl that already resolved to a web page
        # keeps its subject-based identity ("x", subject) here ON PURPOSE — the
        # subject still names the image's content, and rule 1 compares what's ON
        # SCREEN (content), not page numbers. A painting-region fallback can never
        # collide with that identity, so no false positives either.
        neighbor_targets = set()
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(ordered_ids):
                nd = plan_by_id.get(ordered_ids[j])
                ns = scenes_by_id.get(ordered_ids[j])
                if nd is None or ns is None:
                    continue
                neighbor_targets.add(visual_target(ns, nd))
        pick = pick_fallback_region(s, pages, used_targets, neighbor_targets)
        if pick:
            pn, panel = pick
            d["kind"], d["panel_ref"], d["fallback"] = "painting_region", panel, reason
            s["page_ref"], s["panel_ref"] = pn, panel
            used_targets.add(("r", pn, panel))
        else:
            d["kind"], d["panel_ref"], d["fallback"] = "painting_full", -1, reason
            s["panel_ref"] = -1
        manifest.append({**original, "fallback": reason, "attempted": attempted})
        log(f"[hunt] scene {d['scene_id']}: {reason} → fallback {d['kind']}")

    intro_id = next((s["scene_id"] for s in scenes if s.get("is_intro")), None)
    assign_motions(plan, intro_scene_id=intro_id)

    (root / "narration.json").write_text(json.dumps(narration, indent=2, ensure_ascii=False))
    (root / "visual_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    # NOTE: manifest written even when nothing resolved — reruns SKIP unless force
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    ctx["extra_image_credits"] = credits
    ctx["sources"] = list(dict.fromkeys(
        (ctx.get("sources") or []) + [c["source_url"] for c in credits if c["source_url"]]))
    (root / "art_context.json").write_text(json.dumps(ctx, indent=2, ensure_ascii=False))
    log(f"[hunt] done — {resolved}/{requested} related scene(s) resolved")
    return {"requested": requested, "resolved": resolved, "credits": credits}
