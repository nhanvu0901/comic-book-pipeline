"""Tests for parity between stages.clip_fetch add and ui.web_routes.process_clip_pick:
1. Row key derivation & validation:
   - Bookends with 2+ visual beats derive '<sid>:<fi>' keys via stages.review_gate.bookend_row_keys.
   - CLI 'add' and 'beats' use the same derivation.
   - If hook is fragmented, passing '--beat intro' fails with a clear message explaining it is
     fragmented and listing valid keys.
   - Reject keys matching no current row.
2. Entry parity:
   - CLI produces entry with preview_file, section file, source_start, source_end, beat_duration, fit_to_beat.
   - beat_duration from TTS timings when available; documented fallback otherwise.
3. Legacy manifests:
   - Entries without preview_file or with legacy 'intro' keys keep working in UI, routes, and Stage 5.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from stages import clip_fetch, review_gate
from stages.stage_4 import tts_status
from stages.stage_5 import clips, shots
from ui import bridge, web_routes
from ui.screens import s_review_gate as sg


def _setup_project(tmp_path: Path, monkeypatch, *, hook_frags: int = 2, outro_frags: int = 1) -> Path:
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(review_gate, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(sg, "PROJECTS_ROOT", tmp_path)

    project_root = tmp_path / "test_proj"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "review" / "clips").mkdir(parents=True, exist_ok=True)

    intro_scene = {
        "scene_id": 1,
        "is_intro": True,
        "text": "The cold open hook.",
        "visual_beats": [{"text": f"Hook beat {i}"} for i in range(hook_frags)] if hook_frags >= 2 else [],
    }
    story_scene = {
        "scene_id": 2,
        "text": "Story scene text.",
        "visual_beats": [{"text": "Story beat 0"}, {"text": "Story beat 1"}],
    }
    outro_scene = {
        "scene_id": 3,
        "is_outro": True,
        "text": "The closing line.",
        "visual_beats": [{"text": f"Outro beat {i}"} for i in range(outro_frags)] if outro_frags >= 2 else [],
    }

    narration = {"scenes": [intro_scene, story_scene, outro_scene]}
    (project_root / "narration.json").write_text(json.dumps(narration, ensure_ascii=False), encoding="utf-8")
    return project_root


# ─── 1. Row key derivation & validation tests ────────────────────────────────


def test_row_key_derivation_matches_review_gate(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2, outro_frags=1)
    narration = json.loads((root / "narration.json").read_text(encoding="utf-8"))

    expected_keys = ["1:0", "1:1", "2:0", "2:1", "outro"]
    derived_rows = review_gate.review_beat_rows(narration)
    assert [k for k, _txt in derived_rows] == expected_keys

    # CLI beat_rows uses the exact same keys
    cli_rows = clip_fetch.beat_rows(root)
    assert [k for k, _txt, _clips in cli_rows] == expected_keys


def test_clip_fetch_add_rejects_intro_when_hook_is_fragmented(tmp_path, monkeypatch, capsys):
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    fake_source = root / "review" / "clips" / "source.mp4"
    fake_source.write_bytes(b"dummy")

    with pytest.raises(SystemExit) as exc:
        clip_fetch.main([
            "add", "--project", "test_proj",
            "--file", str(fake_source),
            "--start", "0.0", "--end", "2.0",
            "--beat", "intro"
        ])

    assert exc.value.code != 0
    err = (capsys.readouterr().err + capsys.readouterr().out + str(exc.value)).lower()
    assert "fragmented" in err
    assert "1:0" in err
    assert "1:1" in err


def test_clip_fetch_add_accepts_intro_when_hook_is_single_fragment(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch, hook_frags=1)
    fake_source = root / "review" / "clips" / "source.mp4"
    fake_source.write_bytes(b"dummy")

    def fake_pick(project_root, beat, video_id, start, beat_duration, **kw):
        return {"beat": beat, "file": "review/clips/sec.mp4", "preview_file": "review/clips/prev.mp4"}

    monkeypatch.setattr(clip_fetch, "process_clip_pick", fake_pick)

    rc = clip_fetch.main([
        "add", "--project", "test_proj",
        "--file", str(fake_source),
        "--start", "0.0", "--end", "2.0",
        "--beat", "intro"
    ])
    assert rc == 0


def test_clip_fetch_add_rejects_unknown_beat_key(tmp_path, monkeypatch, capsys):
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    fake_source = root / "review" / "clips" / "source.mp4"
    fake_source.write_bytes(b"dummy")

    with pytest.raises(SystemExit) as exc:
        clip_fetch.main([
            "add", "--project", "test_proj",
            "--file", str(fake_source),
            "--start", "0.0", "--end", "2.0",
            "--beat", "99:0"
        ])

    assert exc.value.code != 0
    err = (capsys.readouterr().err + capsys.readouterr().out + str(exc.value)).lower()
    assert "unknown beat" in err or "invalid" in err
    assert "1:0" in err


# ─── 2. Entry shape parity with UI process_clip_pick ──────────────────────────


def test_clip_fetch_add_entry_shape_parity(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    clips_dir = root / "review" / "clips"
    src_file = clips_dir / "vid123.mp4"
    src_file.write_bytes(b"source_video_data")
    (clips_dir / "vid123.json").write_text(json.dumps({
        "source_url": "https://www.youtube.com/watch?v=12345678901",
        "title": "Sample Clip"
    }), encoding="utf-8")

    # Set TTS duration for 1:0
    (root / "review" / "tts_status.json").write_text(json.dumps({
        "completed": True,
        "beat_durations": {"1:0": 3.75}
    }), encoding="utf-8")

    def fake_section(url, out_dir, *, start, beat_duration, end=None, **kw):
        out = Path(out_dir) / f"sec_{start}_{end}.mp4"
        out.write_bytes(b"section_data")
        return out

    def fake_render(sec_path, *, start, beat_duration, out_path, **kw):
        out = Path(out_path)
        out.write_bytes(b"preview_data")
        return out

    monkeypatch.setattr(clip_fetch, "fetch_clip_section", fake_section)
    monkeypatch.setattr(clips, "render_clip_preview", fake_render)

    rc = clip_fetch.main([
        "add", "--project", "test_proj",
        "--file", str(src_file),
        "--start", "10.0", "--end", "14.5",
        "--beat", "1:0",
        "--crop-cx", "0.6"
    ])
    assert rc == 0

    doc = clips.read_manifest_doc(root)
    assert len(doc["clips"]) == 1
    entry = doc["clips"][0]

    # Full shape parity check with process_clip_pick
    assert entry["beat"] == "1:0"
    assert entry["start"] == 0.0
    assert entry["source_start"] == 10.0
    assert entry["source_end"] == 14.5
    assert entry["end"] == pytest.approx(4.5)
    assert entry["fit_to_beat"] is True
    assert entry["beat_duration"] == pytest.approx(3.75)
    assert entry["preview_file"] == "review/clips/preview_1_0.mp4"
    assert (root / entry["preview_file"]).is_file()
    assert (root / entry["file"]).is_file()
    assert entry.get("crop") == {"cx": 0.6, "cy": 0.5}


def test_beat_duration_fallbacks(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    # 1. When TTS duration is available:
    (root / "review" / "tts_status.json").write_text(json.dumps({
        "beat_durations": {"1:0": 4.2}
    }), encoding="utf-8")
    assert clip_fetch.resolve_beat_duration(root, "1:0") == 4.2

    # 2. When TTS duration is missing, fall back to narration estimate:
    dur = clip_fetch.resolve_beat_duration(root, "1:1")
    assert dur > 0.0

    # 3. When narration estimate is not available, fall back to (end - start):
    dur_span = clip_fetch.resolve_beat_duration(root, "unknown", start=2.0, end=5.5)
    assert dur_span == pytest.approx(3.5)

    # 4. Final fallback default:
    assert clip_fetch.resolve_beat_duration(root, "unknown") == 3.0


# ─── 3. Legacy manifest compatibility ─────────────────────────────────────────


def test_legacy_manifest_without_preview_file_served_in_route_and_ui(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch, hook_frags=1)
    clips_dir = root / "review" / "clips"
    legacy_file = clips_dir / "old_clip.mp4"
    legacy_file.write_bytes(b"legacy_mp4_bytes")

    legacy_entry = {
        "id": "old_clip",
        "file": "review/clips/old_clip.mp4",
        "beat": "intro",
        "start": 5.0,
        "end": 8.0,
    }
    (clips_dir / "clips.json").write_text(json.dumps({"clips": [legacy_entry]}), encoding="utf-8")

    # In UI, _load_clips_manifest loads it
    manifest = sg._load_clips_manifest("test_proj")
    assert "intro" in manifest

    # In web route, /api/clip_preview resolves the legacy file when preview_file is absent
    import asyncio
    res = asyncio.run(web_routes.clip_preview(project="test_proj", beat="intro"))
    assert str(res.path) == str(legacy_file.resolve())


def test_legacy_intro_key_in_fragmented_hook_manifest(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    clips_dir = root / "review" / "clips"
    legacy_file = clips_dir / "hook_clip.mp4"
    legacy_file.write_bytes(b"hook_bytes")

    legacy_entry = {
        "id": "hook_clip",
        "file": "review/clips/hook_clip.mp4",
        "beat": "intro",
        "start": 0.0,
        "end": 3.0,
    }
    (clips_dir / "clips.json").write_text(json.dumps({"clips": [legacy_entry]}), encoding="utf-8")

    # Stage 5 apply_clips_to_shots still resolves it to the intro shot
    sl = [shots.Shot(shot_id=0, scene_id=1, duration_seconds=3.0, panel_bbox={}, source_image="p.png",
                     motion="zoom_in", is_intro=True)]
    clips.apply_clips_to_shots(sl, [("intro", clips.ClipEntry(id="hook_clip", file=str(legacy_file)))],
                               log=lambda m: None)
    assert sl[0].clip_id == "hook_clip"


# ─── 4. Review findings regression & negative tests ──────────────────────────


def test_clip_fetch_add_with_desc_only(tmp_path, monkeypatch):
    """[P2 finding 1] CLI add --desc ... resolves best beat without AttributeError on ClipEntry."""
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    clips_dir = root / "review" / "clips"
    src_file = clips_dir / "desc_clip.mp4"
    src_file.write_bytes(b"data")

    def fake_section(url, out_dir, *, start, beat_duration, end=None, **kw):
        out = Path(out_dir) / f"sec_{start}_{end}.mp4"
        out.write_bytes(b"section_data")
        return out

    def fake_render(sec_path, *, start, beat_duration, out_path, **kw):
        out = Path(out_path)
        out.write_bytes(b"preview_data")
        return out

    monkeypatch.setattr(clip_fetch, "fetch_clip_section", fake_section)
    monkeypatch.setattr(clips, "render_clip_preview", fake_render)

    rc = clip_fetch.main([
        "add", "--project", "test_proj",
        "--file", str(src_file),
        "--start", "0.0", "--end", "2.0",
        "--desc", "The cold open hook beat"
    ])
    assert rc == 0

    doc = clips.read_manifest_doc(root)
    assert len(doc["clips"]) == 1
    # Placed on one of the story/hook beats matching the description
    assert doc["clips"][0]["beat"] in ("1:0", "1:1", "2:0", "2:1")


def test_remove_clip_for_beat_removes_legacy_aliased_bookend(tmp_path, monkeypatch):
    """[P2 finding 2] Rolling back an aliased bookend clip deletes the legacy entry from clips.json."""
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    clips_dir = root / "review" / "clips"
    legacy_file = clips_dir / "hook_clip.mp4"
    legacy_file.write_bytes(b"hook_bytes")

    legacy_entry = {
        "id": "hook_clip",
        "file": "review/clips/hook_clip.mp4",
        "beat": "intro",
        "start": 0.0,
        "end": 3.0,
    }
    (clips_dir / "clips.json").write_text(json.dumps({"clips": [legacy_entry]}), encoding="utf-8")

    # 1. Manifest loader maps legacy "intro" to the first fragment row "1:0"
    loaded = sg._load_clips_manifest("test_proj")
    assert "1:0" in loaded

    # 2. Rolling back "1:0" in the review UI drops "intro" from clips.json on disk
    sg._remove_clip_for_beat("test_proj", "1:0")

    # 3. Reloading manifest shows it is completely gone on disk and in UI
    on_disk = clips.read_manifest_doc(root)["clips"]
    assert on_disk == []
    reloaded = sg._load_clips_manifest("test_proj")
    assert reloaded == {}


def test_remove_clip_for_beat_removes_legacy_aliased_outro(tmp_path, monkeypatch):
    """[P2 finding 2] Rolling back an aliased outro clip deletes the legacy entry from clips.json."""
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2, outro_frags=2)
    clips_dir = root / "review" / "clips"
    legacy_file = clips_dir / "outro_clip.mp4"
    legacy_file.write_bytes(b"outro_bytes")

    legacy_entry = {
        "id": "outro_clip",
        "file": "review/clips/outro_clip.mp4",
        "beat": "outro",
        "start": 0.0,
        "end": 3.0,
    }
    (clips_dir / "clips.json").write_text(json.dumps({"clips": [legacy_entry]}), encoding="utf-8")

    loaded = sg._load_clips_manifest("test_proj")
    assert "3:0" in loaded

    sg._remove_clip_for_beat("test_proj", "3:0")

    on_disk = clips.read_manifest_doc(root)["clips"]
    assert on_disk == []
    reloaded = sg._load_clips_manifest("test_proj")
    assert reloaded == {}


def test_clip_fetch_add_rejects_negative_start(tmp_path, monkeypatch, capsys):
    """[P2 finding 3] CLI add rejects negative start time."""
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    fake_source = root / "review" / "clips" / "source.mp4"
    fake_source.write_bytes(b"dummy")

    with pytest.raises(SystemExit) as exc:
        clip_fetch.main([
            "add", "--project", "test_proj",
            "--file", str(fake_source),
            "--start", "-1.5",
            "--beat", "1:0"
        ])
    assert exc.value.code != 0
    err = (capsys.readouterr().err + capsys.readouterr().out + str(exc.value)).lower()
    assert "start" in err and "non-negative" in err


def test_clip_fetch_add_rejects_inverted_span(tmp_path, monkeypatch, capsys):
    """[P2 finding 3] CLI add rejects end <= start."""
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    fake_source = root / "review" / "clips" / "source.mp4"
    fake_source.write_bytes(b"dummy")

    with pytest.raises(SystemExit) as exc:
        clip_fetch.main([
            "add", "--project", "test_proj",
            "--file", str(fake_source),
            "--start", "10.0", "--end", "5.0",
            "--beat", "1:0"
        ])
    assert exc.value.code != 0
    err = (capsys.readouterr().err + capsys.readouterr().out + str(exc.value)).lower()
    assert "end" in err and "greater than start" in err


def test_clip_fetch_add_rejects_equal_start_and_end(tmp_path, monkeypatch, capsys):
    """[P2 finding 3] CLI add rejects end == start."""
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    fake_source = root / "review" / "clips" / "source.mp4"
    fake_source.write_bytes(b"dummy")

    with pytest.raises(SystemExit) as exc:
        clip_fetch.main([
            "add", "--project", "test_proj",
            "--file", str(fake_source),
            "--start", "5.0", "--end", "5.0",
            "--beat", "1:0"
        ])
    assert exc.value.code != 0
    err = (capsys.readouterr().err + capsys.readouterr().out + str(exc.value)).lower()
    assert "end" in err and "greater than start" in err


def test_process_clip_pick_validates_start_and_end(tmp_path, monkeypatch):
    """[P2 finding 3] process_clip_pick raises ValueError on invalid start/end."""
    root = _setup_project(tmp_path, monkeypatch, hook_frags=2)
    with pytest.raises(ValueError, match="start must be a non-negative number"):
        clip_fetch.process_clip_pick(root, "1:0", "vid123", start=-1.0, beat_duration=3.0)

    with pytest.raises(ValueError, match="greater than start"):
        clip_fetch.process_clip_pick(root, "1:0", "vid123", start=10.0, beat_duration=3.0, end=5.0)

    with pytest.raises(ValueError, match="greater than start"):
        clip_fetch.process_clip_pick(root, "1:0", "vid123", start=5.0, beat_duration=3.0, end=5.0)

