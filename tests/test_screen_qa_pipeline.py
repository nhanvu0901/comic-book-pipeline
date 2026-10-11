"""screen_qa Stage 5 end to end: the real run_screen_qa_pipeline / assemble_project on the fixture,
with real ffmpeg and synthetic audio — final.mp4 contract, audio sync, hard cuts around clips,
reuse, and the review-gate dispatch."""
from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

import config
from stages.stage_5 import pipeline as p5
from stages.stage_5 import shots as sh
from stages.stage_5.screen_shots import run_screen_qa_pipeline

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "screen_qa_project"
FPS = 30
AUDIO_SECONDS = 12.5
TIMINGS = [
    {"scene_id": 1, "start": 0.4, "end": 4.0},      # lead silence
    {"scene_id": 2, "start": 4.5, "end": 8.0},      # gaps between scenes
    {"scene_id": 3, "start": 8.2, "end": 12.1},
]


def _ff() -> str:
    return sh._require_ffmpeg()


def _probe(path: Path, entries: str, stream: str = "v:0") -> dict:
    ff = Path(_ff())
    probe = ff.with_name("ffprobe" + ff.suffix)
    res = subprocess.run([str(probe) if probe.is_file() else "ffprobe", "-v", "error",
                          "-select_streams", stream, "-show_entries", entries, "-of", "json",
                          str(path)], capture_output=True, text=True, check=True)
    streams = json.loads(res.stdout).get("streams") or [{}]
    return streams[0]


def _duration(path: Path) -> float:
    ff = Path(_ff())
    probe = ff.with_name("ffprobe" + ff.suffix)
    res = subprocess.run([str(probe) if probe.is_file() else "ffprobe", "-v", "error",
                          "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _make_clip(path: Path, seconds: float = 6.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ff(), "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30", "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path)],
                   check=True, capture_output=True)
    return path


@pytest.fixture(autouse=True)
def _quiet_config(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    monkeypatch.setattr(config, "AUTO_GENERATE_BG_MUSIC", False)     # no LLM / MiniMax in tests
    monkeypatch.setattr(config, "ENABLE_OUTRO_CARD", False)


def _project(tmp_path: Path, *, picks: bool = True) -> tuple[Path, dict]:
    root = tmp_path / "screen_proj"
    (root / "review" / "clips").mkdir(parents=True)
    (root / "review" / "custom").mkdir(parents=True)
    narration = json.loads((FIXTURE_DIR / "narration.json").read_text())
    (root / "narration.json").write_text(json.dumps(narration))
    (root / "screen_context.json").write_text((FIXTURE_DIR / "screen_context.json").read_text())
    subprocess.run([_ff(), "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={AUDIO_SECONDS}",
                    "-ar", "24000", "-ac", "1", str(root / "audio.wav")],
                   check=True, capture_output=True)
    if picks:
        _make_clip(root / "review" / "clips" / "a.mp4")
        _make_clip(root / "review" / "clips" / "b.mp4")
        Path(root / "review" / "clips" / "broken.mp4").write_bytes(b"not a video")
        from PIL import Image
        Image.new("RGB", (1280, 720), (30, 80, 140)).save(root / "review" / "custom" / "still.png")
        (root / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
            {"id": "a", "file": "review/clips/a.mp4", "beat": "1:0", "start": 0.0, "end": 3.0},
            {"id": "b", "file": "review/clips/b.mp4", "beat": "3:1", "start": 0.0, "end": 3.0},
            # beat 2:1: primary is corrupt, the backup is a good clip → level 2
            {"id": "bad", "file": "review/clips/broken.mp4", "beat": "2:1", "start": 0.0, "end": 2.0,
             "backup": {"id": "bad-backup", "file": "review/clips/b.mp4", "start": 1.0, "end": 3.0}},
        ]}))
        (root / "review" / "custom" / "custom_images.json").write_text(json.dumps({"images": [
            {"file": "review/custom/still.png", "beat_key": "2:0", "desc": "", "enrich_status": "pending"}]}))
        (root / "review" / "locks.json").write_text(json.dumps({
            "approved": True, "locks": {"2:0": {"custom_image": "review/custom/still.png",
                                                 "source": "custom"}}}))
    return root, narration


def _run(root, narration, **kw):
    logs: list[str] = []
    res = run_screen_qa_pipeline(
        "screen_proj", root, narration, log=logs.append, audio_path=root / "audio.wav",
        audio_duration=_wav_seconds(root / "audio.wav"), scene_timings=TIMINGS,
        word_timestamps=None, **kw)
    return res, logs


def test_final_mp4_meets_the_contract_and_covers_the_audio(tmp_path):
    root, narration = _project(tmp_path)
    res, logs = _run(root, narration, force=True)
    final = root / "final.mp4"
    assert res.final_path == str(final) and final.is_file()

    v = _probe(final, "stream=codec_name,profile,width,height,pix_fmt,r_frame_rate")
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"], v["r_frame_rate"]) == \
        ("h264", 1080, 1920, "yuv420p", "30/1")
    assert v["profile"] == "High"
    a = _probe(final, "stream=codec_name,sample_rate,channels", stream="a:0")
    assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("aac", "48000", 2)

    # the video covers the audio: -shortest trims the tail, so final == audio length
    assert _duration(final) == pytest.approx(_wav_seconds(root / "audio.wav"), abs=0.2)
    # the silent video is at least as long as the audio (no clipped last word)
    assert _duration(root / "video_silent.mp4") >= _wav_seconds(root / "audio.wav") - 1 / FPS
    # and its length is EXACTLY the sum of the shots' whole frames (xfade padding nets out)
    shots_json = json.loads((root / "shots.json").read_text())
    assert isinstance(shots_json, list) and shots_json
    frames = sum(e["frames"] for e in shots_json)
    assert frames / FPS == pytest.approx(_duration(root / "video_silent.mp4"), abs=2 / FPS)
    # every shot file honours the contract
    from stages.stage_5.clips import verify_shot_contract
    for e in shots_json:
        verify_shot_contract(root / "shots" / f"shot_{e['shot_id']:03d}.mp4", e["frames"])
    assert (root / "title.txt").read_text().strip() == narration["banner_title"]
    assert res.shot_count == len(shots_json) and res.scene_count == 3


def test_every_beat_reports_the_level_it_actually_rendered_at(tmp_path):
    root, narration = _project(tmp_path)
    run_screen_qa_pipeline_logs = _run(root, narration, force=True)[1]
    entries = {k: e for e in json.loads((root / "shots.json").read_text()) for k in e["beats"]}
    assert entries["1:0"]["level_name"] == "clip"
    assert entries["2:0"]["level_name"] == "still" and entries["2:0"]["custom_image"].endswith("still.png")
    assert entries["2:1"]["level_name"] == "backup_clip"
    assert entries["2:1"]["level_notes"][0].startswith("L1:")
    assert entries["3:1"]["level_name"] == "clip"
    assert entries["3:0"]["level_name"] == "card" and entries["3:0"]["level_notes"] is None
    assert any("clip failed" in m for m in run_screen_qa_pipeline_logs)


def test_a_project_with_no_picks_at_all_still_renders_a_complete_video_of_cards(tmp_path):
    root, narration = _project(tmp_path, picks=False)
    res, _ = _run(root, narration, force=True)
    assert Path(res.final_path).is_file()
    levels = {e["level_name"] for e in json.loads((root / "shots.json").read_text())}
    assert levels == {"card"}
    assert _duration(root / "final.mp4") == pytest.approx(AUDIO_SECONDS, abs=0.2)


def test_hard_cuts_around_clip_shots_dissolves_only_between_non_clip_scenes(tmp_path, monkeypatch):
    """Scenes 1-2 are cards (dissolve between them is fine); scene 3 holds the clip. The clip
    scene must never reach the dissolve assembler — it is hard-cut onto the end of the run."""
    root, narration = _project(tmp_path, picks=False)
    _make_clip(root / "review" / "clips" / "a.mp4")
    (root / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "a", "file": "review/clips/a.mp4", "beat": "3:1", "start": 0.0, "end": 3.0}]}))
    seen: list[dict] = []
    real = p5._assemble_groups

    def spy(shots, groups, out_path, **kw):
        seen.append({"scenes": [g[0] for g in groups],
                     "clip_shots": [s.shot_id for s in shots
                                    if s.clip_path and not s.clip_fallback
                                    and any(s.scene_id == g[0] for g in groups)]})
        return real(shots, groups, out_path, **kw)
    monkeypatch.setattr(p5, "_assemble_groups", spy)
    monkeypatch.setattr(config, "XFADE_TRANSITION", "dissolve")
    monkeypatch.setattr(config, "TRANSITION_WHIP_PROB", 0.0)
    res, _ = _run(root, narration, force=True)
    levels = {k: e["level_name"] for e in json.loads((root / "shots.json").read_text())
              for k in e["beats"]}
    assert levels["3:1"] == "clip"
    assert len(seen) == 1 and seen[0]["scenes"] == [1, 2], seen      # clip scene 3 never dissolved
    assert seen[0]["clip_shots"] == []
    # the spliced result is still one clean stream of exactly the planned frames
    frames = sum(e["frames"] for e in json.loads((root / "shots.json").read_text()))
    assert frames / FPS == pytest.approx(_duration(root / "video_silent.mp4"), abs=2 / FPS)


def test_rerun_reuses_unchanged_shots_and_rerenders_only_the_one_that_changed(tmp_path):
    root, narration = _project(tmp_path)
    _run(root, narration, force=True)

    def _drop_outputs():
        for name in ("final.mp4", "video_silent.mp4", "audio_mixed.wav"):
            (root / name).unlink(missing_ok=True)

    n = len(json.loads((root / "shots.json").read_text()))
    _drop_outputs()
    _, logs = _run(root, narration, force=False)
    assert sum("reusing shot_" in m for m in logs) == n

    # Master swaps the still for beat 2:0 → only that shot re-renders
    from PIL import Image
    import os, time
    still = root / "review" / "custom" / "still.png"
    Image.new("RGB", (900, 900), (200, 20, 20)).save(still)
    os.utime(still, (time.time() + 5, time.time() + 5))
    _drop_outputs()
    _, logs = _run(root, narration, force=False)
    assert sum("reusing shot_" in m for m in logs) == n - 1
    assert (root / "final.mp4").is_file()


def test_panels_only_plans_without_rendering(tmp_path):
    root, narration = _project(tmp_path)
    res, _ = _run(root, narration, panels_only=True)
    assert res.final_path == "" and res.shots and (root / "shots.json").is_file()
    assert not (root / "final.mp4").exists() and not list((root / "shots").glob("shot_*.mp4"))


def test_an_existing_final_is_kept_unless_forced(tmp_path):
    root, narration = _project(tmp_path, picks=False)
    _run(root, narration, force=True)
    before = (root / "final.mp4").stat().st_mtime_ns
    res, logs = _run(root, narration, force=False)
    assert (root / "final.mp4").stat().st_mtime_ns == before
    assert any("already exists" in m for m in logs) and res.final_path


def test_long_form_frame_left_by_an_earlier_render_does_not_leak_in(tmp_path):
    root, narration = _project(tmp_path, picks=False)
    sh.set_output_frame("panel_walk")                # a long-form render ran earlier in this process
    try:
        _run(root, narration, force=True)
        assert (sh.OUTPUT_W, sh.OUTPUT_H) == (1080, 1920)
        assert _probe(root / "final.mp4", "stream=width,height")["width"] == 1080
    finally:
        sh.set_output_frame("recap")


# ─── the real entry point: assemble_project → review gate → screen_qa ──────────

def test_assemble_project_dispatches_screen_qa_only_after_the_review_gate(tmp_path, monkeypatch):
    import stages.review_gate as rg
    root, narration = _project(tmp_path, picks=False)
    (root / "word_timestamps.json").write_text("[]")
    (root / "scene_timings.json").write_text(json.dumps(TIMINGS))
    monkeypatch.setattr(p5, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)

    with pytest.raises(SystemExit):                  # not approved → hard gate, like every mode
        p5.assemble_project("screen_proj", force=True)

    locks = {"approved": True, "approved_at": "now", "locks": {},
             "narration_sha1": rg.narration_sha1(root)}
    (root / "review" / "locks.json").write_text(json.dumps(locks))
    res = p5.assemble_project("screen_proj", force=True, progress=lambda m: None)
    assert Path(res.final_path) == root / "final.mp4" and (root / "final.mp4").is_file()
    assert all(e["level_name"] == "card" for e in json.loads((root / "shots.json").read_text()))
