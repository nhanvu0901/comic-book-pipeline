"""A5 long-form: per-chapter Cartesia TTS via comic Stage 4 (read-only,
PROJECTS_ROOT runtime override) + WAV stitch with inter-chapter silence.

Why per chapter: Stage 4 sends ONE request with the full transcript; an 8-12
minute script (~9-10k chars) is far past the size the single-request path was
tuned for, and chapter-sized calls keep retries cheap. The stitcher owns the
ONLY timing merge — offsets are computed from the actual stitched frame
counts, so scene_timings/word_timestamps can never drift from the audio."""
import json
import wave
from pathlib import Path

from .config import ART_LF_CHAPTER_GAP_S, get_art_project_path


def _chapter_dir(root: Path, chapter_id: int) -> Path:
    d = root / "_chapters" / f"ch_{chapter_id:02d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def synthesize_longform(project_name: str, *, force: bool = False,
                        log=print) -> dict:
    root = get_art_project_path(project_name)
    chapters_path = root / "chapters.json"
    if not chapters_path.exists():
        raise FileNotFoundError(
            f"{chapters_path} missing — run long-form narrate first")
    chapters = json.loads(chapters_path.read_text())
    narration = json.loads((root / "narration.json").read_text())
    scenes_by_id = {s["scene_id"]: s for s in narration["scenes"]}

    if (root / "audio.wav").exists() and not force:
        log("[tts-lf] audio.wav exists — skipping (force to redo)")
        return {"skipped": True}

    import stages.stage_4.pipeline as s4
    prev_root = s4.PROJECTS_ROOT
    per_chapter: list[dict] = []
    try:
        s4.PROJECTS_ROOT = root / "_chapters"
        for ch in chapters:
            ch_dir = _chapter_dir(root, ch["chapter_id"])
            ch_narration = dict(narration)
            ch_narration["scenes"] = [scenes_by_id[sid] for sid in ch["scene_ids"]]
            (ch_dir / "narration.json").write_text(
                json.dumps(ch_narration, indent=2, ensure_ascii=False))
            log(f"[tts-lf] chapter {ch['chapter_id']}/{len(chapters)} "
                f"({len(ch['scene_ids'])} scenes)…")
            s4.synthesize_project(ch_dir.name, force=force)
            per_chapter.append({
                "wav": ch_dir / "audio.wav",
                "timings": json.loads((ch_dir / "scene_timings.json").read_text()),
                "words": json.loads((ch_dir / "word_timestamps.json").read_text()),
            })
    finally:
        s4.PROJECTS_ROOT = prev_root

    # ── stitch WAVs + offset every timing from REAL frame counts ────────────
    out_wav = root / "audio.wav"
    all_timings: list[dict] = []
    all_words: list[dict] = []
    with wave.open(str(per_chapter[0]["wav"]), "rb") as first:
        params = first.getparams()
    framerate = params.framerate
    gap_frames = int(round(ART_LF_CHAPTER_GAP_S * framerate))
    silence = b"\x00" * (gap_frames * params.sampwidth * params.nchannels)

    frames_written = 0
    with wave.open(str(out_wav), "wb") as out:
        out.setparams(params)
        for k, (ch, data) in enumerate(zip(chapters, per_chapter)):
            offset = frames_written / framerate
            ch["start"] = round(offset, 3)
            with wave.open(str(data["wav"]), "rb") as w:
                if (w.getframerate(), w.getsampwidth(), w.getnchannels()) != (
                        params.framerate, params.sampwidth, params.nchannels):
                    raise RuntimeError(
                        f"chapter {ch['chapter_id']} wav params differ — "
                        "cannot stitch")
                out.writeframes(w.readframes(w.getnframes()))
                frames_written += w.getnframes()
            for t in data["timings"]:
                all_timings.append({**t, "start": round(t["start"] + offset, 3),
                                    "end": round(t["end"] + offset, 3)})
            for wd in data["words"]:
                all_words.append({**wd, "start": round(wd["start"] + offset, 3),
                                  "end": round(wd["end"] + offset, 3)})
            if k < len(per_chapter) - 1:
                out.writeframes(silence)
                frames_written += gap_frames

    (root / "scene_timings.json").write_text(
        json.dumps(all_timings, indent=2, ensure_ascii=False))
    (root / "word_timestamps.json").write_text(
        json.dumps(all_words, indent=2, ensure_ascii=False))
    chapters_path.write_text(json.dumps(chapters, indent=2, ensure_ascii=False))
    total = frames_written / framerate
    log(f"[tts-lf] stitched {len(chapters)} chapters → audio.wav "
        f"({total:.1f}s, gap {ART_LF_CHAPTER_GAP_S}s)")
    return {"chapters": len(chapters), "duration": round(total, 2)}
