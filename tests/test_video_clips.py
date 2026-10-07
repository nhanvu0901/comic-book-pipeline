"""Opt-in VIDEO-CLIP shots (stages/stage_5/clips.py) and the clip helper CLI (stages/clip_fetch.py):
  • manifest parsing (review/clips/clips.json) and crop hints
  • precedence: explicit clip > custom image > panel lock > matcher; a desc-placed clip only
    takes a free beat
  • applying clips to shots (continuous playback, several clips per beat, stale keys)
  • time-split / loop-close keep the clip consistent
  • a project WITHOUT a manifest is untouched (same list object, empty clip fields, same shots.json)
  • the render contract (h264 yuv420p 1080x1920 30fps, exact frames, no audio) and `-c copy` concat
  • fallback to the panel on any clip failure, with the reason in shots.json
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from stages.stage_5 import clips, shots
from stages.stage_5.schema import Shot

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")


def _shot(shot_id, scene_id, dur=1.0, *, is_intro=False, beat_id=None, caption="", src="p.png"):
    return Shot(shot_id=shot_id, scene_id=scene_id, duration_seconds=dur,
                panel_bbox={"x": 0, "y": 0, "w": 10, "h": 10}, source_image=src,
                motion="zoom_in", is_intro=is_intro, beat_id=beat_id, caption_text=caption)


def _entry(cid="c", beat="2", start=0.0, end=0.0, file="/abs/c.mp4", **kw):
    return clips.ClipEntry(id=cid, file=file, start=start, end=end, beat=beat, **kw)


def _project(tmp_path, monkeypatch, name="proj"):
    import config
    import stages.review_gate as rg
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / name
    root.mkdir()
    (root / "comic_context.json").write_text(json.dumps({"plot_source": "batcave"}))
    return root


def _write_manifest(root: Path, items: list[dict]) -> Path:
    p = root / "review" / "clips" / "clips.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"clips": items}))
    return p


# ─── manifest parsing ────────────────────────────────────────────────────────────

def test_parse_manifest_resolves_paths_ids_and_defaults(tmp_path):
    entries, problems = clips.parse_manifest({"clips": [
        {"file": "review/clips/abc.mp4", "start": 1.5, "end": 3, "beat": "2",
         "source_url": "https://youtu.be/abc"},
        {"file": "/elsewhere/x.mp4", "beat": "intro"},                 # no start/end
        {"file": "review/clips/abc.mp4", "start": 4, "beat": "3"},     # same stem → unique id
        {"file": "off.mp4", "beat": "4", "enabled": False},            # disabled → ignored
        {"source_url": "https://youtu.be/zzz", "beat": "5", "id": "later"},
    ]}, tmp_path)
    assert problems == []
    assert [e.id for e in entries] == ["abc", "x", "abc-3", "later"]
    assert entries[0].file == str(tmp_path / "review/clips/abc.mp4")
    assert (entries[0].start, entries[0].end) == (1.5, 3.0)
    assert entries[0].source_url == "https://youtu.be/abc"
    assert entries[1].file == "/elsewhere/x.mp4"
    assert (entries[1].start, entries[1].end) == (0.0, 0.0)          # open-ended
    assert entries[3].file == ""                                      # not fetched yet


def test_parse_manifest_skips_and_reports_bad_entries(tmp_path):
    entries, problems = clips.parse_manifest({"clips": [
        "not-an-object",
        {"beat": "1"},                                                # no file / url
        {"file": "a.mp4", "beat": "1", "start": 5, "end": 4},         # end before start
        {"file": "a.mp4", "beat": "1", "start": -1},                  # negative
        {"file": "a.mp4", "start": 1},                                # no beat, no desc
        {"file": "b.mp4", "beat": "1", "crop": {"cx": 7}},            # bad crop → kept, no crop
    ]}, tmp_path)
    assert [e.id for e in entries] == ["b"]
    assert entries[0].crop == {}
    assert len(problems) == 6
    assert any("crop ignored" in p for p in problems)


def test_parse_manifest_without_a_clips_list():
    assert clips.parse_manifest({}, Path(".")) == ([], [])
    entries, problems = clips.parse_manifest({"clips": "nope"}, Path("."))
    assert entries == [] and problems


def test_parse_crop_shapes():
    assert clips.parse_crop(None) == ({}, "")
    assert clips.parse_crop({"cx": 0.3}) == ({"cx": 0.3, "cy": 0.5}, "")
    assert clips.parse_crop({"x": 0.1, "y": 0, "w": 0.5, "h": 1}) == (
        {"x": 0.1, "y": 0.0, "w": 0.5, "h": 1.0}, "")
    for bad in ({"cx": -0.1}, {"x": 0.8, "y": 0, "w": 0.5, "h": 1}, {"x": 0.1}, "left"):
        crop, why = clips.parse_crop(bad)
        assert crop == {} and why


def test_crop_region_cx_window_and_clamping():
    # landscape 1920x1080 → the largest 9:16 window is 608x1080, centred on cx, clamped inside
    assert clips.crop_region(1920, 1080, {"cx": 0.5, "cy": 0.5}) == (656, 0, 608, 1080)
    assert clips.crop_region(1920, 1080, {"cx": 0.0, "cy": 0.5}) == (0, 0, 608, 1080)
    assert clips.crop_region(1920, 1080, {"cx": 1.0, "cy": 0.5}) == (1312, 0, 608, 1080)
    assert clips.crop_region(1920, 1080, {"x": 0.25, "y": 0, "w": 0.5, "h": 1}) == (480, 0, 960, 1080)
    assert clips.crop_region(640, 360, {}) == (0, 0, 640, 360)


def test_load_manifest_missing_or_malformed_is_empty(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    assert clips.load_manifest("proj") == []
    p = root / "review" / "clips" / "clips.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    logs = []
    assert clips.load_manifest("proj", log=logs.append) == []
    assert any("unreadable" in m for m in logs)


# ─── precedence ──────────────────────────────────────────────────────────────────

_NARR = {"scenes": [
    {"scene_id": 1, "text": "The hook line", "is_intro": True},
    {"scene_id": 2, "text": "The ring is buried in the planet core"},
    {"scene_id": 3, "text": "Parallax attacks the battery"},
    {"scene_id": 4, "text": "The planet goes dark and sleeps"},
    {"scene_id": 5, "text": "The closing line", "is_outro": True},
]}


def test_desc_only_clip_lands_on_its_best_free_beat():
    e = _entry("p", beat="", desc="Parallax attacks the central battery")
    assert clips.resolve_clip_assignments([e], _NARR) == [("3", e)]


def test_desc_only_clip_never_takes_a_claimed_or_explicitly_clipped_beat():
    """Beat 3 is the best word match, but it is panel-locked/custom-imaged (claimed) → the guess
    goes elsewhere; an explicit clip on beat 4 is also off-limits to it."""
    guess = _entry("g", beat="", desc="Parallax attacks the battery")
    pick = _entry("x", beat="4")
    out = clips.resolve_clip_assignments([guess, pick], _NARR, claimed_beats={"3"})
    beats = {e.id: bk for bk, e in out}
    assert beats["x"] == "4"
    assert beats["g"] not in ("3", "4")


def test_explicit_clip_outranks_panel_lock_and_custom_image_and_keeps_them_as_fallback(
        tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    (root / "review").mkdir()
    (root / "review" / "locks.json").write_text(json.dumps(
        {"approved": True, "locks": {"2": {"panels": [{"page": 1, "panel": 0}]}}}))
    _write_manifest(root, [
        {"id": "a", "file": "review/clips/a.mp4", "start": 2, "end": 5, "beat": "2"},
        {"id": "b", "file": "review/clips/b.mp4", "start": 0, "beat": "3"},
        {"id": "g", "file": "review/clips/g.mp4", "desc": "Parallax attacks the battery"},
    ])
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    monkeypatch.setattr(shots, "_apply_review_locks", lambda *_a, **_k: None)
    monkeypatch.setattr(shots, "_resolve_custom_images",
                        lambda *_a, **_k: {"3": "/abs/custom.jpg"})
    fake = [_shot(0, 1, is_intro=True), _shot(1, 2), _shot(2, 3), _shot(3, 4), _shot(4, 5)]
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: fake)
    monkeypatch.setattr(shots, "_build_shots_per_chunk_locked", lambda *a, **k: fake)
    out = shots.build_shots(
        json.loads(json.dumps(_NARR)), caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
        pages_by_number={1: {"source_image": "p.png", "panels": [],
                             "image_dimensions": {"width": 1, "height": 1},
                             "page_type": "story"}},
        project="proj")
    by_scene = {s.scene_id: s for s in out}
    assert by_scene[2].clip_id == "a" and by_scene[2].clip_path.endswith("a.mp4")
    assert by_scene[2].clip_in == 2.0 and by_scene[2].clip_out == 5.0
    assert by_scene[2].source_image == "p.png"                 # panel kept as the fallback
    assert by_scene[3].clip_id == "b"
    assert by_scene[3].custom_image == "/abs/custom.jpg"       # custom image kept as the fallback
    # the desc-placed clip matches beat 3 best, but 2 (locked) and 3 (custom + clipped) are taken
    assert by_scene[4].clip_id == "g"


def test_build_shots_without_a_manifest_is_untouched(tmp_path, monkeypatch):
    """The byte-identical guarantee: no review/clips/clips.json → the very same list object,
    every clip field at its default."""
    _project(tmp_path, monkeypatch)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    fake = [_shot(0, 1), _shot(1, 2)]
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: fake)
    out = shots.build_shots(
        {"scenes": [{"scene_id": 1, "text": "a"}, {"scene_id": 2, "text": "b"}]},
        caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
        pages_by_number={1: {"source_image": "p.png", "panels": [],
                             "image_dimensions": {"width": 1, "height": 1},
                             "page_type": "story"}},
        project="proj")
    assert out is fake
    default = Shot(shot_id=0, scene_id=0, duration_seconds=1, panel_bbox={}, source_image="",
                   motion="zoom_in")
    for s in out:
        for k in ("clip_path", "clip_in", "clip_out", "clip_crop", "clip_id",
                  "clip_source_url", "clip_fallback"):
            assert getattr(s, k) == getattr(default, k)


# ─── applying clips onto shots ───────────────────────────────────────────────────

def test_one_clip_plays_continuously_across_a_beats_shots():
    sl = [_shot(0, 1), _shot(1, 2, 1.5), _shot(2, 2, 2.0), _shot(3, 3)]
    clips.apply_clips_to_shots(sl, [("2", _entry(start=10.0, end=20.0))], log=lambda m: None)
    assert [s.clip_in for s in sl[1:3]] == [10.0, 11.5]
    assert sl[0].clip_path == "" and sl[3].clip_path == ""
    assert all(s.clip_out == 20.0 for s in sl[1:3])


def test_fragment_intro_and_outro_keys_target_single_shots():
    sl = [_shot(0, 1, is_intro=True), _shot(1, 2), _shot(2, 2), _shot(3, 2), _shot(4, 3)]
    clips.apply_clips_to_shots(sl, [("intro", _entry("i")), ("2:1", _entry("f")),
                                    ("outro", _entry("o"))], log=lambda m: None)
    assert [s.clip_id for s in sl] == ["i", "", "f", "", "o"]


def test_fragment_key_finds_the_shot_that_speaks_it():
    narration = {"scenes": [{"scene_id": 2, "visual_beats": [
        {"text": "first words here"}, {"text": "second clause about the ring"}]}]}
    sl = [_shot(0, 2, caption="first words here second clause about the ring"), _shot(1, 2,
                                                                                     caption="unrelated")]
    clips.apply_clips_to_shots(sl, [("2:1", _entry("f"))], narration, log=lambda m: None)
    assert sl[0].clip_id == "f" and sl[1].clip_id == ""


def test_fragment_key_overrides_a_scene_key_on_the_same_scene():
    sl = [_shot(0, 2), _shot(1, 2), _shot(2, 2)]
    clips.apply_clips_to_shots(sl, [("2:1", _entry("frag")), ("2", _entry("scene"))],
                               log=lambda m: None)
    assert [s.clip_id for s in sl] == ["scene", "frag", "scene"]


def test_several_clips_on_a_one_shot_beat_split_it_and_keep_the_timeline():
    sl = [_shot(0, 1), _shot(1, 2, 3.0), _shot(2, 3)]
    clips.apply_clips_to_shots(sl, [("2", _entry("a", start=5)), ("2", _entry("b", start=9)),
                                    ("2", _entry("c", start=1))], log=lambda m: None)
    assert [s.clip_id for s in sl] == ["", "a", "b", "c", ""]
    assert sum(s.duration_seconds for s in sl) == pytest.approx(5.0)
    assert [s.shot_id for s in sl] == [0, 1, 2, 3, 4]
    assert [s.clip_in for s in sl[1:4]] == [5.0, 9.0, 1.0]
    assert all(s.scene_id == 2 for s in sl[1:4])


def test_fewer_clips_than_shots_share_the_beat_by_duration():
    sl = [_shot(0, 2, 1.0), _shot(1, 2, 1.0), _shot(2, 2, 2.0)]
    clips.apply_clips_to_shots(sl, [("2", _entry("a", start=0)), ("2", _entry("b", start=30))],
                               log=lambda m: None)
    assert [s.clip_id for s in sl] == ["a", "a", "b"]
    assert [s.clip_in for s in sl] == [0.0, 1.0, 30.0]


def test_a_beat_too_short_for_its_clips_drops_the_extras():
    logs = []
    sl = [_shot(0, 2, 0.7)]
    clips.apply_clips_to_shots(sl, [("2", _entry("a")), ("2", _entry("b"))], log=logs.append)
    assert [s.clip_id for s in sl] == ["a"]
    assert any("too short" in m and "b" in m for m in logs)


def test_stale_beat_key_is_reported_not_raised():
    logs = []
    sl = [_shot(0, 1)]
    missed = clips.apply_clips_to_shots(sl, [("9", _entry("x")), ("7:2", _entry("y"))],
                                        log=logs.append)
    assert missed == ["9", "7:2"]
    assert sl[0].clip_id == ""


def test_an_entry_without_a_local_file_records_why():
    sl = [_shot(0, 2)]
    clips.apply_clips_to_shots(sl, [("2", _entry("u", file="", source_url="https://y/u"))],
                               log=lambda m: None)
    assert sl[0].clip_path == ""
    assert "no local file" in sl[0].clip_fallback
    assert clips.shot_log_entry(sl[0])["rendered"] is False


def test_time_split_advances_the_clip_in_point():
    a = _shot(0, 2, 5.0)
    clips._stamp(a, _entry(start=10.0), 10.0)
    out = shots._time_split_shots([a, _shot(1, 3)], 2.0)
    frags = [s for s in out if s.scene_id == 2]
    assert len(frags) == 3
    assert [s.clip_in for s in frags] == [10.0, 11.667, 13.334]


def test_loop_tail_carve_advances_the_clip_and_panels_are_unchanged():
    a = _shot(0, 2, 4.0)
    clips._stamp(a, _entry(start=3.0), 3.0)
    head, tail = shots._time_split_shots([a], 0.0, loop_tail=1.0)
    assert (head.clip_in, tail.clip_in) == (3.0, 6.0)
    p_head, p_tail = shots._time_split_shots([_shot(0, 2, 4.0)], 0.0, loop_tail=1.0)
    assert p_head.clip_in == p_tail.clip_in == 0.0 and p_tail.clip_id == ""


def test_close_loop_echoes_an_opening_clip_and_clears_an_outro_clip():
    first, last = _shot(0, 1, is_intro=True), _shot(1, 2)
    clips._stamp(first, _entry("open", start=4.0), 4.0)
    clips._stamp(last, _entry("end", start=9.0), 9.0)
    shots._close_loop([first, last])
    assert (last.clip_id, last.clip_in) == ("open", 4.0)
    first2, last2 = _shot(0, 1, is_intro=True), _shot(1, 2)
    clips._stamp(last2, _entry("end", start=9.0), 9.0)
    shots._close_loop([first2, last2])
    assert last2.clip_id == "" and last2.clip_path == ""


# ─── shots.json record ───────────────────────────────────────────────────────────

def test_shot_log_entry_only_for_clip_shots():
    plain = _shot(0, 1)
    assert clips.shot_log_entry(plain) is None
    s = _shot(1, 2, 3.0)
    clips._stamp(s, _entry("k", start=1.0, end=2.5, source_url="https://y/k"), 1.0)
    rec = clips.shot_log_entry(s)
    assert rec["id"] == "k" and rec["rendered"] is True and rec["fallback_reason"] is None
    assert rec["frozen_tail_seconds"] == 1.5
    assert rec["source_url"] == "https://y/k"


def test_write_shots_log_adds_clip_block_only_where_wanted(tmp_path):
    from stages.stage_5.pipeline import _write_shots_log
    plain, clipped = _shot(0, 1), _shot(1, 2)
    clips._stamp(clipped, _entry("k", start=1.0), 1.0)
    clipped.clip_fallback = "FileNotFoundError: clip file missing"
    out = tmp_path / "shots.json"
    _write_shots_log([plain, clipped], [], tmp_path, out, lambda m: None)
    data = json.loads(out.read_text())
    assert "clip" not in data[0]
    assert data[1]["clip"]["fallback_reason"] == "FileNotFoundError: clip file missing"
    assert data[1]["clip"]["rendered"] is False


# ─── render contract (real ffmpeg) ───────────────────────────────────────────────

def _make_source(path: Path, *, size="640x360", rate=25, dur=4.0, codec=("libx264",),
                 audio=True) -> Path:
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=d={dur}", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", *codec, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _probe(path: Path) -> list[dict]:
    res = subprocess.run([FFPROBE, "-v", "error", "-count_packets", "-show_entries",
                          "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,"
                          "nb_read_packets", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)["streams"]


def _clip_shot(src: Path, dur: float, *, clip_in=0.0, clip_out=0.0, crop=None, shot_id=0):
    s = _shot(shot_id, 2, dur)
    s.clip_path, s.clip_in, s.clip_out, s.clip_id = str(src), clip_in, clip_out, "t"
    s.clip_crop = crop or {}
    return s


@needs_ffmpeg
def test_clip_shot_meets_the_shot_contract(tmp_path):
    src = _make_source(tmp_path / "src.mp4", rate=25)
    out = clips.render_clip_shot(_clip_shot(src, 2.0, clip_in=1.0), tmp_path / "shot.mp4")
    (st,) = _probe(out)                                 # the source's audio is gone
    assert st["codec_type"] == "video" and st["codec_name"] == "h264"
    assert (st["width"], st["height"]) == (1080, 1920)
    assert st["pix_fmt"] == "yuv420p" and st["r_frame_rate"] == "30/1"
    assert int(st["nb_read_packets"]) == 60


@needs_ffmpeg
def test_a_clip_shorter_than_its_shot_holds_its_last_frame(tmp_path):
    src = _make_source(tmp_path / "src.mp4", audio=False)
    shot = _clip_shot(src, 2.0, clip_in=1.0, clip_out=1.5)       # 0.5s of clip for a 2s shot
    out = clips.render_clip_shot(shot, tmp_path / "shot.mp4")
    (st,) = _probe(out)
    assert int(st["nb_read_packets"]) == 60
    # (frame hashes can't show the hold: x264 keeps refining a static frame across P-frames)
    res = subprocess.run([FFMPEG, "-hide_banner", "-i", str(out), "-vf",
                          "freezedetect=n=-50dB:d=0.3", "-f", "null", "-"],
                         capture_output=True, text=True)
    starts = [float(ln.rsplit(":", 1)[1]) for ln in res.stderr.splitlines() if "freeze_start" in ln]
    assert len(starts) == 1 and 0.4 <= starts[0] <= 0.55     # moves for 0.5s, then holds
    assert "freeze_end" not in res.stderr                    # ... to the very end
    assert clips.frozen_tail_seconds(shot) == 1.5


@needs_ffmpeg
def test_a_shot_after_the_clip_ran_out_holds_the_out_point(tmp_path):
    """One 1s clip over two 1s shots: the 2nd shot's in-point (start+1.0) is AT the out-point —
    it must hold the out frame, not play on past "end"."""
    src = _make_source(tmp_path / "src.mp4", audio=False)
    sl = [_shot(0, 2, 1.0), _shot(1, 2, 1.0)]
    clips.apply_clips_to_shots(sl, [("2", _entry(start=1.0, end=2.0, file=str(src)))],
                               log=lambda m: None)
    assert [s.clip_in for s in sl] == [1.0, 2.0]
    assert clips.frozen_tail_seconds(sl[1]) == 1.0
    out = clips.render_clip_shot(sl[1], tmp_path / "shot.mp4")
    (st,) = _probe(out)
    assert int(st["nb_read_packets"]) == 30
    res = subprocess.run([FFMPEG, "-hide_banner", "-i", str(out), "-vf",
                          "freezedetect=n=-50dB:d=0.3", "-f", "null", "-"],
                         capture_output=True, text=True)
    starts = [float(ln.rsplit(":", 1)[1]) for ln in res.stderr.splitlines() if "freeze_start" in ln]
    assert starts and starts[0] < 0.1 and "freeze_end" not in res.stderr


@needs_ffmpeg
def test_subject_crop_fill_and_corner_logo_keep_the_contract(tmp_path):
    from PIL import Image
    src = _make_source(tmp_path / "wide.mp4", size="1920x1080", rate=24, dur=2.0, audio=False)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 128)).save(logo)
    out = clips.render_clip_shot(_clip_shot(src, 1.0, crop={"cx": 0.7, "cy": 0.5}),
                                 tmp_path / "shot.mp4", corner_logo=logo)
    (st,) = _probe(out)
    assert (st["width"], st["height"], int(st["nb_read_packets"])) == (1080, 1920, 30)


@needs_ffmpeg
def test_clip_and_panel_shots_concat_by_stream_copy(tmp_path, monkeypatch):
    from PIL import Image
    from stages.stage_5.pipeline import _concat
    monkeypatch.setattr(shots, "PANEL_UPSCALE", False)
    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1200), (90, 30, 140)).save(page)
    panel = _shot(0, 1, 1.0, src=str(page))
    panel.panel_bbox = {"x": 0, "y": 0, "w": 800, "h": 1200}
    a = shots.render_shot(panel, tmp_path / "shot_000.mp4")
    b = clips.render_clip_shot(_clip_shot(_make_source(tmp_path / "s.mp4"), 1.5, shot_id=1),
                               tmp_path / "shot_001.mp4")
    cat = _concat([a, b, a], tmp_path / "cat.mp4")
    (st,) = _probe(cat)
    assert int(st["nb_read_packets"]) == 30 + 45 + 30


# ─── fallback ────────────────────────────────────────────────────────────────────

@needs_ffmpeg
@pytest.mark.parametrize("problem", ["missing", "past_end"])
def test_a_failing_clip_falls_back_to_the_panel(tmp_path, monkeypatch, problem):
    from PIL import Image
    monkeypatch.setattr(shots, "PANEL_UPSCALE", False)
    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1200), (20, 120, 60)).save(page)
    if problem == "missing":
        shot = _clip_shot(tmp_path / "gone.mp4", 0.5)
    else:
        shot = _clip_shot(_make_source(tmp_path / "s.mp4", dur=2.0), 0.5, clip_in=30.0)
    shot.source_image = str(page)
    shot.panel_bbox = {"x": 0, "y": 0, "w": 800, "h": 1200}
    out = shots.render_shot(shot, tmp_path / "shot.mp4", progress=lambda m: None)
    (st,) = _probe(out)
    assert int(st["nb_read_packets"]) == 15
    assert shot.clip_fallback
    assert ("missing" in shot.clip_fallback) if problem == "missing" else ("past the end" in shot.clip_fallback)
    assert (tmp_path / "_panels" / "panel_000.png").exists()      # it really rendered the panel


@needs_ffmpeg
def test_failing_clip_with_no_panel_either_raises_clearly(tmp_path):
    shot = _clip_shot(tmp_path / "gone.mp4", 0.5)
    shot.source_image = str(tmp_path / "no-page.png")
    with pytest.raises(RuntimeError, match="fallback panel"):
        shots.render_shot(shot, tmp_path / "shot.mp4", progress=lambda m: None)


# ─── panel sheet ─────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_panel_sheet_shows_a_clip_frame_and_survives_a_missing_clip(tmp_path):
    from PIL import Image
    from stages.stage_5.panel_sheet import _clip_thumb, build_panel_sheet
    src = _make_source(tmp_path / "s.mp4", audio=False)
    thumb = _clip_thumb(str(src), 1.0, 100)
    assert thumb is not None and thumb.height == 100
    assert _clip_thumb(str(tmp_path / "gone.mp4"), 0.0, 100) is None
    page = tmp_path / "page.png"
    Image.new("RGB", (80, 120), (1, 2, 3)).save(page)
    ok, gone = _clip_shot(src, 1.0), _clip_shot(tmp_path / "gone.mp4", 1.0)
    for s in (ok, gone):
        s.source_image, s.panel_bbox = str(page), {"x": 0, "y": 0, "w": 80, "h": 120}
    assert build_panel_sheet([ok, gone], tmp_path / "sheet.jpg").exists()


# ─── clip_fetch CLI helpers ─────────────────────────────────────────────────────

from stages import clip_fetch  # noqa: E402


def test_parse_search_results_flags_video_pages_and_dedupes():
    payload = {"results": {
        "web": [{"url": "https://www.youtube.com/watch?v=gKiT1ekWIAA", "title": "Clip",
                 "description": "Green Lantern"},
                {"url": "https://www.youtube.com/@dckids", "title": "Channel", "snippets": ["s"]},
                {"url": "https://www.youtube.com/watch?v=gKiT1ekWIAA", "title": "dup"}],
        "news": [{"url": "https://youtu.be/abcdefghijk", "title": "Short"}]}}
    rows = clip_fetch.parse_search_results(payload)
    assert [r["title"] for r in rows] == ["Clip", "Channel", "Short"]
    assert [r["is_video"] for r in rows] == [True, False, True]


def test_youtube_search_goes_through_the_youcom_client_on_youtube_only():
    from stages.research_scout.youcom import RawCall

    class Stub:
        def __init__(self, call):
            self.call, self.seen = call, None

        def search(self, query, profile):
            self.seen = (query, profile)
            return self.call

    ok = Stub(RawCall(api="search", payload={"results": {"web": [
        {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "t"}]}}))
    assert clip_fetch.youtube_search("mogo", client=ok)[0]["is_video"]
    assert ok.seen == ("mogo", {"include_domains": ["youtube.com"]})
    with pytest.raises(RuntimeError, match="HTTP 401"):
        clip_fetch.youtube_search("x", client=Stub(RawCall(api="search", error="HTTP 401")))


def test_ytdlp_args_use_node_and_merge_to_mp4(tmp_path):
    args = clip_fetch.ytdlp_args("https://youtu.be/x", tmp_path, max_height=720)
    joined = " ".join(args)
    assert "--js-runtimes node" in joined
    assert "--merge-output-format mp4" in joined
    assert "height<=720" in joined
    assert args[args.index("-o") + 1] == str(tmp_path / "%(id)s.%(ext)s")
    assert args[-1] == "https://youtu.be/x"


def test_sheet_times_widen_the_step_past_max_frames():
    times, step = clip_fetch.sheet_times(10.0, every=2.0)
    assert times == [0.0, 2.0, 4.0, 6.0, 8.0] and step == 2.0
    times, step = clip_fetch.sheet_times(600.0, every=1.0, max_frames=10)
    assert len(times) == 10 and step == pytest.approx(60.0)
    times, _ = clip_fetch.sheet_times(10.0, every=0.5, start=4.0, end=5.0)
    assert times == [4.0, 4.5, 5.0]


def test_add_entry_writes_a_project_relative_validated_entry(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    cdir = root / "review" / "clips"
    cdir.mkdir(parents=True)
    (cdir / "abc.mp4").write_bytes(b"")
    (cdir / "abc.json").write_text(json.dumps({"source_url": "https://youtu.be/abc"}))
    e = clip_fetch.add_entry(root, {"file": str(cdir / "abc.mp4"), "start": 1.0, "end": 2.0,
                                    "beat": "3:1", "crop": {"cx": 0.4, "cy": 0.5}})
    assert e["file"] == "review/clips/abc.mp4"
    assert e["source_url"] == "https://youtu.be/abc"          # credit filled from the fetch meta
    doc = json.loads((cdir / "clips.json").read_text())
    assert doc["clips"] == [e]
    parsed, problems = clips.parse_manifest(doc, root)
    assert problems == [] and parsed[0].crop == {"cx": 0.4, "cy": 0.5}
    with pytest.raises(ValueError):
        clip_fetch.add_entry(root, {"file": "x.mp4", "start": 5.0, "end": 1.0, "beat": "1"})


@needs_ffmpeg
def test_fetch_normalizes_any_codec_and_frame_rate(tmp_path, monkeypatch):
    """Sources arrive as VP9/AV1 at 24/25/60 fps: fetch always transcodes to h264 30fps and
    builds a contact sheet. yt-dlp is stubbed (no network)."""
    enc = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    codec = ("libvpx-vp9", "-b:v", "200k") if "libvpx-vp9" in enc else ("mpeg4",)
    raw = _make_source(tmp_path / "raw.webm" if codec[0] == "libvpx-vp9" else tmp_path / "raw.mkv",
                       rate=24, dur=3.0, codec=codec, audio=False)

    def fake_download(url, out_dir, **_k):
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"zzzzzzzzzzz{raw.suffix}"
        shutil.copy(raw, dst)
        return dst

    monkeypatch.setattr(clip_fetch, "download", fake_download)
    res = clip_fetch.fetch_clip("https://www.youtube.com/watch?v=zzzzzzzzzzz", tmp_path / "clips",
                                every=1.0, log=lambda m: None)
    st = [s for s in _probe(Path(res["file"])) if s["codec_type"] == "video"][0]
    assert st["codec_name"] == "h264" and st["r_frame_rate"] == "30/1"
    assert st["pix_fmt"] == "yuv420p" and (st["width"], st["height"]) == (640, 360)
    assert Path(res["sheet"]).exists()
    assert res["meta"]["source_url"] == "https://www.youtube.com/watch?v=zzzzzzzzzzz"
    again = clip_fetch.fetch_clip("https://www.youtube.com/watch?v=zzzzzzzzzzz",
                                  tmp_path / "clips", sheet=False, log=lambda m: None)
    assert again["raw"] == ""                                 # cached: no second download
