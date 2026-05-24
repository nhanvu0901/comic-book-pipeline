"""
v5 Phase 2 — VLM cluster naming.

After Magi v3 produces per-page character cluster_ids, we still don't know WHO
each cluster is (Magi just gives anonymous cluster_0, cluster_1, ...). This
module asks a VLM to identify each cluster by showing 3 sample crops + the
comic_context.characters list as a name candidates list.

Result is saved to projects/<name>/cluster_to_name.json:
    {"<cluster_id>": "<character_name>", ...}

Stage 5 uses this mapping to score panels by character identity more reliably
than VLM-only character name extraction (which often confuses Spider-Man variants
when multiple appear in the same comic).
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Callable

from PIL import Image
from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, VLM_MODELS_BATCH


def _vlm_client() -> OpenAI:
    return OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def _crop_to_b64(page_image_path: Path, bbox: dict, pad: float = 0.1) -> str:
    """Crop the character bbox (with small padding) and return base64 JPEG."""
    with Image.open(page_image_path) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
        px, py = int(w * pad), int(h * pad)
        left = max(0, x - px)
        top = max(0, y - py)
        right = min(iw, x + w + px)
        bottom = min(ih, y + h + py)
        crop = im.crop((left, top, right, bottom))
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _gather_cluster_samples(
    pages: list[dict], project_root: Path, max_samples: int = 3,
) -> dict[int, list[tuple[Path, dict]]]:
    """Walk pages and group character crops by cluster_id.

    Returns: {cluster_id: [(page_image_path, char_bbox), ...]} up to max_samples per cluster.

    Reads Magi's raw `characters` data — but our preprocessed page JSON doesn't
    keep per-character entries; it folds them into panel_infos. We need access
    to per-character data which we stash on the page JSON at preprocess time."""
    by_cluster: dict[int, list[tuple[Path, dict]]] = {}
    for page in pages:
        page_image_path = Path(page.get("source_image", ""))
        if not page_image_path.exists():
            continue
        for panel in page.get("panels", []) or []:
            for cid in panel.get("cluster_ids", []) or []:
                # We only have the PANEL bbox, not individual char bbox here.
                # Use the panel bbox as the crop region (will include character + context).
                if len(by_cluster.get(cid, [])) >= max_samples:
                    continue
                by_cluster.setdefault(cid, []).append((page_image_path, panel.get("bbox", {})))
    return by_cluster


def _parse_vlm_response(raw: str) -> dict:
    """Extract JSON dict from possibly-fenced LLM output."""
    for pat in (r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"):
        m = re.search(pat, raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(raw[i : j + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def resolve_cluster_names(
    pages: list[dict],
    comic_context: dict,
    project_root: Path,
    progress: Callable[[str], None] | None = None,
) -> dict[int, str]:
    """Run VLM to identify each Magi cluster. Returns {cluster_id: name} mapping.

    For each cluster, send up to 3 representative panel crops + comic_context's
    known-characters list. VLM returns most likely name or "Unknown"."""
    log = progress or (lambda _m: None)
    if not OPENROUTER_API_KEY:
        log("[cluster-namer] no OPENROUTER_API_KEY — skipping naming")
        return {}

    by_cluster = _gather_cluster_samples(pages, project_root)
    if not by_cluster:
        log("[cluster-namer] no Magi clusters found in pages — skipping")
        return {}

    known_chars = comic_context.get("characters") or []
    summary_chars = (comic_context.get("summary") or {}).get("characters") or []
    char_list_str = ", ".join(known_chars) if known_chars else "(unknown — guess by visual)"
    if summary_chars and not known_chars:
        char_list_str = ", ".join(c.get("name", "") for c in summary_chars if c.get("name"))

    client = _vlm_client()
    chain = list(VLM_MODELS_BATCH)
    cluster_to_name: dict[int, str] = {}

    for cluster_id, samples in sorted(by_cluster.items()):
        if not samples:
            continue
        crops_b64 = [_crop_to_b64(p, bbox) for p, bbox in samples[:3]]
        prompt = (
            f"You are identifying a comic character. The {len(crops_b64)} images "
            f"below show different panels where this character appears.\n\n"
            f"Comic context:\n"
            f"  Title: {comic_context.get('title', '?')}\n"
            f"  Series: {comic_context.get('series', '?')} {comic_context.get('issues', '')}\n"
            f"  Known characters in this story: {char_list_str}\n\n"
            f"Which named character is shown? Match visual to the character list.\n"
            f"If unclear, respond 'Unknown'.\n\n"
            f"Return JSON: "
            f'{{"name": "<one from list, or Unknown>", "confidence": "high|medium|low"}}'
        )
        content: list[dict] = [{"type": "text", "text": prompt}]
        for b64 in crops_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })

        name = "Unknown"
        for model in chain:
            try:
                resp = client.with_options(timeout=60).chat.completions.create(
                    model=model, max_tokens=200,
                    messages=[{"role": "user", "content": content}],
                )
                raw = (resp.choices[0].message.content or "").strip()
                parsed = _parse_vlm_response(raw)
                if isinstance(parsed, dict) and parsed.get("name"):
                    name = str(parsed.get("name", "Unknown")).strip()
                    break
            except Exception as exc:
                log(f"[cluster-namer] cluster_{cluster_id} via {model}: {type(exc).__name__}")
                continue
        cluster_to_name[cluster_id] = name
        log(f"[cluster-namer] cluster_{cluster_id} → {name!r}")

    # Persist mapping for downstream stages.
    out_path = project_root / "cluster_to_name.json"
    out_path.write_text(json.dumps(
        {str(k): v for k, v in cluster_to_name.items()}, indent=2, ensure_ascii=False
    ))
    log(f"[cluster-namer] saved → {out_path.name}")
    return cluster_to_name
