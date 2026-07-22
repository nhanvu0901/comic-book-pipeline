"""Review-lock now applies to ALL THREE modes (Master 2026-07-14): a HARD gate, panels chosen
after narrate. These lock the additive contract:
  (a) micro_moment fragment locks override the writer's visual-beat pins in-memory;
  (b) an "intro" lock becomes narration.cold_open_lock (honored by the cold-open scorer);
  (c) recap WITH body locks routes to the chunk-locked builder; no locks → the old per-chunk path;
  (d) _qa_locks still returns locks ONLY for answer_research (Q&A regression) — _review_locks is
      the mode-agnostic reader;
  (e) ensure_reviewed blocks every mode even when --skip-review (skip_flag=True) is passed.
No render is performed — the builders are stubbed.
"""
import json

import pytest

import config
import stages.review_gate as rg
import stages.stage_5.shots as shots


def _pg(panels, src):
    return {"source_image": src, "image_dimensions": {"width": 600, "height": 2700},
            "page_type": "story", "panels": panels, "text_blocks": []}


# ─── (a) micro fragment lock overrides the writer's visual-beat pin ──────────────

def test_apply_micro_locks_overrides_fragment_pin():
    narration = {"mode": "micro_moment", "scenes": [
        {"scene_id": 3, "text": "A B", "visual_beats": [
            {"text": "A", "page": 1, "panel": 0},
            {"text": "B", "page": 1, "panel": 1}]}]}
    locks = {"3:1": {"panels": [{"page": 7, "panel": 4}], "source": "batcave"},
             "intro": {"panels": [{"page": 9, "panel": 0}]}}
    shots._apply_visual_beat_locks(narration, locks)
    vbs = narration["scenes"][0]["visual_beats"]
    assert (vbs[0]["page"], vbs[0]["panel"]) == (1, 0)   # untouched fragment
    assert (vbs[1]["page"], vbs[1]["panel"]) == (7, 4)   # fragment 1 overridden to Master's pick
    assert "cold_open_lock" not in narration             # "intro" is NOT handled here


def test_apply_micro_locks_whole_scene_key_sets_every_fragment():
    narration = {"mode": "micro_moment", "scenes": [
        {"scene_id": 2, "text": "A B", "visual_beats": [
            {"text": "A", "page": 1, "panel": 0},
            {"text": "B", "page": 1, "panel": 1}]}]}
    shots._apply_visual_beat_locks(narration, {"2": {"panels": [{"page": 3, "panel": 5}]}})
    vbs = narration["scenes"][0]["visual_beats"]
    assert all((b["page"], b["panel"]) == (3, 5) for b in vbs)


# ─── (b) "intro" lock → narration.cold_open_lock ─────────────────────────────────

def test_intro_lock_sets_cold_open_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "m"
    (proj / "review").mkdir(parents=True)
    (proj / "review" / "locks.json").write_text(json.dumps(
        {"approved": True, "locks": {"intro": {"panels": [{"page": 5, "panel": 2}]}}}))

    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: [1])

    narration = {"mode": "micro_moment", "scenes": [
        {"scene_id": 1, "text": "hook", "is_intro": True},
        {"scene_id": 2, "text": "body"}]}
    shots.build_shots(narration, caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
                      pages_by_number={1: _pg([], "p1.png")}, project="m")
    assert narration["cold_open_lock"] == [5, 2]


# ─── (c) recap WITH locks → chunk-locked builder; no locks → per-chunk ───────────

def _recap_project(tmp_path, monkeypatch, locks):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "recap"
    (proj / "review").mkdir(parents=True, exist_ok=True)
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "batcave"}))  # NOT Q&A
    (proj / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": locks}))
    return "recap"


def test_recap_routes_locked_with_locks_and_plain_without(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_build_shots_per_chunk_locked",
                        lambda *a, **k: calls.append("locked") or [1])
    monkeypatch.setattr(shots, "_build_shots_per_chunk",
                        lambda *a, **k: calls.append("chunk") or [1])
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    narration = {"scenes": [{"scene_id": 1, "text": "x"}]}     # no mode → recap
    chunks = [{"text": "x", "start": 0.0, "end": 1.0}]
    pages = {1: _pg([{"bbox": {"x": 0, "y": 0, "w": 600, "h": 900}, "description": "d",
                      "characters": []}], "p1.png")}

    _recap_project(tmp_path, monkeypatch, {"1": {"panels": [{"page": 1, "panel": 0}]}})
    shots.build_shots(narration, caption_chunks=chunks, pages_by_number=pages, project="recap")
    assert calls == ["locked"]                                 # recap + body locks → locked builder

    calls.clear()
    _recap_project(tmp_path, monkeypatch, {})                  # no locks
    shots.build_shots(narration, caption_chunks=chunks, pages_by_number=pages, project="recap")
    assert calls == ["chunk"]                                  # unchanged per-chunk path


def test_recap_intro_only_lock_stays_on_per_chunk(tmp_path, monkeypatch):
    """An 'intro'-only recap lock pins the cold-open (cold_open_lock) but has no body lock, so
    the render keeps the byte-identical per-chunk path (locked builder needs a body lock)."""
    calls = []
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_build_shots_per_chunk_locked",
                        lambda *a, **k: calls.append("locked") or [1])
    monkeypatch.setattr(shots, "_build_shots_per_chunk",
                        lambda *a, **k: calls.append("chunk") or [1])
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    _recap_project(tmp_path, monkeypatch, {"intro": {"panels": [{"page": 4, "panel": 1}]}})
    narration = {"scenes": [{"scene_id": 1, "text": "hook", "is_intro": True},
                            {"scene_id": 2, "text": "body"}]}
    shots.build_shots(narration, caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
                      pages_by_number={1: _pg([], "p1.png")}, project="recap")
    assert calls == ["chunk"]
    assert narration["cold_open_lock"] == [4, 1]


# ─── (d) _qa_locks gates on answer_research; _review_locks is mode-agnostic ──────

def test_qa_locks_answer_research_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "p"
    (proj / "review").mkdir(parents=True)
    (proj / "review" / "locks.json").write_text(json.dumps(
        {"approved": True, "locks": {"1": {"panels": [{"page": 1, "panel": 0}]}}}))

    # recap: _qa_locks empty, _review_locks sees the locks
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "batcave"}))
    assert shots._qa_locks("p") == {}
    assert shots._review_locks("p")

    # answer_research: _qa_locks returns them
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    assert shots._qa_locks("p")


# ─── (e) ensure_reviewed blocks all modes even with skip_flag=True ───────────────

@pytest.mark.parametrize("ctx", [None, {"plot_source": "batcave"},
                                 {"plot_source": "answer_research"}])
def test_ensure_reviewed_hard_gate_ignores_skip_flag(tmp_path, monkeypatch, ctx):
    monkeypatch.setattr(rg, "REVIEW_GATE", True)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "g"
    proj.mkdir()
    (proj / "narration.json").write_text("{}")
    if ctx is not None:
        (proj / "comic_context.json").write_text(json.dumps(ctx))
    with pytest.raises(SystemExit):
        rg.ensure_reviewed("g", skip_flag=True)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
