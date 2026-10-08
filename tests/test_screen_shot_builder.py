"""build_shots_for_screen_qa — independent of comic panels, keyed by the review beat scheme, and
fed from the SAME on-disk formats the review UI / P1 /moments_review write (no ffmpeg here)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import config
from stages.stage_5.schema import Shot
from stages.stage_5.screen_beats import AUDIO_TAIL_PAD
from stages.stage_5.screen_shots import ScreenShot, build_shots_for_screen_qa

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "screen_qa_project"
FPS = 30
TIMINGS = [
    {"scene_id": 1, "start": 0.0, "end": 4.2},
    {"scene_id": 2, "start": 4.2, "end": 8.1},
    {"scene_id": 3, "start": 8.1, "end": 12.1},
]


@pytest.fixture()
def narration() -> dict:
    return json.loads((FIXTURE_DIR / "narration.json").read_text())


def _by_key(shots):
    return {k: s for s in shots for k in s.beat_keys}


def test_builds_valid_shots_with_no_comic_pages_at_all(narration):
    shots = build_shots_for_screen_qa(narration, TIMINGS, pages_by_number=None)
    assert shots and all(isinstance(s, Shot) and isinstance(s, ScreenShot) for s in shots)
    for s in shots:
        assert s.source_image == "" and s.panel_bbox == {}      # no comic panel anywhere
        assert s.duration_seconds >= 0.4 and s.caption_text
        assert s.motion in ("zoom_in", "pan_down", "pan_up", "pan_right")   # all really move
    assert [s.shot_id for s in shots] == list(range(len(shots)))


def test_every_motion_is_one_the_zoompan_builder_implements(narration):
    from stages.stage_5 import shots as sh
    shots = build_shots_for_screen_qa(narration, TIMINGS)
    for s in shots:
        expr = sh._zoompan_expr(s.motion, 30)
        assert "z='1.00'" not in expr, f"{s.motion} would render a frozen frame"


def test_durations_cover_the_audio_timeline_exactly(narration):
    for audio in (12.1, 12.6):
        shots = build_shots_for_screen_qa(narration, TIMINGS, audio_duration=audio)
        total = sum(s.duration_seconds for s in shots)
        frames = sum(int(round(s.duration_seconds * FPS)) for s in shots)
        assert frames == round(total * FPS)                          # whole frames only
        assert total >= audio - 1e-9
        # the comic rule: shots that END before the audio get audio + 0.20s of tail, else untouched
        want = 12.1 if audio <= 12.1 else audio + AUDIO_TAIL_PAD
        assert total == pytest.approx(want, abs=1 / FPS)


def test_positional_call_from_p3_core_with_empty_timings_works(narration):
    # stages/screen_pipeline.py calls build_shots_for_screen_qa(nar, {}, {})
    shots = build_shots_for_screen_qa(narration, {}, {})
    assert shots
    assert sum(s.duration_seconds for s in shots) == pytest.approx(
        sum(sc["target_seconds"] for sc in narration["scenes"]), abs=3 / FPS)


def test_intro_flag_marks_only_the_first_shot_of_the_hook(narration):
    shots = build_shots_for_screen_qa(narration, TIMINGS, project=None)
    assert [s.is_intro for s in shots].count(True) == 1 and shots[0].is_intro


def test_empty_narration_builds_nothing():
    assert build_shots_for_screen_qa({"mode": "screen_qa", "scenes": []}) == []


# ─── clips: P1's manifest, review beat keys ───────────────────────────────────

def _manifest(**over):
    return [{"id": "c-first", "file": "/clips/a.mp4", "beat": "1:0", "start": 1.5, "end": 4.0,
             "source_url": "https://youtu.be/AAAAAAAAAAA", **over}]


def test_a_clip_on_the_first_fragment_lands_on_the_first_shot_not_the_second(narration):
    """Keys are 0-based like review_gate: the 1-based 'sid:idx+1' the first draft used would
    have put this clip on fragment 1:1."""
    shots = build_shots_for_screen_qa(narration, TIMINGS, clips_manifest=_manifest())
    by = _by_key(shots)
    assert by["1:0"].clip_path == "/clips/a.mp4" and by["1:0"].clip_id == "c-first"
    assert by["1:0"].clip_in == 1.5 and by["1:0"].clip_out == 4.0
    assert not by["1:1"].clip_path or by["1:1"] is by["1:0"]


def test_a_clip_on_the_second_fragment_is_kept_as_its_own_shot_not_merged_away(narration):
    shots = build_shots_for_screen_qa(
        narration, TIMINGS, clips_manifest=_manifest(beat="1:1", id="c-second"))
    by = _by_key(shots)
    assert by["1:1"].clip_id == "c-second"
    assert by["1:1"] is not by["1:0"]                  # a pick protects its beat from the merge
    assert by["1:1"].duration_seconds < 1.5            # (it IS the short fragment)


def test_a_clip_on_a_single_fragment_bookend_uses_the_legacy_intro_key():
    nar = {"mode": "screen_qa", "scenes": [
        {"scene_id": 1, "text": "Hook line.", "is_intro": True, "target_seconds": 2.0,
         "visual_beats": [{"text": "Hook line.", "query": "q"}]},
        {"scene_id": 2, "text": "Body of the answer here.", "target_seconds": 3.0},
        {"scene_id": 3, "text": "Closing line.", "is_outro": True, "target_seconds": 2.0}]}
    man = [{"id": "ci", "file": "/c/i.mp4", "beat": "intro", "start": 0, "end": 2},
           {"id": "co", "file": "/c/o.mp4", "beat": "outro", "start": 0, "end": 2},
           {"id": "cb", "file": "/c/b.mp4", "beat": "2", "start": 0, "end": 3}]
    shots = build_shots_for_screen_qa(nar, None, clips_manifest=man)
    assert [s.clip_id for s in shots] == ["ci", "cb", "co"]


def test_a_stale_beat_key_is_skipped_not_fatal(narration):
    shots = build_shots_for_screen_qa(
        narration, TIMINGS, clips_manifest=_manifest(beat="9:9", id="ghost"))
    assert shots and not any(s.clip_path for s in shots)


def test_backup_clip_is_stamped_from_the_manifest_entry(narration):
    man = _manifest(source_start=61.0, backup={
        "id": "b1", "file": "/clips/backup.mp4", "start": 0.0, "end": 3.0,
        "source_url": "https://youtu.be/BBBBBBBBBBB", "source_start": 200.0})
    shots = build_shots_for_screen_qa(narration, TIMINGS, clips_manifest=man)
    s = _by_key(shots)["1:0"]
    assert (s.backup_clip_path, s.backup_clip_id) == ("/clips/backup.mp4", "b1")
    assert s.backup_clip_out == 3.0 and s.backup_source_start == 200.0
    assert s.clip_source_start == 61.0


# ─── disk: the formats the UI really writes ───────────────────────────────────

def _project(tmp_path: Path, narration: dict) -> Path:
    root = tmp_path / "proj"
    (root / "review" / "clips").mkdir(parents=True)
    (root / "narration.json").write_text(json.dumps(narration))
    return root


def test_reads_clips_json_from_the_project_folder(tmp_path, narration):
    root = _project(tmp_path, narration)
    (root / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "x", "file": "review/clips/x.mp4", "beat": "2:0", "start": 0, "end": 2,
         "source_url": "https://youtu.be/CCCCCCCCCCC"}]}))
    shots = build_shots_for_screen_qa(narration, TIMINGS, project_root=root)
    s = _by_key(shots)["2:0"]
    assert s.clip_id == "x" and Path(s.clip_path) == root / "review" / "clips" / "x.mp4"


def test_a_still_locked_through_the_review_sidecar_lands_on_its_beat(tmp_path, narration):
    """review/custom/custom_images.json is {"images": [...]} and the lock lives in locks.json —
    the first draft read it as {beat: path} and never found a single image."""
    root = _project(tmp_path, narration)
    custom = root / "review" / "custom"
    custom.mkdir()
    (custom / "shot.png").write_bytes(b"x")
    (custom / "custom_images.json").write_text(json.dumps({"images": [
        {"file": "review/custom/shot.png", "beat_key": "2:1", "desc": "", "enrich_status": "pending"}]}))
    (root / "review" / "locks.json").write_text(json.dumps({
        "approved": False, "locks": {"2:1": {"custom_image": "review/custom/shot.png",
                                              "source": "custom"}}}))
    shots = build_shots_for_screen_qa(narration, TIMINGS, project_root=root)
    s = _by_key(shots)["2:1"]
    assert Path(s.custom_image) == root / "review" / "custom" / "shot.png"
    assert not _by_key(shots)["2:0"].custom_image


def test_a_clip_outranks_a_still_on_the_same_beat_and_keeps_it_as_the_fallback(tmp_path, narration):
    root = _project(tmp_path, narration)
    (root / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "x", "file": "review/clips/x.mp4", "beat": "2:0", "start": 0, "end": 2}]}))
    shots = build_shots_for_screen_qa(narration, TIMINGS, project_root=root,
                                      custom_images={"2:0": "/abs/still.png"})
    s = _by_key(shots)["2:0"]
    assert s.clip_id == "x" and s.custom_image == "/abs/still.png"


def test_screen_context_is_loaded_from_the_project_folder(tmp_path, narration):
    root = _project(tmp_path, narration)
    ctx = json.loads((FIXTURE_DIR / "screen_context.json").read_text())
    (root / "screen_context.json").write_text(json.dumps(ctx))
    nar = json.loads(json.dumps(narration))
    for sc in nar["scenes"]:
        for b in sc["visual_beats"]:
            b["query"] = ""
    shots = build_shots_for_screen_qa(nar, TIMINGS, project_root=root)
    by = _by_key(shots)
    # "Tony Stark" is named in scene 2's words -> that research item's visual_query
    assert by["2:0"].query == "Tony Stark holographic Mobius strip simulation"
