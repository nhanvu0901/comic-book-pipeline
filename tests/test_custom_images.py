"""Tests for the "Add custom image per beat" feature (Master-approved design):
  • ui/custom_image.py     — instant add (copy + sidecar) + best-effort background enrich
  • stages/review_gate.py  — lock_custom_image (v3 additive lock shape)
  • stages/stage_5/shots.py — assign_custom_images (argmax placement) + the render-path
    resolve (custom_image bypasses crop-from-page)
  • stages/stage_5/panel_sheet.py — sheet shows the custom image, not the old panel

The match score is NEVER a select/reject gate here — every test that touches assign_custom_images
asserts every image still gets SOME beat, only the beat CHOICE varies with score.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stages.stage_5.schema import Shot
from stages.stage_5 import shots
from stages.stage_5 import shots as shots_
from stages.stage_5.panel_sheet import build_panel_sheet
from ui.custom_image import add_custom_image, enrich_custom_image, list_custom_images


def _tiny_jpg(path: Path, color=(10, 20, 30)) -> Path:
    Image = pytest.importorskip("PIL.Image")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), color).save(path, "JPEG")
    return path


# ─── ui/custom_image.py: instant add (no network) ────────────────────────────────

def test_add_custom_image_copies_file_and_writes_sidecar(tmp_path):
    src = _tiny_jpg(tmp_path / "src.jpg")
    entry = add_custom_image(tmp_path, src, "3")

    assert entry["beat_key"] == "3"
    assert entry["enrich_status"] == "pending"
    assert entry["desc"] == ""
    dest = tmp_path / entry["file"]
    assert dest.exists()
    assert dest.read_bytes() == src.read_bytes()
    assert entry["file"].startswith("review/custom/custom_")

    sidecar = json.loads((tmp_path / "review/custom/custom_images.json").read_text())
    assert sidecar["images"] == [entry]
    assert list_custom_images(tmp_path) == [entry]


def test_add_custom_image_from_bytes_web_mode(tmp_path):
    """Flet WEB mode: FilePickerFile.path is always None, only .bytes is populated
    (with_data=True). `src_image` need not exist on disk — only its name/extension
    matter — the bytes ARE the file content."""
    payload = _tiny_jpg(tmp_path / "src_for_bytes.jpg").read_bytes()
    entry = add_custom_image(tmp_path, Path("photo_from_browser.jpg"), "1", data=payload)
    dest = tmp_path / entry["file"]
    assert dest.exists()
    assert dest.read_bytes() == payload
    assert entry["file"].endswith(".jpg")


def test_add_custom_image_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        add_custom_image(tmp_path, tmp_path / "nope.jpg", "1")


def test_add_custom_image_appends_multiple(tmp_path):
    add_custom_image(tmp_path, _tiny_jpg(tmp_path / "a.jpg"), "1")
    add_custom_image(tmp_path, _tiny_jpg(tmp_path / "b.jpg"), "2")
    imgs = list_custom_images(tmp_path)
    assert len(imgs) == 2
    assert {e["beat_key"] for e in imgs} == {"1", "2"}


# ─── ui/custom_image.py: enrich is the VLM describe only, and degrades gracefully ──

def _sidecar_entry(root):
    return json.loads((root / "review/custom/custom_images.json").read_text())["images"][0]


def test_enrich_never_raises_when_the_sdk_is_unavailable(tmp_path, monkeypatch):
    import stages._claude_sdk as sdk
    entry = add_custom_image(tmp_path, _tiny_jpg(tmp_path / "src.jpg"), "1")

    monkeypatch.setattr(sdk, "sdk_available", lambda: False)

    logs = []
    enrich_custom_image(tmp_path, entry["file"], log=logs.append)  # must not raise

    updated = _sidecar_entry(tmp_path)
    assert updated["desc"] == ""
    assert "sdk_unavailable" in updated["enrich_status"]
    assert any("enrich done" in m for m in logs)


def test_enrich_stores_the_vlm_description_and_marks_ok(tmp_path, monkeypatch):
    import stages._claude_sdk as sdk
    entry = add_custom_image(tmp_path, _tiny_jpg(tmp_path / "src.jpg"), "1")
    seen = {}

    def fake_vision(system, user, **_kw):
        seen["user"] = user
        return "  A hero flying over a city at night.  "

    monkeypatch.setattr(sdk, "sdk_available", lambda: True)
    monkeypatch.setattr(sdk, "sdk_complete_vision", fake_vision)

    enrich_custom_image(tmp_path, entry["file"], log=lambda _m: None)

    updated = _sidecar_entry(tmp_path)
    assert updated["desc"] == "A hero flying over a city at night."   # stripped, stored as-is
    assert updated["enrich_status"] == "ok"                           # ok iff a desc was written
    assert str(tmp_path / entry["file"]) in seen["user"]              # the VLM was pointed at the file


def test_enrich_vlm_failure_or_empty_answer_leaves_the_image_usable(tmp_path, monkeypatch):
    import stages._claude_sdk as sdk
    entry = add_custom_image(tmp_path, _tiny_jpg(tmp_path / "src.jpg"), "1")
    monkeypatch.setattr(sdk, "sdk_available", lambda: True)

    def boom(*_a, **_k):
        raise RuntimeError("not logged in")

    monkeypatch.setattr(sdk, "sdk_complete_vision", boom)
    enrich_custom_image(tmp_path, entry["file"], log=lambda _m: None)          # must not raise
    updated = _sidecar_entry(tmp_path)
    assert updated["desc"] == "" and "desc_failed" in updated["enrich_status"]

    monkeypatch.setattr(sdk, "sdk_complete_vision", lambda *_a, **_k: None)   # judge answered nothing
    enrich_custom_image(tmp_path, entry["file"], log=lambda _m: None)
    updated = _sidecar_entry(tmp_path)
    assert updated["desc"] == "" and updated["enrich_status"] == "desc_empty"


def test_enrich_missing_file_marks_status(tmp_path):
    logs = []
    enrich_custom_image(tmp_path, "review/custom/ghost.jpg", log=logs.append)
    # no sidecar entry to update, but must not raise — nothing else to assert.
    assert any("missing" in m for m in logs)


# ─── stages/review_gate.py mirror: UI normalizer agrees with the shared contract ─

def test_ui_normalizer_matches_review_gate_contract():
    from ui.screens.s_review_gate import _normalize_lock_custom_image
    assert _normalize_lock_custom_image({"custom_image": "review/custom/x.jpg"}) == "review/custom/x.jpg"
    assert _normalize_lock_custom_image({"page": 1, "panel": 0}) is None
    assert _normalize_lock_custom_image(None) is None


# ─── stages/stage_5/shots.py: assign_custom_images (pure argmax + contention) ────

def test_assign_custom_images_argmax_two_images_three_beats_contention():
    """Both images' TOP beat is beat 'b1'; image A scores higher there → A wins b1, B
    falls through to its own next-best FREE beat. Neither image is ever dropped."""
    beats = [("b1", "alpha text"), ("b2", "beta text"), ("b3", "gamma text")]
    images = [{"file": "imgA.jpg"}, {"file": "imgB.jpg"}]
    # score_fn(text, image) — table keyed by (image file, beat text)
    table = {
        ("imgA.jpg", "alpha text"): 0.9, ("imgA.jpg", "beta text"): 0.2, ("imgA.jpg", "gamma text"): 0.1,
        ("imgB.jpg", "alpha text"): 0.8, ("imgB.jpg", "beta text"): 0.7, ("imgB.jpg", "gamma text"): 0.05,
    }
    out = shots.assign_custom_images(
        beats, images, {}, score_fn=lambda text, img: table[(img["file"], text)])
    assert out["b1"] == "imgA.jpg"     # higher score wins the contested beat
    assert out["b2"] == "imgB.jpg"     # loser falls through to its next-best FREE beat
    assert "b3" not in out             # no image left to claim it
    assert set(out.values()) == {"imgA.jpg", "imgB.jpg"}   # both images placed SOMEWHERE


def test_assign_custom_images_locked_bypasses_argmax():
    """A Master hand-lock wins outright, even against a much higher score elsewhere —
    a score is never a veto over an explicit lock."""
    beats = [("b1", "x"), ("b2", "y")]
    images = [{"file": "imgA.jpg"}]
    out = shots.assign_custom_images(
        beats, images, {"b2": "imgA.jpg"}, score_fn=lambda text, img: 1.0 if text == "x" else 0.0)
    assert out == {"b2": "imgA.jpg"}   # locked beat wins despite scoring 0.0 there


def test_assign_custom_images_no_images_or_no_beats_is_noop():
    assert shots.assign_custom_images([], [{"file": "a.jpg"}], {}, score_fn=lambda t, i: 0.0) == {}
    assert shots.assign_custom_images([("b1", "x")], [], {}, score_fn=lambda t, i: 0.0) == {}


def test_resolve_custom_images_noop_when_no_sidecar(tmp_path, monkeypatch):
    """No review/custom/custom_images.json at all → {} — the byte-identical no-op path."""
    import config
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    import stages.review_gate as rg
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "noimg").mkdir()
    assert shots._resolve_custom_images("noimg", {"scenes": []}) == {}


def test_score_custom_image_is_the_word_overlap_of_beat_and_desc():
    img = {"file": "x.jpg", "desc": "A hero flying over the city"}   # content words: hero flying city
    assert shots._score_custom_image("The hero is flying over the city", img) == 1.0
    assert shots._score_custom_image("The hero lands", img) == 0.25          # 1 shared of 4 distinct
    assert shots._score_custom_image("Mayor signs the budget", img) == 0.0   # nothing in common
    # no desc yet (enrich pending/failed) → 0.0 on every beat, never an error
    assert shots._score_custom_image("The hero flying", {"file": "x.jpg"}) == 0.0
    assert shots._score_custom_image("The hero flying", {"file": "x.jpg", "desc": "  "}) == 0.0


def _custom_project(tmp_path, monkeypatch, descs):
    """A project with one custom image per entry of `descs` (the image's `desc`, "" = enrich never
    ran). The images are added to beat "1" — the sidecar's beat_key is display-only."""
    import config
    import stages.review_gate as rg
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "p"
    for i in range(len(descs)):
        add_custom_image(proj, _tiny_jpg(tmp_path / f"src{i}.jpg"), "1")
    sc_path = proj / "review/custom/custom_images.json"
    doc = json.loads(sc_path.read_text())
    for entry, desc in zip(doc["images"], descs):
        entry["desc"] = desc          # set directly (skips the network VLM call)
    sc_path.write_text(json.dumps(doc))
    return doc["images"]


def test_resolve_custom_images_lands_an_image_on_the_beat_its_desc_shares_words_with(tmp_path, monkeypatch):
    images = _custom_project(tmp_path, monkeypatch, ["a hero flying over the city"])
    narration = {"scenes": [{"scene_id": 1, "text": "The mayor signed the budget in a quiet office"},
                            {"scene_id": 2, "text": "A hero flying through the sky above the city"},
                            {"scene_id": 3, "text": "The crowd cheered in the street"}]}
    out = shots._resolve_custom_images("p", narration)
    assert list(out.keys()) == ["2"]          # the beat sharing "hero flying city", not the first beat
    assert Path(out["2"]).as_posix().endswith(images[0]["file"])


def test_resolve_custom_images_still_places_an_image_with_no_desc(tmp_path, monkeypatch):
    """No VLM desc yet (enrich pending/failed) → it scores 0.0 on every beat, but a custom image is
    NEVER dropped: it takes the earliest free beat in story order (Master can lock it elsewhere)."""
    images = _custom_project(tmp_path, monkeypatch, [""])
    narration = {"scenes": [{"scene_id": 1, "text": "other beat"},
                            {"scene_id": 2, "text": "match beat"}]}
    out = shots._resolve_custom_images("p", narration)
    assert list(out.keys()) == ["1"]
    assert Path(out["1"]).as_posix().endswith(images[0]["file"])


def test_resolve_custom_images_places_every_image_described_or_not(tmp_path, monkeypatch):
    """One described image and one without: the described one wins the beat it matches, the other
    falls to the earliest remaining free beat — both appear."""
    images = _custom_project(tmp_path, monkeypatch, ["a hero flying over the city", ""])
    narration = {"scenes": [{"scene_id": 1, "text": "The mayor signed the budget"},
                            {"scene_id": 2, "text": "A hero flying above the city"},
                            {"scene_id": 3, "text": "The crowd cheered"}]}
    out = shots._resolve_custom_images("p", narration)
    by_beat = {bk: Path(p).name for bk, p in out.items()}
    assert by_beat == {"2": Path(images[0]["file"]).name, "1": Path(images[1]["file"]).name}


# ─── stages/stage_5/shots.py: applying the assignment onto the shot list ─────────

def _shot(shot_id, scene_id, *, is_intro=False, beat_id=None):
    return Shot(shot_id=shot_id, scene_id=scene_id, duration_seconds=1.0,
                panel_bbox={"x": 0, "y": 0, "w": 10, "h": 10}, source_image="p.png",
                motion="zoom_in", is_intro=is_intro, beat_id=beat_id)


def test_apply_custom_images_scene_level_stamps_every_sub_shot():
    shot_list = [_shot(0, 2), _shot(1, 2), _shot(2, 3)]
    shots._apply_custom_images_to_shots(shot_list, {"2": "/abs/custom.jpg"})
    assert shot_list[0].custom_image == "/abs/custom.jpg"
    assert shot_list[1].custom_image == "/abs/custom.jpg"   # every sub-shot of scene 2
    assert shot_list[2].custom_image == ""                  # scene 3 untouched


def test_apply_custom_images_intro_key_targets_is_intro_shot():
    shot_list = [_shot(0, 1, is_intro=True), _shot(1, 2)]
    shots._apply_custom_images_to_shots(shot_list, {"intro": "/abs/hook.jpg"})
    assert shot_list[0].custom_image == "/abs/hook.jpg"
    assert shot_list[1].custom_image == ""


def test_apply_custom_images_fragment_key_targets_ordinal_shot():
    shot_list = [_shot(0, 5), _shot(1, 5), _shot(2, 5)]
    shots._apply_custom_images_to_shots(shot_list, {"5:1": "/abs/frag.jpg"})
    assert shot_list[0].custom_image == ""
    assert shot_list[1].custom_image == "/abs/frag.jpg"     # fragment index 1 == 2nd shot
    assert shot_list[2].custom_image == ""


def test_apply_custom_images_uses_beat_id_when_set():
    """Q&A locked builder gives every shot a unique scene_id but preserves the real story
    boundary in beat_id — grouping must key off beat_id when present."""
    shot_list = [_shot(0, 100, beat_id=7), _shot(1, 101, beat_id=7), _shot(2, 102, beat_id=8)]
    shots._apply_custom_images_to_shots(shot_list, {"7": "/abs/x.jpg"})
    assert shot_list[0].custom_image == "/abs/x.jpg"
    assert shot_list[1].custom_image == "/abs/x.jpg"
    assert shot_list[2].custom_image == ""


def test_apply_custom_images_key_for_a_missing_scene_is_noop_not_raise():
    """A stale lock naming a scene that no longer exists has nowhere to go — skip, never raise."""
    shot_list = [_shot(0, 1)]
    shots._apply_custom_images_to_shots(shot_list, {"99": "/abs/x.jpg"})
    assert shot_list[0].custom_image == ""


def test_fragment_index_past_the_end_clamps_instead_of_dropping_the_image(capsys):
    """CHANGED 2026-07-30 (was: silently a no-op). fi indexes a scene's shots in render order,
    which assumes 1 fragment = 1 shot; when something upstream merges fragments, fi overruns and
    the old code dropped Master's image with no output. Master's rule is that a custom image
    ALWAYS reaches final.mp4, so clamp onto the nearest real shot and say so."""
    shot_list = [_shot(0, 1)]
    shots._apply_custom_images_to_shots(shot_list, {"1:5": "/abs/y.jpg"})
    assert shot_list[0].custom_image == "/abs/y.jpg", "clamped, not dropped"
    assert "clamping" in capsys.readouterr().out, "a misplaced image must be reported"


def test_apply_custom_images_empty_map_or_shots_is_noop():
    shot_list = [_shot(0, 1)]
    shots._apply_custom_images_to_shots(shot_list, {})
    assert shot_list[0].custom_image == ""
    shots._apply_custom_images_to_shots([], {"1": "/abs/x.jpg"})  # must not raise


# ─── render path: custom_image bypasses crop-from-page ──────────────────────────

def test_load_custom_panel_loads_file_directly(tmp_path):
    src = _tiny_jpg(tmp_path / "custom.jpg", color=(200, 50, 50))
    out = tmp_path / "panel_000.png"
    shots._load_custom_panel(str(src), out)
    assert out.exists() and out.suffix == ".png"
    from PIL import Image
    with Image.open(out) as im:
        assert im.mode == "RGB"
        # JPEG is lossy — allow a small rounding delta instead of exact equality.
        px = im.getpixel((0, 0))
        assert all(abs(a - b) <= 2 for a, b in zip(px, (200, 50, 50)))


def test_load_custom_panel_missing_raises():
    with pytest.raises(FileNotFoundError):
        shots._load_custom_panel("/no/such/file.jpg", Path("/tmp/out.png"))


# ─── panel_sheet.py: shows the custom image, not the (possibly missing) old panel ─

def test_panel_sheet_prefers_custom_image(tmp_path):
    custom = _tiny_jpg(tmp_path / "custom.jpg")
    shot_list = [
        {"scene_id": 1, "source_image": str(tmp_path / "does_not_exist.png"),
         "panel_bbox": {"x": 0, "y": 0, "w": 5, "h": 5}, "custom_image": str(custom),
         "duration_seconds": 1.0},
    ]
    out = build_panel_sheet(shot_list, tmp_path / "sheet.jpg")
    assert out.exists()   # succeeded via the custom image, not the missing source_image


def test_panel_sheet_no_custom_is_unchanged(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    page = tmp_path / "page.png"
    Image.new("RGB", (100, 100), (5, 5, 5)).save(page)
    shot_list = [{"scene_id": 1, "source_image": str(page),
                 "panel_bbox": {"x": 0, "y": 0, "w": 10, "h": 10},
                 "duration_seconds": 1.0}]
    out = build_panel_sheet(shot_list, tmp_path / "sheet2.jpg")
    assert out.exists()


# ─── end-to-end: build_shots() wires resolve → apply on top of a stubbed builder ─

def test_build_shots_end_to_end_applies_locked_custom_image(tmp_path, monkeypatch):
    """A Master hand-lock ({"custom_image": ...} in locks.json) needs NO description at all
    (assign_custom_images returns before ever calling score_fn for a fully-locked image) —
    proving the "enrich never ran, still lockable" contract end to end."""
    import config
    import stages.review_gate as rg
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "recap"
    proj.mkdir()
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "batcave"}))

    entry = add_custom_image(proj, _tiny_jpg(tmp_path / "src.jpg"), "2")
    (proj / "review").mkdir(exist_ok=True)
    (proj / "review" / "locks.json").write_text(json.dumps(
        {"approved": True, "locks": {"2": {"custom_image": entry["file"]}}}))

    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    fake_shots = [_shot(0, 1), _shot(1, 2), _shot(2, 2)]
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: fake_shots)

    narration = {"scenes": [{"scene_id": 1, "text": "a"}, {"scene_id": 2, "text": "b"}]}
    out = shots.build_shots(
        narration, caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
        pages_by_number={1: {"source_image": "p.png", "panels": [],
                             "image_dimensions": {"width": 1, "height": 1},
                             "page_type": "story"}},
        project="recap")

    abs_custom = str(proj / entry["file"])
    assert out[0].custom_image == ""            # scene 1 untouched
    assert out[1].custom_image == abs_custom     # scene 2's shots overridden
    assert out[2].custom_image == abs_custom


def test_build_shots_no_custom_images_is_byte_identical(tmp_path, monkeypatch):
    """No review/custom/custom_images.json anywhere → every shot's custom_image stays the
    dataclass default "" — the explicit no-op guarantee the design requires."""
    import config
    import stages.review_gate as rg
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "recap"
    proj.mkdir()
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "batcave"}))

    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    fake_shots = [_shot(0, 1), _shot(1, 2)]
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: fake_shots)

    narration = {"scenes": [{"scene_id": 1, "text": "a"}, {"scene_id": 2, "text": "b"}]}
    out = shots.build_shots(
        narration, caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
        pages_by_number={1: {"source_image": "p.png", "panels": [],
                             "image_dimensions": {"width": 1, "height": 1},
                             "page_type": "story"}},
        project="recap")
    assert out is fake_shots                     # same list object, untouched
    assert all(s.custom_image == "" for s in out)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))


# ─── guarantee: a custom image must NEVER vanish silently (Master 2026-07-30) ─────

def test_build_shots_raises_when_a_custom_image_reaches_no_shot(monkeypatch):
    """The old summary line printed "assigned N beat(s)" straight from the map length — it
    reported success without inspecting one shot, and on broken-adamantium it said 2 while the
    render contained 0. Master: "I want all the custom image always have in our final mp4", so a
    gap must stop the render rather than ship a video missing Master's own picks."""
    narration = {"mode": "explore_answer", "scenes": [{"scene_id": 1, "text": "a"}]}
    monkeypatch.setattr(shots, "_resolve_custom_images",
                        lambda *_a, **_k: {"77:0": "/abs/never-lands.jpg"})
    monkeypatch.setattr(shots, "_build_shots_per_scene",
                        lambda *_a, **_k: [_shot(0, 1)])
    monkeypatch.setattr(shots, "_apply_review_locks", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="never reached a shot"):
        shots.build_shots(narration, project="p")


def test_build_shots_reports_how_many_custom_images_actually_landed(monkeypatch, capsys):
    narration = {"mode": "explore_answer", "scenes": [{"scene_id": 1, "text": "a"}]}
    monkeypatch.setattr(shots, "_resolve_custom_images", lambda *_a, **_k: {"1": "/abs/x.jpg"})
    monkeypatch.setattr(shots, "_build_shots_per_scene", lambda *_a, **_k: [_shot(0, 1)])
    monkeypatch.setattr(shots, "_apply_review_locks", lambda *_a, **_k: None)
    shots.build_shots(narration, project="p")
    assert "1/1 beat(s) landed on a shot" in capsys.readouterr().out


# ─── fragment→shot must follow the WORDS, not the ordinal (Master 2026-07-30) ──────

def _capshot(i, cap, dur=2.0, beat_id=2):
    return Shot(shot_id=i, scene_id=i + 1, duration_seconds=dur,
                panel_bbox={"x": 0, "y": 0, "w": 9, "h": 9}, source_image="p.png",
                motion="zoom_in", caption_text=cap, beat_id=beat_id)


_ADAMANTIUM_NARRATION = {"scenes": [{"scene_id": 2, "visual_beats": [
    {"text": "Adamantium is Marvel's indestructible metal —"},
    {"text": "it coats Wolverine's skeleton,"},
    {"text": "and the rule is simple: nothing breaks it."},
    {"text": "Doc Green, a genius variant of Hulk powered by a super-serum,"},
    {"text": "decided to test that."},
]}]}


def _adamantium_shots():
    """The REAL shot list broken-adamantium rendered: the Q&A builder cuts on caption-chunk and
    silence boundaries, so five fragments became three shots and the cuts fall MID-fragment."""
    return [
        _capshot(0, "Adamantium is Marvel's indestructible metal — it", 2.4),
        _capshot(1, "coats Wolverine's skeleton, and the rule is simple: nothing breaks it.", 3.6),
        _capshot(2, "Doc Green, a genius variant of Hulk powered by a super-serum, "
                    "decided to test that.", 4.7),
    ]


def test_fragment_image_never_lands_on_a_later_fragments_shot():
    """The shipped bug: ordinal lookup sent 2:2's image to idxs[2] — the shot reading
    "Doc Green, a genius variant of Hulk" — so Master's image played over Doc Green and Doc
    Green's own panel never appeared in the video. fi was IN range, so the out-of-range clamp
    never fired and nothing was logged."""
    shots = _adamantium_shots()
    shots_._apply_custom_images_to_shots(shots, {"2:2": "/SHIELD.jpg"}, _ADAMANTIUM_NARRATION)
    carrying = [s for s in shots if s.custom_image == "/SHIELD.jpg"]
    assert len(carrying) == 1
    assert "the rule is simple" in carrying[0].caption_text
    assert "Doc Green" not in carrying[0].caption_text, "must not cover the next fragment's panel"


def test_two_images_sharing_one_shot_split_it_instead_of_one_being_lost():
    """2:1 and 2:2 both resolve to the middle shot. Without a split only one image can ever be
    seen — Master locked two and expects two."""
    shots = _adamantium_shots()
    before = round(sum(s.duration_seconds for s in shots), 3)
    shots_._apply_custom_images_to_shots(
        shots, {"2:1": "/SKELETON.jpg", "2:2": "/SHIELD.jpg"}, _ADAMANTIUM_NARRATION)

    imgs = [s.custom_image for s in shots if s.custom_image]
    assert sorted(imgs) == ["/SHIELD.jpg", "/SKELETON.jpg"], "both images must survive"
    by_img = {s.custom_image: s.caption_text for s in shots if s.custom_image}
    assert "coats Wolverine's skeleton" in by_img["/SKELETON.jpg"]
    assert "the rule is simple" in by_img["/SHIELD.jpg"]
    # the audio is untouched, so the split must not change total screen time
    assert round(sum(s.duration_seconds for s in shots), 3) == before
    assert [s.shot_id for s in shots] == list(range(len(shots))), "shot_id stays positional"


def test_doc_green_keeps_its_own_panel_after_the_split():
    shots = _adamantium_shots()
    shots_._apply_custom_images_to_shots(
        shots, {"2:1": "/SKELETON.jpg", "2:2": "/SHIELD.jpg"}, _ADAMANTIUM_NARRATION)
    dg = [s for s in shots if "Doc Green" in s.caption_text]
    assert len(dg) == 1 and not dg[0].custom_image, "Doc Green's panel must be visible again"


def test_two_custom_images_across_split_shots_land_correctly():
    """When a scene has 2 fragments and 2 shots, but the first shot ends with the leading words
    of the second fragment (e.g. '...damage, but three' / 'injuries should have killed him...'),
    the second fragment's image must land on the second shot where the majority of its words
    are spoken, rather than colliding with the first shot and dropping an image."""
    narration = {
        "scenes": [
            {
                "scene_id": 1,
                "visual_beats": [
                    "Wolverine walks off lethal damage,",
                    "but three injuries should have killed him outright."
                ]
            }
        ]
    }
    shots = [
        _capshot(0, "Wolverine walks off lethal damage, but three", 2.472, beat_id=1),
        _capshot(1, "injuries should have killed him outright.", 2.119, beat_id=1),
    ]
    shots_._apply_custom_images_to_shots(
        shots,
        {"1:0": "/img_damage.jpg", "1:1": "/img_injuries.jpg"},
        narration
    )
    assert shots[0].custom_image == "/img_damage.jpg"
    assert shots[1].custom_image == "/img_injuries.jpg"

