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


# ─── (f) "outro" lock → the outro scene's anchor + loop-close stays off ──────────

def test_outro_lock_pins_outro_anchor_and_skips_loop_close(tmp_path, monkeypatch):
    """The review UI now has an OUTRO row. Master's pick must reach the render: it overwrites the
    outro scene's (page_ref, panel_ref) — the per-chunk builder's outro PIN — and _close_loop is
    skipped so the cold-open clone can't overwrite the very panel Master locked."""
    closed = []
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", True)
    monkeypatch.setattr(shots, "_close_loop", lambda sh: closed.append(1))
    # the builder stub returns sentinels, not Shots → keep the loop-tail carve out of the way
    monkeypatch.setattr(shots, "_time_split_shots", lambda sh, mx, **k: sh)
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: [1])
    _recap_project(tmp_path, monkeypatch, {"outro": {"panels": [{"page": 8, "panel": 3}]}})
    narration = {"scenes": [{"scene_id": 1, "text": "hook", "is_intro": True},
                            {"scene_id": 2, "text": "body"},
                            {"scene_id": 3, "text": "bye", "is_outro": True,
                             "page_ref": 99, "panel_ref": 0}]}
    shots.build_shots(narration, caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
                      pages_by_number={1: _pg([], "p1.png")}, project="recap")
    outro = narration["scenes"][-1]
    assert (outro["page_ref"], outro["panel_ref"]) == (8, 3)   # lock beats Stage 3's own anchor
    assert closed == []                                        # loop clone would erase the pick

    # no outro lock → untouched anchor AND the loop closes exactly as before
    closed.clear()
    _recap_project(tmp_path, monkeypatch, {})
    narration = {"scenes": [{"scene_id": 1, "text": "hook", "is_intro": True},
                            {"scene_id": 3, "text": "bye", "is_outro": True,
                             "page_ref": 99, "panel_ref": 0}]}
    shots.build_shots(narration, caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
                      pages_by_number={1: _pg([], "p1.png")}, project="recap")
    assert (narration["scenes"][-1]["page_ref"], narration["scenes"][-1]["panel_ref"]) == (99, 0)
    assert closed == [1]


def test_bookend_only_lock_does_not_route_recap_to_locked_builder(tmp_path, monkeypatch):
    """An intro/outro-only lock is a BOOKEND, not a body lock: routing a legacy recap into the
    locked builder on it would leave every body scene with nothing locked to render from."""
    calls = []
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_build_shots_per_chunk_locked",
                        lambda *a, **k: calls.append("locked") or [1])
    monkeypatch.setattr(shots, "_build_shots_per_chunk",
                        lambda *a, **k: calls.append("chunk") or [1])
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    _recap_project(tmp_path, monkeypatch, {"outro": {"panels": [{"page": 8, "panel": 3}]}})
    shots.build_shots({"scenes": [{"scene_id": 1, "text": "x"},
                                  {"scene_id": 2, "text": "bye", "is_outro": True}]},
                      caption_chunks=[{"text": "x", "start": 0.0, "end": 1.0}],
                      pages_by_number={1: _pg([], "p1.png")}, project="recap")
    assert calls == ["chunk"]


# ─── (g) Q&A locked builder: bookend locks beat the subject-panel sequence ───────

def test_qa_locked_builder_honors_intro_and_outro_locks(tmp_path, monkeypatch):
    """Q&A used to bookend from subject_panels.json / the fallback matcher and IGNORE the locks
    (there was no intro/outro review row). A locked bookend is a hand pick → it wins, and the
    multi-panel subject hook collapses to that ONE panel."""
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(shots, "PANEL_TEXT_EMBED", False)   # deterministic geometric fallback
    from stages import _panel_index
    monkeypatch.setattr(_panel_index, "load_vectors", lambda project: {})
    proj = tmp_path / "qa"
    (proj / "review").mkdir(parents=True)
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (proj / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": {
        "2": {"panels": [{"page": 1, "panel": 1}]},          # body beat
        "intro": {"panels": [{"page": 1, "panel": 2}]},
        "outro": {"panels": [{"page": 1, "panel": 3}]},
    }}))
    panels = [{"index": i, "bbox": {"x": 0, "y": 100 * i, "w": 600, "h": 100},
               "description": f"d{i}", "characters": []} for i in range(4)]
    pages = {1: _pg(panels, "p1.png")}
    narration = {"mode": "explore_answer", "scenes": [
        {"scene_id": 1, "text": "hook", "is_intro": True, "page_ref": 1, "panel_ref": -1},
        {"scene_id": 2, "text": "body", "page_ref": 1, "panel_ref": -1},
        {"scene_id": 3, "text": "bye", "is_outro": True, "page_ref": 1, "panel_ref": -1}]}
    chunks = [{"text": "hook", "start": 0.0, "end": 2.0},
              {"text": "body", "start": 2.0, "end": 4.0},
              {"text": "bye", "start": 4.0, "end": 6.0}]
    timings = [{"scene_id": 1, "start": 0.0, "end": 2.0}, {"scene_id": 2, "start": 2.0, "end": 4.0},
               {"scene_id": 3, "start": 4.0, "end": 6.0}]
    out = shots.build_shots(narration, scene_timings=timings, caption_chunks=chunks,
                            pages_by_number=pages, project="qa")
    ys = [int(s.panel_bbox["y"]) for s in out]
    assert ys[0] == 200, f"intro must render the locked p1/2, got y={ys[0]}"
    assert ys[-1] == 300, f"outro must render the locked p1/3, got y={ys[-1]}"
    assert out[0].is_intro is True


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
