"""Copy a REAL production project into my test repo's projects/ as an explore_answer project (data-level
conversion only) — never touches the production tree (read-only source)."""
import json, pathlib, shutil, sys

SRC = pathlib.Path(r"D:\code\comic-book-pipeline\projects\logans_winter_soldier_programming_takes_over_threatening_to_")
ROOT = pathlib.Path(r"D:\code\cbp-video-test-p1\repo\projects")
name = sys.argv[1] if len(sys.argv) > 1 else "qa_e2e"
dst = ROOT / name
if dst.exists():
    shutil.rmtree(dst)
SKIP = shutil.ignore_patterns(
    "final.mp4", "final.mp4.verified.json", "video_silent.mp4", "audio.wav", "audio_mixed.wav",
    "word_timestamps.json", "scene_timings.json", "caption_chunks.json", "narration.tts.sha256",
    "tts_voice.json", "shots", "shots.json", "concat_list.txt", "_whip_bridges", "music.json",
    "review_backup", "logs", "*_writer_prompt.md")
shutil.copytree(SRC, dst, ignore=SKIP)

nar = json.loads((dst / "narration.json").read_text(encoding="utf-8"))
nar["mode"] = "explore_answer"
(dst / "narration.json").write_text(json.dumps(nar, indent=2, ensure_ascii=False), encoding="utf-8")
cc = json.loads((dst / "comic_context.json").read_text(encoding="utf-8"))
cc["plot_source"] = "answer_research"
(dst / "comic_context.json").write_text(json.dumps(cc, indent=2, ensure_ascii=False), encoding="utf-8")
st = json.loads((dst / "state.json").read_text(encoding="utf-8"))
st["current_stage"] = 5; st["project_name"] = name
(dst / "state.json").write_text(json.dumps(st, indent=2), encoding="utf-8")
print("project:", dst)
print("scenes:", [(s["scene_id"], len(s.get("visual_beats") or []), s.get("is_intro"), s.get("is_outro"), len(s["text"].split())) for s in nar["scenes"]])
locks = json.loads((dst / "review" / "locks.json").read_text(encoding="utf-8"))
print("locks:", sorted(locks.get("locks", {})), "approved:", locks.get("approved"))
print("review:", sorted(p.name for p in (dst / "review").iterdir()))
size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
print("size MB:", round(size / 1e6, 1))
