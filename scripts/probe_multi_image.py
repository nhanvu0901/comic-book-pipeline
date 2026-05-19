"""
Probe whether each model in VLM_MODELS actually accepts multi-image input via
OpenRouter, and whether the response makes sense as a "reading across multiple
pages" — i.e. confirms the model genuinely sees all images, not just the last.

Usage:
    python -m scripts.probe_multi_image
    python -m scripts.probe_multi_image --pages 3 --project "What if gwen stacy"
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, VLM_MODELS, PROJECTS_ROOT


PROMPT = """You are looking at multiple comic book pages in reading order.

Tell me:
1. How many pages did you receive?
2. For each page (in order), give a ONE-sentence summary.
3. Are these pages part of a continuous sequence (yes/no), and why?

Respond with ONLY this JSON shape, no prose, no markdown:
{
  "image_count_seen": <int>,
  "page_summaries": ["page 1 summary...", "page 2 summary...", ...],
  "is_continuous_sequence": "yes" | "no",
  "reasoning": "one sentence"
}"""


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def find_test_pages(project_name: str, n: int) -> list[Path]:
    raw = PROJECTS_ROOT / project_name / "raw_comic"
    if not raw.exists():
        raise FileNotFoundError(f"No raw_comic folder for project {project_name!r}")
    pages = sorted(raw.glob("ch01_page_*.jpg"))
    if len(pages) < n:
        raise RuntimeError(f"Need {n} pages, project has {len(pages)}")
    # Skip cover (page 1) — pick consecutive middle pages where action usually happens.
    start = min(20, len(pages) - n)
    return pages[start:start + n]


def probe_one(client: OpenAI, model: str, pages: list[Path]) -> dict:
    content: list[dict] = [{"type": "text", "text": PROMPT}]
    for p in pages:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encode_image(p)}"},
        })

    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=900,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        return {
            "status": "API_ERROR",
            "elapsed_s": round(time.time() - t0, 1),
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "raw": "",
            "parsed": None,
        }

    elapsed = round(time.time() - t0, 1)
    raw = (resp.choices[0].message.content or "").strip()

    # Try to extract JSON
    parsed: dict | None = None
    for chunk in (raw, raw.split("```json", 1)[-1].split("```", 1)[0], raw.split("```", 1)[-1].split("```", 1)[0]):
        try:
            parsed = json.loads(chunk.strip())
            break
        except (json.JSONDecodeError, IndexError):
            continue
    if parsed is None:
        # last resort — find first {...} block
        i, j = raw.find("{"), raw.rfind("}")
        if i != -1 and j > i:
            try:
                parsed = json.loads(raw[i:j + 1])
            except json.JSONDecodeError:
                pass

    if parsed is None:
        return {
            "status": "UNPARSEABLE_JSON",
            "elapsed_s": elapsed,
            "error": "",
            "raw": raw[:400],
            "parsed": None,
        }

    saw = parsed.get("image_count_seen")
    expected = len(pages)
    summaries = parsed.get("page_summaries") or []

    if saw == expected and len(summaries) == expected:
        verdict = "PASS"
    elif saw == expected:
        verdict = "PARTIAL (count ok, summary count off)"
    else:
        verdict = f"FAIL (saw {saw}, expected {expected})"

    return {
        "status": verdict,
        "elapsed_s": elapsed,
        "error": "",
        "raw": "",
        "parsed": parsed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="What if gwen stacy",
                        help="Project name to pull test pages from")
    parser.add_argument("--pages", type=int, default=3,
                        help="Pages per request (3 = safe ceiling for Nemotron)")
    args = parser.parse_args()

    if not OPENROUTER_API_KEY:
        print("FATAL: OPENROUTER_API_KEY not set. Run after creating .env.", file=sys.stderr)
        sys.exit(1)

    pages = find_test_pages(args.project, args.pages)
    print(f"Using {len(pages)} test pages:")
    for p in pages:
        print(f"  - {p.name} ({p.stat().st_size // 1024} KB)")
    print()

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

    results: list[tuple[str, dict]] = []
    for model in VLM_MODELS:
        print(f"Probing {model}...")
        r = probe_one(client, model, pages)
        results.append((model, r))
        print(f"  → {r['status']}  ({r['elapsed_s']}s)")
        if r["error"]:
            print(f"    error: {r['error']}")
        if r.get("raw"):
            print(f"    raw[:200]: {r['raw'][:200]}")
        if r.get("parsed"):
            p = r["parsed"]
            print(f"    image_count_seen: {p.get('image_count_seen')}")
            print(f"    is_continuous_sequence: {p.get('is_continuous_sequence')}")
            summaries = p.get("page_summaries") or []
            for i, s in enumerate(summaries, start=1):
                print(f"    page {i}: {str(s)[:140]}")
        print()

    print("─" * 70)
    print("SUMMARY")
    print("─" * 70)
    for model, r in results:
        flag = "✅" if r["status"] == "PASS" else ("⚠️ " if "PARTIAL" in r["status"] else "❌")
        print(f"  {flag} {model}: {r['status']}  ({r['elapsed_s']}s)")


if __name__ == "__main__":
    main()
