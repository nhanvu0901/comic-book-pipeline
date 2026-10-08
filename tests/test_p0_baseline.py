"""P0 byte-identity: with ENABLE_VIDEO_CLIPS off, the stable render path (panels, whip bridges, the
legacy opt-in clip shots) writes exactly the shots.json and video_silent.mp4 commit 6788212 wrote.

The baseline is PER PLATFORM (tests/fixtures/p0_baseline.json, platforms -> platform_key()): an encoded
mp4 carries its x264 build and shots.json carries the OS's path separators, so hashes only compare
like with like. Every entry was captured by RUNNING 6788212 on that platform
(scripts/p0_baseline_capture.py) — never taken from HEAD. A machine without an entry skips (it has
nothing to compare against) and says how to make one."""
import json
import shutil
from pathlib import Path

import pytest

from stages.stage_5 import pipeline
from stages.stage_5.schema import Shot
from tests import p0_scenarios

FIXTURE = Path(__file__).parent / "fixtures" / "p0_baseline.json"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="ffmpeg/ffprobe not installed")
# platforms this project renders on; each must keep a captured baseline
SHIPPING_PLATFORMS = ("darwin", "win32")


def _doc() -> dict:
    return json.loads(FIXTURE.read_text())


@needs_ffmpeg
def test_flag_off_is_byte_identical_to_the_6788212_baseline_for_this_platform():
    key = p0_scenarios.platform_key()
    entry = _doc()["platforms"].get(key)
    if entry is None:
        pytest.skip(f"no 6788212 baseline for platform '{key}' — capture one: "
                    f"git worktree add /tmp/p0 6788212 && python scripts/p0_baseline_capture.py "
                    f"--repo /tmp/p0 --out tests/fixtures/p0_baseline.json")
    got = p0_scenarios.run_all(Path("projects"))        # config pinned, ENABLE_VIDEO_CLIPS False
    assert set(got) == set(entry["scenarios"])
    for name, want in entry["scenarios"].items():
        assert got[name]["shots_json_sha256"] == want["shots_json_sha256"], f"{name}: shots.json drifted from 6788212"
        assert got[name]["video_silent_sha256"] == want["video_silent_sha256"], f"{name}: video_silent.mp4 drifted from 6788212"


def test_every_shipping_platform_has_a_baseline_captured_from_the_baseline_commit():
    doc = _doc()
    for plat in SHIPPING_PLATFORMS:
        entries = {k: v for k, v in doc["platforms"].items() if k.split("|")[0] == plat}
        assert entries, f"no baseline captured on {plat}: run scripts/p0_baseline_capture.py there"
        for key, e in entries.items():
            assert e["commit"].startswith("6788212"), f"{key}: baseline not taken from 6788212 ({e['commit']})"
            assert set(e["scenarios"]) == set(p0_scenarios.SCENARIOS), key
            for sc in e["scenarios"].values():
                assert len(sc["shots_json_sha256"]) == 64 and len(sc["video_silent_sha256"]) == 64


def test_the_whip_scenario_really_has_whips_and_the_plain_one_does_not():
    """m3: the first baseline never triggered a whip, so a whip regression could not have shown in it."""
    bbox = {"x": 0, "y": 0, "w": 10, "h": 10}
    five = [Shot(shot_id=i, scene_id=i + 1, duration_seconds=1.2, panel_bbox=bbox, source_image="x.png",
                 motion="zoom_in") for i in range(5)]
    assert sorted(pipeline._pick_whip_boundaries(p0_scenarios.PROJECT_WHIP, five)) == [0, 2, 3]
    three = five[:1] + [Shot(shot_id=1, scene_id=1, duration_seconds=1.0, panel_bbox=bbox, source_image="x.png",
                             motion="zoom_in"),
                        Shot(shot_id=2, scene_id=2, duration_seconds=1.0, panel_bbox=bbox, source_image="x.png",
                             motion="zoom_in")]
    three[0].scene_id = 1
    assert pipeline._pick_whip_boundaries(p0_scenarios.PROJECT_PLAIN, three) == {}


@needs_ffmpeg
def test_the_scenarios_do_not_leak_their_pins_into_the_session():
    import config
    import stages.stage_5.shots as shots
    before = (config.XFADE_DURATION, config.TRANSITION_WHIP_PROB, config.ENABLE_VIDEO_CLIPS, shots.PANEL_UPSCALE)
    with p0_scenarios.pinned():
        assert config.ENABLE_VIDEO_CLIPS is False and shots.PANEL_UPSCALE is False
    assert (config.XFADE_DURATION, config.TRANSITION_WHIP_PROB, config.ENABLE_VIDEO_CLIPS, shots.PANEL_UPSCALE) == before
