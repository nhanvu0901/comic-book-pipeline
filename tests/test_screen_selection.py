"""stages/stage_5/screen_selection.py — the picks behind the screen_qa review screen, written to
the files the rest of the pipeline already reads (no flet, no ffmpeg)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stages import review_gate as rg
from stages.stage_5 import screen_selection as sel
from stages.stage_5 import shots as sh

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "screen_qa_project"


@pytest.fixture()
def root(tmp_path) -> Path:
    r = tmp_path / "proj"
    (r / "review" / "clips").mkdir(parents=True)
    (r / "review" / "custom").mkdir(parents=True)
    (r / "narration.json").write_text((FIXTURE_DIR / "narration.json").read_text())
    return r


def _clip(beat: str, vid: str = "AAAAAAAAAAA", **kw) -> dict:
    return {"id": vid, "file": f"review/clips/{vid}.mp4", "beat": beat, "start": 0.0, "end": 3.0,
            "source_url": f"https://www.youtube.com/watch?v={vid}", **kw}


def _write_clips(root: Path, *entries: dict) -> None:
    (root / "review" / "clips" / "clips.json").write_text(json.dumps({"clips": list(entries)}))


def _add_image(root: Path, beat: str, name: str = "a.png") -> str:
    rel = f"review/custom/{name}"
    (root / rel).write_bytes(b"x")
    side = root / "review" / "custom" / "custom_images.json"
    doc = json.loads(side.read_text()) if side.exists() else {"images": []}
    doc["images"].append({"file": rel, "beat_key": beat, "desc": "", "enrich_status": "pending"})
    side.write_text(json.dumps(doc))
    return rel


# ─── clips ────────────────────────────────────────────────────────────────────

def test_load_and_remove_clips(root):
    _write_clips(root, _clip("1:0"), _clip("2:1", "BBBBBBBBBBB"), {**_clip("3:0", "CCCCCCCCCCC"),
                                                                   "enabled": False})
    assert set(sel.load_clips(root)) == {"1:0", "2:1"}               # disabled entries ignored
    assert sel.remove_clip(root, "1:0") is True
    assert set(sel.load_clips(root)) == {"2:1"}
    assert sel.remove_clip(root, "1:0") is False


def test_a_project_with_no_manifest_has_no_clips(root):
    assert sel.load_clips(root) == {} and sel.load_stills(root) == {}


def test_set_backup_only_when_the_primary_is_still_the_one_it_was_chosen_for(root):
    _write_clips(root, _clip("1:0", "AAAAAAAAAAA"))
    bk = {"id": "b", "file": "review/clips/b.mp4", "start": 0.0}
    assert sel.set_backup(root, "1:0", bk, only_if_clip_id="ZZZZZZZZZZZ") is False
    assert "backup" not in sel.load_clips(root)["1:0"]
    assert sel.set_backup(root, "1:0", bk, only_if_clip_id="AAAAAAAAAAA") is True
    assert sel.load_clips(root)["1:0"]["backup"]["id"] == "b"
    assert sel.set_backup(root, "1:0", None) is True
    assert "backup" not in sel.load_clips(root)["1:0"]


# ─── stills ───────────────────────────────────────────────────────────────────

def test_lock_and_unlock_a_still_through_locks_json_and_the_sidecar(root):
    rel = _add_image(root, "2:0")
    sel.lock_still(root, "2:0", rel)
    assert sel.load_stills(root) == {"2:0": rel}
    # exactly what Stage 5's own resolver sees
    nar = json.loads((root / "narration.json").read_text())
    assert sh._resolve_custom_images(str(root), nar) == {"2:0": str(root / rel)}
    assert sel.unlock_still(root, "2:0") is True
    assert sel.load_stills(root) == {}
    # …and the sidecar no longer holds it, so Stage 5 cannot auto-place it on another beat
    assert sh._resolve_custom_images(str(root), nar) == {}
    assert (root / rel).exists()                                     # the file itself is kept


def test_replacing_a_still_drops_the_old_one_from_the_sidecar(root):
    old, new = _add_image(root, "2:0", "old.png"), _add_image(root, "2:0", "new.png")
    sel.lock_still(root, "2:0", old)
    sel.lock_still(root, "2:0", new)
    nar = json.loads((root / "narration.json").read_text())
    assert sh._resolve_custom_images(str(root), nar) == {"2:0": str(root / new)}


def test_changing_a_pick_withdraws_the_approval(root):
    sel.set_approved(root, True)
    assert sel.is_approved(root)
    rel = _add_image(root, "1:0")
    sel.lock_still(root, "1:0", rel)
    assert not sel.is_approved(root)
    sel.set_approved(root, True)
    _write_clips(root, _clip("1:1"))
    sel.remove_clip(root, "1:1")
    assert not sel.is_approved(root)


def test_clear_beat_returns_to_the_text_card(root):
    rel = _add_image(root, "2:0")
    sel.lock_still(root, "2:0", rel)
    _write_clips(root, _clip("2:0", backup={"id": "b", "file": "x"}), _clip("2:1", "BBBBBBBBBBB"))
    sel.clear_beat(root, "2:0")
    assert set(sel.load_clips(root)) == {"2:1"} and sel.load_stills(root) == {}


# ─── approval = the review gate ───────────────────────────────────────────────

def test_approving_satisfies_the_stage_4_and_5_review_gate(root):
    with pytest.raises(SystemExit):
        rg.ensure_reviewed(root, log=lambda m: None)
    sel.set_approved(root, True)
    rg.ensure_reviewed(root, log=lambda m: None)                     # passes
    assert sel.approved_at(root)
    nar = json.loads((root / "narration.json").read_text())
    nar["title"] = "edited after approval"
    (root / "narration.json").write_text(json.dumps(nar))
    with pytest.raises(SystemExit):                                  # stale approval is rejected
        rg.ensure_reviewed(root, log=lambda m: None)
    sel.set_approved(root, False)
    assert not sel.is_approved(root)


# ─── narration changed → every pick cleared (Master decision a) ───────────────

def test_a_changed_narration_clears_every_pick_and_the_approval(root):
    assert sel.sync_with_narration(root) is False                    # first sight: just pin
    rel = _add_image(root, "2:0")
    sel.lock_still(root, "2:0", rel)
    _write_clips(root, _clip("1:0"), _clip("3:1", "BBBBBBBBBBB"))
    sel.set_approved(root, True)
    assert sel.sync_with_narration(root) is False                    # unchanged: picks kept
    assert sel.load_clips(root) and sel.load_stills(root)

    nar = json.loads((root / "narration.json").read_text())
    nar["scenes"][0]["text"] += " Extra words."
    (root / "narration.json").write_text(json.dumps(nar))
    assert sel.sync_with_narration(root) is True
    assert sel.load_clips(root) == {} and sel.load_stills(root) == {}
    assert not sel.is_approved(root)
    assert sh._resolve_custom_images(str(root), nar) == {}
    assert sel.sync_with_narration(root) is False                    # and now it is pinned again


# ─── TTS durations: one path ──────────────────────────────────────────────────

def test_tts_status_is_read_from_the_cache_path_only(root):
    assert sel.tts_status(root) == {"completed": False, "scene_durations": {}, "beat_durations": {}}
    (root / "review" / "tts_status.json").write_text(json.dumps({"scene_durations": {"1": 99}}))
    assert sel.tts_status(root)["scene_durations"] == {}            # the old UI path is NOT consulted
    (root / "cache" / "tts").mkdir(parents=True)
    (root / "cache" / "tts" / "status.json").write_text(json.dumps(
        {"completed": True, "scene_durations": {"1": 4.0, "2": 4.0, "3": 4.0}}))
    nar = json.loads((root / "narration.json").read_text())
    secs = sel.beat_seconds(root, nar)
    assert secs["1:0"] + secs["1:1"] == pytest.approx(4.0, abs=0.1)


# ─── backup clip ──────────────────────────────────────────────────────────────

def _shortlist(*vids):
    return [{"id": v, "url": f"https://www.youtube.com/watch?v={v}", "title": v,
             "moments": [{"start": 42.0, "end": 46.0}]} for v in vids]


def test_suggest_backup_takes_the_next_video_of_the_cached_shortlist(root):
    _write_clips(root, _clip("1:0", "AAAAAAAAAAA"))
    (root / "review" / "clips" / "search_1_0.json").write_text(
        json.dumps(_shortlist("AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC")))
    got = {}

    def fetch(url, clip_dir, *, start, beat_duration, log):
        got.update(url=url, start=start, dur=beat_duration)
        f = Path(clip_dir) / "BBBBBBBBBBB_42.0_3.0.mp4"
        f.write_bytes(b"x")
        return f
    bk = sel.suggest_backup(root, "1:0", seconds=3.0, fetch=fetch,
                            search=lambda *a, **k: pytest.fail("cached shortlist must be used"))
    assert got == {"url": "https://www.youtube.com/watch?v=BBBBBBBBBBB", "start": 42.0, "dur": 3.0}
    assert bk["id"] == "BBBBBBBBBBB-backup" and bk["file"] == "review/clips/BBBBBBBBBBB_42.0_3.0.mp4"
    assert bk["start"] == 0.0 and bk["source_start"] == 42.0
    assert sel.load_clips(root)["1:0"]["backup"] == bk


def test_suggest_backup_skips_a_failing_download_and_never_picks_the_primary(root):
    _write_clips(root, _clip("1:0", "AAAAAAAAAAA"))
    (root / "review" / "clips" / "search_1_0.json").write_text(
        json.dumps(_shortlist("AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC")))
    tried = []

    def fetch(url, clip_dir, *, start, beat_duration, log):
        tried.append(url[-11:])
        if url.endswith("BBBBBBBBBBB"):
            raise RuntimeError("HTTP 403")
        f = Path(clip_dir) / "c.mp4"
        f.write_bytes(b"x")
        return f
    bk = sel.suggest_backup(root, "1:0", fetch=fetch)
    assert tried == ["BBBBBBBBBBB", "CCCCCCCCCCC"] and bk["id"] == "CCCCCCCCCCC-backup"


def test_suggest_backup_searches_only_when_nothing_is_cached_and_survives_failure(root):
    _write_clips(root, _clip("1:0"))
    calls = []

    def search(q, **kw):
        calls.append(q)
        raise RuntimeError("no network")
    assert sel.suggest_backup(root, "1:0", query="quantum van", search=search) is None
    assert calls == ["quantum van"]
    assert sel.suggest_backup(root, "9:9", query="x", search=search) is None     # no such beat


def test_suggest_backup_is_dropped_if_the_primary_was_replaced_meanwhile(root):
    _write_clips(root, _clip("1:0", "AAAAAAAAAAA"))
    (root / "review" / "clips" / "search_1_0.json").write_text(
        json.dumps(_shortlist("BBBBBBBBBBB")))

    def fetch(url, clip_dir, *, start, beat_duration, log):
        _write_clips(root, _clip("1:0", "ZZZZZZZZZZZ"))              # Master re-picked during the download
        f = Path(clip_dir) / "b.mp4"
        f.write_bytes(b"x")
        return f
    assert sel.suggest_backup(root, "1:0", fetch=fetch) is None
    assert "backup" not in sel.load_clips(root)["1:0"]
