"""Motion-polish layer: hard-cut default + soft edges, flash accents, caption pop.
No rendering — only filtergraph/command construction and generated ASS text."""
from dataclasses import dataclass, field
from pathlib import Path

import config
from stages.stage_5 import pipeline as P
from stages.stage_5 import captions as C


@dataclass
class FakeShot:
    scene_id: int
    duration_seconds: float
    caption_text: str = ""
    is_intro: bool = False


def test_new_knob_defaults():
    # Master 2026-07-05: old pacing/look kept by default — polish features are OPT-IN knobs.
    assert config.FLASH_ACCENTS is False
    assert config.FLASH_ACCENTS_MAX == 3
    assert config.CAPTION_POP is False
    assert config.MIRROR_PANELS is False  # stays OFF: backwards-lettering slop risk
    assert config.TITLE_BANNER_HOOK_ONLY is False


# ── title banner: hook-window only ───────────────────────────────────────────

def test_shot_banner_text_hook_only_shows_on_intro_shot_only():
    intro = FakeShot(1, 1.0, is_intro=True)
    body = FakeShot(2, 1.0, is_intro=False)
    assert P._shot_banner_text(intro, "HOOK LINE", hook_only=True) == "HOOK LINE"
    assert P._shot_banner_text(body, "HOOK LINE", hook_only=True) == ""


def test_shot_banner_text_legacy_always_on():
    body = FakeShot(2, 1.0, is_intro=False)
    assert P._shot_banner_text(body, "HOOK LINE", hook_only=False) == "HOOK LINE"


def test_shot_banner_text_empty_banner_stays_empty():
    intro = FakeShot(1, 1.0, is_intro=True)
    assert P._shot_banner_text(intro, "", hook_only=True) == ""
    assert P._shot_banner_text(intro, "", hook_only=False) == ""


# ── _assemble_video: cut mode (default) vs dissolve mode (opt-in) ───────────

def test_cut_mode_assembly_uses_concat_not_xfade(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "XFADE_TRANSITION", "cut")
    monkeypatch.setattr(config, "XFADE_DURATION", 0.25)
    monkeypatch.setattr(config, "XFADE_SOFT_EDGES", False)
    monkeypatch.setattr(config, "FLASH_ACCENTS", False)

    calls = {"concat": [], "xfade": []}
    monkeypatch.setattr(P, "_concat", lambda paths, out: (calls["concat"].append(paths), out)[1])
    monkeypatch.setattr(P, "_xfade_chain", lambda *a, **k: calls["xfade"].append(a))

    shots = [FakeShot(1, 2.0), FakeShot(2, 2.0), FakeShot(3, 2.0)]
    paths = [tmp_path / f"s{i}.mp4" for i in range(3)]
    P._assemble_video(shots, paths, tmp_path / "final.mp4")

    assert calls["xfade"] == []
    assert len(calls["concat"]) == 1
    assert calls["concat"][0] == paths  # no scene split, no flash inserted


def test_dissolve_mode_still_constructible(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "XFADE_TRANSITION", "dissolve")
    monkeypatch.setattr(config, "XFADE_DURATION", 0.25)
    monkeypatch.setattr(config, "XFADE_SOFT_EDGES", False)
    monkeypatch.setattr(config, "FLASH_ACCENTS", False)

    monkeypatch.setattr(P, "_concat", lambda paths, out: out)  # per-scene concat stub
    captured = {}
    monkeypatch.setattr(
        P, "_xfade_chain",
        lambda clips, durs, out_path, x, transition: captured.update(
            clips=clips, durs=durs, x=x, transition=transition) or out_path,
    )

    shots = [FakeShot(1, 2.0), FakeShot(2, 3.0), FakeShot(3, 1.5)]
    paths = [tmp_path / f"s{i}.mp4" for i in range(3)]
    P._assemble_video(shots, paths, tmp_path / "final.mp4")

    assert captured["transition"] == "dissolve"
    assert captured["x"] == 0.25
    assert len(captured["clips"]) == 3
    assert captured["durs"] == [2.0, 3.0, 1.5]


# ── flash accents ─────────────────────────────────────────────────────────

def test_pick_flash_boundaries_only_action_and_capped_prefers_late():
    groups = [(1, [], 0), (2, [], 0), (3, [], 0), (4, [], 0), (5, [], 0)]
    scene_action = {1: False, 2: True, 3: True, 4: True, 5: True}
    picked = P._pick_flash_boundaries(groups, scene_action, exclude=set(), cap=3)
    assert picked == {1, 2, 3}  # 4 qualifying boundaries, cap 3 -> drops the earliest


def test_pick_flash_boundaries_skips_calm_scenes():
    groups = [(1, [], 0), (2, [], 0), (3, [], 0)]
    scene_action = {1: True, 2: False, 3: True}
    # boundary0 -> scene2 (calm) doesn't qualify; boundary1 -> scene3 (action) does
    assert P._pick_flash_boundaries(groups, scene_action, exclude=set(), cap=3) == {1}


def test_pick_flash_boundaries_respects_exclude():
    groups = [(1, [], 0), (2, [], 0)]
    scene_action = {1: True, 2: True}
    assert P._pick_flash_boundaries(groups, scene_action, exclude={0}, cap=3) == set()


def test_interleave_flashes_inserts_at_boundary():
    groups = [(1, [Path("a.mp4")], 1.0), (2, [Path("b.mp4")], 1.0), (3, [Path("c.mp4")], 1.0)]
    flash = Path("flash.mp4")
    out = P._interleave_flashes(groups, {0}, flash)
    assert out == [Path("a.mp4"), flash, Path("b.mp4"), Path("c.mp4")]


def test_interleave_flashes_never_inserts_past_last_boundary():
    groups = [(1, [Path("a.mp4")], 1.0), (2, [Path("b.mp4")], 1.0)]
    flash = Path("flash.mp4")
    # boundary 1 doesn't exist for 2 groups (only boundary 0) -- guard holds even
    # if a caller passes an out-of-range index.
    out = P._interleave_flashes(groups, {0, 1}, flash)
    assert out == [Path("a.mp4"), flash, Path("b.mp4")]


def test_interleave_flashes_noop_without_flash_clip():
    groups = [(1, [Path("a.mp4")], 1.0), (2, [Path("b.mp4")], 1.0)]
    out = P._interleave_flashes(groups, {0}, None)
    assert out == [Path("a.mp4"), Path("b.mp4")]


def test_scene_action_flags_merges_across_shots_in_scene():
    shots = [
        FakeShot(1, 1.0, caption_text="She walked home quietly."),
        FakeShot(1, 1.0, caption_text="Then Iron Man's blast slammed into the wall."),
        FakeShot(2, 1.0, caption_text="They talked calmly about the plan."),
    ]
    flags = P._scene_action_flags(shots)
    assert flags[1] is True    # one action shot flips the whole scene
    assert flags[2] is False


# ── caption pop ───────────────────────────────────────────────────────────

def _fixture_words(n=3, wps=3.4):
    """n words at `wps` words/sec — the measured narration rate — starting at t=0."""
    dur = 1.0 / wps
    t = 0.0
    words = []
    for i in range(n):
        words.append({"word": f"w{i}", "start": t, "end": t + dur})
        t += dur
    return words


def test_caption_pop_tag_present_when_enabled():
    ass = C.build_ass(_fixture_words(3), total_duration=10.0, caption_pop=True)
    assert r"\t(0,60" in ass
    assert r"\t(60,120" in ass


def test_caption_pop_tag_absent_when_disabled():
    ass = C.build_ass(_fixture_words(3), total_duration=10.0, caption_pop=False)
    assert r"\t(" not in ass


def test_caption_pop_only_on_chunk_entrance_not_every_word():
    ass = C.build_ass(_fixture_words(3), total_duration=10.0, caption_pop=True)
    assert ass.count(r"\t(0,60") == 1  # one 3-word chunk -> exactly one entrance pop


def test_caption_pop_none_defaults_from_config(monkeypatch):
    monkeypatch.setattr(config, "CAPTION_POP", False)
    ass = C.build_ass(_fixture_words(3), total_duration=10.0)  # caption_pop=None
    assert r"\t(" not in ass


def test_chunk_durations_within_target_band():
    # Measured narration rate ~3.4 words/sec (see project_stage3_length_band memory)
    # -> a 3-word chunk lands near 0.7-0.9s, inside the 0.5-1.2s cadence target.
    chunks = C._chunk_words(_fixture_words(9))
    assert len(chunks) == 3
    for c in chunks:
        dur = c["end"] - c["start"]
        assert 0.5 <= dur <= 1.2, dur
