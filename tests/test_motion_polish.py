"""Motion-polish layer: hard-cut default + soft edges, flash accents, caption pop.
No rendering — only filtergraph/command construction and generated ASS text."""
import importlib
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
    beat_id: int | None = None  # Q&A locked shots carry the real narration scene


def test_new_knob_defaults():
    # Master 2026-07-05: old pacing/look kept by default — polish features are OPT-IN knobs.
    assert config.FLASH_ACCENTS is False
    assert config.FLASH_ACCENTS_MAX == 3
    assert config.CAPTION_POP is False
    assert config.MIRROR_PANELS is False  # stays OFF: backwards-lettering slop risk


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

    monkeypatch.setattr(config, "XFADE_ROTATE", "dissolve,slideleft,slideright")
    monkeypatch.setattr(P, "_concat", lambda paths, out: out)  # per-scene concat stub
    captured = {}
    monkeypatch.setattr(
        P, "_xfade_chain",
        lambda clips, durs, out_path, x, transition, rotate=None: captured.update(
            clips=clips, durs=durs, x=x, transition=transition, rotate=rotate) or out_path,
    )

    shots = [FakeShot(1, 2.0), FakeShot(2, 3.0), FakeShot(3, 1.5)]
    paths = [tmp_path / f"s{i}.mp4" for i in range(3)]
    P._assemble_video(shots, paths, tmp_path / "final.mp4")

    assert captured["transition"] == "dissolve"
    assert captured["x"] == 0.25
    assert len(captured["clips"]) == 3
    assert captured["durs"] == [2.0, 3.0, 1.5]
    # "more animation between scenes": recap scenes (beat_id None) are all real
    # boundaries → the resolved per-boundary list cycles the XFADE_ROTATE set.
    assert captured["rotate"] == ["dissolve", "slideleft"]


def test_rotate_boundaries_qa_intra_beat_stays_dissolve():
    """Q&A locked shots: unique scene_id per shot but beat_id = real answer item.
    Boundaries WITHIN one item keep a plain dissolve; only item changes rotate.
    The final boundary into the outro card is always a dissolve."""
    rotate = ["dissolve", "slideleft", "slideright"]
    # 5 shots, 2 real beats: shots 1-3 = item 1, shots 4-5 = item 2.
    shots = [FakeShot(1, 2.0, beat_id=1), FakeShot(2, 2.0, beat_id=1),
             FakeShot(3, 2.0, beat_id=1), FakeShot(4, 2.0, beat_id=2),
             FakeShot(5, 2.0, beat_id=2)]
    groups = [(s.scene_id, [Path(f"s{s.scene_id}.mp4")], s.duration_seconds) for s in shots]
    per = P._rotate_boundaries(shots, groups, rotate, has_outro=True)
    #        1-2        2-3        3-4 (item change)  4-5        →outro
    assert per == ["dissolve", "dissolve", "dissolve", "dissolve", "dissolve"]
    # item change at 3→4 consumes rotate[0]="dissolve"; force a visible check with
    # a 3-beat layout: item changes at 2→3 and 4→5 cycle rotate in order.
    shots2 = [FakeShot(1, 2.0, beat_id=1), FakeShot(2, 2.0, beat_id=1),
              FakeShot(3, 2.0, beat_id=2), FakeShot(4, 2.0, beat_id=2),
              FakeShot(5, 2.0, beat_id=3)]
    groups2 = [(s.scene_id, [Path(f"s{s.scene_id}.mp4")], s.duration_seconds) for s in shots2]
    per2 = P._rotate_boundaries(shots2, groups2, rotate, has_outro=True)
    assert per2 == ["dissolve", "dissolve", "dissolve", "slideleft", "dissolve"]


def test_rotate_boundaries_off_returns_none():
    shots = [FakeShot(1, 2.0), FakeShot(2, 2.0)]
    groups = [(1, [Path("a.mp4")], 2.0), (2, [Path("b.mp4")], 2.0)]
    assert P._rotate_boundaries(shots, groups, None, has_outro=False) is None


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


# ── caption style knobs (A/B via env, default = current approved look) ──────

def test_ass_header_default_style_unchanged():
    # Regression guard: knobs must reproduce the pre-existing hardcoded style.
    assert config.CAPTION_FONT_SIZE == 84
    assert config.CAPTION_ALIGNMENT == 2
    assert config.CAPTION_MARGIN_V == 300
    assert config.CAPTION_OUTLINE == 8
    assert ("Style: ComicsUnlocked,Anton,84,&H00FFFFFF,&H00000000,&H00000000,"
            "&H00000000,1,0,0,0,100,100,0,0,1,8,0,2,60,60,300,1") in C.ASS_HEADER


def test_ass_header_honors_env_override(monkeypatch):
    monkeypatch.setenv("CAPTION_FONT_SIZE", "120")
    monkeypatch.setenv("CAPTION_ALIGNMENT", "5")
    monkeypatch.setenv("CAPTION_MARGIN_V", "700")
    monkeypatch.setenv("CAPTION_OUTLINE", "4")
    importlib.reload(config)
    importlib.reload(C)
    try:
        assert "Anton,120," in C.ASS_HEADER
        assert ",4,0,5,60,60,700,1" in C.ASS_HEADER
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(C)


def test_chunk_durations_within_target_band():
    # Measured narration rate ~3.4 words/sec (see project_stage3_length_band memory)
    # -> a 3-word chunk lands near 0.7-0.9s, inside the 0.5-1.2s cadence target.
    chunks = C._chunk_words(_fixture_words(9))
    assert len(chunks) == 3
    for c in chunks:
        dur = c["end"] - c["start"]
        assert 0.5 <= dur <= 1.2, dur
