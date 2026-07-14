"""Q&A (explore_answer) sentence-driven render: one shot PER NARRATION SENTENCE, panel
driven by review/sentence_panels.json. Recap projects (plot_source != answer_research) or
Q&A projects before that file exists must take the UNCHANGED per-chunk/per-scene path.

Covers: one-shot-per-sentence + correct panel resolution, sparse (null) reuse, the
is_intro / uncovered-scene fallback, duration tiling, and the build_shots gate both ways."""
import json

import config
from stages.stage_5 import shots
from stages.stage_5.shots import (_build_shots_per_sentence, _load_sentence_panels,
                                  build_shots)


def _pg(panels, src, w=1500, h=1500):
    return {"panels": panels, "source_image": src,
            "image_dimensions": {"width": w, "height": h}}


def _panel(w, h):
    return {"bbox": {"x": 0, "y": 0, "w": w, "h": h}}


def test_one_shot_per_sentence_with_panel_and_sparse_reuse():
    """Each sentence → its own shot with its (page,panel); a null panel REUSES the
    previous shot's panel; durations tile to the next sentence's start."""
    narration = {"scenes": [
        {"scene_id": 1, "text": "Punisher line."},
        {"scene_id": 2, "text": "Deadpool line."},
    ]}
    pages = {
        1: _pg([_panel(700, 1200)], "p1.png"),   # (1,0)
        2: _pg([_panel(800, 900)], "p2.png"),     # (2,0)
    }
    sentence_panels = {"scenes": [
        {"scene_id": 1, "sentences": [
            {"text": "a", "start": 0.0, "end": 1.0, "page": 1, "panel": 0},
            {"text": "b", "start": 1.0, "end": 2.0, "page": None, "panel": None},  # sparse → reuse (1,0)
        ]},
        {"scene_id": 2, "sentences": [
            {"text": "c", "start": 2.5, "end": 3.5, "page": 2, "panel": 0},
        ]},
    ]}
    built = _build_shots_per_sentence(narration, sentence_panels, pages, [])
    assert len(built) == 3                                   # one shot per sentence
    assert [s.scene_id for s in built] == [1, 1, 2]
    assert [s.caption_text for s in built] == ["a", "b", "c"]
    assert built[0].panel_bbox["w"] == 700                  # (1,0)
    assert built[1].panel_bbox["w"] == 700                  # sparse → reused (1,0)
    assert built[2].panel_bbox["w"] == 800                  # (2,0)
    # durations tile to the next start; the last uses its own end.
    assert built[0].duration_seconds == 1.0                 # 1.0 - 0.0
    assert built[1].duration_seconds == 1.5                 # 2.5 - 1.0 (silence absorbed forward)
    assert built[2].duration_seconds == 1.0                 # 3.5 - 2.5 (own end)


def test_intro_and_uncovered_scene_fall_back_to_per_scene(monkeypatch):
    """The is_intro scene (never sentence-split) keeps the cold-open; a scene the match
    step didn't cover falls back to the per-scene matcher. Both come from _match_panels."""
    narration = {"scenes": [
        {"scene_id": 1, "is_intro": True, "text": "the hook"},
        {"scene_id": 2, "text": "Deadpool line."},
    ]}
    intro_panel = {"bbox": {"x": 0, "y": 0, "w": 999, "h": 1400}}
    pages = {2: _pg([_panel(800, 900)], "p2.png")}
    sentence_panels = {"scenes": [
        {"scene_id": 2, "sentences": [
            {"text": "c", "start": 2.0, "end": 3.0, "page": 2, "panel": 0},
            {"text": "d", "start": 3.0, "end": 4.0, "page": None, "panel": None},
        ]},
    ]}
    scene_timings = [{"scene_id": 1, "start": 0.0, "end": 2.0}]
    # Only the intro (scene 1) is a fallback unit → the matcher is asked exactly for it.
    monkeypatch.setattr(shots, "_match_panels", lambda units, *a, **k: [(intro_panel, "s.png")])
    built = _build_shots_per_sentence(narration, sentence_panels, pages, scene_timings)
    assert len(built) == 3
    assert [s.scene_id for s in built] == [1, 2, 2]
    assert built[0].is_intro is True and built[0].motion == "zoom_in"
    assert built[0].no_mirror is True                       # cold-open is never mirrored
    assert built[0].panel_bbox["w"] == 999                  # intro panel from _match_panels
    assert built[0].duration_seconds == 2.0                 # tiles to scene-2 first sentence start
    assert built[1].panel_bbox["w"] == 800                  # (2,0)
    assert built[2].panel_bbox["w"] == 800                  # sparse → reused (2,0)


def test_first_ever_sparse_sentence_seeds_from_scene_anchor():
    """A first-ever sparse (null) sentence, with no earlier shot to reuse, seeds from the
    scene's own (page_ref, panel_ref) anchor."""
    narration = {"scenes": [{"scene_id": 1, "text": "x", "page_ref": 1, "panel_ref": 0}]}
    pages = {1: _pg([_panel(640, 1100)], "p1.png")}
    sentence_panels = {"scenes": [
        {"scene_id": 1, "sentences": [{"text": "x", "start": 0.0, "end": 1.5,
                                       "page": None, "panel": None}]},
    ]}
    built = _build_shots_per_sentence(narration, sentence_panels, pages, [])
    assert len(built) == 1
    assert built[0].panel_bbox["w"] == 640                  # seeded from scene anchor (1,0)


def _write_project(root, name, *, plot_source, sentence_panels):
    proj = root / name
    (proj / "review").mkdir(parents=True)
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": plot_source}))
    if sentence_panels is not None:
        (proj / "review" / "sentence_panels.json").write_text(json.dumps(sentence_panels))
    return proj


def test_gate_only_fires_for_answer_research_with_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    sp = {"scenes": [{"scene_id": 1, "sentences": [{"text": "x", "start": 0, "end": 1}]}]}
    _write_project(tmp_path, "qa", plot_source="answer_research", sentence_panels=sp)
    _write_project(tmp_path, "recap", plot_source="claude-sdk-web", sentence_panels=sp)
    _write_project(tmp_path, "qa_nofile", plot_source="answer_research", sentence_panels=None)

    assert _load_sentence_panels("qa") == sp                # answer_research + file → fires
    assert _load_sentence_panels("recap") is None           # recap → never fires (even w/ file)
    assert _load_sentence_panels("qa_nofile") is None       # answer_research, no file → per-chunk
    assert _load_sentence_panels(None) is None


def test_build_shots_routes_on_gate(monkeypatch):
    """build_shots calls the sentence builder iff the gate returns panels; otherwise the
    unchanged per-chunk path — proven by which builder fires."""
    narration = {"scenes": [{"scene_id": 1, "text": "x"}]}
    pages = {1: _pg([_panel(700, 1200)], "p1.png")}
    chunks = [{"text": "x", "start": 0.0, "end": 1.0}]
    calls = []
    monkeypatch.setattr(shots, "_build_shots_per_sentence", lambda *a, **k: calls.append("sentence") or [])
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: calls.append("chunk") or [])

    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)
    build_shots(narration, caption_chunks=chunks, pages_by_number=pages, project="p")
    assert calls == ["chunk"]                               # gate off → unchanged path

    calls.clear()
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: {"scenes": []})
    build_shots(narration, caption_chunks=chunks, pages_by_number=pages, project="p")
    assert calls == ["sentence"]                            # gate on → sentence path


# ── Q&A caption-chunk render, restricted to Master's LOCKED panels ────────────
import re                                                    # noqa: E402

import stages._embedding as _embedding                       # noqa: E402
import stages._panel_index as _panel_index                   # noqa: E402
import stages.review_gate as _review_gate                     # noqa: E402


def _panel_at(y, desc):
    return {"bbox": {"x": 0, "y": y, "w": 600, "h": 900}, "description": desc, "characters": []}


def _fake_score(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    cw = set(re.findall(r"[a-z]+", (chunk_text or "").lower()))
    dw = set(re.findall(r"[a-z]+", str(panel.get("description", "")).lower()))
    sim = min(0.9, 0.15 * len(cw & dw))
    return sim, sim


def test_qa_chunk_locked_gate_routes(monkeypatch):
    """build_shots routes a Q&A project WITH locks to the chunk-locked builder, and everything
    else (recap, or Q&A without locks) to the UNCHANGED per-chunk path."""
    narration = {"scenes": [{"scene_id": 1, "text": "x"}]}
    pages = {1: _pg([_panel(700, 1200)], "p1.png")}
    chunks = [{"text": "x", "start": 0.0, "end": 1.0}]
    calls = []
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)   # stub builders return [1], not Shots — skip the loop-tail carve
    monkeypatch.setattr(shots, "_build_shots_per_chunk_locked", lambda *a, **k: calls.append("locked") or [1])
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: calls.append("chunk") or [1])
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)

    monkeypatch.setattr(shots, "_qa_locks", lambda project: {"1": {"panels": [{"page": 1, "panel": 0}]}})
    build_shots(narration, caption_chunks=chunks, pages_by_number=pages, project="qa")
    assert calls == ["locked"]                               # Q&A + locks → chunk-locked

    calls.clear()
    monkeypatch.setattr(shots, "_qa_locks", lambda project: {})
    build_shots(narration, caption_chunks=chunks, pages_by_number=pages, project="recap")
    assert calls == ["chunk"]                                # no locks → unchanged per-chunk


def _setup_qa(tmp_path, monkeypatch, locks):
    """Write a minimal answer_research project with `locks` (scene_id str -> {"panels":[...]}) and
    stub all scoring so the chunk-locked builder runs deterministically (no network/SDK/Qdrant).
    Returns the project slug 'qa'. review_gate binds PROJECTS_ROOT at import (module-level), so we
    patch it there too — else the gate resolves the real projects dir and never fires."""
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(_review_gate, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "qa"
    (proj / "review").mkdir(parents=True)
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (proj / "narration.json").write_text(json.dumps({"scenes": [{"scene_id": 1, "text": "s"}]}))
    (proj / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": locks}))
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(_panel_index, "load_vectors", lambda project: {})
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    monkeypatch.setattr(shots, "_blend_image_content", lambda *a, **k: None)
    return "qa"


_LOCK_3 = {"1": {"panels": [{"page": 5, "panel": 0}, {"page": 5, "panel": 1},
                            {"page": 5, "panel": 2}], "source": "batcave"}}
_PAGES_3 = {
    5: _pg([_panel_at(0, "punisher vomit"), _panel_at(900, "deadpool punch"),
            _panel_at(1800, "carnage grin")], "p5.png", w=600, h=2700),
    # Unlocked magnet: matches EVERY chunk best — must never be chosen (pool is Master-restricted).
    9: _pg([{"bbox": {"x": 0, "y": 0, "w": 600, "h": 900},
             "description": "punisher vomit deadpool punch carnage grin", "characters": []}],
           "p9.png", w=600, h=900),
}


def test_qa_chunk_locked_segments_into_distinct_panels(tmp_path, monkeypatch):
    """6 chunks over 3 locked panels + a long beat → K=3 contiguous groups, each a DISTINCT locked
    panel held ≥ min. Never the p9 magnet (pool is Master-restricted). Each shot a unique scene_id
    (→ the assembler dissolves between them)."""
    slug = _setup_qa(tmp_path, monkeypatch, _LOCK_3)
    monkeypatch.setattr(shots, "PANEL_RERANK", False)        # isolate segmentation from VLM
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)       # isolate from the loop-tail carve
    # one 2s chunk per subject → 3 groups, each ≥ min, one distinct panel apiece.
    words = ["punisher vomit", "deadpool punch", "carnage grin"]
    chunks = [{"text": w, "start": 2.0 * i, "end": 2.0 * (i + 1)} for i, w in enumerate(words)]
    timings = [{"scene_id": 1, "start": 0.0, "end": 6.0}]
    built = build_shots({"scenes": [{"scene_id": 1, "text": "s"}]}, scene_timings=timings,
                        caption_chunks=chunks, pages_by_number=_PAGES_3, project=slug)

    assert len(built) == 3                                   # K = min(3 panels, 3 chunks, 6/1.5)
    assert all(s.duration_seconds >= shots.QA_MIN_SHOT_SECONDS for s in built)
    assert all(s.source_image == "p5.png" for s in built)   # never the p9 magnet
    assert {s.panel_bbox["y"] for s in built} == {0, 900, 1800}   # 3 DISTINCT locked panels
    assert [s.scene_id for s in built] == [1, 2, 3]         # unique scene_id per shot → dissolve


def test_qa_chunk_locked_duration_caps_shot_count(tmp_path, monkeypatch):
    """A short beat yields FEWER shots than locked panels: 3 panels locked but only ~2s of audio →
    floor(2/1.5)=1 → a single held shot (no sub-1.5s jump)."""
    slug = _setup_qa(tmp_path, monkeypatch, _LOCK_3)
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)       # isolate from the loop-tail carve
    chunks = [{"text": "punisher", "start": 0.0, "end": 0.5},
              {"text": "vomit", "start": 0.5, "end": 1.0},
              {"text": "punisher", "start": 1.0, "end": 1.5},
              {"text": "vomit", "start": 1.5, "end": 2.0}]
    timings = [{"scene_id": 1, "start": 0.0, "end": 2.0}]
    built = build_shots({"scenes": [{"scene_id": 1, "text": "s"}]}, scene_timings=timings,
                        caption_chunks=chunks, pages_by_number=_PAGES_3, project=slug)

    assert len(built) == 1                                   # duration caps K to 1
    assert round(built[0].duration_seconds, 2) == 2.0       # whole beat, one hold
    assert built[0].source_image == "p5.png"


def test_qa_chunk_locked_vlm_rerank_overrides_weak_cosine(tmp_path, monkeypatch):
    """PANEL_RERANK on + weak cosine → the VLM judge (stubbed) re-picks a locked panel per group.
    The judge forces ALL groups to (5,2), but the per-beat NO-REUSE rule reassigns the duplicate
    picks to the other locked panels → 3 DISTINCT shots (no duplicate scene, Master 2026-07-07),
    with the rerank still visible on the first group. Previously this collapsed to a single hold."""
    slug = _setup_qa(tmp_path, monkeypatch, _LOCK_3)
    monkeypatch.setattr(shots, "PANEL_RERANK", True)
    monkeypatch.setattr(shots, "PANEL_RERANK_COS_CEIL", 0.66)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)       # isolate from the loop-tail carve
    # _vlm_rerank gets cands=[(idx, src, panel, tb), ...]; return idx 2 → locked panel (5,2).
    monkeypatch.setattr(shots, "_vlm_rerank", lambda line, cands, **k: 2)
    words = ["punisher vomit", "deadpool punch", "carnage grin"]
    chunks = [{"text": w, "start": 2.0 * i, "end": 2.0 * (i + 1)} for i, w in enumerate(words)]
    timings = [{"scene_id": 1, "start": 0.0, "end": 6.0}]
    built = build_shots({"scenes": [{"scene_id": 1, "text": "s"}]}, scene_timings=timings,
                        caption_chunks=chunks, pages_by_number=_PAGES_3, project=slug)

    assert len(built) == 3                                   # no-reuse: 3 distinct locked panels
    assert built[0].panel_bbox["y"] == 1800                 # (5,2), the VLM-forced first pick
    ys = [s.panel_bbox["y"] for s in built]
    assert len(set(ys)) == 3, f"panels must be distinct (no-reuse), got {ys}"
    assert round(sum(s.duration_seconds for s in built), 2) == 6.0


# ─── Bubble-avoiding cover-crop window (frame-1 slop fix) ─────────────────────

def test_choose_crop_offset_clean_panel_stays_centered():
    from stages.stage_5.shots import _choose_crop_offset
    # 2160×1920 scaled panel, 1080×1920 out → slack only on x; no bubbles
    assert _choose_crop_offset(2160, 1920, 1080, 1920, []) == (540, 0)


def test_choose_crop_offset_slides_away_from_bubble():
    from stages.stage_5.shots import _choose_crop_offset
    # big empty bubble on the LEFT half of the centered window → slide right
    bubble = [{"x": 500, "y": 100, "w": 500, "h": 800}]
    x0, y0 = _choose_crop_offset(2160, 1920, 1080, 1920, bubble)
    assert y0 == 0 and x0 > 540, f"window should slide right, got x0={x0}"
    # bounded: never beyond max shift (540 + 0.35*1080 = 918)
    assert x0 <= 540 + int(1080 * 0.35)


def test_choose_crop_offset_tiny_bubble_no_churn():
    from stages.stage_5.shots import _choose_crop_offset
    # bubble covers <2% of the window → hysteresis keeps dead center
    tiny = [{"x": 1000, "y": 100, "w": 60, "h": 60}]
    assert _choose_crop_offset(2160, 1920, 1080, 1920, tiny) == (540, 0)


# ─── Q&A subject bookend (intro/outro on-subject, not spectacle) ─────────────

def test_qa_subject_panels_picks_recurring_name_biggest_first():
    from stages.stage_5.shots import _qa_subject_panels
    lc = {
        2: [(("4", 0), {"description": "Doctor Doom holds a glowing orb", "bbox": {"w": 500, "h": 400}}, "p4", []),
            (("7", 0), {"description": "Storm unleashes lightning", "bbox": {"w": 900, "h": 900}}, "p7", [])],
        3: [(("26", 0), {"description": "Victor Von Doom reborn, Doom transformed", "bbox": {"w": 520, "h": 300}}, "p26", [])],
        4: [(("69", 1), {"description": "Doom buried under squirrels", "bbox": {"w": 300, "h": 300}}, "p69", [])],
    }
    out = _qa_subject_panels(lc)
    srcs = [s for (_p, s) in out]
    assert "p7" not in srcs, "Storm (biggest but not the subject) must be excluded"
    assert set(srcs) == {"p4", "p26", "p69"}          # only Doom panels
    assert srcs[0] == "p4"                              # biggest Doom panel first


def test_qa_subject_panels_empty_when_no_recurring_name():
    from stages.stage_5.shots import _qa_subject_panels
    # every panel a different name → nothing recurs → no confident subject
    lc = {2: [(("4", 0), {"description": "Storm strikes", "bbox": {"w": 500, "h": 400}}, "p4", [])],
          3: [(("9", 0), {"description": "Colossus punches", "bbox": {"w": 500, "h": 400}}, "p9", [])]}
    assert _qa_subject_panels(lc) == []
