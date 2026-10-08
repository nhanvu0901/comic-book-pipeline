"""Review gate, ENABLE_VIDEO_CLIPS=1: the MP4 chips, rollback, the pick listener, the live duration chip
and — the binding rule — "any narration edit clears ALL selections of the project".

The invalidation is tested through the REAL code: the module-level clear_project_locks_and_clips
directly, and then the whole screen built headless (FakePage) on a synthetic project with a save
replayed through the real handler — not a copy of the rule re-implemented in the test."""
import json
from pathlib import Path

import pytest

import ui  # noqa: F401 — flet compat patches before screens import it
import flet as ft

import config
from stages.stage_4 import background_tts, tts_status
from ui import bridge, web_routes
from ui.screens import s_review_gate as sg
from ui.state import AppState
from tests.test_review_gate_grid_hide import FakePage, _walk


def _patch_roots(monkeypatch, root: Path):
    monkeypatch.setattr(config, "PROJECTS_ROOT", root)
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", root)
    monkeypatch.setattr(sg, "PROJECTS_ROOT", root)


# ─── module-level helpers ─────────────────────────────────────────────────────

def test_load_and_remove_clips_manifest(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    clips_dir = tmp_path / "test_proj" / "review" / "clips"
    clips_dir.mkdir(parents=True)
    (clips_dir / "clips.json").write_text(json.dumps({"clips": [
        {"id": "c1", "beat": "1:0", "file": "review/clips/c1.mp4", "start": 5.0, "enabled": True},
        {"id": "c2", "beat": "2:1", "file": "review/clips/c2.mp4", "start": 10.0, "enabled": False},
        {"id": "c3", "beat": "3:0", "file": "review/clips/c3.mp4", "start": 15.0},
    ]}), "utf-8")

    loaded = sg._load_clips_manifest("test_proj")
    assert set(loaded) == {"1:0", "3:0"}                  # the disabled entry is not a selection
    sg._remove_clip_for_beat("test_proj", "1:0")
    assert set(sg._load_clips_manifest("test_proj")) == {"3:0"}


def test_beat_durations_come_from_the_one_status_file(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    proj = tmp_path / "test_proj"
    (proj / "review").mkdir(parents=True)
    assert sg._load_beat_durations("test_proj") == {}
    (proj / "review" / "tts_status.json").write_text(json.dumps(
        {"completed": False, "beat_durations": {"1:0": 2.9, "1:1": 3.5}}))
    assert sg._load_beat_durations("test_proj") == {"1:0": 2.9, "1:1": 3.5}
    # a stray file at the OLD runner path is not a second source of truth
    (proj / "cache" / "tts").mkdir(parents=True)
    (proj / "cache" / "tts" / "status.json").write_text(json.dumps({"beat_durations": {"1:0": 99.0}}))
    assert sg._load_beat_durations("test_proj")["1:0"] == 2.9


def test_clear_project_locks_and_clips_is_total_and_resyncs_tts(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    resyncs = []
    monkeypatch.setattr(background_tts, "request_resync", lambda p: resyncs.append(p))
    review = tmp_path / "test_proj" / "review"
    (review / "clips").mkdir(parents=True)
    (review / "clips" / "clips.json").write_text(json.dumps({"clips": [{"id": "c1", "beat": "1:0"}]}), "utf-8")
    (review / "clips" / "kept_section.mp4").write_bytes(b"x")
    (review / "locks.json").write_text(json.dumps({
        "locks": {"1:0": {"panels": [{"page": 2, "panel": 1}]}, "outro": {"custom_image": "review/custom/a.png"}},
        "approved": True, "approved_at": "2026-10-08T10:00:00"}), "utf-8")

    out = sg.clear_project_locks_and_clips("test_proj")

    assert out["locks"] == {} and out["approved"] is False and out["approved_at"] is None
    on_disk = json.loads((review / "locks.json").read_text())
    assert on_disk["locks"] == {} and on_disk["approved"] is False
    assert not (review / "clips" / "clips.json").exists()
    assert (review / "clips" / "kept_section.mp4").exists()      # downloaded sections stay cached
    assert resyncs == ["test_proj"]                              # the background TTS re-plans


# ─── the whole screen, headless ───────────────────────────────────────────────

SCENES = [
    {"scene_id": 1, "text": "Hook line here.", "is_intro": True, "word_count": 3, "target_seconds": 0.88},
    {"scene_id": 2, "text": "Peter fell, then he rose again.", "word_count": 6, "target_seconds": 1.76,
     "visual_beats": ["Peter fell,", "then he rose again."]},
    {"scene_id": 3, "text": "It ended badly. Nobody cheered.", "word_count": 5, "target_seconds": 1.47,
     "visual_beats": ["It ended badly.", "Nobody cheered."]},
    {"scene_id": 4, "text": "Follow for more.", "is_outro": True, "word_count": 3, "target_seconds": 0.88},
]


@pytest.fixture
def screen(tmp_path, monkeypatch):
    """A synthetic Q&A project with panel locks on two beats, clips on two others, a TTS status — and
    a factory that builds the real review screen against it (flag ON by default)."""
    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    monkeypatch.setattr(ft.BaseControl, "update", lambda self: None)       # nothing is mounted
    monkeypatch.setattr(sg, "save_state", lambda s: None)
    calls = {"resync": [], "start": [], "bump": []}
    monkeypatch.setattr(background_tts, "request_resync", lambda p: calls["resync"].append(p))
    monkeypatch.setattr(background_tts, "start_background_tts", lambda p, *a: calls["start"].append(p))
    monkeypatch.setattr(background_tts, "bump_priority_beat", lambda p, b: calls["bump"].append((p, b)))
    web_routes.clear_moment_picked_listeners()

    proj = tmp_path / "qa"
    review = proj / "review"
    (review / "clips").mkdir(parents=True)
    (proj / "narration.json").write_text(json.dumps({"words_per_second": 3.4, "scenes": SCENES}))
    (review / "candidates.json").write_text(json.dumps({"beats": [
        {"beat_key": "2:0", "unit": "fragment", "scene_id": 2, "narration_text": "Peter fell,",
         "candidates": [], "source": {}, "pre_selected": []}]}))
    (review / "locks.json").write_text(json.dumps({"approved": True, "approved_at": "t", "locks": {
        "2:0": {"panels": [{"page": 1, "panel": 0}]}, "3:1": {"panels": [{"page": 1, "panel": 1}]}}}))
    (review / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "aaaaaaaaaaa", "beat": "2:1", "file": "review/clips/a.mp4", "start": 0.0},
        {"id": "bbbbbbbbbbb", "beat": "3:0", "file": "review/clips/b.mp4", "start": 0.0}]}))
    (review / "tts_status.json").write_text(json.dumps(
        {"completed": False, "beat_durations": {"2:0": 1.2, "2:1": 2.6}}))

    class S:
        root = proj
        c = calls

        def build(self, page=None):
            self.page = page or FakePage()
            self.root_ctrl = sg.build(self.page, AppState(project_name="qa", current_stage=5),
                                      on_go=lambda s: None, on_state_change=lambda: calls.setdefault("reload", []).append(1))
            return self.root_ctrl

        def locks(self):
            return json.loads((review / "locks.json").read_text())

        def clips(self):
            return json.loads((review / "clips" / "clips.json").read_text())["clips"] \
                if (review / "clips" / "clips.json").exists() else None
    return S()


class _Ev:
    def __init__(self, control):
        self.control = control


def _fields(root) -> dict[str, ft.TextField]:
    return {str(c.label): c for c in _walk(root) if isinstance(c, ft.TextField) and c.multiline}


def _type(field, text):
    field.value = text
    field.on_change(_Ev(field))


def _save(root):
    next(c for c in _walk(root) if isinstance(c, ft.OutlinedButton)
         and "Save narration edits" in str(c.content or "")).on_click(None)


def _walk_visible(control, depth: int = 0):
    """_walk, minus every subtree under a control that is not visible (a hidden chip is still a
    control in the tree, but Master never sees it)."""
    if control is None or depth > 60 or getattr(control, "visible", True) is False:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in (getattr(control, attr, None) or []):
            yield from _walk_visible(child, depth + 1)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk_visible(content, depth + 1)


def _texts(root) -> list[str]:
    return [str(c.value) for c in _walk_visible(root) if isinstance(c, ft.Text) and c.value is not None]


def test_a_narration_edit_clears_every_lock_and_clip_of_the_project(screen):
    root = screen.build()
    assert set(screen.locks()["locks"]) == {"2:0", "3:1"} and len(screen.clips()) == 2

    _type(_fields(root)["s3 · mảnh 2"], "Nobody cheered at all.")
    _save(root)

    assert screen.locks()["locks"] == {}, "ALL panel locks go — not just the edited scene's"
    assert screen.locks()["approved"] is False
    assert screen.clips() is None, "ALL MP4 clips go — clips.json is deleted"
    assert screen.c["resync"] == ["qa"], "the background TTS is told to re-plan (only changed sentences re-synthesize)"
    new_nar = json.loads((screen.root / "narration.json").read_text())
    assert new_nar["scenes"][2]["visual_beats"][1] == "Nobody cheered at all."
    assert screen.c.get("reload"), "the screen reloads against the cleared state"


def test_a_save_that_changes_nothing_keeps_the_selections(screen):
    root = screen.build()
    _save(root)                                            # nothing typed
    assert set(screen.locks()["locks"]) == {"2:0", "3:1"} and len(screen.clips()) == 2
    assert screen.c["resync"] == []
    _type(_fields(root)["s3 · mảnh 2"], "Nobody cheered.")  # typed the SAME text back
    _save(root)
    assert len(screen.clips()) == 2 and screen.c["resync"] == []


def test_every_structural_edit_clears_everything_too(screen):
    """Delete-a-line (trash), split and merge all rewrite narration.json: each must clear all."""
    root = screen.build()
    drop = next(c for c in _walk(root) if isinstance(c, ft.IconButton)
                and str(c.tooltip or "").startswith("Xóa dòng này"))
    drop.on_click(None)
    assert screen.locks()["locks"] == {} and screen.clips() is None and screen.c["resync"] == ["qa"]


def test_with_the_flag_off_an_edit_keeps_the_stable_behaviour(screen, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    root = screen.build()
    assert screen.c["start"] == [], "flag OFF: no background TTS"
    _type(_fields(root)["s3 · mảnh 2"], "Nobody cheered at all.")
    _save(root)
    assert screen.clips() is not None and len(screen.clips()) == 2          # clips untouched
    assert "2:0" in screen.locks()["locks"]                                  # locks kept
    assert screen.locks()["approved"] is False                               # (the old rule: un-approve)
    assert screen.c["resync"] == []
    assert not any(isinstance(c, ft.IconButton) and "Dùng MP4" in str(c.tooltip or "") for c in _walk(root))


def test_mp4_chip_duration_chip_and_use_mp4_button(screen):
    root = screen.build()
    assert screen.c["start"] == ["qa"], "the screen starts the background TTS"
    texts = _texts(root)
    assert texts.count("MP4") == 2                                   # the two beats that carry a clip
    assert "1.2s" in texts and "2.6s" in texts                       # measured so far
    assert texts.count("…s") >= 3                                    # the rest wait for the TTS
    buttons = [c for c in _walk(root) if isinstance(c, ft.IconButton) and "Dùng MP4" in str(c.tooltip or "")]
    assert len(buttons) == 6                                         # one per review beat: intro, 2:0, 2:1, 3:0, 3:1, outro
    buttons[1].on_click(None)                                        # beat "2:0"
    assert screen.c["bump"] == [("qa", "2:0")], "opening the picker moves that beat to the front of the TTS queue"


def test_rollback_removes_only_that_beats_clip(screen):
    root = screen.build()
    undo = [c for c in _walk(root) if isinstance(c, ft.IconButton) and "Hủy MP4" in str(c.tooltip or "")]
    assert len(undo) == 2
    undo[0].on_click(None)
    assert [c["beat"] for c in screen.clips()] == ["3:0"]
    assert "2:0" in screen.locks()["locks"]                          # the panel locks are untouched
    undo_left = [c for c in _walk(screen.root_ctrl) if isinstance(c, ft.IconButton) and "Hủy MP4" in str(c.tooltip or "")]
    assert len(undo_left) == 1, "the rolled-back card loses its undo button"


def test_the_pick_listener_repaints_the_card_and_is_not_stacked(screen):
    page = FakePage()
    root = screen.build(page)
    assert web_routes.listener_count() == 1
    screen.build(page)                                               # the screen rebuilt itself (m6)
    screen.build(page)
    assert web_routes.listener_count() == 1, "same page+project must REPLACE its listener, not add one"
    other = FakePage()
    screen.build(other)
    assert web_routes.listener_count() == 2                          # a second browser tab is its own

    # a finished pick for this project paints the MP4 chip on its card (payload carries "project")
    web_routes._broadcast_moment_picked({"project": "qa", "state": "ready", "beat": "3:1",
                                         "id": "ccccccccccc", "file": "review/clips/c.mp4",
                                         "start": 0.0})
    assert _texts(screen.root_ctrl).count("MP4") == 3
    # another project's pick is ignored
    web_routes._broadcast_moment_picked({"project": "other", "state": "ready", "beat": "1", "id": "x"})
    assert _texts(screen.root_ctrl).count("MP4") == 3
    # a listener that raises (closed page) is dropped, not retried forever
    web_routes.add_moment_picked_listener(lambda p: 1 / 0, key="dead")
    n = web_routes.listener_count()
    web_routes._broadcast_moment_picked({"project": "qa", "beat": "9", "state": "error", "message": "x"})
    assert web_routes.listener_count() == n - 1


def test_clip_beats_do_not_warn_about_missing_panels(screen):
    texts = _texts(screen.build())
    assert "MP4 clip selected" in texts
