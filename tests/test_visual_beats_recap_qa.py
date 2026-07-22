"""visual_beats ported to RECAP + Q&A (2026-07-18).

Micro_moment already emits per-scene `visual_beats` (verbatim fragments → one panel per
drawn moment). This suite covers the port to the other two writers so Stage 5 cuts per
fragment there too instead of holding one panel for a whole 11-12s Q&A scene:

  1. both Q&A writer prompts carry the simplicity rules + the VISUAL BEATS contract;
  2. the recap writer prompt carries the same VISUAL BEATS contract;
  3. _to_narration carries verbatim STRING beats through, and DROPS a stale multi-fragment
     split (kept safe after post-writer text normalisation) so Stage 5 never mis-slices;
  4. review_gate.build_candidates emits one review ROW per fragment for a NON-micro (recap)
     scene that has visual_beats;
  5. the Q&A chunk-locked builder SPLITS a scene by its fragments (not an even time-share),
     drawing each fragment's panel from Master's locked pool.

No network: embeddings / matcher / thumbs are stubbed the same way the sibling suites do.
"""
import json
import re

import pytest

import config
import stages.stage_3.explore_answer as ea
import stages.stage_3.write_script as ws
import stages.review_gate as rg
import stages._embedding as _embedding
import stages._panel_index as _panel_index
from stages.stage_3.schema import Beat, Glossary
from stages.stage_5 import shots
from stages.stage_5.shots import build_shots


# ─── 1 + 2: writer prompts carry the new contracts ───────────────────────────

def test_qa_prompts_carry_simplicity_and_visual_beats_rules():
    for system in (ea._EXPLORE_WRITE_SYSTEM_LIST, ea._EXPLORE_WRITE_SYSTEM_EXPLAIN):
        # Fix 3 — the three simplicity rules ported from micro_moment
        assert "ONE EVENT PER SENTENCE" in system
        assert "TELL THE STORY, NOT THE PICTURES" in system
        assert "STRIP JARGON" in system and "would a first-time viewer know it" in system
        # Fix 2 — the visual-beats contract + it landing in the return shape
        assert "VISUAL BEATS" in system and "VERBATIM ONLY" in system
        assert '"visual_beats"' in system
        # existing household-name rule is untouched (must not be contradicted)
        assert "Name only household names" in system


def test_recap_write_system_carries_visual_beats():
    assert "VISUAL BEATS" in ws._WRITE_SYSTEM and "VERBATIM ONLY" in ws._WRITE_SYSTEM
    # the JSON return-shape example the writer sees names the field too (built in write_scenes'
    # user prompt) — assert it via a tiny capture of that prompt.
    captured = {}

    def _fake(*, system, user, **kw):
        captured["user"] = user
        return json.dumps({"scenes": [{"text": "x", "visual_beats": ["x"],
                                        "connective": None, "beat_id": 1}]}), "m"

    mp = pytest.MonkeyPatch()
    mp.setattr(ws, "call_with_chain", _fake)
    try:
        beat = Beat(id=1, function="SETUP", name="b", summary="s", page_refs=[3],
                    key_panels=[], cause="", characters_active=["Frank"])
        page = {"page_number": 3, "is_story_page": True,
                "image_dimensions": {"width": 1000, "height": 1500},
                "panels": [{"index": 0, "bbox": {"x": 0, "y": 0, "w": 900, "h": 900},
                            "description": "d", "dialog": []}]}
        ws.write_scenes([beat], Glossary(characters={}), {"title": "T", "plot_summary": "p"},
                        [page], "recap_summary")
    finally:
        mp.undo()
    assert '"visual_beats"' in captured["user"]


# ─── 3: _to_narration carries verbatim beats, drops a stale split ─────────────

def test_to_narration_carries_verbatim_string_beats_and_drops_stale():
    parsed = {"scenes": [
        # verbatim (fragments concatenate back to text) → KEPT as a 2-way split
        {"text": "Frank hunts the giant, then he wins.",
         "visual_beats": ["Frank hunts the giant,", "then he wins."],
         "connective": None, "beat_id": 1},
        # NON-verbatim (text was normalised "vs."->"versus" AFTER the writer split it) → DROPPED
        {"text": "Frank versus the world today.",
         "visual_beats": ["Frank vs the", "world today."],
         "connective": None, "beat_id": 2},
    ]}
    nar = ws._to_narration(parsed, [], Glossary(characters={}), "recap_summary", "m")
    assert [ws_beat_text(b) for b in nar.scenes[0].visual_beats] == \
        ["Frank hunts the giant,", "then he wins."]
    assert nar.scenes[1].visual_beats == []          # stale split dropped → one held panel (safe)


def ws_beat_text(b):
    return b["text"] if isinstance(b, dict) else str(b)


# ─── 4: review_gate emits per-fragment rows for a NON-micro scene ────────────

def test_build_candidates_emits_per_fragment_rows_for_recap(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "recap"
    proj.mkdir()
    # recap (no "mode": micro; no answer_research) scene carrying verbatim visual_beats
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "Frank hunts, then wins.", "page_ref": 10, "panel_ref": 0,
         "visual_beats": ["Frank hunts,", "then wins."]},
    ]}))

    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        for _ in units:
            candidates_out.append([
                {"page": 10, "panel_idx": 0, "score": 9.0, "cosine": 0.8,
                 "panel": {"description": "d", "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}},
                 "src": "p10.png"}])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)

    beats = json.loads(rg.build_candidates("recap", k=5).read_text())["beats"]
    assert {b["unit"] for b in beats} == {"fragment"}                 # per-fragment rows now
    assert {b["beat_key"] for b in beats} == {"2:0", "2:1"}
    frag0 = next(b for b in beats if b["beat_key"] == "2:0")
    assert frag0["narration_text"] == "Frank hunts,"                  # fragment text, not whole scene
    assert frag0["pre_selected"] == []                               # string beat → no pin


# ─── 5: Q&A chunk-locked builder splits a scene by its fragments ─────────────

def _pg(panels, src, w=600, h=2700):
    return {"panels": panels, "source_image": src,
            "image_dimensions": {"width": w, "height": h}}


def _panel_at(y, desc):
    return {"bbox": {"x": 0, "y": y, "w": 600, "h": 900}, "description": desc, "characters": []}


def _fake_score(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    cw = set(re.findall(r"[a-z]+", (chunk_text or "").lower()))
    dw = set(re.findall(r"[a-z]+", str(panel.get("description", "")).lower()))
    sim = min(0.9, 0.3 * len(cw & dw))
    return sim, sim


def test_qa_locked_splits_by_fragment_from_lock_pool(tmp_path, monkeypatch):
    """A Q&A scene with 3 visual_beats but only 2 panels locked ("1:0","1:1") → 3 shots (one per
    FRAGMENT), the panels reused across the extra fragment. This distinguishes the fragment path
    from the OLD time-partition, which capped K at #locked panels (2) → only 2 shots. Also proves
    the lock pool is gathered from the per-fragment keys."""
    _VB = ["punisher vomit", "deadpool punch", "carnage grin"]
    _TEXT = " ".join(_VB)
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "qa"
    (proj / "review").mkdir(parents=True)
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 1, "text": _TEXT, "visual_beats": _VB}]}))
    (proj / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": {
        "1:0": {"panels": [{"page": 5, "panel": 0}], "source": "batcave"},
        "1:1": {"panels": [{"page": 5, "panel": 1}], "source": "batcave"}}}))

    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(_panel_index, "load_vectors", lambda project: {})
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    monkeypatch.setattr(shots, "_blend_image_content", lambda *a, **k: None)
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)

    pages = {5: _pg([_panel_at(0, "punisher vomit"), _panel_at(900, "deadpool punch")], "p5.png")}
    # word-aligned caption chunks so _split_members_by_clause buckets one fragment each; ~2.5s each
    chunks = [{"text": t, "start": 2.5 * i, "end": 2.5 * (i + 1)} for i, t in enumerate(_VB)]
    timings = [{"scene_id": 1, "start": 0.0, "end": 7.5}]
    built = build_shots({"scenes": [{"scene_id": 1, "text": _TEXT, "visual_beats": _VB}]},
                        scene_timings=timings, caption_chunks=chunks,
                        pages_by_number=pages, project="qa")

    assert len(built) == 3                                       # one shot PER fragment (>#locks)
    assert all(s.source_image == "p5.png" for s in built)
    ys = [s.panel_bbox["y"] for s in built]
    assert set(ys) == {0, 900}                                  # both locked panels used, one reused


# ─── 6 + 7: recap WITH locks + visual_beats keeps its fragment split (live bug) ──

def test_recap_with_visual_beats_and_locks_routes_to_per_chunk_and_pins(tmp_path, monkeypatch):
    """Live bug (wolverine-debt-of-death): recap with visual_beats + scene-level locks used to
    route the WHOLE project through the Q&A chunk-LOCKED builder, collapsing every scene to 1
    shot. Now a visual_beats recap takes the per-chunk (unlocked) builder and the locks are pinned
    onto the fragments. A LEGACY recap with NO visual_beats still uses the locked builder."""
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "recap"
    (proj / "review").mkdir(parents=True)
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "batcave"}))  # NOT Q&A
    (proj / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": {
        "2": {"panels": [{"page": 5, "panel": 0}], "source": "batcave"}}}))

    calls = []
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    monkeypatch.setattr(shots, "_build_shots_per_chunk_locked", lambda *a, **k: calls.append("locked") or [1])
    monkeypatch.setattr(shots, "_build_shots_per_chunk", lambda *a, **k: calls.append("chunk") or [1])
    monkeypatch.setattr(shots, "_load_sentence_panels", lambda project: None)

    narration = {"scenes": [                                   # no "mode" → recap
        {"scene_id": 2, "text": "he runs then hides", "visual_beats": ["he runs", "then hides"]}]}
    chunks = [{"text": "he runs then hides", "start": 0.0, "end": 3.0}]
    pages = {5: _pg([_panel_at(0, "run"), _panel_at(900, "hide")], "p5.png")}
    shots.build_shots(narration, caption_chunks=chunks, pages_by_number=pages, project="recap")

    assert calls == ["chunk"]                                  # per-chunk builder, NOT the locked one
    vbs = narration["scenes"][0]["visual_beats"]               # scene-level lock pinned EVERY fragment
    assert all(isinstance(b, dict) and (b["page"], b["panel"]) == (5, 0) for b in vbs)


def test_recap_same_panel_lock_fragments_stay_separate_shots(tmp_path, monkeypatch):
    """A recap scene locked to ONE panel but with 3 fragments must render 3 SHOTS on that panel
    (varied motion), NOT collapse to 1 — PIN-DUP MERGE is micro-only now. Drives _build_shots_per_chunk
    directly: all 3 fragments are pinned to (5,0), so no matcher/network is needed."""
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)
    narration = {"mode": "recap_summary", "scenes": [
        {"scene_id": 1, "text": "a b c", "visual_beats": [
            {"text": "a", "page": 5, "panel": 0},
            {"text": "b", "page": 5, "panel": 0},
            {"text": "c", "page": 5, "panel": 0}]}]}
    chunks = [{"text": t, "start": 2.0 * i, "end": 2.0 * (i + 1)} for i, t in enumerate("abc")]
    timings = [{"scene_id": 1, "start": 0.0, "end": 6.0}]
    pages = {5: _pg([_panel_at(0, "the one panel")], "p5.png")}

    built = shots._build_shots_per_chunk(narration, chunks, pages, timings, word_timestamps=None)
    assert len(built) == 3                                     # 3 fragments → 3 shots (not merged)
    assert all(s.panel_bbox["y"] == 0 and s.source_image == "p5.png" for s in built)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
