"""Mascot render: final.mp4 clean-copy rules, atomic replace, still frame and preview."""
import hashlib
import json
import shutil
import subprocess

import numpy as np
import pytest

from stages.mascot import kit as kit_mod
from stages.mascot import render
from stages.mascot.config import Cue, MascotConfig, Position
from tests.test_mascot_kit import make_kit

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

W, H = 108, 192


def _synth(path, pattern="testsrc", dur=2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{pattern}=size={W}x{H}:rate=30:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def _probe(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                   str(path)])
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    return {"w": v["width"], "h": v["height"], "fps": v["avg_frame_rate"], "pix": v["pix_fmt"],
            "vcodec": v["codec_name"], "acodec": a and a["codec_name"], "dur": float(d["format"]["duration"])}


def _grab(path, t):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# bottom-left box the sprite covers at x=0.2, feet on the bottom edge
LEFT = (slice(130, 192), slice(4, 40))
RIGHT = (slice(130, 192), slice(68, 104))


@pytest.fixture
def proj(tmp_path, monkeypatch):
    kits = tmp_path / "kits"
    make_kit(kits)
    monkeypatch.setattr(kit_mod, "MASCOT_ASSETS", kits)
    p = tmp_path / "projects" / "demo"
    p.mkdir(parents=True)
    _synth(p / "final.mp4")
    return p


def cfg_of(**kw):
    base = dict(kit="fx", accessories=["hat"], size_pct=30.0,
                cues=[Cue(t=0.0, move="rise"), Cue(t=1.2, move="hop")])
    base.update(kw)
    return MascotConfig(**base)


def test_first_render_makes_clean_copy_and_keeps_stream_properties(proj):
    original = _sha(proj / "final.mp4")
    before = _probe(proj / "final.mp4")
    seen = []
    out = render.render_final(proj, cfg_of(), progress=seen.append)
    assert out == proj / "final.mp4"
    clean = proj / "mascot" / "final_clean.mp4"
    assert _sha(clean) == original
    after = _probe(proj / "final.mp4")
    for k in ("w", "h", "fps", "pix", "vcodec", "acodec"):
        assert after[k] == before[k], k
    assert abs(after["dur"] - before["dur"]) < 0.15
    assert render.has_mascot_output(proj) and render.source_video(proj) == clean
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)
    assert (proj / "mascot.json").is_file()
    assert not list(proj.glob("*.tmp.mp4"))
    # the mascot really is drawn
    clean_f, new_f = _grab(clean, 1.0), _grab(proj / "final.mp4", 1.0)
    assert np.abs(clean_f[LEFT] - new_f[LEFT]).mean() > 15
    assert np.abs(clean_f[RIGHT] - new_f[RIGHT]).mean() < 6


def test_second_render_reads_the_clean_copy_no_stacking(proj):
    render.render_final(proj, cfg_of())
    clean = proj / "mascot" / "final_clean.mp4"
    clean_sha = _sha(clean)
    first = _grab(proj / "final.mp4", 1.0)
    clean_f = _grab(clean, 1.0)
    # second render puts the mascot on the RIGHT: the old left spot must be clean again, not a ghost
    right = cfg_of(position=Position.from_preset("bottom_right"))
    render.render_final(proj, right)
    assert _sha(clean) == clean_sha
    second = _grab(proj / "final.mp4", 1.0)
    assert np.abs(first[LEFT] - clean_f[LEFT]).mean() > 15
    assert np.abs(second[LEFT] - clean_f[LEFT]).mean() < 6
    assert np.abs(second[RIGHT] - clean_f[RIGHT]).mean() > 15
    # same config twice → the same picture (not a mascot on a mascot)
    render.render_final(proj, right)
    assert np.abs(_grab(proj / "final.mp4", 1.0) - second).mean() < 3


def test_replaced_final_is_a_fresh_stage5_render(proj):
    render.render_final(proj, cfg_of())
    old_clean = _sha(proj / "mascot" / "final_clean.mp4")
    new = proj.parent / "new.mp4"
    _synth(new, pattern="smptebars")
    shutil.copyfile(new, proj / "final.mp4")             # Stage 5 re-rendered
    assert not render.has_mascot_output(proj)
    assert render.source_video(proj) == proj / "final.mp4"
    render.render_final(proj, cfg_of())
    assert _sha(proj / "mascot" / "final_clean.mp4") == _sha(new) != old_clean
    assert render.has_mascot_output(proj)


def test_disabled_restores_the_clean_copy(proj):
    original = _sha(proj / "final.mp4")
    render.render_final(proj, cfg_of())
    assert _sha(proj / "final.mp4") != original
    out = render.render_final(proj, cfg_of(enabled=False))
    assert _sha(out) == original
    assert not render.has_mascot_output(proj)
    assert render.source_video(proj) == proj / "final.mp4"
    assert render.restore_clean(proj) is False           # nothing left to restore
    # re-enabling renders from the clean copy again
    render.render_final(proj, cfg_of())
    assert render.has_mascot_output(proj) and _sha(proj / "mascot" / "final_clean.mp4") == original


def test_failed_encoder_leaves_final_untouched(proj, monkeypatch):
    original = _sha(proj / "final.mp4")
    monkeypatch.setattr(render, "VIDEO_CODEC_ARGS", ["-c:v", "no_such_codec"])
    with pytest.raises(RuntimeError):
        render.render_final(proj, cfg_of())                          # very first render fails
    assert _sha(proj / "final.mp4") == original and not list(proj.glob("*.tmp.mp4"))
    monkeypatch.undo()
    monkeypatch.setattr(kit_mod, "MASCOT_ASSETS", proj.parent.parent / "kits")
    render.render_final(proj, cfg_of())
    rendered = _sha(proj / "final.mp4")
    monkeypatch.setattr(render, "VIDEO_CODEC_ARGS", ["-c:v", "no_such_codec"])
    with pytest.raises(RuntimeError):
        render.render_final(proj, cfg_of())                          # a later render fails
    assert _sha(proj / "final.mp4") == rendered
    assert render.has_mascot_output(proj) and not list(proj.glob("*.tmp.mp4"))


def test_no_leftover_ffmpeg_after_failure(proj, monkeypatch):
    monkeypatch.setattr(render, "VIDEO_CODEC_ARGS", ["-c:v", "no_such_codec"])
    with pytest.raises(RuntimeError):
        render.render_final(proj, cfg_of())
    ps = subprocess.run(["pgrep", "-f", str(proj)], capture_output=True, text=True).stdout.split()
    assert ps == []


def test_no_visible_cue_leaves_the_video_clean(proj):
    original = _sha(proj / "final.mp4")
    render.render_final(proj, cfg_of(cues=[]))
    assert _sha(proj / "final.mp4") == original and not render.has_mascot_output(proj)


def test_frame_at_is_cached(proj, monkeypatch):
    a = render.frame_at(proj, 1.0, width=54)
    assert a.is_file() and a.parent == proj / "mascot" / "cache" / "frames"
    from PIL import Image
    assert Image.open(a).size == (54, 96)

    def boom(*args, **kw):
        raise AssertionError("ffmpeg must not run again")
    monkeypatch.setattr(render.subprocess, "run", boom)
    assert render.frame_at(proj, 1.0, width=54) == a
    assert render.frame_at(proj, 1.0 + 0.01, width=54) == a      # same 1/30 s bucket
    monkeypatch.undo()
    assert render.frame_at(proj, 1.0, width=40) != a             # another width → another file
    assert render.frame_at(proj, 99.0, width=54).is_file()       # past the end → last frame


def test_frame_at_reads_the_clean_video_after_a_render(proj):
    render.render_final(proj, cfg_of())
    f = render.frame_at(proj, 1.0, width=W)
    from PIL import Image
    clean = _grab(proj / "mascot" / "final_clean.mp4", 1.0)
    got = np.asarray(Image.open(f).convert("RGB")).astype(int)
    assert np.abs(got - clean)[LEFT].mean() < 8                  # no mascot in the still


def test_render_preview_window_and_audio(proj):
    seen = []
    out = render.render_preview(proj, cfg_of(), 1.0, span=1.0, width=54, progress=seen.append)
    assert out == proj / "mascot" / "preview.mp4"
    info = _probe(out)
    assert (info["w"], info["h"]) == (54, 96) and info["acodec"] == "aac"
    assert abs(info["dur"] - 1.0) < 0.15
    assert seen[0] == 0.0 and seen[-1] == 1.0
    assert _sha(proj / "final.mp4") and not render.has_mascot_output(proj)   # preview never touches final
    # span longer than the clip → clamped to the video
    out2 = render.render_preview(proj, cfg_of(), 0.2, span=10.0, width=54)
    assert abs(_probe(out2)["dur"] - 2.0) < 0.2


def test_envelope_prefers_audio_wav_when_lengths_match(proj):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
                    str(proj / "audio.wav")], check=True)
    info = render._probe(proj / "final.mp4")
    env = render._envelope_for(proj, proj / "final.mp4", info)
    assert env.max() == pytest.approx(1.0, abs=1e-6) or env.max() > 0.9
    # a wav of the wrong length is ignored (falls back to the video's audio): still an envelope
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=5",
                    str(proj / "audio.wav")], check=True)
    assert render._envelope_for(proj, proj / "final.mp4", info).max() > 0.9
