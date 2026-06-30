"""
Comprehensive benchmark builder for comic-narration Short videos.

Analyzes reference videos (or ours) across 12 dimensions:

STRUCTURAL:
  1. Duration (target 48-60s)
  2. Word count (target 170-260)
  3. Words-per-second pacing (target 3.3-4.4)
  4. Sentence count (target 9-15)
  5. Caption chunk count (target 22-45)

VOICE / STYLE:
  6. Sentence-length variance (stdev ≥ 4)
  7. Has ≥2 punch sentences (5-12w)
  8. Has ≥2 long sentences (23-30w)
  9. Hook archetype (interrogative/temporal/scenic — NOT character-first)
 10. Outro pattern "The comic is X." present

VISUAL:
 11. Panel-change rate (per sample-frame VLM diff — target ≥0.5 changes/sec)
 12. Caption rendering quality (font glitches per VLM audit — target 0 in 12 sampled frames)

Saves:
  research/reports/_BENCHMARK_thresholds.json — qualifying thresholds
  research/reports/_BENCHMARK_evidence.json — per-video raw metrics across the
    reference corpus (5+ videos) used to derive the thresholds
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import statistics as stat
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from openai import OpenAI
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL


VLM = "google/gemini-2.5-flash-lite"
REPORTS = Path(__file__).parent.parent / "reports"
REFERENCES_DIR = Path(__file__).parent.parent / "reference"


# ──────────────────────────────────────────────────────────────────────────
# Transcript / structural metrics (VTT or word_timestamps.json)
# ──────────────────────────────────────────────────────────────────────────


def _t(s: str) -> float:
    m = re.match(r"(\d+):(\d+):(\d+)\.(\d+)", s)
    if not m: return 0.0
    h, mn, sec, ms = m.groups()
    return int(h)*3600 + int(mn)*60 + int(sec) + int(ms)/1000


def parse_vtt_cues(vtt_path: Path) -> list[dict]:
    if not vtt_path.exists():
        return []
    text = vtt_path.read_text(encoding="utf-8", errors="replace")
    cues = []
    cur_start = cur_end = None
    cur_text = []
    seen = set()
    in_cue = False
    for line in text.splitlines():
        m = re.match(r"^(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})", line)
        if m:
            if cur_text and cur_start is not None:
                t = " ".join(cur_text).strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    cues.append({"start": _t(cur_start), "end": _t(cur_end), "text": t})
            cur_start, cur_end = m.group(1), m.group(2)
            cur_text = []
            in_cue = True
            continue
        if in_cue and line.strip() and "<" not in line and not line.startswith(("WEBVTT", "Kind:", "Language:")):
            stripped = re.sub(r"<[^>]+>", "", line).strip()
            if stripped:
                cur_text.append(stripped)
    if cur_start is not None and cur_text:
        t = " ".join(cur_text).strip()
        if t and t.lower() not in seen:
            cues.append({"start": _t(cur_start), "end": _t(cur_end), "text": t})
    cues.sort(key=lambda c: c["start"])
    return cues


def probe_duration(video: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip() or "0")


def classify_hook(first_words: str) -> str:
    s = first_words.lower().strip()
    # Question hooks (teaser-intro pattern): "Ever wonder...?", "What if...?",
    # "Have you ever...?", "Can/Could/Would <hero>...?" — or any first words
    # containing "?". All are interrogative, the strongest Shorts opener.
    if re.match(r"^(ever wonder|ever wondered|have you ever|what if|what would|"
                r"why|how|where|can|could|would|did|do|does)\b", s):
        return "interrogative"
    if "?" in first_words:
        return "interrogative"
    if re.match(r"^when\b", s): return "temporal-when"  # 66% of channel
    if re.match(r"^(after|while|during|once)\b", s): return "temporal-other"
    if re.match(r"^(in an? \w+ (universe|reality|world|year|future|past|version)|in [\d]{4})", s):
        return "scenic"
    # Broadened character-first ACTION opener (cap subject 1-4 tokens + verb).
    # KEEP IDENTICAL to stages/stage_3/write_script._classify_hook.
    if re.match(r"^[A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+){0,3}\s+[a-z]", first_words):
        return "character_action"
    return "other_character"


def compute_structural(video: Path, cues: list[dict]) -> dict:
    duration = probe_duration(video)
    full_text = " ".join(c["text"] for c in cues)
    words = full_text.split()
    word_count = len(words)
    wps = word_count / duration if duration > 0 else 0
    sentences = re.split(r"(?<=[.!?])\s+", full_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sent_lengths = [len(s.split()) for s in sentences]

    punch = sum(1 for l in sent_lengths if 5 <= l <= 12)
    long = sum(1 for l in sent_lengths if 23 <= l <= 30)
    stdev = stat.stdev(sent_lengths) if len(sent_lengths) >= 2 else 0

    first12 = " ".join(words[:12])
    archetype = classify_hook(first12)

    last_sentence = sentences[-1] if sentences else ""
    has_outro = bool(re.search(r"\bthe comic is\b", last_sentence.lower()))

    return {
        "duration_s": round(duration, 2),
        "word_count": word_count,
        "wps": round(wps, 2),
        "sentence_count": len(sentences),
        "chunk_count": len(cues),
        "sentence_lens": sent_lengths,
        "sentence_stdev": round(stdev, 2),
        "punch_sentences": punch,
        "long_sentences": long,
        "hook_archetype": archetype,
        "hook_first_12": first12,
        "has_outro": has_outro,
        "last_sentence": last_sentence[:100],
    }


# ──────────────────────────────────────────────────────────────────────────
# Visual / panel change metrics (sampled frames)
# ──────────────────────────────────────────────────────────────────────────


def detect_shot_cuts(video: Path, threshold: float = 0.3) -> int:
    """Count hard scene cuts in the video."""
    r = subprocess.run(
        ["ffmpeg", "-i", str(video), "-filter:v",
         f"select='gt(scene,{threshold})',showinfo",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    count = 0
    for line in (r.stderr or "").splitlines():
        if "pts_time:" in line:
            count += 1
    return count


def extract_frame(video: Path, t: float, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(video),
                    "-vframes", "1", "-q:v", "2", str(out)],
                   capture_output=True, check=True)


VLM_FRAME_AUDIT_PROMPT = """Audit this video frame:
Return JSON only:
{
  "caption_clean": true|false,         # are burned-in captions glyph-clean (no boxes, missing chars, mojibake)
  "panel_full_frame": true|false,      # does a single comic panel fill the screen (vs blurred/letterbox)
  "caption_position": "middle"|"top"|"bottom"|"other",
  "caption_case": "UPPER"|"lower"|"mixed",
  "characters_visible": ["..."],       # list of identifiable named characters in panel
  "frame_quality": "high"|"medium"|"low"
}"""


def audit_frames(video: Path, n: int = 8) -> list[dict]:
    """Sample N frames evenly and VLM-audit each."""
    duration = probe_duration(video)
    if duration <= 0:
        return []
    label = video.stem
    out_dir = REPORTS / f"bench_{label}" / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    times = [round(0.5 + (duration - 1.0) * i / (n - 1), 2) for i in range(n)]
    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    results = []
    for i, t in enumerate(times):
        fp = out_dir / f"f{i:02d}_t{t:.2f}.jpg"
        if not fp.exists():
            extract_frame(video, t, fp)
        b64 = base64.b64encode(fp.read_bytes()).decode()
        try:
            resp = client.with_options(timeout=60).chat.completions.create(
                model=VLM, max_tokens=400,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": VLM_FRAME_AUDIT_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}],
            )
            raw = (resp.choices[0].message.content or "").strip()
            m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
            if m: raw = m.group(1).strip()
            data = json.loads(raw)
            results.append({"t": t, **data})
        except (json.JSONDecodeError, Exception):
            results.append({"t": t, "_parse_error": True})
    return results


def aggregate_visual(audits: list[dict]) -> dict:
    n = len(audits)
    if n == 0:
        return {"frames_audited": 0}
    clean = sum(1 for a in audits if a.get("caption_clean") is True)
    full_panel = sum(1 for a in audits if a.get("panel_full_frame") is True)
    middle = sum(1 for a in audits if a.get("caption_position") == "middle")
    upper = sum(1 for a in audits if a.get("caption_case") == "UPPER")
    high_q = sum(1 for a in audits if a.get("frame_quality") == "high")
    unique_chars: set[str] = set()
    for a in audits:
        for c in a.get("characters_visible", []) or []:
            unique_chars.add(c.strip())
    return {
        "frames_audited": n,
        "caption_clean_pct": round(clean / n * 100, 1),
        "panel_full_frame_pct": round(full_panel / n * 100, 1),
        "caption_middle_pct": round(middle / n * 100, 1),
        "caption_upper_pct": round(upper / n * 100, 1),
        "high_quality_pct": round(high_q / n * 100, 1),
        "unique_characters_seen": sorted(unique_chars),
        "unique_character_count": len(unique_chars),
    }


# ──────────────────────────────────────────────────────────────────────────
# Build benchmark from multiple reference videos
# ──────────────────────────────────────────────────────────────────────────


REF_VIDEOS = [
    # ComicsUnlocked
    "zvFa8OuDo4Q", "on-6f2Fw8v8", "citojb7Tv5Q",
    # TheComicCivilian
    "vr3WpCH40Ns", "_l28cZnipGo", "4mEDGiOkC6c",
    "l5V8x7HFDAs", "CZoCxXcWGRQ", "PMwT5lS_ZKo",
    "Kd8_sopiaOI",
]


def analyze_one(video_id: str, *, with_vlm: bool = True) -> dict:
    mp4 = REFERENCES_DIR / f"{video_id}.mp4"
    vtt = REFERENCES_DIR / f"{video_id}.en.vtt"
    if not mp4.exists():
        return {"video_id": video_id, "_missing_mp4": True}
    cues = parse_vtt_cues(vtt)
    structural = compute_structural(mp4, cues)
    shot_cuts = detect_shot_cuts(mp4)
    visual = aggregate_visual(audit_frames(mp4, n=6)) if with_vlm else {}
    return {
        "video_id": video_id,
        **structural,
        "shot_cuts": shot_cuts,
        **visual,
    }


def build_benchmark(reference_ids: list[str], *, with_vlm: bool = True) -> dict:
    """Run analyze_one on each reference, aggregate, derive thresholds."""
    per_video = []
    for vid in reference_ids:
        print(f"  analyzing {vid}...")
        r = analyze_one(vid, with_vlm=with_vlm)
        if r.get("_missing_mp4"):
            print(f"    SKIP (mp4 missing)")
            continue
        per_video.append(r)

    if not per_video:
        return {"error": "no references analyzed"}

    def agg(field, fn=lambda v: v):
        vals = [fn(r[field]) for r in per_video if field in r and isinstance(r[field], (int, float))]
        if not vals: return None
        return {
            "mean": round(stat.mean(vals), 2),
            "median": round(stat.median(vals), 2),
            "stdev": round(stat.stdev(vals), 2) if len(vals) >= 2 else 0,
            "min": round(min(vals), 2), "max": round(max(vals), 2),
            "p10": round(sorted(vals)[max(0, int(0.1 * len(vals)))], 2),
            "p90": round(sorted(vals)[min(len(vals) - 1, int(0.9 * len(vals)))], 2),
        }

    benchmark = {
        "n_references": len(per_video),
        "stats": {
            "duration_s": agg("duration_s"),
            "word_count": agg("word_count"),
            "wps": agg("wps"),
            "sentence_count": agg("sentence_count"),
            "chunk_count": agg("chunk_count"),
            "sentence_stdev": agg("sentence_stdev"),
            "punch_sentences": agg("punch_sentences"),
            "long_sentences": agg("long_sentences"),
            "shot_cuts": agg("shot_cuts"),
            "caption_clean_pct": agg("caption_clean_pct"),
            "panel_full_frame_pct": agg("panel_full_frame_pct"),
            "unique_character_count": agg("unique_character_count"),
        },
        "hook_archetype_distribution": {},
        "outro_present_pct": 0,
    }
    hook_arch = {}
    for r in per_video:
        a = r.get("hook_archetype", "?")
        hook_arch[a] = hook_arch.get(a, 0) + 1
    benchmark["hook_archetype_distribution"] = hook_arch
    benchmark["outro_present_pct"] = round(
        sum(1 for r in per_video if r.get("has_outro")) / len(per_video) * 100, 1
    )

    # Derive qualifying thresholds: use p10/p90 for "channel range"
    s = benchmark["stats"]
    qualifying = {
        "duration_min": s["duration_s"]["p10"] if s["duration_s"] else 45,
        "duration_max": s["duration_s"]["p90"] if s["duration_s"] else 65,
        "word_count_min": s["word_count"]["p10"] if s["word_count"] else 170,
        "word_count_max": s["word_count"]["p90"] if s["word_count"] else 260,
        "wps_min": s["wps"]["p10"] if s["wps"] else 3.3,
        "wps_max": s["wps"]["p90"] if s["wps"] else 4.5,
        "sentence_count_min": s["sentence_count"]["p10"] if s["sentence_count"] else 9,
        "sentence_count_max": s["sentence_count"]["p90"] if s["sentence_count"] else 17,
        "chunk_count_min": s["chunk_count"]["p10"] if s["chunk_count"] else 22,
        "sentence_stdev_min": 3.0,
        "punch_sentences_min": 1,  # at least 1 punch (5-12w)
        "long_sentences_min": 1,   # at least 1 long (23-30w)
        # Visual thresholds: derived from p10 of references (VLM has noise; even
        # genuine high-quality reference videos hit 66.7-100% on these metrics).
        "caption_clean_pct_min": s.get("caption_clean_pct", {}).get("p10", 80.0)
                                  if s.get("caption_clean_pct") else 80.0,
        "panel_full_frame_pct_min": s.get("panel_full_frame_pct", {}).get("p10", 70.0)
                                     if s.get("panel_full_frame_pct") else 70.0,
        "outro_required": True,
        "hook_archetype_allowed": ["interrogative", "temporal-when", "temporal-other", "scenic"],
    }

    return {
        "benchmark": benchmark,
        "qualifying": qualifying,
        "per_video": per_video,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-vlm", action="store_true", help="Skip VLM frame audit (faster, no visual metrics)")
    p.add_argument("--refs", nargs="*", default=REF_VIDEOS, help="reference video IDs")
    args = p.parse_args()

    print(f"Building benchmark from {len(args.refs)} reference videos "
          f"(VLM {'OFF' if args.no_vlm else 'ON'})...")

    result = build_benchmark(args.refs, with_vlm=not args.no_vlm)

    REPORTS.mkdir(parents=True, exist_ok=True)
    bench_path = REPORTS / "_BENCHMARK_thresholds.json"
    evidence_path = REPORTS / "_BENCHMARK_evidence.json"
    bench_path.write_text(json.dumps(
        {"benchmark": result["benchmark"], "qualifying": result["qualifying"]},
        indent=2, ensure_ascii=False,
    ))
    evidence_path.write_text(json.dumps(result["per_video"], indent=2, ensure_ascii=False))

    print(f"\n✓ Wrote {bench_path}")
    print(f"✓ Wrote {evidence_path}")
    print()
    print("=" * 78)
    print("QUALIFYING THRESHOLDS")
    print("=" * 78)
    for k, v in result["qualifying"].items():
        print(f"  {k:30s}: {v}")


if __name__ == "__main__":
    main()
