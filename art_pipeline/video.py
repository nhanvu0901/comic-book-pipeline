"""A6: art assembler wrapper, plus the two authenticity artifacts (spec §6.4/§6.5):
  youtube_description.txt — museum credit + objectURL + CC0 + sources
  art_projects/_variety_log.csv — anti-template structure fingerprints
Runtime overrides (never file edits): MIRROR_PANELS off (mirroring famous artworks
is factually wrong), INPAINT_BUBBLE_TEXT off (could erase signatures). These are
passed to render_shot's crop path via the comic stage_5.shots globals."""
import csv
import datetime
import json
from pathlib import Path

from .config import ART_PROJECTS_ROOT, get_art_project_path, ART_LF_OUTPUT_W, ART_LF_OUTPUT_H

VARIETY_LOG = ART_PROJECTS_ROOT / "_variety_log.csv"
_VARIETY_WINDOW = 3  # warn when the last N fingerprints are identical


def build_youtube_description(ctx: dict) -> str:
    lines = [ctx.get("title", ""), ""]
    for a in ctx.get("artworks", []):
        lines.append(f"“{a.get('title', '')}” — {a.get('artist', '')} ({a.get('year', '')})")
        if a.get("credit_line"):
            lines.append(a["credit_line"])
        if a.get("object_url"):
            lines.append(a["object_url"])
        lines.append("")
    lines.append("Artwork images are in the public domain (CC0), courtesy of "
                 "The Metropolitan Museum of Art Open Access program.")
    srcs = [s for s in (ctx.get("sources") or []) if s]
    if srcs:
        lines += ["", "Sources:"] + srcs
    extra = ctx.get("extra_image_credits") or []
    if extra:
        lines += ["", "Additional images:"]
        for c in extra:
            who = f" — {c.get('author')}" if c.get("author") else ""
            lines.append(f"“{c.get('title', '')}”{who} ({c.get('license', '')}), "
                         f"{c.get('source_url', '')}")
    return "\n".join(lines).strip() + "\n"


def build_youtube_chapters(chapters: list[dict]) -> str:
    lines = []
    for ch in chapters:
        start = float(ch.get("start") or 0.0)
        m, s = divmod(int(start), 60)
        lines.append(f"{m:02d}:{s:02d} {ch.get('title', '')}")
    return "\n".join(lines) + "\n"


def discover_bgm(root: Path) -> Path | None:
    """User-supplied music: first bgm.* in the project folder (user decision:
    manual per-video pick, no auto library)."""
    for ext in ("mp3", "m4a", "wav", "ogg", "flac"):
        p = root / f"bgm.{ext}"
        if p.exists():
            return p
    return None


def structure_fingerprint(narration: dict) -> str:
    # Format v2 (2026-06-12): mode|count|short/longform|intro/cold|outro/hard-end.
    # Rows logged before this date are 4-field v1; v1 never matches v2 —
    # intentional clean migration break for the anti-template window.
    scenes = narration.get("scenes") or []
    longform = any(s.get("chapter_id") for s in scenes)
    return "|".join([
        str(narration.get("mode", "")),
        str(len(scenes)),
        "longform" if longform else "short",
        "intro" if any(s.get("is_intro") for s in scenes) else "cold",
        "outro" if any(s.get("is_outro") for s in scenes) else "hard-end",
    ])


def append_variety_log(project_name: str, narration: dict, *,
                       path: Path = VARIETY_LOG) -> str:
    """Append this video's fingerprint; return a warning string when the last
    _VARIETY_WINDOW entries (including this one) are identical, else ""."""
    fp = structure_fingerprint(narration)
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["project", "fingerprint", "date"])
        w.writerow([project_name, fp, datetime.date.today().isoformat()])
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    last = [r["fingerprint"] for r in rows[-_VARIETY_WINDOW:]]
    if len(last) == _VARIETY_WINDOW and len(set(last)) == 1:
        return (f"⚠ last {_VARIETY_WINDOW} videos share the same structure ({fp}) — "
                "vary the mode/hook/scene count (anti-template, spec §6.5)")
    return ""


def assemble_art(project_name: str, **kwargs):
    import stages.stage_5.shots as shots
    from .assemble import assemble_art_video

    root = get_art_project_path(project_name)
    longform = (root / "chapters.json").exists()
    if longform and not kwargs.get("bg_music_path"):
        bgm = discover_bgm(root)
        if bgm:
            kwargs["bg_music_path"] = str(bgm)
        else:
            print("[assemble] WARNING: long-form without BGM — drop bgm.mp3 into the project")

    # render_shot's crop path still honors these comic globals — never mirror
    # or inpaint a real artwork; long-form additionally renders 16:9.
    prev = (shots.MIRROR_PANELS, shots.INPAINT_BUBBLE_TEXT,
            shots.OUTPUT_W, shots.OUTPUT_H, shots.TARGET_ASPECT)
    shots.MIRROR_PANELS = False
    shots.INPAINT_BUBBLE_TEXT = False
    if longform:
        shots.OUTPUT_W = ART_LF_OUTPUT_W
        shots.OUTPUT_H = ART_LF_OUTPUT_H
        shots.TARGET_ASPECT = ART_LF_OUTPUT_W / ART_LF_OUTPUT_H
    try:
        result = assemble_art_video(project_name, **kwargs)
    finally:
        (shots.MIRROR_PANELS, shots.INPAINT_BUBBLE_TEXT,
         shots.OUTPUT_W, shots.OUTPUT_H, shots.TARGET_ASPECT) = prev

    ctx = json.loads((root / "art_context.json").read_text())
    description = build_youtube_description(ctx)
    if longform:
        chapters = json.loads((root / "chapters.json").read_text())
        chapters_txt = build_youtube_chapters(chapters)
        (root / "youtube_chapters.txt").write_text(chapters_txt)
        description += ("\nChapters:\n" + chapters_txt +
                        "\nSubtitles available — turn on CC.\n")
    (root / "youtube_description.txt").write_text(description)
    narration = json.loads((root / "narration.json").read_text())
    warning = append_variety_log(project_name, narration, path=VARIETY_LOG)
    if warning:
        print(f"[video] {warning}")
    return result
