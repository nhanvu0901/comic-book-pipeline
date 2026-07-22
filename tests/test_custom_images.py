"""Tests for the "Add custom image per beat" feature (Master-approved design):
  • ui/custom_image.py     — instant add (copy + sidecar) + best-effort background enrich
  • stages/review_gate.py  — lock_custom_image (v3 additive lock shape)
  • stages/stage_5/shots.py — assign_custom_images (argmax placement) + the render-path
    resolve (custom_image bypasses crop-from-page)
  • stages/stage_5/panel_sheet.py — sheet shows the custom image, not the old panel

Cosine is NEVER a select/reject gate here — every test that touches assign_custom_images
asserts every image still gets SOME beat, only the beat CHOICE varies with score.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stages.stage_5.schema import Shot
from stages.stage_5 import shots
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


# ─── ui/custom_image.py: enrich degrades gracefully (SDK/embed/Qdrant all down) ──

def test_enrich_never_raises_when_everything_unavailable(tmp_path, monkeypatch):
    import stages._claude_sdk as sdk
    import stages._img_index as img_index
    entry = add_custom_image(tmp_path, _tiny_jpg(tmp_path / "src.jpg"), "1")

    monkeypatch.setattr(sdk, "sdk_available", lambda: False)
    monkeypatch.setattr(img_index, "img_embed_available", lambda: False)

    logs = []
    enrich_custom_image(tmp_path, entry["file"], log=logs.append)  # must not raise

    sidecar = json.loads((tmp_path / "review/custom/custom_images.json").read_text())
    updated = sidecar["images"][0]
    assert updated["desc"] == ""
    assert "sdk_unavailable" in updated["enrich_status"]
    assert any("enrich done" in m for m in logs)


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
    assert out["b1"] == "imgA.jpg"     # higher cosine wins the contested beat
    assert out["b2"] == "imgB.jpg"     # loser falls through to its next-best FREE beat
    assert "b3" not in out             # no image left to claim it
    assert set(out.values()) == {"imgA.jpg", "imgB.jpg"}   # both images placed SOMEWHERE


def test_assign_custom_images_locked_bypasses_argmax():
    """A Master hand-lock wins outright, even against a much higher cosine elsewhere —
    cosine is never a veto over an explicit lock."""
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


def test_resolve_custom_images_scores_via_desc_semantic_sim(tmp_path, monkeypatch):
    import config
    import stages.review_gate as rg
    import stages._embedding as _embedding
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "p"
    add_custom_image(proj, _tiny_jpg(tmp_path / "src.jpg"), "1")
    # give the sidecar entry a desc directly (skip the network VLM call)
    sc_path = proj / "review/custom/custom_images.json"
    doc = json.loads(sc_path.read_text())
    doc["images"][0]["desc"] = "a hero flying"
    sc_path.write_text(json.dumps(doc))

    monkeypatch.setattr(_embedding, "semantic_sim",
                        lambda a, b: 0.9 if "hero" in b else 0.0)
    narration = {"scenes": [{"scene_id": 1, "text": "a hero flying through the sky"}]}
    out = shots._resolve_custom_images("p", narration)
    assert list(out.keys()) == ["1"]
    assert out["1"].endswith(doc["images"][0]["file"])


def test_resolve_custom_images_falls_back_to_siglip_when_no_desc(tmp_path, monkeypatch):
    """No VLM desc yet (enrich pending/failed) → argmax falls back to SigLIP image-vector
    (from Qdrant) · SigLIP text-embed(beat_text) — BOTH sides mocked here, no live LM
    Studio / embedding-server call (Master's stop-all-embed-calls order)."""
    import numpy as np
    import config
    import stages.review_gate as rg
    import stages._img_index as img_index
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "p"
    entry = add_custom_image(proj, _tiny_jpg(tmp_path / "src.jpg"), "1")  # desc left "" on purpose

    fake_vec = np.array([1.0, 0.0], dtype="float32")
    monkeypatch.setattr(shots, "_load_custom_image_vectors",  # mocked Qdrant read
                        lambda project: {entry["file"]: fake_vec})

    def fake_embed_texts(texts):  # mocked SigLIP text tower — no model load, no network
        return np.array([[1.0, 0.0] if "match" in t else [0.0, 1.0] for t in texts],
                        dtype="float32")
    monkeypatch.setattr(img_index, "embed_texts", fake_embed_texts)

    narration = {"scenes": [{"scene_id": 1, "text": "other beat"},
                            {"scene_id": 2, "text": "match beat"}]}
    out = shots._resolve_custom_images("p", narration)
    assert list(out.keys()) == ["2"]   # the SigLIP-aligned beat wins, not scene 1


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


def test_apply_custom_images_unmatched_key_is_noop_not_raise():
    shot_list = [_shot(0, 1)]
    shots._apply_custom_images_to_shots(shot_list, {"99": "/abs/x.jpg", "1:5": "/abs/y.jpg"})
    assert shot_list[0].custom_image == ""


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
    """A Master hand-lock ({"custom_image": ...} in locks.json) needs NO embedding backend
    at all (assign_custom_images returns before ever calling score_fn for a fully-locked
    image) — proving the "LM Studio down, still add-able" contract end to end."""
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
