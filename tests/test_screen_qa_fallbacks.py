"""The screen_qa never-crash chain, every level FORCED:

  L1 clip → L2 backup clip → L3 still (Ken Burns) → L4 text card → plain frame

Each test breaks everything above the level it wants and checks (a) which level rendered,
(b) the shot honours the shot contract, and (c) the four kinds of shot can be spliced by
pipeline._concat's `-c copy` (same codec parameters), which is what makes hard cuts around clips
possible.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

import config
from stages.stage_5 import shots as sh
from stages.stage_5 import screen_shots
from stages.stage_5.clips import verify_shot_contract
from stages.stage_5.screen_shots import ScreenShot, render_screen_shot

FPS = 30


def _ff() -> str:
    return sh._require_ffmpeg()


def _make_clip(path: Path, seconds: float = 4.0, size: str = "640x360", color: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    src = f"color=c={color}:s={size}:r=30" if color else f"testsrc2=s={size}:r=30"
    subprocess.run([_ff(), "-y", "-f", "lavfi", "-i", src, "-t", f"{seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path)],
                   check=True, capture_output=True)
    return path


def _make_still(path: Path, size=(1280, 720), color=(40, 90, 160)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _shot(seconds: float = 1.5, **kw) -> ScreenShot:
    base = dict(shot_id=3, scene_id=1, duration_seconds=seconds, panel_bbox={}, source_image="",
                motion="zoom_in", caption_text="Tony Stark tests an inverted Mobius strip.",
                beat_keys=["1:0"])
    base.update(kw)
    return ScreenShot(**base)


def _sig(path: Path) -> dict:
    res = subprocess.run(
        [str(Path(_ff()).with_name("ffprobe" + Path(_ff()).suffix)) if Path(_ff()).with_name(
            "ffprobe" + Path(_ff()).suffix).is_file() else "ffprobe", "-v", "error",
         "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,profile,level,width,height,pix_fmt,r_frame_rate,time_base,"
         "sample_aspect_ratio,nb_read_frames", "-count_frames", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return json.loads(res.stdout)["streams"][0]


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)


@pytest.fixture()
def ctx():
    return {"question": "How did the Avengers travel back in time?",
            "items": [{"entity": "Tony Stark", "adaptation_title": "Avengers: Endgame", "year": 2019}]}


def test_level_1_primary_clip(tmp_path):
    clip = _make_clip(tmp_path / "primary.mp4")
    s = _shot(clip_path=str(clip), clip_in=0.5, clip_out=3.5, clip_id="c1")
    out = render_screen_shot(s, tmp_path / "shot_003.mp4", work_dir=tmp_path / "w")
    assert out.is_file() and s.render_level == 1
    assert s.clip_fallback == "" and s.level_notes == []
    verify_shot_contract(out, round(1.5 * FPS))


def test_level_1_works_with_the_clip_flag_off_too(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    clip = _make_clip(tmp_path / "primary.mp4")
    s = _shot(clip_path=str(clip), clip_in=0.0, clip_out=3.0, clip_id="c1")
    render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w")
    assert s.render_level == 1


def test_level_2_backup_clip_when_the_primary_is_corrupt(tmp_path):
    bad = tmp_path / "primary.mp4"
    bad.write_bytes(b"this is not an mp4")                       # exists, but cannot decode
    backup = _make_clip(tmp_path / "backup.mp4", color="orange")
    s = _shot(clip_path=str(bad), clip_id="c1", backup_clip_path=str(backup),
              backup_clip_in=0.0, backup_clip_out=2.0, backup_clip_id="c1-backup")
    out = render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w")
    assert s.render_level == 2
    assert s.clip_path == str(backup) and s.clip_id == "c1-backup"
    assert s.clip_fallback == ""                 # it IS a clip shot → assembly hard-cuts around it
    assert len(s.level_notes) == 1 and s.level_notes[0].startswith("L1:")
    verify_shot_contract(out, round(1.5 * FPS))


def test_level_2_backup_clip_when_the_primary_file_is_missing(tmp_path):
    backup = _make_clip(tmp_path / "backup.mp4")
    s = _shot(clip_path=str(tmp_path / "gone.mp4"), clip_id="c1", backup_clip_path=str(backup),
              backup_clip_out=2.0)
    render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w")
    assert s.render_level == 2


def test_level_3_still_when_both_clips_fail(tmp_path):
    still = _make_still(tmp_path / "still.png")
    s = _shot(clip_path=str(tmp_path / "gone.mp4"), clip_id="c1",
              backup_clip_path=str(tmp_path / "also_gone.mp4"), custom_image=str(still))
    out = render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w")
    assert s.render_level == 3
    assert s.clip_fallback and "L1:" in s.clip_fallback and "L2:" in s.clip_fallback
    verify_shot_contract(out, round(1.5 * FPS))


def test_level_3_still_alone_needs_no_clip_at_all(tmp_path):
    still = _make_still(tmp_path / "still.png", size=(720, 1280))
    s = _shot(custom_image=str(still))
    out = render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w")
    assert s.render_level == 3 and s.clip_fallback == ""
    verify_shot_contract(out, round(1.5 * FPS))


def test_the_still_really_moves_ken_burns_not_a_frozen_frame(tmp_path):
    still = _make_still(tmp_path / "still.png", size=(1080, 1920))
    # a gradient so a zoom changes pixels
    img = Image.linear_gradient("L").resize((1080, 1920)).convert("RGB")
    img.save(still)
    s = _shot(custom_image=str(still), motion="zoom_in", duration_seconds=1.0)
    out = render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w")
    frames = tmp_path / "f_%02d.png"
    subprocess.run([_ff(), "-y", "-i", str(out), "-vf", "select='eq(n,0)+eq(n,29)'", "-vsync", "0",
                    str(frames)], check=True, capture_output=True)
    first, last = Image.open(tmp_path / "f_01.png"), Image.open(tmp_path / "f_02.png")
    assert ImageChops.difference(first.convert('RGB'), last.convert('RGB')).getbbox() is not None


def test_level_4_card_when_the_still_is_corrupt_too(tmp_path, ctx):
    bad_still = tmp_path / "still.png"
    bad_still.write_bytes(b"not a png")
    s = _shot(clip_path=str(tmp_path / "gone.mp4"), custom_image=str(bad_still))
    out = render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w", screen_context=ctx)
    assert s.render_level == 4
    assert [n[:2] for n in s.level_notes] == ["L1", "L3"]
    verify_shot_contract(out, round(1.5 * FPS))


def test_level_4_is_the_normal_path_for_a_beat_nobody_picked_anything_for(tmp_path, ctx):
    s = _shot()
    out = render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w", screen_context=ctx)
    assert s.render_level == 4 and s.clip_fallback == "" and s.level_notes == []
    verify_shot_contract(out, round(1.5 * FPS))


def test_the_card_carries_the_words_the_item_title_and_the_channel(tmp_path, ctx):
    s = _shot()
    headline, body, footer = screen_shots._card_texts(s, ctx)
    assert headline == "Avengers: Endgame (2019)"            # the item whose entity is named
    assert body == s.caption_text
    assert footer == config.CHANNEL_NAME
    # no item named -> the question
    other = _shot(caption_text="Someone else entirely.")
    assert screen_shots._card_texts(other, ctx)[0] == ctx["question"]


def test_blank_frame_when_even_the_card_renderer_dies(tmp_path, monkeypatch):
    import utils.screen_card as card

    def boom(*a, **k):
        raise OSError("font exploded")
    monkeypatch.setattr(card, "render_screen_card", boom)
    s = _shot()
    out = render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w")
    assert s.render_level == 4 and s.level_notes[-1].startswith("L4:")
    verify_shot_contract(out, round(1.5 * FPS))


def test_a_shot_is_never_left_without_a_file_whatever_breaks(tmp_path, monkeypatch):
    """Everything in the chain raises except the plain frame → still a valid shot."""
    import utils.screen_card as card
    monkeypatch.setattr(card, "render_screen_card", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(screen_shots, "render_clip_shot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("y")))
    monkeypatch.setattr(sh, "render_shot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("z")))
    clip = _make_clip(tmp_path / "c.mp4")
    still = _make_still(tmp_path / "s.png")
    s = _shot(clip_path=str(clip), backup_clip_path=str(clip), custom_image=str(still))
    out = render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w")
    assert out.is_file()
    assert [n[:2] for n in s.level_notes] == ["L1", "L2", "L3", "L4"]
    verify_shot_contract(out, round(1.5 * FPS))


# ─── network: a clip that only names its source ────────────────────────────────

def test_a_missing_section_is_fetched_from_the_right_source_moment(tmp_path, monkeypatch):
    clip = _make_clip(tmp_path / "fetched.mp4")
    calls = []

    def fake_fetch(url, start, seconds, root, log):
        calls.append((url, start, seconds, root))
        return clip
    monkeypatch.setattr(screen_shots, "_fetch_section", fake_fetch)
    s = _shot(clip_path=str(tmp_path / "section_deleted.mp4"), clip_id="c1", clip_in=0.5,
              clip_source_url="https://youtu.be/AAAAAAAAAAA", clip_source_start=61.0)
    render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w", project_root=tmp_path)
    assert s.render_level == 1
    # section t=0 sits at source 61.0, in-point 0.5 into it → 61.5, never "0.5s of the video"
    assert calls[0][0] == "https://youtu.be/AAAAAAAAAAA" and calls[0][1] == pytest.approx(61.5)
    assert calls[0][2] == pytest.approx(1.5)


def test_a_failed_download_just_drops_to_the_next_level(tmp_path, monkeypatch):
    def dead(*a, **k):
        raise RuntimeError("yt-dlp: HTTP 403")
    monkeypatch.setattr(screen_shots, "_fetch_section", dead)
    s = _shot(clip_source_url="https://youtu.be/AAAAAAAAAAA", clip_in=2.0,
              clip_fallback="clip 'c1' has no local file yet")
    render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w", project_root=tmp_path)
    assert s.render_level == 4 and "403" in s.clip_fallback


def test_no_network_attempt_when_fetching_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_shots, "_fetch_section",
                        lambda *a, **k: pytest.fail("must not download"))
    s = _shot(clip_source_url="https://youtu.be/AAAAAAAAAAA", clip_fallback="no file yet")
    render_screen_shot(s, tmp_path / "s.mp4", work_dir=tmp_path / "w", project_root=tmp_path,
                       fetch_missing=False)
    assert s.render_level == 4


# ─── the four kinds of shot splice with a stream copy ─────────────────────────

def test_clip_backup_still_and_card_shots_share_codec_parameters_and_concat_with_copy(tmp_path):
    clip = _make_clip(tmp_path / "c.mp4")
    still = _make_still(tmp_path / "s.png")
    ctx = {"question": "Q?"}
    made = []
    for i, kw in enumerate([
        dict(clip_path=str(clip), clip_out=3.0),                                      # L1
        dict(clip_path=str(tmp_path / "gone.mp4"), backup_clip_path=str(clip),
             backup_clip_out=3.0),                                                    # L2
        dict(custom_image=str(still)),                                                # L3
        dict(),                                                                       # L4
    ]):
        s = _shot(shot_id=i, duration_seconds=1.0 + 0.1 * i, **kw)
        made.append((s, render_screen_shot(s, tmp_path / f"shot_{i:03d}.mp4",
                                           work_dir=tmp_path / "w", screen_context=ctx)))
    assert [s.render_level for s, _ in made] == [1, 2, 3, 4]
    sigs = [_sig(p) for _, p in made]
    keys = ("codec_name", "profile", "level", "width", "height", "pix_fmt", "r_frame_rate",
            "time_base", "sample_aspect_ratio")
    for k in keys:
        assert len({str(sg.get(k)) for sg in sigs}) == 1, f"{k} differs across levels: " \
            f"{[sg.get(k) for sg in sigs]}"
    lst = tmp_path / "list.txt"
    lst.write_text("\n".join(f"file '{p.resolve()}'" for _, p in made) + "\n")
    joined = tmp_path / "joined.mp4"
    subprocess.run([_ff(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(joined)], check=True, capture_output=True)
    total = sum(round((1.0 + 0.1 * i) * FPS) for i in range(4))
    assert int(_sig(joined)["nb_read_frames"]) == total
