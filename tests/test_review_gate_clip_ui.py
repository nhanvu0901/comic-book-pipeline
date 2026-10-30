"""Review gate, ENABLE_VIDEO_CLIPS=1: the MP4 chips, rollback, the pick listener, the live duration chip
and — the binding rule — "any narration edit clears ALL selections of the project".

The invalidation is tested through the REAL code: the module-level clear_project_locks_and_clips
directly, and then the whole screen built headless (FakePage) on a synthetic project with a save
replayed through the real handler — not a copy of the rule re-implemented in the test."""
import json
import threading
from pathlib import Path

import pytest

import ui  # noqa: F401 — flet compat patches before screens import it
import flet as ft
from flet_video import Video

import config
from stages.stage_4 import background_tts, tts_status
from stages.stage_5 import clips
from ui import bridge, web_routes
from ui.screens import s_review_gate as sg
from ui.state import AppState
from tests.test_review_gate_grid_hide import FakePage


def _walk(control, depth: int = 0):
    """Yield every control in the tree (controls / content / actions children)."""
    if control is None or depth > 60:
        return
    yield control
    for attr in ("controls", "actions"):
        children = getattr(control, attr, None)
        if isinstance(children, (list, tuple)):
            for child in children:
                yield from _walk(child, depth + 1)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content, depth + 1)


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


def test_latest_overlapping_pick_wins_the_durable_manifest_and_preview(tmp_path, monkeypatch):
    """A slower old pick may finish after a new one, but it must not replace the new selection."""
    _patch_roots(monkeypatch, tmp_path)
    project, review = tmp_path / "test_proj", tmp_path / "test_proj" / "review"
    review.mkdir(parents=True)
    (review / "tts_status.json").write_text(json.dumps({"beat_durations": {"1:0": 2.0}}))
    old_started, release_old = threading.Event(), threading.Event()
    renders = []

    def fake_fetch(url, out_dir, **_kw):
        video_id = url.rsplit("=", 1)[-1]
        out = Path(out_dir) / f"{video_id}.mp4"
        out.write_bytes(video_id.encode())
        return out

    def fake_render(section_path, *, out_path, **_kw):
        renders.append(Path(out_path))
        marker = len(renders)
        if marker == 1:
            old_started.set()
            assert release_old.wait(5), "old pick was never released"
        Path(out_path).write_text(f"render-{marker}", encoding="utf-8")
        return Path(out_path)

    monkeypatch.setattr(web_routes.clip_fetch, "fetch_clip_section", fake_fetch)
    monkeypatch.setattr(clips, "render_clip_preview", fake_render)
    web_routes._PICKS.clear()
    try:
        old_gen = web_routes._set_pick("test_proj", "1:0", state="queued")
        old = threading.Thread(
            target=web_routes._run_pick_job,
            args=("test_proj", project, "1:0", "AAAAAAAAAAA", 0.0, old_gen),
        )
        old.start()
        assert old_started.wait(5), "old pick never reached its render"

        new_gen = web_routes._set_pick("test_proj", "1:0", state="queued")
        new_entry = web_routes._run_pick_job(
            "test_proj", project, "1:0", "AAAAAAAAAAA", 1.0, new_gen,
        )
        release_old.set()
        old.join(5)

        manifest = clips.read_manifest_doc(project)["clips"]
        assert not old.is_alive()
        assert new_entry and new_entry["source_start"] == 1.0
        assert manifest == [new_entry]
        assert new_entry["preview_file"] == "review/clips/preview_1_0.mp4"
        assert (project / new_entry["preview_file"]).read_text(encoding="utf-8") == "render-2"
        assert not (project / "review" / "clips" / f"preview_1_0.pending_{old_gen}.mp4").exists()
        assert (project / "review" / "clips" / "AAAAAAAAAAA.mp4").exists(), "shared source stays for B"
    finally:
        web_routes._PICKS.clear()


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
        children = getattr(control, attr, None)
        if isinstance(children, (list, tuple)):
            for child in children:
                yield from _walk_visible(child, depth + 1)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk_visible(content, depth + 1)


def _texts(root) -> list[str]:
    return [str(c.value) for c in _walk_visible(root) if isinstance(c, ft.Text) and c.value is not None]


def test_a_narration_edit_preserves_locks_and_clips_and_resyncs_tts(screen):
    root = screen.build()
    assert set(screen.locks()["locks"]) == {"2:0", "3:1"} and len(screen.clips()) == 2

    _type(_fields(root)["s3 · mảnh 2"], "Nobody cheered at all.")
    _save(root)

    assert set(screen.locks()["locks"]) == {"2:0", "3:1"}, "panel locks must be preserved on narration edit"
    assert screen.locks()["approved"] is False
    assert len(screen.clips()) == 2, "MP4 clips must be preserved on narration edit"
    assert screen.c["resync"] == ["qa"], "the background TTS is told to re-plan (only changed sentences re-synthesize)"
    new_nar = json.loads((screen.root / "narration.json").read_text())
    assert new_nar["scenes"][2]["visual_beats"][1] == "Nobody cheered at all."


def test_a_save_that_changes_nothing_keeps_the_selections(screen):
    root = screen.build()
    _save(root)                                            # nothing typed
    assert set(screen.locks()["locks"]) == {"2:0", "3:1"} and len(screen.clips()) == 2
    assert screen.c["resync"] == []
    _type(_fields(root)["s3 · mảnh 2"], "Nobody cheered.")  # typed the SAME text back
    _save(root)
    assert len(screen.clips()) == 2 and screen.c["resync"] == []


def test_every_structural_edit_preserves_and_remaps_locks_and_clips(screen):
    """Split, insert, merge, and drop-line must preserve unrelated selections and remap shifted fragment keys."""
    # Give 2:0 a candidate pool so we can verify split + insert inherit candidates on disk and in UI
    cands_path = screen.root / "review" / "candidates.json"
    cands_path.write_text(json.dumps({"beats": [
        {"beat_key": "2:0", "unit": "fragment", "scene_id": 2, "narration_text": "Peter fell,",
         "candidates": [{"page": 1, "panel": 0, "thumb": "review/thumbs/p001_0.jpg"}],
         "source": {"title": "Cyclops #4"}, "pre_selected": []},
        {"beat_key": "2:1", "unit": "fragment", "scene_id": 2, "narration_text": "then he rose again.",
         "candidates": [{"page": 1, "panel": 0, "thumb": "review/thumbs/p001_0.jpg"}],
         "source": {"title": "Cyclops #4"}, "pre_selected": []},
    ]}))
    prev_2_1 = screen.root / "review" / "clips" / "preview_2_1.mp4"
    prev_2_1.write_bytes(b"mp4data")
    (screen.root / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "aaaaaaaaaaa", "beat": "2:1", "file": "review/clips/a.mp4",
         "preview_file": "review/clips/preview_2_1.mp4", "start": 0.0},
        {"id": "bbbbbbbbbbb", "beat": "3:0", "file": "review/clips/b.mp4", "start": 0.0},
    ]}))

    page = FakePage()
    root = screen.build(page)

    # 1. Split 2:0 ("Peter fell,") -> 2:0 ("Peter") and 2:1 ("fell,"), shifting old 2:1 -> 2:2
    split_btns = [c for c in _walk(root) if isinstance(c, ft.IconButton)
                  and str(c.tooltip or "") == "Tách mảnh ngay trên thành 2"]
    # First split button is below "intro", second is below "2:0"
    split_btns[1].on_click(None)
    cut_btn = next(c for c in _walk(page.dialogs[-1]) if isinstance(c, ft.IconButton)
                   and "Tách sau từ #1" in str(c.tooltip or ""))
    cut_btn.on_click(None)

    assert screen.locks()["locks"]["2:0"] == {"panels": [{"page": 1, "panel": 0}]}
    assert screen.locks()["locks"]["3:1"] == {"panels": [{"page": 1, "panel": 1}]}
    clips_after_split = {c["beat"]: c for c in screen.clips()}
    assert set(clips_after_split) == {"2:2", "3:0"}, "2:1 clip shifted to 2:2 after splitting 2:0"
    assert clips_after_split["2:2"]["preview_file"] == "review/clips/preview_2_2.mp4"
    assert (screen.root / "review" / "clips" / "preview_2_2.mp4").read_bytes() == b"mp4data"
    saved_cands = {b["beat_key"]: b for b in json.loads(cands_path.read_text())["beats"]}
    assert len(saved_cands["2:1"]["candidates"]) == 1, "new split fragment persisted with candidates"

    # 2. Insert a brand-new scene between scene 2 and scene 3
    insert_btns = [c for c in _walk(screen.root_ctrl) if isinstance(c, ft.IconButton)
                   and str(c.tooltip or "") == "Thêm scene mới tại đây"]
    # Boundary index 4 is right after 2:2 (before 3:0)
    insert_btns[4].on_click(None)
    dlg = page.dialogs[-1]
    tf = next(c for c in _walk(dlg) if isinstance(c, ft.TextField))
    tf.value = "A brand new beat appears."
    add_btn = next(c for c in _walk(dlg) if isinstance(c, ft.ElevatedButton))
    add_btn.on_click(None)

    assert screen.locks()["locks"]["2:0"] == {"panels": [{"page": 1, "panel": 0}]}
    assert screen.locks()["locks"]["3:1"] == {"panels": [{"page": 1, "panel": 1}]}
    assert {c["beat"] for c in screen.clips()} == {"2:2", "3:0"}
    saved_cands = {b["beat_key"]: b for b in json.loads(cands_path.read_text())["beats"]}
    assert "5:0" in saved_cands and saved_cands["5:0"]["unit"] == "fragment"
    assert len(saved_cands["5:0"]["candidates"]) == 1, "inserted beat inherits neighbor candidates"

    # 3. Drop fragment 3:0 (trash icon on 3:0) -> 3:0's clip is dropped, 3:1's panel lock shifts to 3:0
    drop_btns = [c for c in _walk(screen.root_ctrl) if isinstance(c, ft.IconButton)
                 and str(c.tooltip or "").startswith("Xóa dòng này")]
    # Fragments in order: 2:0, 2:1, 2:2, 5:0, 3:0, 3:1 -> index 4 is 3:0
    drop_btns[4].on_click(None)
    assert screen.locks()["locks"]["3:0"] == {"panels": [{"page": 1, "panel": 1}]}
    assert "3:1" not in screen.locks()["locks"]
    assert {c["beat"] for c in screen.clips()} == {"2:2"}


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


def test_mp4_chip_duration_chip_and_use_mp4_button(screen, monkeypatch):
    monkeypatch.setattr(bridge, "WEB_MODE", True)
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


def test_desktop_mode_disables_mp4_button_with_tooltip_and_snack_and_keeps_rollback(screen):
    """In desktop mode (WEB_MODE=False), the MP4 button is disabled with a clear tooltip/snack,
    while existing clips still show their MP4 badge and functional rollback."""
    page = FakePage()
    root = screen.build(page)

    # 1. MP4 picker button is disabled on all beats with tooltip and snack
    buttons = [c for c in _walk(root) if isinstance(c, ft.IconButton) and c.icon == ft.Icons.VIDEO_FILE_OUTLINED]
    assert len(buttons) == 6
    for btn in buttons:
        assert btn.disabled is True
        assert btn.tooltip == "MP4 picker cần chạy UI với --lan"

    # Clicking the disabled button does NOT bump TTS and displays the snack
    buttons[1].on_click(None)
    assert screen.c["bump"] == [], "desktop MP4 button must not bump TTS queue"
    assert len(page.overlay) > 0, "desktop MP4 click must show a snackbar"
    assert getattr(page.overlay[-1], "open", False) is True
    assert page.overlay[-1].content.value == "MP4 picker cần chạy UI với --lan"

    # 2. Existing clips still show their MP4 badge
    texts = _texts(root)
    assert texts.count("MP4") == 2

    # 3. Existing clips still offer functional rollback (UNDO button)
    undo_buttons = [c for c in _walk(root) if isinstance(c, ft.IconButton) and "Hủy MP4" in str(c.tooltip or "")]
    assert len(undo_buttons) == 2
    for u in undo_buttons:
        assert not u.disabled

    undo_buttons[0].on_click(None)
    assert [c["beat"] for c in screen.clips()] == ["3:0"], "rollback still removes clip in desktop mode"
    assert "2:0" in screen.locks()["locks"], "panel lock restored"



def test_rollback_removes_only_that_beats_clip(screen):
    root = screen.build()
    undo = [c for c in _walk(root) if isinstance(c, ft.IconButton) and "Hủy MP4" in str(c.tooltip or "")]
    assert len(undo) == 2
    undo[0].on_click(None)
    assert [c["beat"] for c in screen.clips()] == ["3:0"]
    assert "2:0" in screen.locks()["locks"]                          # the panel locks are untouched
    undo_left = [c for c in _walk(screen.root_ctrl) if isinstance(c, ft.IconButton) and "Hủy MP4" in str(c.tooltip or "")]
    assert len(undo_left) == 1, "the rolled-back card loses its undo button"


def test_selected_area_shows_picked_mp4_then_restores_comic_panel(screen):
    """A ready pick has a visible preview and source, while rollback restores the panel lock."""
    preview = screen.root / "review" / "clips" / "preview_2_0.mp4"
    preview.write_bytes(b"preview")
    entry = {"id": "ccccccccccc", "beat": "2:0", "file": "review/clips/c.mp4",
             "preview_file": "review/clips/preview_2_0.mp4", "start": 0.0,
             "source_url": "https://www.youtube.com/watch?v=ccccccccccc", "source_start": 12.5}
    root = screen.build()
    assert "p01·0" in _texts(root)

    clips.upsert_beat_clip(screen.root, entry)
    web_routes._broadcast_moment_picked({"project": "qa", "state": "ready", **entry})
    videos = [c for c in _walk_visible(screen.root_ctrl) if isinstance(c, Video)]
    assert videos and videos[0].playlist[0].resource == str(preview)
    assert any("youtube.com/watch?v=ccccccccccc" in t for t in _texts(screen.root_ctrl))
    assert "p01·0" not in _texts(screen.root_ctrl), "the selected area must show MP4 instead of its fallback panel"

    undo = [c for c in _walk(screen.root_ctrl)
            if isinstance(c, ft.IconButton) and "Hủy MP4" in str(c.tooltip or "")]
    # The first rollback belongs to the newly picked 2:0 beat in video order.
    undo[0].on_click(None)
    assert not any(isinstance(c, Video) for c in _walk_visible(screen.root_ctrl))
    assert "p01·0" in _texts(screen.root_ctrl)

    # A range pick with source_start and source_end displays the full range on the beat card
    range_entry = {**entry, "end": 10.0, "fit_to_beat": True, "source_start": 5.0, "source_end": 15.0}
    clips.upsert_beat_clip(screen.root, range_entry)
    web_routes._broadcast_moment_picked({"project": "qa", "state": "ready", **range_entry})
    assert any("From 5.0s → 15.0s (fit to beat)" in t for t in _texts(screen.root_ctrl))


def test_selected_mp4_uses_preview_api_in_web_mode(screen, monkeypatch):
    """A browser gets a project-scoped HTTP URL, with a version after a replacement pick."""
    monkeypatch.setattr(bridge, "WEB_MODE", True)
    preview = screen.root / "review" / "clips" / "preview_2_0.mp4"
    preview.write_bytes(b"preview")
    entry = {"id": "ccccccccccc", "beat": "2:0", "file": "review/clips/c.mp4",
             "preview_file": "review/clips/preview_2_0.mp4", "start": 0.0}
    screen.build()
    clips.upsert_beat_clip(screen.root, entry)
    web_routes._broadcast_moment_picked({"project": "qa", "state": "ready", **entry})

    videos = [c for c in _walk_visible(screen.root_ctrl) if isinstance(c, Video)]
    assert len(videos) == 1
    assert videos[0].playlist[0].resource == (
        f"/api/clip_preview?project=qa&beat=2%3A0&t={preview.stat().st_mtime_ns}")


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


def test_a_pick_or_a_rollback_after_approve_withdraws_the_approval(screen):
    """Approve covers what Stage 5 will render; a clip picked or rolled back afterwards changes that."""
    root = screen.build()
    assert screen.locks()["approved"] is True
    web_routes._broadcast_moment_picked({"project": "qa", "state": "ready", "beat": "3:1", "id": "ccccccccccc",
                                         "file": "review/clips/c.mp4", "start": 0.0})
    assert screen.locks()["approved"] is False and screen.locks()["approved_at"] is None
    assert "2:0" in screen.locks()["locks"], "only the approval goes — the panel locks stay"
    # approve again, then roll a clip back
    doc = screen.locks(); doc["approved"] = True; doc["approved_at"] = "t2"
    (screen.root / "review" / "locks.json").write_text(json.dumps(doc))
    screen.build()
    undo = [c for c in _walk(screen.root_ctrl) if isinstance(c, ft.IconButton) and "Hủy MP4" in str(c.tooltip or "")]
    undo[0].on_click(None)
    assert screen.locks()["approved"] is False


def test_bookend_split_and_merge_preserves_clip_and_panel_locks(screen):
    """Splitting an unfragmented 'intro' card rebinds its clip/lock to '1:0', and merging back
    restores 'intro' so reload + Stage 5 never lose the hook's MP4 or panel lock."""
    (screen.root / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": [
        {"id": "hookclip111", "beat": "intro", "file": "review/clips/hook.mp4", "start": 0.0},
    ]}))
    page = FakePage()
    root = screen.build(page)

    # Split "intro" ("Hook line here.") after word #1
    split_btns = [c for c in _walk(root) if isinstance(c, ft.IconButton)
                  and str(c.tooltip or "") == "Tách mảnh ngay trên thành 2"]
    split_btns[0].on_click(None)
    cut_btn = next(c for c in _walk(page.dialogs[-1]) if isinstance(c, ft.IconButton)
                   and "Tách sau từ #1" in str(c.tooltip or ""))
    cut_btn.on_click(None)

    assert [c["beat"] for c in screen.clips()] == ["1:0"]

    # Merge "1:1" back into "1:0" -> only 1 fragment left on intro, so beat_key returns to "intro"
    merge_btns = [c for c in _walk(screen.root_ctrl) if isinstance(c, ft.IconButton)
                  and "Gộp mảnh này" in str(c.tooltip or "")]
    merge_btns[1].on_click(None)
    assert [c["beat"] for c in screen.clips()] == ["intro"]


def test_insert_between_same_scene_fragments_adds_fragment_and_shifts_clips(screen):
    """Inserting a new beat between 2:0 and 2:1 inserts into scene 2's visual_beats at 2:1 and
    shifts the old 2:1 MP4 clip to 2:2."""
    page = FakePage()
    root = screen.build(page)
    # Boundary index 2 is between 2:0 (card #1) and 2:1 (card #2)
    insert_btns = [c for c in _walk(root) if isinstance(c, ft.IconButton)
                   and str(c.tooltip or "") == "Thêm scene mới tại đây"]
    insert_btns[2].on_click(None)
    dlg = page.dialogs[-1]
    tf = next(c for c in _walk(dlg) if isinstance(c, ft.TextField))
    tf.value = "inserted middle clause,"
    add_btn = next(c for c in _walk(dlg) if isinstance(c, ft.ElevatedButton))
    add_btn.on_click(None)

    nar = json.loads((screen.root / "narration.json").read_text())
    s2 = next(s for s in nar["scenes"] if s["scene_id"] == 2)
    assert s2["visual_beats"] == ["Peter fell,", "inserted middle clause,", "then he rose again."]
    assert screen.locks()["locks"]["2:0"] == {"panels": [{"page": 1, "panel": 0}]}
    assert {c["beat"] for c in screen.clips()} == {"2:2", "3:0"}

